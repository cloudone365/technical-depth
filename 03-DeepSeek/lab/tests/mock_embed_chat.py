#!/usr/bin/env python3
"""Deterministic bag-of-words embeddings + canned chat answer, for testing rag_demo.py in CI."""
import sys
import json, hashlib, math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
def vec(t, d=64):
    v=[0.0]*d
    for w in t.lower().split():
        h=int(hashlib.md5(w.encode()).hexdigest(),16); v[h%d]+=1.0
    n=math.sqrt(sum(x*x for x in v)) or 1; return [x/n for x in v]
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        r=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path.endswith('/embeddings'):
            out={"data":[{"index":i,"embedding":vec(t)} for i,t in enumerate(r['input'])]}
        else:
            out={"choices":[{"message":{"content":"<think>x</think>Use a preStop sleep and a long grace period [1]."}}]}
        b=json.dumps(out).encode(); self.send_response(200); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 8766), H).serve_forever()
