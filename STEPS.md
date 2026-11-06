# STEPS: Autoscaling and Load Testing (k6, HPA, KEDA on k3s)

## Prerequisites (set these up BEFORE step 1)

| Need | Details |
|---|---|
| **AWS account** | Any account that can launch EC2. No IAM role, no other AWS service. One region for everything. |
| **Instance type** | `m7i-flex.large` (2 vCPU, 8 GiB). The whole cluster plus KEDA needs about 3 GiB. |
| **OS image (AMI)** | Ubuntu Server 24.04 LTS (64-bit x86). 22.04 also works. |
| **Disk** | 20 GiB gp3. |
| **Key pair** | An RSA `.pem` key pair created in EC2 (step 0a). |
| **Security group** | Created in step 0b. **Inbound: exactly two rules, both with source My IP: SSH TCP 22; Custom TCP 8080 (the app and the console).** **Outbound: leave the default (All traffic, 0.0.0.0/0)**, because the cluster pulls images from Docker Hub and ghcr.io, and the scripts download from GitHub. The Kubernetes API is not exposed. |
| **Code on the instance** | Option A: private GitHub repo plus a fine-grained token (Contents: Read) as the `git clone` password. Option B: copy the zip with `scp` (step 2). |
| **Accounts / tokens** | None. |
| **On your laptop** | A terminal with `ssh`/`scp` and a web browser. **No SSH tunnel is needed:** open `http://<PUBLIC-IP>:8080/console/` directly. |

**Time:** about 25 minutes of setup the first time (preflight downloads images, installs KEDA and runs the HPA and KEDA scenarios once), about 12 minutes for the full tour. **Cost:** about $0.10/hour at your quoted rate, plus pennies for the disk. Do step 11 when finished.

---

## Step 0: Create the key pair, security group and instance (AWS console)

**0a. Key pair.** EC2 > Network & Security > Key pairs > **Create key pair**. Name `as-lab-key`, type **RSA**, format **.pem**. The file downloads once; keep it safe. Lock down its permissions:

```bash
# Linux / macOS
chmod 400 ~/Downloads/as-lab-key.pem
```
```powershell
# Windows PowerShell (OpenSSH)
icacls "$HOME\Downloads\as-lab-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

**0b. Security group.** EC2 > Network & Security > Security Groups > **Create security group**.
- Name: `as-lab-sg`, Description: `SSH and demo pages from my IP`, VPC: the default VPC.
- **Inbound rules > Add rule** (twice; Type `SSH` first, then `Custom TCP`; Protocol `TCP`; **Source `My IP`** both times):
  1. Port range `22` (SSH)
  2. Port range `8080` (the app and the console)
- **Outbound rules:** leave the default rule (`All traffic`, `0.0.0.0/0`).
- Click **Create security group**.

> Showing someone on another network? "My IP" blocks them. Share your screen, or add their public IP followed by `/32` to the 8080 rule. Never use `0.0.0.0/0`.

**0c. Launch the instance.** EC2 > Instances > **Launch instances**.
- Name: `as-lab-box`
- OS Image: **Ubuntu Server 24.04 LTS**, **64-bit (x86)**
- Instance type: `m7i-flex.large`
- Key pair: `as-lab-key`
- Network settings > **Edit**: Auto-assign public IP **Enable**; **Select existing security group** > `as-lab-sg`
- Storage: **20 GiB**, `gp3`
- Advanced details > IAM instance profile: **No IAM role**
- **Launch instance**, wait for `Running` and `2/2 passed`, copy the **Public IPv4 address** (`<PUBLIC-IP>` below).

> If your network changes, SSH and the pages time out: edit **both** inbound rules and set Source to My IP again. Stop/start also changes the public IP.

## Step 1: Connect (from your laptop)

```bash
ssh -i ~/Downloads/as-lab-key.pem ubuntu@<PUBLIC-IP>
```
Type `yes` at the fingerprint prompt. Everything below runs **on the instance**.

## Step 2: Update packages and get the code

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git unzip
```

**Option A (private GitHub repo):**
```bash
git clone https://github.com/<you>/autoscaling-loadtest.git
cd autoscaling-loadtest
```
Username = your GitHub username, password = the fine-grained token.

**Option B (no GitHub). From your laptop, in the folder holding the zip:**
```bash
scp -i ~/Downloads/as-lab-key.pem autoscaling-loadtest.zip ubuntu@<PUBLIC-IP>:~
```
Then on the instance:
```bash
unzip autoscaling-loadtest.zip
cd autoscaling-loadtest
```

## Step 3: Make the scripts executable (do not skip)

Zipped or uploaded files often lose the executable flag, which gives `Permission denied`.

```bash
chmod +x scripts/*.sh scripts/*.py app/worker.sh
ls -l scripts/ app/        # every .sh line should start with -rwxr-xr-x
```

## Step 4: Install Docker and k6

```bash
./scripts/setup.sh
```
Runs `apt update`/`upgrade`, installs base tools and `python3-yaml`, installs Docker Engine and Compose v2 from Docker's repo (fallback: Ubuntu's `docker.io`), then downloads **k6 0.54.0** from GitHub into `/usr/local/bin`. Takes 3-5 minutes and ends with `Setup complete.` If it says to log out and in for the docker group, either do that or keep going (the scripts use `sudo` by themselves).

## Step 5: Preflight (run well before the demo, and do not skip it)

```bash
./scripts/preflight.sh
```
Checks Docker, k6, disk (8 GB), memory (4 GB), port 8080, and that Docker Hub, GitHub and ghcr.io are reachable; pulls the k3s image; **starts the cluster, installs KEDA 2.15.1**, runs `smoke.sh`, then runs the **real `hpa` and `keda` scenarios once** and the unit tests. Every line says `ok` and the last line is `Preflight passed. Both autoscalers work on this instance.` First run: 15-25 minutes, most of it image downloads. These are cached in a Docker volume, so the demo itself starts fast.

## Step 6: Start the stack

```bash
./scripts/up.sh
```
Starts k3s (about 20 s when images are cached), makes sure KEDA, Redis, the web app, the workers and the console are running, and prints:

```
Console : http://<PUBLIC-IP>:8080/console/
App     : http://<PUBLIC-IP>:8080/
```

## Step 7: Health check

```bash
./scripts/smoke.sh
```
Every line says `ok` and it ends with **`All 13 checks passed.`** (Two checks wait up to 90 s for the first CPU numbers.)

## Step 8: Open the console (on your laptop)

`http://<PUBLIC-IP>:8080/console/`: **Web pods** (ready pods, CPU as a percentage of the request, total millicores, what the HPA wants), **Workers** (ready workers, jobs waiting, jobs processed), two charts that fill as tests run, and a **Test results** table. **Keep this tab open.** The app itself is at `http://<PUBLIC-IP>:8080/` (it names the pod that served you).

## Step 9: Run the autoscaling tour (on the instance)

```bash
./scripts/demo.sh gameday          # all three scenarios: about 12 minutes
```
Or one at a time:
```bash
./scripts/demo.sh baseline         # one fixed pod: k6 says BAD (about 2.5 minutes)
./scripts/demo.sh hpa              # HPA: pods 1 -> about 5 -> 1, k6 says GOOD (about 4 minutes)
./scripts/demo.sh keda             # 60 jobs: workers 0 -> several -> 0, 60 processed (about 2 minutes)
```
Each ends with a green `PASS`. In a second terminal, watch it happen:
```bash
./scripts/kubectl.sh -n demo get hpa -w          # CPU and replicas, live
./scripts/kubectl.sh -n demo get pods -w
```
Tune it if your instance is slower than expected (all optional):
```bash
WARM=100 ./scripts/demo.sh hpa                   # a longer ramp-up before the judged phase
LOAD_RATE=8 ./scripts/demo.sh gameday            # fewer requests per second
```

## Step 10: Unit tests (optional)

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
Ends with `OK`. On the instance the repo, manifest, web-app and k6-summary tests run; the tests that need `redis-server` and `redis-cli` are skipped (this guide does not install them).

## Step 11: Clean up, then terminate the instance

```bash
./scripts/destroy.sh
```
Removes the cluster container and its volume (all cached images too) and the downloaded files. In the AWS console: EC2 > Instances > `as-lab-box` > **Instance state > Terminate instance**. Optionally delete `as-lab-sg` and `as-lab-key`.

---

## What you should see

| Step | Result |
|---|---|
| 4 | Docker and `k6 v0.54.0` versions, then `Setup complete.` |
| 5 | All `ok`, then `Preflight passed.` |
| 6 | The console and app URLs with your public IP |
| 7 | `All 13 checks passed.` |
| 8 | Ready pods 1, CPU low, workers 0, queue 0 |
| 9 | `baseline`: steady phase "p95 NNNN ms" far over 500, k6 BAD, `PASS`. `hpa`: "went from 1 pod up to 4-6 pods", k6 GOOD, pods back to 1, `PASS`. `keda`: all `ok`, `PASS`. Then `ALL SCENARIOS PASSED` |
| 10 | `OK` |

## Risky dependencies and fallbacks

| Dependency | Risk | Fallback |
|---|---|---|
| Docker Hub (`rancher/k3s`, `python:3.12-slim`, `redis:7-alpine`) | Rate limit. The k3s node pulls anonymously, so `docker login` on the host does not help it | Wait and run `./scripts/up.sh` again; the images stay cached in the volume once pulled |
| ghcr.io (KEDA images) and GitHub (KEDA manifest, k6) | Blocked or slow | Outbound rule must be All traffic; re-run `./scripts/up.sh` or `./scripts/setup.sh` |
| Speed of the instance | The HPA needs about 45 s to catch up; a slow or busy instance needs longer | `WARM=100 ./scripts/demo.sh hpa`, or a lower `LOAD_RATE` |
| `privileged` k3s container | Fine here, not for production | none needed |
| Do not run `destroy.sh` before the demo | It deletes the cached images (the next start takes 5+ minutes) | Re-run preflight afterwards |
| MinIO, LocalStack, S3 | **Not used** | none needed |

## If something goes wrong

| Symptom | Fix |
|---|---|
| SSH: `Connection timed out` | Your IP changed. Edit `as-lab-sg`, inbound rule 22, Source My IP. Check the instance is Running with a public IP. |
| Console or app never loads | (1) Use `http://` and port 8080; the console is `/console/` with the final slash. (2) The rule needs Source My IP and your current IP. (3) Check the instance's current public IP. (4) On the instance `curl -i http://localhost:8080/console/healthz` should return `200`; if so, it is the security group. |
| Testing the public IP from the instance hangs | Expected. Use `localhost` on the instance. |
| SSH: `Permission denied (publickey)` | User `ubuntu`, the right `.pem`, `chmod 400`. |
| `./scripts/x.sh: Permission denied` | `chmod +x scripts/*.sh scripts/*.py app/worker.sh` (step 3), or `bash scripts/x.sh`. |
| `bad interpreter: /usr/bin/env bash^M` | `sed -i 's/\r$//' scripts/*.sh scripts/*.py app/worker.sh` |
| `k6: command not found` | Run `./scripts/setup.sh` (step 4). |
| Preflight: `Port 8080 is free` fails | `sudo ss -ltnp \| grep ':8080 '`; stop that process, or `./scripts/destroy.sh` if it is an old run. |
| `up.sh`: `The API did not become ready` | `sudo docker logs --tail 40 autoscale-server`. Not enough memory is the usual cause: check `free -m`. |
| `up.sh`: `Could not download KEDA's manifest` | GitHub is not reachable: check the outbound rule. |
| `up.sh`: KEDA or a pod stays `ContainerCreating` or `ImagePullBackOff` | Image downloads are slow or rate-limited: `./scripts/kubectl.sh -n demo get pods`, `./scripts/kubectl.sh -n keda get pods`, then wait; re-run `./scripts/up.sh`. |
| `smoke.sh`: `the HPA can read CPU` fails | metrics-server needs about a minute after the first start. Check `./scripts/kubectl.sh top pods -n demo`; then re-run smoke. |
| `smoke.sh`: `the KEDA ScaledObject is Ready` fails | `./scripts/kubectl.sh -n demo describe scaledobject workers`; usually KEDA is still starting. Re-run `./scripts/up.sh`. |
| `demo.sh hpa`: k6 says BAD although pods scaled | The autoscaler was slower than the ramp-up. `WARM=100 ./scripts/demo.sh hpa`, or `LOAD_RATE=8`. Check `./scripts/kubectl.sh -n demo get hpa`: `<unknown>` under TARGETS means no CPU numbers yet. |
| `demo.sh hpa`: pods do not go back to 1 | Wait: the scale-down waits 30 s, then removes 2 pods per 15 s. A leftover request backlog delays it. |
| `demo.sh baseline`: k6 says GOOD | The instance is faster than expected. Raise the load: `LOAD_RATE=14 ./scripts/demo.sh baseline`. |
| `demo.sh keda`: workers never appear | `./scripts/kubectl.sh -n demo describe scaledobject workers` and `./scripts/kubectl.sh -n keda logs deploy/keda-operator --tail 30`. |
| `demo.sh`: `The cluster is not running` | `./scripts/up.sh` |
| Disk full | `sudo docker system prune -af` (this removes the cached images; the next start downloads them again) |
