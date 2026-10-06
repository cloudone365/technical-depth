#!/usr/bin/env python3
"""Tiny OpenAI-compatible mock LLM (Chapters 11, 20).

Lets you test ingress, streaming, timeouts, auth and autoscaling with zero GPU.
  GET  /health                -> 200 "ok"
  GET  /v1/models             -> one fake model
  POST /v1/chat/completions   -> JSON, or SSE when {"stream": true}
Env: TOKENS_PER_SEC (default 20), MODEL_NAME, STARTUP_DELAY (simulated weight load)
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TPS = float(os.environ.get("TOKENS_PER_SEC", "20"))
MODEL = os.environ.get("MODEL_NAME", "mock-llm")
WORDS = ("The DGX Spark shares one pool of LPDDR5x memory between the Grace CPU "
         "and the Blackwell GPU so there is no PCIe copy between them .").split()
READY_AT = time.time() + float(os.environ.get("STARTUP_DELAY", "0"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # one line per request, to stdout
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            if time.time() < READY_AT:
                return self._json(503, {"status": "loading"})
            return self._json(200, {"status": "ok"})
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": "not found"})
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        n = int(req.get("max_tokens", 32))
        tokens = [WORDS[i % len(WORDS)] for i in range(n)]
        if not req.get("stream"):
            time.sleep(n / TPS)
            return self._json(200, {"id": "cmpl-mock", "object": "chat.completion", "model": MODEL,
                                    "choices": [{"index": 0, "finish_reason": "length",
                                                 "message": {"role": "assistant", "content": " ".join(tokens)}}],
                                    "usage": {"completion_tokens": n}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for tok in tokens:
            chunk = {"object": "chat.completion.chunk", "model": MODEL,
                     "choices": [{"index": 0, "delta": {"content": tok + " "}}]}
            self._chunk(f"data: {json.dumps(chunk)}\n\n")
            time.sleep(1.0 / TPS)
        self._chunk("data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")

    def _chunk(self, s):
        b = s.encode()
        self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
