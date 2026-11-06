import json
import os
import re
import stat
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = ["scripts/" + f for f in os.listdir(os.path.join(ROOT, "scripts")) if f.endswith(".sh")]

try:
    import yaml
except ImportError:
    yaml = None


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def manifests(name):
    return [d for d in yaml.safe_load_all(read("k8s/" + name)) if d]


class Repo(unittest.TestCase):
    def test_scripts_parse_and_are_executable(self):
        for rel in SCRIPTS + ["scripts/cm.py", "scripts/k6summary.py", "app/worker.sh"]:
            path = os.path.join(ROOT, rel)
            self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR, rel + " is not executable")
            if rel.endswith(".sh"):
                self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0, rel)

    def test_required_files(self):
        for rel in ("docker-compose.yml", ".env.example", ".gitignore", ".gitattributes", "load/ramp.js", "app/web.py", "app/console.py",
                    "README.md", "STEPS.md", "DEMO.md", "DIAGRAM_PROMPT.md"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_wait_until_is_never_given_a_command_substitution(self):
        # `wait_until 60 test "$(x)" = y` runs x once, so the wait can never succeed. Use a helper function instead.
        for rel in SCRIPTS:
            for n, line in enumerate(read(rel).splitlines(), 1):
                if re.search(r"wait_until\s+\d+\s.*\$\(", line) and not line.lstrip().startswith("#"):
                    self.fail(f"{rel}:{n} passes $(...) to wait_until: {line.strip()}")

    def test_no_pipe_into_grep_q(self):
        for rel in SCRIPTS:
            self.assertNotRegex(read(rel), r"\|\s*grep -q", rel)

    def test_only_port_8080_is_published(self):
        text = read("docker-compose.yml")
        self.assertEqual(re.findall(r'^\s*-\s*"(\d+:\d+)"', text, re.M), ["8080:80"])

    def test_every_script_run_by_hand_is_documented(self):
        steps = read("STEPS.md")
        self.assertIn("chmod +x scripts/*.sh scripts/*.py app/worker.sh", steps)
        for cmd in ("setup.sh", "preflight.sh", "up.sh", "smoke.sh", "demo.sh gameday", "destroy.sh"):
            self.assertIn(cmd, steps)

    def test_demo_doc_is_brief(self):
        text = read("DEMO.md")
        self.assertLess(len(text.split()), 450)
        self.assertLess(len(text.splitlines()), 40)


@unittest.skipUnless(yaml, "PyYAML not installed (apt install python3-yaml)")
class Manifests(unittest.TestCase):
    def test_every_manifest_parses(self):
        for f in os.listdir(os.path.join(ROOT, "k8s")):
            self.assertTrue(manifests(f), f)

    def test_hpa_needs_cpu_requests_on_the_scaled_pods(self):
        web = [d for d in manifests("web.yaml") if d["kind"] == "Deployment"][0]
        res = web["spec"]["template"]["spec"]["containers"][0]["resources"]
        self.assertIn("cpu", res["requests"])        # without a CPU request the HPA shows <unknown> and never scales
        self.assertIn("cpu", res["limits"])

    def test_hpa_settings(self):
        hpa = manifests("hpa.yaml")[0]
        self.assertEqual(hpa["spec"]["scaleTargetRef"]["name"], "web")
        self.assertEqual((hpa["spec"]["minReplicas"], hpa["spec"]["maxReplicas"]), (1, 6))
        self.assertEqual(hpa["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"], 70)
        self.assertEqual(hpa["spec"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"], 30)

    def test_keda_scales_the_workers_from_zero_on_the_redis_list_the_workers_read(self):
        so = manifests("scaledobject.yaml")[0]
        self.assertEqual(so["spec"]["minReplicaCount"], 0)
        self.assertEqual(so["spec"]["scaleTargetRef"]["name"], "workers")
        trigger = so["spec"]["triggers"][0]
        self.assertEqual(trigger["type"], "redis")
        self.assertEqual(trigger["metadata"]["listName"], "jobs")
        self.assertIn('lpop jobs', read("app/worker.sh"))
        workers = manifests("workers.yaml")[0]
        self.assertEqual(workers["spec"]["replicas"], 0)

    def test_workers_get_time_to_finish_their_job(self):
        spec = manifests("workers.yaml")[0]["spec"]["template"]["spec"]
        self.assertGreaterEqual(spec["terminationGracePeriodSeconds"], 3)

    def test_code_configmaps_match_what_up_sh_creates(self):
        lib = read("scripts/lib.sh")
        for name in ("web-code", "worker-script", "console-code"):
            self.assertIn(name, lib)
        names = {v["configMap"]["name"] for f in ("web.yaml", "workers.yaml", "console.yaml") for d in manifests(f)
                 if d["kind"] == "Deployment" for v in d["spec"]["template"]["spec"]["volumes"]}
        self.assertEqual(names, {"web-code", "worker-script", "console-code"})

    def test_console_rbac_is_read_only(self):
        role = [d for d in manifests("console.yaml") if d["kind"] == "Role"][0]
        for rule in role["rules"]:
            self.assertTrue(set(rule["verbs"]) <= {"get", "list"}, rule)
        self.assertIn("metrics.k8s.io", [g for r in role["rules"] for g in r["apiGroups"]])

    def test_ingress_paths(self):
        paths = {d["metadata"]["name"]: d["spec"]["rules"][0]["http"]["paths"][0]["path"]
                 for f in ("web.yaml", "console.yaml") for d in manifests(f) if d["kind"] == "Ingress"}
        self.assertEqual(paths, {"web": "/", "console": "/console"})

    def test_images_are_pinned_to_a_tag(self):
        for f in os.listdir(os.path.join(ROOT, "k8s")):
            for d in manifests(f):
                if d["kind"] == "Deployment":
                    for c in d["spec"]["template"]["spec"]["containers"]:
                        self.assertNotIn(":latest", c["image"])
                        self.assertIn(":", c["image"])


if __name__ == "__main__":
    unittest.main()
