#!/usr/bin/env python3
"""Streaming reasoning-model stand-in for stream_probe.py tests (Volume 21).

Streams 20 reasoning deltas then 10 answer deltas, `delay` seconds apart.
  python3 tests/mock_stream.py 18770            # well-behaved stream
  python3 tests/mock_stream.py 18771 buffered   # same bytes, all flushed at the end (a buffering proxy)
  python3 tests/mock_stream.py 18772 truncated  # stops in the reasoning with finish_reason=length
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18770
MODE = sys.argv[2] if len(sys.argv) > 2 else "normal"
DELAY = 0.03


def ev(delta=None, finish=None, usage=None):
    d = {"id": "mock", "object": "chat.completion.chunk", "choices": []}
    if delta is not None or finish:
        d["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
    if usage:
        d["usage"] = usage
    return f"data: {json.dumps(d)}\n\n".encode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        parts = [ev({"role": "assistant"})]
        parts += [ev({"reasoning_content": f"step {i}. "}) for i in range(20)]
        if MODE == "truncated":
            parts += [ev(finish="length"), ev(usage={"prompt_tokens": 30, "completion_tokens": 20})]
        else:
            parts += [ev({"content": w}) for w in "The trip is 205 minutes . ANSWER : 205".split()]
            parts += [ev(finish="stop"), ev(usage={"prompt_tokens": 30, "completion_tokens": 30})]
        parts.append(b"data: [DONE]\n\n")
        if MODE == "buffered":
            time.sleep(DELAY * len(parts))
            self.wfile.write(b"".join(parts))
        else:
            for p in parts:
                self.wfile.write(p)
                self.wfile.flush()
                time.sleep(DELAY)
        self.close_connection = True


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
