#!/usr/bin/env python3
"""Coder stand-in for fim_eval.py (Volume 03): /v1/completions answers the reference middle
for even-numbered tasks and `pass` for odd ones, so pass@1 must be exactly 50 %;
/v1/chat/completions answers every task correctly inside a ```python block."""
import json
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TASKS = [json.loads(line) for line in open(pathlib.Path(__file__).resolve().parent.parent / "data" / "fim_tasks.jsonl")]


def task_for(text):
    return next(t for t in TASKS if t["prefix"] in text)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/completions") and "prompt" in req:
            p = req["prompt"]
            assert p.startswith("<|fim_prefix|>") and "<|fim_suffix|>" in p and p.endswith("<|fim_middle|>")
            t = task_for(p)
            mid = t["middle"] if int(t["id"][-2:]) % 2 == 0 else "    pass\n"
            out = {"choices": [{"index": 0, "text": mid, "finish_reason": "stop"}]}
        else:
            t = task_for(req["messages"][-1]["content"])
            out = {"choices": [{"index": 0, "message": {"role": "assistant",
                                                        "content": "```python\n" + t["middle"] + "```"}}]}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
