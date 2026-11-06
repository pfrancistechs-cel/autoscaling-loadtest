#!/usr/bin/env bash
# Shared helpers. Sourced by the other scripts, not run directly.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# Use plain docker if the current user may talk to the daemon, otherwise sudo.
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi
DC="$DOCKER compose"

SERVER="autoscale-server"                            # the container that IS the Kubernetes node
APP_URL="${APP_URL:-http://127.0.0.1:8080}"          # the web app, as seen from the instance itself
CONSOLE_URL="${CONSOLE_URL:-$APP_URL/console}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results}"
P95_LIMIT="${P95_LIMIT:-500}"                        # ms: the latency promise k6 checks in the steady phase
LOAD_RATE="${LOAD_RATE:-10}"                         # requests per second in the steady phase
LOAD_MS="${LOAD_MS:-30}"                             # CPU milliseconds burned per request
WARM="${WARM:-70}"; STEADY="${STEADY:-40}"           # seconds: ramp-up (not judged) and steady phase (judged)
export APP_URL CONSOLE_URL RESULTS_DIR

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32mok\033[0m   %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
say()  { printf '    %s\n' "$*"; }

set_env() { # KEY VALUE: add the line to .env unless it is already there
  grep "^$1=" "$ROOT/.env" >/dev/null || echo "$1=$2" >> "$ROOT/.env"
}
ensure_env() { # create .env on first run and give the cluster a random join token
  [ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
  set_env K3S_TOKEN "$(openssl rand -hex 16)"
  set -a; . "$ROOT/.env"; set +a
}
ensure_env
KEDA_VERSION="${KEDA_VERSION:-2.15.1}"

# ---- kubectl runs INSIDE the k3s container (nothing to install on the instance) ----
kc() { $DOCKER exec -i "$SERVER" kubectl "$@"; }      # kc get nodes
kd() { kc -n demo "$@"; }                             # same, in the demo namespace

public_ip() { # EC2 metadata (IMDSv2), then a public what-is-my-ip fallback
  local tok ip=""
  tok="$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token \
         -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  if [ -n "$tok" ]; then
    ip="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $tok" \
          http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
  if [ -z "$ip" ]; then
    ip="$(curl -fsS -m 4 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  echo "${ip:-<EC2-PUBLIC-IP>}"
}

http_code() { curl -s -o /dev/null -m 4 -w '%{http_code}' "$@" 2>/dev/null || true; }   # 000 = no connection

wait_http() { # LABEL URL [SECONDS]  (any 2xx)
  local label="$1" url="$2" secs="${3:-60}" i
  for ((i = 0; i < secs; i++)); do
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then ok "$label is up"; return 0; fi
    sleep 1
  done
  fail "$label did not answer at $url within ${secs}s"
  return 1
}

wait_until() { # SECONDS COMMAND...   (retries every second; the command is re-run each time)
  local secs="$1" i; shift
  for ((i = 0; i < secs; i++)); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# ---- conditions for wait_until. They re-read the cluster on every try ("$(...)" typed into wait_until's arguments would run only once) ----
api_ready()   { [ "$(kc get --raw /readyz 2>/dev/null)" = "ok" ]; }
node_ready()  { local out; out="$(kc get nodes --no-headers 2>/dev/null)"; [ "$(awk '$2 == "Ready"' <<<"$out" | wc -l | tr -d ' ')" = "1" ]; }
deploy_ready() { # NAMESPACE NAME: at least one ready replica
  local n; n="$(kc -n "$1" get deploy "$2" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)"; [ "${n:-0}" -ge 1 ]
}
ready_of() { local n; n="$(kd get deploy "$1" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)"; echo "${n:-0}"; }   # NAME -> ready replicas
web_at_least()     { [ "$(ready_of web)" -ge "$1" ]; }        # N
web_at_most()      { [ "$(ready_of web)" -le "$1" ]; }        # N
workers_at_least() { [ "$(ready_of workers)" -ge "$1" ]; }    # N
workers_at_most()  { [ "$(ready_of workers)" -le "$1" ]; }    # N
queue_len()   { kd exec deploy/redis -- redis-cli llen jobs 2>/dev/null | tr -d '[:space:]'; }
processed()   { kd exec deploy/redis -- redis-cli get processed 2>/dev/null | tr -d '[:space:]'; }
queue_empty() { [ "$(queue_len)" = "0" ]; }
processed_is() { [ "$(processed)" = "$1" ]; }                 # N
web_answers() { [ "$(http_code "$APP_URL/healthz")" = "200" ]; }
console_answers() { [ "$(http_code "$CONSOLE_URL/healthz")" = "200" ]; }
hpa_has_metrics() { local v; v="$(kd get hpa web -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null)"; [ -n "$v" ]; }
scaledobject_ready() { [ "$(kd get scaledobject workers -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = "True" ]; }
keda_ready() { deploy_ready keda keda-operator && deploy_ready keda keda-operator-metrics-apiserver; }

apply_code() { # (re)create the three ConfigMaps that carry the code (the app, the worker script, the console)
  python3 "$ROOT/scripts/cm.py" web-code web.py="$ROOT/app/web.py" | kc apply -f - >/dev/null &&
  python3 "$ROOT/scripts/cm.py" worker-script worker.sh="$ROOT/app/worker.sh" | kc apply -f - >/dev/null &&
  python3 "$ROOT/scripts/cm.py" console-code console.py="$ROOT/app/console.py" | kc apply -f - >/dev/null
}
apply_file() { kc apply -f - < "$ROOT/k8s/$1" >/dev/null; }     # NAME.yaml from the k8s folder

post_result() { # NAME OK(true|false) DETAILS: show a line in the console's results table
  python3 - "$1" "$2" "$3" <<'PY' | curl -s -m 4 -o /dev/null -X POST -H 'Content-Type: application/json' -d @- "$CONSOLE_URL/result" || true
import json, sys
print(json.dumps({"name": sys.argv[1], "ok": sys.argv[2] == "true", "details": sys.argv[3]}))
PY
}

run_k6() { # SUMMARY_FILE   runs load/ramp.js against the app; the k6 exit code is returned (99 = thresholds crossed)
  mkdir -p "$RESULTS_DIR"
  k6 run --quiet -e "BASE=$APP_URL" -e "RATE=$LOAD_RATE" -e "MS=$LOAD_MS" -e "WARM=$WARM" -e "STEADY=$STEADY" -e "P95=$P95_LIMIT" \
    --summary-export "$1" "$ROOT/load/ramp.js"
}

# A sampler that records ready web pods every 3 s while a test runs. start_sampler FILE ... stop_sampler
SAMPLER_PID=""
start_sampler() {
  : > "$1"
  ( while true; do echo "$(date +%s) $(ready_of web)" >> "$1"; sleep 3; done ) &
  SAMPLER_PID=$!
}
stop_sampler() { [ -n "$SAMPLER_PID" ] && kill "$SAMPLER_PID" 2>/dev/null; wait "$SAMPLER_PID" 2>/dev/null; SAMPLER_PID=""; return 0; }
max_pods() { awk 'BEGIN {m = 0} $2 + 0 > m {m = $2 + 0} END {print m}' "$1"; }   # FILE -> the most pods seen
