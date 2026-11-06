#!/usr/bin/env python3
"""Prints a Kubernetes ConfigMap (as JSON, which kubectl accepts) holding the given files.
Usage: cm.py NAME KEY=FILE [KEY=FILE ...]    then pipe to: kubectl apply -f -"""
import json
import sys

name, *pairs = sys.argv[1:]
data = {}
for pair in pairs:
    key, _, path = pair.partition("=")
    with open(path, encoding="utf-8") as fh:
        data[key] = fh.read()
print(json.dumps({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": "demo"}, "data": data}))
