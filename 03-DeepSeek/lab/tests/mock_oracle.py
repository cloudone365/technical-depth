#!/usr/bin/env python3
"""OpenAI-compatible stand-in that answers the lab's eval suites (CI for eval_harness.py).

Answers correctly for items whose number is even and wrongly for odd ones, returns a
fake `reasoning_content`, and reports token usage — so the harness's grading,
reasoning accounting and economics can be tested without a GPU.
  python3 tests/mock_oracle.py 8765 &
"""
import json
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"
MATH = {json.loads(l)["question"]: json.loads(l) for l in open(DATA / "math_word.jsonl")}
CODE = {json.loads(l)["prompt"]: json.loads(l) for l in open(DATA / "code_tasks.jsonl")}
JSONT = {json.loads(l)["text"]: json.loads(l) for l in open(DATA / "json_tasks.jsonl")}
SOL = json.load(open(HERE / "fixtures" / "code_solutions.json"))


def answer(user):
    if user in MATH:
        it = MATH[user]
        good = int(it["id"][-2:]) % 2 == 0
        return f"ANSWER: {it['answer'] if good else it['answer'] + 1}"
    if user in CODE:
        it = CODE[user]
        good = int(it["id"][-2:]) % 2 == 0
        return "```python\n" + (SOL[it["id"]] if good else "def nope(): pass") + "\n```"
    text = user.split("\n", 1)[0].removeprefix("Text: ")
    it = JSONT[text]
    good = int(it["id"][-2:]) % 2 == 0
    return json.dumps(it["expected"] if good else {})


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user = req["messages"][-1]["content"]
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": answer(user),
                                                    "reasoning_content": "thinking " * 20}}],
                           "usage": {"prompt_tokens": 50, "completion_tokens": 40}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765), H).serve_forever()
