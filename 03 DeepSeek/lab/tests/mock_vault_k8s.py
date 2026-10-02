#!/usr/bin/env python3
"""One HTTP server that plays both Vault (KV v2 + Kubernetes auth) and the Kubernetes
API (Secrets + workload PATCH), for testing vault_sync.py without either (Volume 32).
  python3 tests/mock_vault_k8s.py 18769     # GET /_state shows secrets and restarts
POST /_rotate changes the Vault value, so the next sync must update + restart."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KV = {"kv/data/spark-lab/deepseek/litellm": {"master_key": "sk-one"}}
SECRETS, RESTARTS = {}, []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        if self.path == "/_state":
            return self.send(200, {"secrets": SECRETS, "restarts": RESTARTS})
        if self.path.startswith("/v1/kv/data/"):
            return self.send(200, {"data": {"data": KV[self.path[4:]]}})
        name = self.path.rsplit("/", 1)[-1]
        if "/secrets/" in self.path:
            return self.send(200, SECRETS[name]) if name in SECRETS else self.send(404, {"reason": "NotFound"})
        self.send(404, {})

    def do_POST(self):
        b = self.body()
        if self.path.endswith("/login"):
            return self.send(200, {"auth": {"client_token": "t", "policies": ["deepseek-serving"], "lease_duration": 600}})
        if self.path == "/_rotate":
            KV["kv/data/spark-lab/deepseek/litellm"]["master_key"] = "sk-two"
            return self.send(200, {})
        SECRETS[b["metadata"]["name"]] = b
        self.send(201, b)

    def do_PUT(self):
        b = self.body()
        SECRETS[b["metadata"]["name"]] = b
        self.send(200, b)

    def do_PATCH(self):
        self.body()
        RESTARTS.append(self.path.rsplit("/", 2)[-2] + "/" + self.path.rsplit("/", 1)[-1])
        self.send(200, {})


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
