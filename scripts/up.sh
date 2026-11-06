#!/usr/bin/env bash
# Starts the cluster, installs KEDA, deploys the app, Redis, the workers and the console, and waits until everything answers.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
mkdir -p "$RESULTS_DIR"; chmod 755 "$RESULTS_DIR"
command -v k6 >/dev/null || die "k6 is missing. Run ./scripts/setup.sh first."

log "Starting the cluster (first start downloads k3s' own images, 1-3 minutes; later starts ~20 seconds)"
$DC up -d

log "Waiting for the Kubernetes API"
wait_until 240 api_ready  || die "The API did not become ready. Run: $DOCKER logs --tail 40 $SERVER"
wait_until 240 node_ready || die "The node never became Ready. Run: $DOCKER logs --tail 40 $SERVER"
ok "API ready, node Ready"
wait_until 300 deploy_ready kube-system traefik        || die "Traefik was not created. Run: ./scripts/kubectl.sh -n kube-system get pods"
wait_until 300 deploy_ready kube-system metrics-server || die "metrics-server is not ready. Run: ./scripts/kubectl.sh -n kube-system get pods"
ok "traefik (the ingress) and metrics-server (CPU numbers for the HPA) are running"

log "Pre-pulling the images the pods use (cached in the k3s volume, so later starts are fast)"
for img in "${PYTHON_IMAGE:-python:3.12-slim}" "${REDIS_IMAGE:-redis:7-alpine}"; do
  $DOCKER exec "$SERVER" crictl pull "$img" >/dev/null 2>&1 && ok "pulled $img" || say "could not pre-pull $img (the pods will pull it themselves)"
done

log "Installing KEDA ${KEDA_VERSION} (the official manifest from GitHub)"
KEDA_YAML="$RESULTS_DIR/keda-${KEDA_VERSION}.yaml"
if [ ! -s "$KEDA_YAML" ]; then
  curl -fsSL --retry 3 -o "$KEDA_YAML" "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml" ||
    die "Could not download KEDA's manifest. Check KEDA_VERSION in .env and that github.com is reachable."
fi
kc apply --server-side --force-conflicts -f - < "$KEDA_YAML" >/dev/null || die "kubectl apply of the KEDA manifest failed"
wait_until 420 keda_ready || die "KEDA did not become ready. Run: ./scripts/kubectl.sh -n keda get pods"
ok "KEDA operator and metrics server are ready"

log "Deploying the demo"
apply_file 00-namespace.yaml
apply_code || die "Could not create the code ConfigMaps"
for f in redis.yaml web.yaml hpa.yaml workers.yaml console.yaml; do apply_file "$f" || die "Could not apply k8s/$f"; done
# KEDA's admission webhook may need a few more seconds after the operator is up; retry the ScaledObject.
wait_until 90 apply_file scaledobject.yaml || die "Could not apply k8s/scaledobject.yaml. Run: ./scripts/kubectl.sh -n keda get pods"
ok "manifests applied"

wait_until 300 deploy_ready demo redis   || die "Redis is not ready. Run: ./scripts/kubectl.sh -n demo get pods"
wait_until 300 deploy_ready demo web     || die "The web app is not ready. Run: ./scripts/kubectl.sh -n demo describe pod -l app=web"
wait_until 300 deploy_ready demo console || die "The console is not ready. Run: ./scripts/kubectl.sh -n demo describe pod -l app=console"
wait_http "the web app" "$APP_URL/healthz" 90 || die "Run: ./scripts/kubectl.sh -n kube-system get pods,svc"
wait_http "the console" "$CONSOLE_URL/healthz" 60 || die "Run: ./scripts/kubectl.sh -n demo logs deploy/console"
wait_until 120 scaledobject_ready && ok "KEDA is watching the queue (ScaledObject Ready)" || say "the ScaledObject is not Ready yet; smoke.sh will check again"

IP="$(public_ip)"
cat <<EOF

Ready. Open these in your browser (from the laptop allowed by the security group):
  Console : http://${IP}:8080/console/    <- keep it open: pods, CPU, queue, workers, test results
  App     : http://${IP}:8080/            <- "Served by pod ..."

Next: ./scripts/smoke.sh   then   ./scripts/demo.sh gameday
EOF
