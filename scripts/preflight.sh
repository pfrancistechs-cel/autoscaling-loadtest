#!/usr/bin/env bash
# Run this BEFORE the demo. It starts the cluster, installs KEDA, runs the smoke test, and runs the real HPA and KEDA
# scenarios once (about 8 minutes), so any failure and any slow image pull shows up now and not in front of an audience.
# Everything is left running; stop it with ./scripts/destroy.sh only AFTER the demo (it deletes the cached images).
set -uo pipefail
. "$(dirname "$0")/lib.sh"

FAILS=0
check() { # LABEL COMMAND...
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$label"; else fail "$label"; FAILS=$((FAILS + 1)); fi
}
pull_with_retry() { # IMAGE
  local i
  for i in 1 2 3; do
    $DOCKER pull "$1" >/dev/null 2>&1 && return 0
    sleep 3
  done
  return 1
}
# Docker Hub's /v2/ endpoint answers HTTP 401 (login required) when it is reachable.
# So 200 and 401 both mean "reachable". Do not add curl -f here.
hub_reachable() {
  local code
  code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' https://registry-1.docker.io/v2/ || true)"
  [ "$code" = "200" ] || [ "$code" = "401" ]
}
github_reachable() { # the KEDA manifest and k6 come from GitHub; any 2xx or 3xx is fine
  local code
  code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' https://github.com/kedacore/keda/releases || true)"
  [[ "$code" =~ ^[23] ]]
}
ghcr_reachable() { # KEDA's images come from ghcr.io; it answers 200 or 401 on /v2/
  local code
  code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' https://ghcr.io/v2/ || true)"
  [ "$code" = "200" ] || [ "$code" = "401" ]
}
disk_ok()   { [ "$(df -BG --output=avail / | tail -n1 | tr -dc '0-9')" -ge 8 ]; }
mem_ok()    { [ "$(free -m | awk '/^Mem:/{print $2}')" -ge 4000 ]; }
port_free() { ! python3 -c 'import socket, sys; s = socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$1"; }
stack_up()  { "$ROOT/scripts/up.sh"; }
all_tests() { PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests; }

log "System checks"
check "Docker daemon reachable"                  $DOCKER info
check "Docker Compose v2 installed"              $DC version
check "python3 is installed"                     python3 --version
check "k6 is installed"                          k6 version
check "At least 8 GB free disk"                  disk_ok
check "At least 4 GB RAM"                        mem_ok
if [ "$($DC ps -q | wc -l | tr -d ' ')" -gt 0 ]; then
  ok "Stack already running (port checks skipped)"
else
  check "Port 8080 is free"                      port_free 8080
fi
check "Docker Hub reachable (HTTP 200 or 401)"   hub_reachable
check "GitHub reachable (KEDA manifest)"         github_reachable
check "ghcr.io reachable (KEDA images)"          ghcr_reachable

log "Pulling the k3s image (up to 3 tries)"
check "Pull ${K3S_IMAGE:-rancher/k3s:v1.30.5-k3s1}" pull_with_retry "${K3S_IMAGE:-rancher/k3s:v1.30.5-k3s1}"

log "Starting everything (first start: 5-8 minutes of image downloads)"
check "up.sh: cluster, KEDA, app, workers and console are up" stack_up
check "smoke.sh: all 13 checks pass"             "$ROOT/scripts/smoke.sh"
log "The real scenarios, once (about 8 minutes)"
check "demo.sh hpa: pods scale up under load and back down" "$ROOT/scripts/demo.sh" hpa
check "demo.sh keda: workers scale from zero and back to zero" "$ROOT/scripts/demo.sh" keda

log "Tests"
check "Unit tests and repo tests"                all_tests

echo
if [ "$FAILS" -eq 0 ]; then
  echo "Preflight passed. Both autoscalers work on this instance. Next: ./scripts/demo.sh gameday"
  exit 0
fi
echo "Preflight found $FAILS problem(s). See the troubleshooting table in STEPS.md." >&2
echo "Pull failed? Run 'sudo docker login' with a free Docker Hub account." >&2
exit 1
