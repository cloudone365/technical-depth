#!/usr/bin/env python3
"""Hugging Face Hub stand-in for catalog_drift.py (Volume 33): /api/models/<repo>/revision/main
returns a SHA derived from the repo name; POST /_bump changes every SHA (an upstream push)."""
import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SALT = [""]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        repo = self.path.split("/api/models/", 1)[1].rsplit("/revision/", 1)[0]
        self.reply({"sha": hashlib.sha1((repo + SALT[0]).encode()).hexdigest(), "lastModified": "2026-09-30T00:00:00Z"})

    def do_POST(self):
        SALT[0] += "x"
        self.reply({})


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
