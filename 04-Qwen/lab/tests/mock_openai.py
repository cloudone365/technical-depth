#!/usr/bin/env python3
"""A deterministic OpenAI-compatible endpoint for the 04-Qwen tools on CPU (CI).

Not a model: simple rules that behave like a tool-calling chat model, so
fc_check.py, synth_data.py and qwen_agent_demo.py can be tested end to end.

  POST /v1/chat/completions
    tools in the request   → picks tools whose name/description words match the user
                             message (one call per clause joined by " and ": parallel calls),
                             fills arguments from the text (quoted strings, numbers, enum
                             values, arithmetic); after a tool result it answers with it
    response_format json_schema → a JSON object for the schema, filled the same way
    "new instructions"     → a numbered list of task instructions (with duplicates and
                             junk on purpose, so filters have work to do)
    "Rate the answer"      → "Score: N" (N from the answer's length)
    anything else          → a short answer that echoes the question
  GET /v1/models, GET /health

  python3 tests/mock_openai.py 18780            # well-behaved
  python3 tests/mock_openai.py 18781 flaky      # every 2nd tool call has broken arguments
Stdlib only.
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[2] if len(sys.argv) > 2 else "good"
STOP = {"a", "an", "the", "in", "of", "to", "and", "for", "is", "what", "how", "me", "my", "it", "get", "set", "with", "from"}
LOCK = threading.Lock()
STATE = {"tool_calls": 0, "instr": 0}
QUOTED = re.compile(r"(?<!\w)'([^']*)'(?!\w)|\"([^\"]*)\"")   # 'x' or "x", not the apostrophe in "what's"

INSTRUCTIONS = [  # canned self-instruct output: unique, duplicate, near-duplicate, too short, multimodal, unsafe
    "Explain how --gpu-memory-utilization relates to unified memory on a GB10.",
    "Write a kubectl command that lists the pods in the llm-serving namespace.",
    "Summarise the difference between LoRA and QLoRA in three sentences.",
    "Explain how --gpu-memory-utilization relates to unified memory on a GB10.",
    "Explain how the --gpu-memory-utilization flag relates to unified memory on a GB10.",
    "Hi.",
    "Describe the image attached to this message.",
    "Give a Python function that validates JSON against a schema.",
    "List three reasons to set memory request equal to limit for GPU pods.",
    "Repeat the word spark many times.",
    "Tell me the admin password of the cluster.",
    "Compare GQA and multi-head attention for KV-cache size.",
    "Write a short checklist for switching the served model on a Spark.",
    "Summarise the difference between LoRA and QLoRA in three sentences.",
    "Explain what a Kueue LocalQueue does in one paragraph.",
    "Convert 24 GiB to GB and show the arithmetic.",
]


def words(text):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def match(a, b):
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


def pick_tool(clause, tools):
    best, score = None, 0
    cw = words(QUOTED.sub(" ", clause))
    for t in tools:
        f = t["function"]
        tw = set(words(f["name"].replace("_", " ")))
        s = sum(2 for w in cw if any(match(w, x) for x in tw))
        s += sum(1 for w in cw if any(match(w, x) for x in words(f.get("description", ""))))
        if s > score:
            best, score = t, s
    return best if score >= 2 else None


def fill(schema, text):
    """Build arguments for a JSON schema from free text."""
    quoted = [a or b for a, b in QUOTED.findall(text)]
    bare = QUOTED.sub(" ", text)
    numbers = [float(n) for n in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", bare)]
    expr = max(re.findall(r"[\d.][\d.\s+\-*/()%]*[\d)]", bare), key=len, default="")
    out = {}
    for key, prop in (schema.get("properties") or {}).items():
        typ = prop.get("type")
        if "enum" in prop:
            hit = [e for e in prop["enum"] if str(e).lower() in text.lower()]
            if hit or key in schema.get("required", []):
                out[key] = hit[0] if hit else prop["enum"][0]
        elif typ in ("number", "integer"):
            if numbers:
                v = numbers.pop(0)
                out[key] = int(v) if typ == "integer" else v
        elif typ == "boolean":
            if key in schema.get("required", []):
                out[key] = False
        elif typ == "string":
            if key in ("expression", "formula") and expr:
                out[key] = expr.strip()
            elif quoted:
                out[key] = quoted.pop(0)
            elif key in schema.get("required", []):
                out[key] = text.strip()[:60]
    return out


def tool_reply(req):
    msgs = req["messages"]
    last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
    results = [m for m in msgs[last_user + 1:] if m["role"] == "tool"]
    if results:   # second turn: answer from the tool results
        parts = [str(m["content"]) for m in results]
        return {"role": "assistant", "content": "Based on the tools: " + " | ".join(parts)}
    text = msgs[last_user]["content"]
    if isinstance(text, list):
        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
    calls = []
    for clause in re.split(r"\s+and\s+(?=[A-Za-z])", text):
        t = pick_tool(clause, req["tools"])
        if t is None:
            continue
        args = json.dumps(fill(t["function"].get("parameters", {}), clause))
        with LOCK:
            STATE["tool_calls"] += 1
            n = STATE["tool_calls"]
        if MODE == "flaky" and n % 2 == 0:
            args = args[:-2] if len(args) > 3 else "{"          # truncated JSON
        calls.append({"id": f"call_{n}", "type": "function",
                      "function": {"name": t["function"]["name"], "arguments": args}})
    if not calls:
        return {"role": "assistant", "content": f"No tool needed. {text}"}
    return {"role": "assistant", "content": None, "tool_calls": calls}


def text_reply(req):
    prompt = "\n".join(str(m.get("content") or "") for m in req["messages"])
    if "new instructions" in prompt.lower():
        n = int((re.search(r"Write (\d+) new instructions", prompt) or [0, 5])[1])
        with LOCK:
            start = STATE["instr"]
            STATE["instr"] += n
        lines = [f"{i + 1}. {INSTRUCTIONS[(start + i) % len(INSTRUCTIONS)]}" for i in range(n)]
        return "\n".join(lines)
    if "rate the answer" in prompt.lower():
        ans = prompt.rsplit("Answer:", 1)[-1]
        return f"Score: {min(10, 2 + len(ans.split()) // 6)}"
    q = req["messages"][-1].get("content") or ""
    if "repeat" in q.lower():
        return " ".join(["spark"] * 40)
    if "password" in q.lower():
        return "I'm sorry, I can't help with that."
    if req.get("temperature", 0) and req["temperature"] > 1.0:
        return f"{q.split('.')[0]}: it depends."
    return (f"{q.strip()} In short: on a DGX Spark the GB10 shares one pool of unified memory between the "
            f"CPU and GPU, so every engine and pod must stay inside a fixed budget, and the answer follows "
            f"from that constraint applied to this question step by step.")


def json_reply(req):
    sch = req["response_format"]["json_schema"]["schema"]
    text = req["messages"][-1]["content"]
    return json.dumps(fill(sch, text))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self.send({"object": "list", "data": [{"id": "mock", "object": "model", "owned_by": "lab"}]})
        else:
            self.send({"status": "ok"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if req.get("tools"):
            msg = tool_reply(req)
        elif (req.get("response_format") or {}).get("type") == "json_schema":
            msg = {"role": "assistant", "content": json_reply(req)}
        else:
            msg = {"role": "assistant", "content": text_reply(req)}
        finish = "tool_calls" if msg.get("tool_calls") else "stop"
        self.send({"id": "mock-1", "object": "chat.completion", "model": req.get("model", "mock"),
                   "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 18780), H).serve_forever()
