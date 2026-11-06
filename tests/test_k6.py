"""The k6 load test with the REAL k6 against the real web app, and the summary judge."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import sim  # noqa: E402

ROOT = sim.ROOT
JUDGE = os.path.join(ROOT, "scripts", "k6summary.py")


def summary(p95, rate, rps=10.0):
    return {"metrics": {"http_req_duration{phase:steady}": {"avg": p95 / 2, "p(95)": p95},
                        "http_req_failed{phase:steady}": {"value": rate}, "http_reqs": {"rate": rps, "count": 400}}}


def judge(data, limit=None):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(data, fh)
    try:
        args = [sys.executable, JUDGE, fh.name] + ([str(limit)] if limit else [])
        r = subprocess.run(args, capture_output=True, text=True)
        return r.returncode, r.stdout
    finally:
        os.remove(fh.name)


class Judge(unittest.TestCase):
    def test_fast_and_clean_is_good(self):
        code, out = judge(summary(120, 0.0))
        self.assertEqual(code, 0)
        self.assertIn("verdict=good", out)
        self.assertIn("p95_ms=120", out)

    def test_slow_is_bad(self):
        self.assertEqual(judge(summary(900, 0.0))[0], 1)

    def test_failures_are_bad(self):
        self.assertEqual(judge(summary(100, 0.02))[0], 1)

    def test_limit_is_a_parameter(self):
        self.assertEqual(judge(summary(300, 0.0), 250)[0], 1)
        self.assertEqual(judge(summary(300, 0.0), 350)[0], 0)

    def test_missing_phase_is_an_error_not_a_pass(self):
        self.assertEqual(judge({"metrics": {"http_reqs": {"rate": 1, "count": 1}}})[0], 2)


@unittest.skipUnless(sim.AVAILABLE, sim.SKIP_REASON)
class RealK6(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = sim.free_port()
        cls.web = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "web.py")], env={**os.environ, "PORT": str(cls.port)},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        import time
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        os.killpg(cls.web.pid, signal.SIGKILL)

    def run_k6(self, rate, ms, p95):
        out = tempfile.mktemp(suffix=".json")
        env = dict(os.environ, no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
        r = subprocess.run(["k6", "run", "--quiet", "-e", f"BASE=http://127.0.0.1:{self.port}", "-e", f"RATE={rate}", "-e", f"MS={ms}",
                            "-e", "WARM=3", "-e", "STEADY=6", "-e", f"P95={p95}", "--summary-export", out, os.path.join(ROOT, "load", "ramp.js")],
                           env=env, capture_output=True, text=True, timeout=120)
        return r.returncode, judge(json.load(open(out)), p95)

    def test_light_load_passes_and_k6_exit_code_agrees(self):
        k6_rc, (judge_rc, out) = self.run_k6(5, 20, 500)
        self.assertEqual(k6_rc, 0)
        self.assertEqual(judge_rc, 0, out)

    def test_overload_fails_and_k6_exit_code_agrees(self):
        # 80 req/s of 30 ms CPU is 2.4 cores of work for a single-threaded Python process: it cannot keep up
        k6_rc, (judge_rc, out) = self.run_k6(80, 30, 300)
        self.assertEqual(k6_rc, 99, out)          # 99 = k6 says a threshold was crossed
        self.assertEqual(judge_rc, 1, out)


if __name__ == "__main__":
    unittest.main()
