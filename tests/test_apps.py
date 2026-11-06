"""The web app, the worker script and the console, each started for real. Redis is the real redis-server."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
import sim  # noqa: E402

ROOT = sim.ROOT
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url):
    return OPENER.open(url, timeout=5).read().decode()


def post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
    return OPENER.open(req, timeout=5).read().decode()


class Procs(unittest.TestCase):
    procs = []

    @classmethod
    def launch(cls, cmd, **env):
        p = subprocess.Popen(cmd, env={**os.environ, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        cls.procs.append(p)
        return p

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        cls.procs = []

    @staticmethod
    def wait(url):
        end = time.time() + 15
        while True:
            try:
                return get(url)
            except Exception:
                if time.time() > end:
                    raise
                time.sleep(0.2)


class WebApp(Procs):
    @classmethod
    def setUpClass(cls):
        cls.port = sim.free_port()
        cls.launch([sys.executable, os.path.join(ROOT, "app", "web.py")], PORT=str(cls.port), POD_NAME="pod-test")
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.wait(cls.base + "/healthz")

    def test_page_names_the_pod(self):
        self.assertIn("pod-test", get(self.base + "/"))

    def test_work_burns_cpu_and_reports_pod(self):
        t = time.thread_time()
        start = time.time()
        body = json.loads(get(self.base + "/work?ms=80"))
        self.assertEqual(body["pod"], "pod-test")
        self.assertGreaterEqual(time.time() - start, 0.07)
        self.assertEqual(body["burned_ms"], 80)

    def test_work_is_capped_and_validated(self):
        self.assertEqual(json.loads(get(self.base + "/work?ms=99999"))["burned_ms"], 500)
        with self.assertRaises(Exception) as ctx:
            get(self.base + "/work?ms=abc")
        self.assertIn("400", str(ctx.exception))

    def test_hits_are_counted(self):
        before = json.loads(get(self.base + "/info"))["hits"]
        get(self.base + "/work?ms=1")
        self.assertEqual(json.loads(get(self.base + "/info"))["hits"], before + 1)

    def test_unknown_path_is_404(self):
        with self.assertRaises(Exception) as ctx:
            get(self.base + "/nope")
        self.assertIn("404", str(ctx.exception))


@unittest.skipUnless(sim.AVAILABLE, sim.SKIP_REASON)
class Worker(Procs):
    def test_worker_processes_every_job_and_finishes_its_job_on_sigterm(self):
        port = sim.free_port()
        tmp = tempfile.mkdtemp()
        self.launch(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no", "--dir", tmp])
        time.sleep(0.8)
        cli = lambda *a: subprocess.run(["redis-cli", "-p", str(port), *a], capture_output=True, text=True).stdout.strip()
        for i in range(6):
            cli("rpush", "jobs", f"j{i}")
        env = dict(os.environ, REDIS_HOST="127.0.0.1", REDIS_PORT=str(port), WORK_SECONDS="0.3")
        worker = subprocess.Popen(["sh", os.path.join(ROOT, "app", "worker.sh")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 20
        while cli("get", "processed") != "6" and time.time() < end:
            time.sleep(0.2)
        self.assertEqual(cli("get", "processed"), "6")
        self.assertEqual(cli("llen", "jobs"), "0")
        # a job taken just before SIGTERM must still be counted
        cli("rpush", "jobs", "last")
        end = time.time() + 5
        while cli("llen", "jobs") != "0" and time.time() < end:
            time.sleep(0.05)
        worker.send_signal(signal.SIGTERM)
        self.assertEqual(worker.wait(timeout=5), 0)
        self.assertEqual(cli("get", "processed"), "7")
        shutil.rmtree(tmp, ignore_errors=True)


class FakeKube(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p.endswith("/deployments/web"):
            body = {"spec": {"replicas": 4}, "status": {"readyReplicas": 3}}
        elif p.endswith("/deployments/workers"):
            body = {"spec": {"replicas": 0}, "status": {}}
        elif p.endswith("/horizontalpodautoscalers/web"):
            body = {"spec": {"minReplicas": 1, "maxReplicas": 6},
                    "status": {"desiredReplicas": 4, "currentMetrics": [{"type": "Resource", "resource": {"current": {"averageUtilization": 85}}}]}}
        elif "metrics.k8s.io" in p:
            body = {"items": [{"containers": [{"usage": {"cpu": "50000000n"}}]}, {"containers": [{"usage": {"cpu": "25m"}}]}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(sim.AVAILABLE, sim.SKIP_REASON)
class Console(Procs):
    @classmethod
    def setUpClass(cls):
        kube_port, redis_port, cls.port = sim.free_port(), sim.free_port(), sim.free_port()
        cls.kube = ThreadingHTTPServer(("127.0.0.1", kube_port), FakeKube)
        threading.Thread(target=cls.kube.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()
        cls.launch(["redis-server", "--port", str(redis_port), "--save", "", "--appendonly", "no", "--dir", cls.tmp])
        time.sleep(0.8)
        for args in (["rpush", "jobs", "a", "b", "c"], ["set", "processed", "41"]):
            subprocess.run(["redis-cli", "-p", str(redis_port), *args], capture_output=True)
        cls.launch([sys.executable, os.path.join(ROOT, "app", "console.py")], PORT=str(cls.port), KUBE_API=f"http://127.0.0.1:{kube_port}",
                   REDIS_HOST="127.0.0.1", REDIS_PORT=str(redis_port), SAMPLE_EVERY="0.3", TOKEN_FILE="/nonexistent")
        cls.base = f"http://127.0.0.1:{cls.port}/console"
        cls.wait(cls.base + "/healthz")

    @classmethod
    def tearDownClass(cls):
        cls.kube.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def test_state_reads_cluster_and_queue(self):
        s = json.loads(get(self.base + "/state"))
        self.assertEqual(s["web"], {"desired": 4, "ready": 3})
        self.assertEqual(s["workers"]["ready"], 0)
        self.assertEqual(s["hpa"]["cpu_percent"], 85)
        self.assertEqual(s["hpa"]["desired"], 4)
        self.assertEqual(s["cpu_millicores"], 75.0)          # 50m (as nanocores) + 25m
        self.assertEqual(s["queue"], {"length": 3, "processed": 41})

    def test_history_fills_by_itself(self):
        time.sleep(1.2)
        self.assertGreaterEqual(len(json.loads(get(self.base + "/state"))["history"]), 2)

    def test_results_can_be_posted_and_are_listed_newest_first(self):
        post(self.base + "/result", {"name": "first", "ok": True, "details": "d1"})
        post(self.base + "/result", {"name": "second", "ok": False, "details": "d2"})
        results = json.loads(get(self.base + "/state"))["results"]
        self.assertEqual([r["name"] for r in results[:2]], ["second", "first"])
        self.assertFalse(results[0]["ok"])

    def test_bad_result_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            post(self.base + "/result", {"nope": 1})
        self.assertIn("400", str(ctx.exception))

    def test_page_is_served(self):
        self.assertIn("Autoscaling", get(self.base + "/"))


class Parsing(unittest.TestCase):
    def test_cpu_units(self):
        sys.path.insert(0, os.path.join(ROOT, "app"))
        import console
        self.assertEqual(console.cpu_millicores("500000000n"), 500.0)
        self.assertEqual(console.cpu_millicores("250m"), 250.0)
        self.assertEqual(console.cpu_millicores("2"), 2000.0)
        self.assertEqual(console.cpu_millicores("1500u"), 1.5)


if __name__ == "__main__":
    unittest.main()
