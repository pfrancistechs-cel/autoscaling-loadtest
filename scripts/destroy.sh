#!/usr/bin/env bash
# Removes everything this project created on the instance: the cluster container, its volume (all state and cached
# images), the downloaded KEDA manifest and the results.   Usage: ./scripts/destroy.sh [--keep-env]
set -uo pipefail
. "$(dirname "$0")/lib.sh"

log "Stopping the cluster and deleting its volume"
$DC down -v --remove-orphans

# k3s starts its pods as containers of its own; if Docker left any behind, remove them
leftover="$($DOCKER ps -aq --filter 'name=^k8s_' --filter 'name=^autoscale-')"
[ -z "$leftover" ] || $DOCKER rm -f $leftover >/dev/null

rm -f "$ROOT"/results/*.json "$ROOT"/results/*.txt "$ROOT"/results/*.yaml
if [ "${1:-}" != "--keep-env" ]; then rm -f "$ROOT/.env"; fi

echo
echo "Cleanup done. (k6 stays installed; the k3s image stays cached: sudo docker image prune -af)"
echo "IMPORTANT: terminate the EC2 instance in the AWS console (EC2 > Instances >"
echo "Instance state > Terminate) so it stops costing money. Then delete the security group."
