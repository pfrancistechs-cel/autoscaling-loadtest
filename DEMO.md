# Demo: Autoscaling and Load Testing (about 7 minutes)

Open `http://<EC2-PUBLIC-IP>:8080/console/` and keep it visible. Left: web pods and CPU (HPA). Right: workers and queue (KEDA).

**1. The idea (30 s).** "Traffic changes, so the number of pods should too. Two triggers: **CPU** (HPA) and **queue length** (KEDA). **k6** proves it works."

**2. Without autoscaling (2 min).**
```bash
./scripts/demo.sh baseline
```
One pod, 10 requests/s. It cannot keep up: k6 says **bad** (p95 far over 500 ms).

**3. With the HPA (3 min).**
```bash
./scripts/demo.sh hpa
```
Same load. Watch pods climb 1 to about 5 and CPU settle near 70%. k6 says **good**. When the load stops, pods return to 1. Reload `http://<EC2-PUBLIC-IP>:8080/`: the pod name changes.

**4. Scale to zero with KEDA (2 min).**
```bash
./scripts/demo.sh keda
```
60 jobs land in a queue. Workers appear from **zero**, the queue drains, all 60 jobs are processed exactly once, workers go back to zero. The HPA cannot do that.

**5. Close (30 s).** "Judge with a promise (p95 and error rate), not a feeling. Set CPU requests or the HPA is blind. Real scale-down waits minutes; ours is 30 s for the demo."

Everything: `./scripts/demo.sh gameday`. Then `./scripts/destroy.sh` and terminate the instance.
