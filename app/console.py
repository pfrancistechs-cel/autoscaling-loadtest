"""Live console, runs inside the cluster. Reads the Kubernetes API (read-only service account), the Redis queue, and keeps
a short history so the page can draw what the autoscalers did. Served under /console/. Standard library only."""
import collections
import json
import os
import socket
import ssl
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NS = os.environ.get("NAMESPACE", "demo")
KUBE_API = os.environ.get("KUBE_API", "https://kubernetes.default.svc")
TOKEN_FILE = os.environ.get("TOKEN_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/token")
CA_FILE = os.environ.get("CA_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
SAMPLE_EVERY = float(os.environ.get("SAMPLE_EVERY", "2"))

HISTORY = collections.deque(maxlen=240)          # 8 minutes at one sample per 2 s
RESULTS = collections.deque(maxlen=20)           # k6 and scenario results posted by scripts/demo.sh
LOCK = threading.Lock()


def kube(path):
    headers = {}
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            headers["Authorization"] = "Bearer " + fh.read().strip()
    except OSError:
        pass
    ctx = None
    if KUBE_API.startswith("https"):
        ctx = ssl.create_default_context(cafile=CA_FILE) if os.path.exists(CA_FILE) else ssl.create_default_context()
    req = urllib.request.Request(KUBE_API + path, headers=headers)
    with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
        return json.load(resp)


def redis_cmd(*args):
    """A tiny Redis client (RESP over a socket): returns an int, a string, or None."""
    payload = f"*{len(args)}\r\n" + "".join(f"${len(str(a).encode())}\r\n{a}\r\n" for a in args)
    with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=2) as sock:
        sock.sendall(payload.encode())
        buf = b""
        while not buf.endswith(b"\r\n") or (buf.startswith(b"$") and buf.count(b"\r\n") < 2 and not buf.startswith(b"$-1")):
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    kind, rest = buf[:1], buf[1:].split(b"\r\n")
    if kind == b":":
        return int(rest[0])
    if kind == b"$":
        return None if rest[0] == b"-1" else rest[1].decode()
    return None


def cpu_millicores(text):
    if text.endswith("n"):
        return int(text[:-1]) / 1e6
    if text.endswith("u"):
        return int(text[:-1]) / 1e3
    if text.endswith("m"):
        return float(text[:-1])
    return float(text) * 1000


def deployment(name):
    try:
        d = kube(f"/apis/apps/v1/namespaces/{NS}/deployments/{name}")
    except Exception:
        return {"desired": None, "ready": None}
    return {"desired": d["spec"].get("replicas", 0), "ready": d["status"].get("readyReplicas", 0)}


def web_cpu():
    try:
        items = kube(f"/apis/metrics.k8s.io/v1beta1/namespaces/{NS}/pods?labelSelector=app%3Dweb")["items"]
    except Exception:
        return None
    return round(sum(cpu_millicores(c["usage"]["cpu"]) for p in items for c in p["containers"]), 1)


def hpa_info():
    try:
        h = kube(f"/apis/autoscaling/v2/namespaces/{NS}/horizontalpodautoscalers/web")
    except Exception:
        return None
    util = None
    for m in h.get("status", {}).get("currentMetrics", []) or []:
        if m.get("type") == "Resource":
            util = m["resource"]["current"].get("averageUtilization")
    return {"cpu_percent": util, "target": 70, "min": h["spec"].get("minReplicas"), "max": h["spec"]["maxReplicas"],
            "desired": h.get("status", {}).get("desiredReplicas")}


def queue():
    try:
        return {"length": redis_cmd("LLEN", "jobs"), "processed": int(redis_cmd("GET", "processed") or 0)}
    except Exception:
        return {"length": None, "processed": None}


def snapshot():
    hpa = hpa_info()
    return {"t": round(time.time(), 1), "web": deployment("web"), "workers": deployment("workers"), "hpa": hpa,
            "cpu_millicores": web_cpu(), "queue": queue()}


def sampler():
    while True:
        try:
            snap = snapshot()
            with LOCK:
                HISTORY.append({"t": snap["t"], "web": snap["web"]["ready"], "workers": snap["workers"]["ready"],
                                "queue": snap["queue"]["length"], "cpu": (snap["hpa"] or {}).get("cpu_percent")})
        except Exception:
            pass
        time.sleep(SAMPLE_EVERY)


PAGE = """<!doctype html><meta charset=utf-8><title>Autoscaling console</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f5f6f8;color:#1b1f24}
header{background:#18222d;color:#fff;padding:14px 22px}header h1{margin:0;font-size:18px}
main{display:grid;gap:16px;padding:16px;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
section{background:#fff;border-radius:8px;padding:14px;box-shadow:0 1px 3px #0002}
h2{margin:0 0 8px;font-size:15px}.big{font-size:40px;font-weight:700}.muted{color:#667085;font-size:13px}
.row{display:flex;gap:24px;flex-wrap:wrap}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:5px 6px;border-bottom:1px solid #e5e7eb;text-align:left}.ok{color:#0a7a3c;font-weight:600}.bad{color:#b42318;font-weight:600}
svg{width:100%;height:150px;background:#fafbfc;border-radius:6px}
</style>
<header><h1>Autoscaling: HPA (CPU) and KEDA (queue)</h1></header>
<main>
<section><h2>Web pods (HPA)</h2><div class=row>
<div><div class=big id=web>-</div><div class=muted>ready pods</div></div>
<div><div class=big id=cpu>-</div><div class=muted>avg CPU of request (target 70%)</div></div>
<div><div class=big id=mc>-</div><div class=muted>total millicores</div></div></div>
<div class=muted id=hpa></div></section>
<section><h2>Workers (KEDA)</h2><div class=row>
<div><div class=big id=workers>-</div><div class=muted>ready workers (can be 0)</div></div>
<div><div class=big id=queue>-</div><div class=muted>jobs waiting</div></div>
<div><div class=big id=done>-</div><div class=muted>jobs processed</div></div></div></section>
<section><h2>Web: pods (blue) and CPU % (red)</h2><svg id=c1 viewBox="0 0 400 150" preserveAspectRatio="none"></svg></section>
<section><h2>Workers (green) and queue length (orange)</h2><svg id=c2 viewBox="0 0 400 150" preserveAspectRatio="none"></svg></section>
<section style="grid-column:1/-1"><h2>Test results (k6 and scenarios)</h2><table id=results></table></section>
</main>
<script>
const nv=v=>v===null||v===undefined?'-':v;
function line(pts,color,max){if(pts.length<2)return'';const n=pts.length;
 const d=pts.map((v,i)=>v===null?null:[i/(n-1)*400,145-(Math.min(v,max)/max)*135]).filter(Boolean).map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
 return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2"/>`}
function chart(id,a,ca,amax,b,cb,bmax){document.getElementById(id).innerHTML=line(a,ca,amax)+line(b,cb,bmax)}
async function tick(){try{const s=await (await fetch('state')).json();
document.getElementById('web').textContent=nv(s.web.ready)+' / '+nv(s.web.desired);
document.getElementById('cpu').textContent=s.hpa&&s.hpa.cpu_percent!==null?s.hpa.cpu_percent+'%':'-';
document.getElementById('mc').textContent=nv(s.cpu_millicores);
document.getElementById('hpa').textContent=s.hpa?`HPA wants ${nv(s.hpa.desired)} pods (min ${s.hpa.min}, max ${s.hpa.max})`:'no HPA';
document.getElementById('workers').textContent=nv(s.workers.ready);
document.getElementById('queue').textContent=nv(s.queue.length);
document.getElementById('done').textContent=nv(s.queue.processed);
const h=s.history;
chart('c1',h.map(x=>x.web),'#2563eb',8,h.map(x=>x.cpu),'#dc2626',300);
chart('c2',h.map(x=>x.workers),'#16a34a',8,h.map(x=>x.queue),'#ea580c',80);
document.getElementById('results').innerHTML='<tr><th>time</th><th>test</th><th>result</th><th>details</th></tr>'+
 s.results.map(r=>`<tr><td>${r.time}</td><td>${r.name}</td><td class="${r.ok?'ok':'bad'}">${r.ok?'PASS':'FAIL'}</td><td>${r.details}</td></tr>`).join('');
}catch(e){}}
tick();setInterval(tick,2000);
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/console/healthz":
            return self.send(200, b"ok\n", "text/plain")
        if path == "/console/state":
            snap = snapshot()
            with LOCK:
                snap["history"] = list(HISTORY)
                snap["results"] = list(reversed(RESULTS))
            return self.send(200, snap)
        if path == "/console":
            return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/console/result":
            return self.send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            item = {"time": time.strftime("%H:%M:%S"), "name": str(body["name"])[:40], "ok": bool(body["ok"]),
                    "details": str(body.get("details", ""))[:300]}
        except (ValueError, KeyError, TypeError):
            return self.send(400, {"error": "need json {name, ok, details}"})
        with LOCK:
            RESULTS.append(item)
        self.send(200, {"stored": True})


if __name__ == "__main__":
    threading.Thread(target=sampler, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8081"))), Handler).serve_forever()
