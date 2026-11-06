#!/usr/bin/env bash
# Run any kubectl command against the cluster (kubectl lives inside the k3s container).
# Examples:  ./scripts/kubectl.sh get nodes      ./scripts/kubectl.sh -n demo get pods -o wide
#            ./scripts/kubectl.sh -n demo get hpa -w      ./scripts/kubectl.sh -n demo top pods
set -uo pipefail
. "$(dirname "$0")/lib.sh"
kc "$@"
