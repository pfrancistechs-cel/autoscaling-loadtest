#!/usr/bin/env bash
# Health check of the running demo (run ./scripts/up.sh first). Ends with "All 13 checks passed."
set -uo pipefail
. "$(dirname "$0")/lib.sh"

TOTAL=0; BAD=0
check() { # LABEL COMMAND...
  local label="$1"; shift
  TOTAL=$((TOTAL + 1))
  if "$@" >/dev/null 2>&1; then ok "$label"; else fail "$label"; BAD=$((BAD + 1)); fi
}
server_running() { [ "$($DOCKER inspect -f '{{.State.Running}}' "$SERVER" 2>/dev/null)" = "true" ]; }
work_returns_pod() { [[ "$(curl -s -m 5 "$APP_URL/work?ms=5")" == *'"pod"'* ]]; }
console_state() { [[ "$(curl -s -m 8 "$CONSOLE_URL/state")" == *'"workers"'* ]]; }

check "Docker daemon reachable"                          $DOCKER info
check "the k3s container is running"                     server_running
check "the Kubernetes API is ready"                      api_ready
check "the node is Ready"                                node_ready
check "the metrics API answers (CPU numbers exist)"      wait_until 90 kc get --raw /apis/metrics.k8s.io/v1beta1/nodes
check "KEDA is installed and its operator is ready"      keda_ready
check "the web deployment has a ready pod"               deploy_ready demo web
check "the app answers on port 8080"                     web_answers
check "/work burns CPU and names the pod that served it" work_returns_pod
check "the console is up and reads the cluster"          console_state
check "the HPA can read CPU (not <unknown>)"             wait_until 90 hpa_has_metrics
check "the KEDA ScaledObject is Ready"                   wait_until 60 scaledobject_ready
check "k6 is installed"                                  k6 version

echo
if [ "$BAD" -eq 0 ]; then echo "All $TOTAL checks passed."; exit 0; fi
echo "$BAD of $TOTAL checks failed." >&2
exit 1
