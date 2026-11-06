"""scripts/demo.sh end to end, with real k6, Redis, workers and web app, against a PRETEND cluster (see tests/clustersim.py).
Proves the scenario logic. It cannot prove that the real HPA and KEDA behave the same: preflight.sh does that on the instance.
Load shapes are shortened, and for `baseline` the latency limit is set to 1 ms so that even a fast local app counts as 'bad'."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import sim  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
FAST = {"WARM": "6", "STEADY": "8"}


@unittest.skipUnless(sim.AVAILABLE, sim.SKIP_REASON)
class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = sim.Sim().start()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def run_demo(self, name, **env):
        code, out = self.s.script("demo.sh", name, **env)
        out = ANSI.sub("", out)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  " + name, out)
        return out

    def results(self):
        return json.loads(sim.OPENER.open(self.s.console_url + "/state", timeout=5).read())["results"]

    def test_1_keda(self):
        out = self.run_demo("keda")
        self.assertIn("exactly once (60)", out)
        self.assertTrue(any(r["name"].startswith("keda") and r["ok"] for r in self.results()))

    def test_2_hpa(self):
        out = self.run_demo("hpa", P95_LIMIT="2000", **FAST)
        self.assertIn("went from 1 pod up to", out)
        self.assertTrue(any(r["name"].startswith("hpa") and r["ok"] for r in self.results()))

    def test_3_baseline(self):
        self.run_demo("baseline", P95_LIMIT="1", **FAST)
        self.assertTrue(os.path.exists(os.path.join(self.s.results, "k6-baseline.json")))

    def test_4_baseline_fails_when_the_app_is_actually_fine(self):
        # a real latency limit and a light load: k6 says GOOD, so the baseline scenario must NOT claim it failed
        code, out = self.s.script("demo.sh", "baseline", P95_LIMIT="5000", **FAST)
        self.assertEqual(code, 1, ANSI.sub("", out))
        self.assertIn("FAIL  baseline", ANSI.sub("", out))

    def test_5_smoke_is_not_runnable_without_cluster_details_but_syntax_is_fine(self):
        self.assertEqual(self.s.script("kubectl.sh", "get", "nodes")[0], 0)


if __name__ == "__main__":
    unittest.main()
