#!/usr/bin/env python3
"""Qwen2.5-Math stand-in for tir_math.py (Volume 04).
TIR: first turn writes a python block that prints the right answer (computed here from
the data), second turn (after ```output) boxes the executed result — so TIR must score 100 %.
CoT: boxes a wrong answer for odd items — so CoT must score 50 %."""
import json
import pathlib
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = pathlib.Path(__file__).resolve().parents[3] / "03 DeepSeek" / "lab" / "data" / "math_word.jsonl"
ITEMS = {json.loads(line)["question"]: json.loads(line) for line in open(DATA)}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user = req["messages"][1]["content"]
        it = ITEMS[user.split("\n")[0]]
        if "integrate natural language reasoning with programs" in user:
            prior = req["messages"][-1]["content"] if req["messages"][-1]["role"] == "assistant" else ""
            if "```output" in prior:
                val = re.findall(r"```output\n(.*?)\n```", prior, re.S)[-1].strip()
                text = f"The program printed {val}, so the answer is \\boxed{{{val}}}."
            else:
                text = f"Let me compute it.\n```python\nprint({it['answer']} + 0)\n```\n"
        else:
            good = int(it["id"][-2:]) % 2 == 0
            text = f"Step by step … \\boxed{{{it['answer'] if good else it['answer'] + 1}}}"
        b = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}],
                        "usage": {"completion_tokens": len(text.split())}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
