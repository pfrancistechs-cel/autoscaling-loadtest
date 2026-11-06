"""Test helper: real web app + real Redis + real console process + the pretend cluster, with the real scripts on top."""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
AVAILABLE = all(shutil.which(t) for t in ("k6", "redis-server", "redis-cli"))
SKIP_REASON = "needs k6, redis-server and redis-cli in PATH (not installed on the demo instance)"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sim:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="as")
        self.bin = os.path.join(self.tmp, "bin")
        self.results = os.path.join(self.tmp, "results")
        os.makedirs(self.bin)
        os.makedirs(self.results)
        self.ports = {k: free_port() for k in ("web", "redis", "sim", "console")}
        self.procs = []
        real_cli = shutil.which("redis-cli")
        with open(os.path.join(self.bin, "redis-cli"), "w") as fh:     # the scripts' `redis-cli` talks to OUR redis port
            fh.write(f'#!/bin/sh\nexec "{real_cli}" -p {self.ports["redis"]} "$@"\n')
        with open(os.path.join(self.bin, "docker"), "w") as fh:
            fh.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(ROOT, "tests", "stubdocker.py")}" "$@"\n')
        for name in ("redis-cli", "docker"):
            os.chmod(os.path.join(self.bin, name), 0o755)
        self.app_url = f"http://127.0.0.1:{self.ports['web']}"
        self.console_url = f"http://127.0.0.1:{self.ports['console']}/console"
        self.env = dict(os.environ, PYTHONUNBUFFERED="1", no_proxy="127.0.0.1", NO_PROXY="127.0.0.1",
                        PATH=self.bin + os.pathsep + os.environ["PATH"], APP_URL=self.app_url, CONSOLE_URL=self.console_url,
                        RESULTS_DIR=self.results, SIM_URL=f"http://127.0.0.1:{self.ports['sim']}/", SIM_APP_URL=self.app_url,
                        SIM_REDIS_PORT=str(self.ports["redis"]), SIM_BIN=self.bin)
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.env.pop(var, None)

    def spawn(self, cmd, **env):
        self.procs.append(subprocess.Popen(cmd, env={**self.env, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True))

    def wait(self, url, secs=20):
        end = time.time() + secs
        while True:
            try:
                OPENER.open(url, timeout=2).read()
                return
            except Exception:
                if time.time() > end:
                    raise RuntimeError("not ready: " + url)
                time.sleep(0.2)

    def start(self):
        self.spawn(["redis-server", "--port", str(self.ports["redis"]), "--save", "", "--appendonly", "no", "--dir", self.tmp])
        self.spawn([sys.executable, os.path.join(ROOT, "app", "web.py")], PORT=str(self.ports["web"]))
        self.spawn([sys.executable, os.path.join(ROOT, "app", "console.py")], PORT=str(self.ports["console"]),
                   KUBE_API="http://127.0.0.1:1", REDIS_HOST="127.0.0.1", REDIS_PORT=str(self.ports["redis"]))
        self.wait(self.app_url + "/healthz")
        self.wait(self.console_url + "/healthz")
        self.spawn([sys.executable, os.path.join(ROOT, "tests", "clustersim.py"), str(self.ports["sim"])])
        end = time.time() + 20
        while True:                                   # the sim answers a POST once it is up
            try:
                self.wait_sim()
                break
            except Exception:
                if time.time() > end:
                    raise
                time.sleep(0.2)
        return self

    def wait_sim(self):
        import json
        req = urllib.request.Request(self.env["SIM_URL"], data=json.dumps({"args": ["get", "--raw", "/readyz"]}).encode(), method="POST")
        OPENER.open(req, timeout=2).read()

    def script(self, *args, timeout=600, **env):
        r = subprocess.run(["bash", os.path.join(ROOT, "scripts", args[0]), *args[1:]], env={**self.env, **env},
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def stop(self):
        for proc in self.procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        subprocess.run(["sh", "-c", "true"])
        time.sleep(0.3)
        shutil.rmtree(self.tmp, ignore_errors=True)
