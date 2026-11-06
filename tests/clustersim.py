"""A tiny pretend Kubernetes cluster, for testing scripts/demo.sh without k3s. It runs as a small HTTP server; the stub
`docker` (tests/stubdocker.py) forwards every `docker exec autoscale-server kubectl ...` to it.

REAL parts: the web app (a real tests process), Redis (real redis-server), the workers (real app/worker.sh processes),
k6 and the scripts. PRETEND parts: the HPA and KEDA control loops (simple rules below) and kubectl itself.
What this proves: the scenario logic, assertions, timing helpers and result handling of demo.sh. What it does NOT prove:
that the real HPA/KEDA behave like these rules. That is checked by preflight.sh on the real cluster."""
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_URL = os.environ["SIM_APP_URL"]
REDIS_PORT = os.environ["SIM_REDIS_PORT"]
BIN = os.environ["SIM_BIN"]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

S = {"web": 1, "hpa": False, "keda": False, "workers": [], "empty_since": None, "idle_since": time.time(), "last_hits": 0,
     "last_t": time.time(), "rate": 0.0}
LOCK = threading.Lock()


def redis(*args):
    return subprocess.run([os.path.join(BIN, "redis-cli"), *args], capture_output=True, text=True).stdout.strip()


def live_workers():
    S["workers"] = [p for p in S["workers"] if p.poll() is None]
    return len(S["workers"])


def tick():
    while True:
        time.sleep(1)
        with LOCK:
            now = time.time()
            try:
                hits = json.load(OPENER.open(APP_URL + "/info", timeout=2))["hits"]
            except Exception:
                hits = S["last_hits"]
            S["rate"] = max(0, hits - S["last_hits"]) / max(0.2, now - S["last_t"])
            S["last_hits"], S["last_t"] = hits, now
            if S["rate"] > 0.5:
                S["idle_since"] = now
            if S["hpa"]:
                want = min(6, max(1, math.ceil(S["rate"] / 3)))
                if want > S["web"]:
                    S["web"] += 1
                elif S["web"] > 1 and now - S["idle_since"] > 4:
                    S["web"] -= 1
            if S["keda"]:
                length = int(redis("-p", REDIS_PORT, "llen", "jobs") or 0)
                if length > 0:
                    S["empty_since"] = None
                    want = min(6, math.ceil(length / 5))
                elif S["empty_since"] is None:
                    S["empty_since"], want = now, live_workers()
                else:
                    want = 0 if now - S["empty_since"] > 3 else live_workers()
                while live_workers() < want:
                    env = dict(os.environ, REDIS_HOST="127.0.0.1", REDIS_PORT=REDIS_PORT, WORK_SECONDS="0.3")
                    S["workers"].append(subprocess.Popen(["sh", os.path.join(ROOT, "app", "worker.sh")], env=env,
                                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                while live_workers() > want:
                    S["workers"].pop().terminate()
                    time.sleep(0.05)


def kubectl(args, stdin):
    a = [x for x in args if x not in ("-i",)]
    if a[:2] == ["-n", "demo"]:
        a = a[2:]
    elif a[:2] in (["-n", "kube-system"], ["-n", "keda"]):
        return "1" if "jsonpath" in " ".join(a) else "ok"
    joined = " ".join(a)
    with LOCK:
        if a[:3] == ["get", "--raw", "/readyz"]:
            return "ok"
        if a[:2] == ["get", "--raw"]:
            return "{}"
        if a[:2] == ["get", "nodes"]:
            return "node1 Ready control-plane 1m v1.30"
        if a[0] == "get" and a[1] == "deploy":
            name = a[2]
            n = {"web": S["web"], "workers": live_workers()}.get(name, 1)
            return str(n) if n else ""
        if a[0] == "get" and a[1] == "hpa":
            return "40"
        if a[0] == "get" and a[1] == "scaledobject":
            return "True"
        if a[0] == "scale":
            if "deploy/web" in a:
                S["web"] = int(joined.split("--replicas=")[1].split()[0])
            return "scaled"
        if a[:2] == ["rollout", "restart"]:
            S["web"] = 1
            return "restarted"
        if a[:2] == ["delete", "hpa"]:
            S["hpa"] = False
            return "deleted"
        if a[0] == "apply":
            if "HorizontalPodAutoscaler" in stdin:
                S["hpa"] = True
            if "ScaledObject" in stdin:
                S["keda"] = True
            return "applied"
        if a[0] == "exec":
            cmd = a[a.index("--") + 1:]
            if cmd[0] == "redis-cli":
                return redis("-p", REDIS_PORT, *cmd[1:])
            if cmd[0] == "sh":
                env = dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"])
                return subprocess.run(["sh", "-c", cmd[2]], env=env, capture_output=True, text=True).stdout
    return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = kubectl(body["args"], body.get("stdin", ""))
        data = out.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    threading.Thread(target=tick, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
