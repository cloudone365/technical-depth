#!/usr/bin/env python3
"""Stand-in server for format_check.py (Volume 23): solves the generated problems,
returns the thinking in reasoning_content (like vLLM's parser) and <answer> in content.
Odd-numbered calls get the answer wrong, so correctness must come out near 50 %."""
import itertools
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

N = itertools.count()


def solve(q):
    n = [int(x) for x in re.findall(r"\d+", q)]
    if "checkpoint" in q:
        return n[1] // n[0]
    return n[0] * n[1] - n[2]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ans = solve(req["messages"][-1]["content"]) + (next(N) % 2)
        msg = {"role": "assistant", "reasoning_content": "work it out", "content": f"<answer>{ans}</answer>"}
        out = json.dumps({"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
