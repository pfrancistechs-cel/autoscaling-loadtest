# Project 19: Autoscaling and Load Testing (k6, HPA, KEDA on k3s)

Two ways Kubernetes adds and removes pods by itself, and a load tester that proves it works:

- **HPA** (Horizontal Pod Autoscaler): scales the web app by **CPU**. Built into Kubernetes.
- **KEDA**: scales workers by **queue length**, including **down to zero pods**, which the HPA cannot do.
- **k6**: generates the load and judges the result against a promise ("p95 latency under 500 ms, under 1% errors").

Everything runs on one EC2 instance: a one-node k3s cluster inside one Docker container. No AWS service and no IAM role.

## How it works

| Piece | What it does |
|---|---|
| `web` (Python, 1 to 6 pods) | `/work?ms=30` burns 30 ms of real CPU, so request rate becomes CPU usage. Each pod asks for 100m CPU and may use 200m, so one pod serves about 6 requests per second. |
| `hpa.yaml` | Keep the average CPU of the web pods near 70% of the request. Scale up at once, scale down after 30 s. |
| `redis` + `workers` (0 to 6 pods) | A job queue (a Redis list) and workers that take a job, "work" 2 s, and count it. |
| `scaledobject.yaml` (KEDA) | One worker per 5 waiting jobs. Empty queue for 15 s: zero workers. |
| `console` (inside the cluster) | Live page at `/console/`: pods, CPU, queue, workers, charts, and each test's result. |
| `load/ramp.js` (k6) | **Open model**: requests arrive at a fixed rate whether or not the app keeps up, like real users. 70 s ramp-up (not judged), then a 40 s steady phase that IS judged. |

## Scenarios (`./scripts/demo.sh <name>`)

| Scenario | What happens | Expected |
|---|---|---|
| `baseline` | The same load on a fixed single pod, no autoscaler | k6 says **bad** (slow answers). One pod cannot serve 10 requests per second |
| `hpa` | The same load with the HPA | Pods go 1 to about 5, k6 says **good**, then pods go back to 1 |
| `keda` | 60 jobs are pushed into the queue | Workers go 0 to several, the queue drains, **all 60 jobs are processed exactly once**, workers go back to 0 |
| `gameday` | All three | `ALL SCENARIOS PASSED` |

Each load test takes about 2 minutes, then up to 2 minutes of scale-down; `keda` takes about 2 minutes.

## Run it

See `STEPS.md` (full guide with the AWS console steps) and `DEMO.md` (the 5-minute script).

```bash
chmod +x scripts/*.sh scripts/*.py app/worker.sh
./scripts/setup.sh        # once: apt update/upgrade, Docker, k6 0.54.0
./scripts/preflight.sh    # starts the cluster, installs KEDA, runs the HPA and KEDA scenarios once
./scripts/up.sh
./scripts/smoke.sh        # All 13 checks passed.
./scripts/demo.sh gameday
./scripts/destroy.sh
```

Security group: inbound 22 and 8080 from My IP. Open `http://<EC2-PUBLIC-IP>:8080/console/` (the live view) and `http://<EC2-PUBLIC-IP>:8080/` (the app). The Kubernetes API is not exposed. `./scripts/kubectl.sh -n demo get hpa -w` watches the HPA from the terminal.

## What to know

- **Requests matter.** The HPA measures CPU against each pod's *request*. Without a CPU request it shows `<unknown>` and never scales. The manifests set one on purpose.
- **Scale-down is slow by design.** The HPA's default waits 5 minutes before removing pods, so traffic dips do not cause flapping. Here it is 30 s so the demo finishes; do not copy that to production.
- **KEDA has two stages.** From 0 to 1 pod KEDA acts itself. From 1 to N it creates an HPA that reads the queue length. That is why it can scale to zero and the plain HPA cannot.
- **Workers must finish their job.** On scale-down a worker receives SIGTERM. `worker.sh` finishes and counts the job in hand before exiting; an earlier version lost it (found by the tests).
- **Capacity is limited.** 2 vCPU are shared by k3s itself, KEDA, k6 and the pods, so the app is capped at 6 pods and the load is sized for about 5.

## Limits (say them in the demo)

- Timing depends on the instance. If the autoscaler is slower than the 70 s ramp-up, the steady phase can fail: `./scripts/demo.sh hpa` with `WARM=100` gives it longer (see STEPS.md).
- The cluster is one node, so there is no node autoscaling (Cluster Autoscaler or Karpenter would add machines; not possible on one instance).
- The load generator runs on the same machine as the cluster. In real tests it runs elsewhere so it does not compete for CPU.
- `privileged` k3s container: fine for a throwaway instance, not for production.

## Tests

`python3 -m unittest discover -s tests -v`. Always run: repo and manifest checks, the k6 summary judge, the web app. They need `k6`, `redis-server` and `redis-cli` (not installed on the demo instance, so skipped there): the worker and console against a real Redis, k6 against the real app (light load passes, overload fails), and `demo.sh` end to end against a **pretend cluster** (`tests/clustersim.py`). The pretend cluster follows simple rules instead of the real HPA and KEDA, so it checks the scenario logic only. Whether the real autoscalers behave is what `preflight.sh` checks, on the instance. Manifests were also checked against the Kubernetes and KEDA schemas with `kubeconform`.
