#!/usr/bin/env python3
"""A stand-in for `docker` for tests only. `docker exec ... autoscale-server kubectl ARGS` is forwarded (with stdin) to the
pretend cluster in tests/clustersim.py; everything else succeeds quietly."""
import json
import os
import sys
import urllib.request

SIM = os.environ.get("SIM_URL", "")


def main(argv):
    if argv[:1] == ["exec"] and "kubectl" in argv:
        args = argv[argv.index("kubectl") + 1:]
        stdin = sys.stdin.read() if "apply" in args else ""      # only `apply -f -` sends a manifest on stdin
        req = urllib.request.Request(SIM, data=json.dumps({"args": args, "stdin": stdin}).encode(), method="POST")
        out = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20).read().decode()
        sys.stdout.write(out + ("\n" if out and not out.endswith("\n") else ""))
        return 0
    if argv[:1] == ["inspect"]:
        print("true")
    if argv[:2] == ["compose", "ps"]:
        print("c1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
