"""The web app that gets autoscaled. /work burns real CPU for ms milliseconds, so load turns into CPU usage, which is
what the Horizontal Pod Autoscaler watches. Standard library only (runs in python:3.12-slim from a ConfigMap)."""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

POD = os.environ.get("POD_NAME", socket.gethostname())
STARTED = time.time()
HITS = 0
LOCK = threading.Lock()
MAX_MS = 500


def burn(ms):
    """Spend about ms milliseconds of CPU time (not wall time: a throttled pod takes longer, like a real one)."""
    end = time.thread_time() + ms / 1000.0
    x = 0
    while time.thread_time() < end:
        x += 1
    return x


PAGE = """<!doctype html><meta charset=utf-8><title>shop</title>
<body style="font-family:system-ui;max-width:640px;margin:3rem auto;padding:0 1rem">
<h1>shop</h1><p>Served by pod <b>{pod}</b>. Reload a few times while the autoscaler works: the pod name changes as pods are added.</p>
<p>Live view: <a href="/console/">/console/</a></p></body>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Pod", POD)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        global HITS
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self.send(200, "ok\n", "text/plain")
        if url.path == "/work":
            try:
                ms = max(0, min(MAX_MS, int(parse_qs(url.query).get("ms", ["30"])[0])))
            except ValueError:
                return self.send(400, {"error": "ms must be an integer"})
            burn(ms)
            with LOCK:
                HITS += 1
                hits = HITS
            return self.send(200, {"pod": POD, "burned_ms": ms, "hits": hits})
        if url.path == "/info":
            return self.send(200, {"pod": POD, "hits": HITS, "uptime_seconds": round(time.time() - STARTED, 1)})
        if url.path == "/":
            return self.send(200, PAGE.format(pod=POD), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
