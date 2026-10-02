#!/usr/bin/env python3
"""Teacher/student stand-in for synth_data.py (Volume 10). Call k of the teacher returns:
k%5==1 → invalid JSON, k%5==2 → wrong answer (fails verification), k%5==3 → the same
problem as the previous valid one (exact duplicate), otherwise a fresh valid problem.
The student answers correctly only for even fresh problems."""
import itertools
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

K = itertools.count()
LAST = {"q": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "response_format" in req:
            k = next(K)
            a, b = 7 + k, 3 + 2 * k
            if k % 5 == 1:
                text = "{not json"
            elif k % 5 == 2:
                text = json.dumps({"question": f"Bad {k}", "expression": f"{a}*{b}", "answer": a * b + 1, "solution": "x"})
            elif k % 5 == 3 and LAST["q"]:
                text = json.dumps(LAST["q"])
            else:
                item = {"question": f"Team {k} runs {a} jobs of {b} GPU-hours each. Total GPU-hours for job batch {k}?",
                        "expression": f"{a}*{b}", "answer": a * b, "solution": f"{a} × {b} = {a * b}."}
                LAST["q"] = item
                text = json.dumps(item)
        else:
            q = req["messages"][0]["content"]
            k = int(q.split()[1])
            ans = (7 + k) * (3 + 2 * k)
            text = f"… ANSWER: {ans if k % 2 == 0 else ans + 5}"
        b = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
