#!/usr/bin/env python3
"""A minimal, auditable tool-calling agent (Volume 30).

OpenAI "tools" protocol against vLLM/SGLang (start vLLM with
`--enable-auto-tool-choice --tool-call-parser hermes` for Qwen2.5 models).
Tools:
  calculator(expression)      safe arithmetic via ast (no eval)
  gpu_slices()                GPU requests per namespace from `kubectl get pods -A -o json`
  search_docs(query, k)       Qdrant search over the repo (after rag_demo.py ingest)
Every tool call and result is printed, and the loop is capped (--max-steps), so
you can see exactly what the model asked for — the basis for guardrails.

  python3 agent_tools.py "How many GPU slices are in use, and what is 17% of 119.7 GiB?" --model qwen2.5-7b
"""
import argparse
import ast
import json
import operator
import subprocess
import urllib.request

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}


def calculator(expression: str):
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.operand))
        raise ValueError("only arithmetic is allowed")
    return {"result": ev(ast.parse(expression, mode="eval").body)}


def gpu_slices():
    try:
        pods = json.loads(subprocess.run(["kubectl", "get", "pods", "-A", "-o", "json"], capture_output=True,
                                         text=True, timeout=20, check=True).stdout)["items"]
    except Exception as e:                                                 # noqa: BLE001
        return {"error": f"kubectl unavailable: {e}"}
    used = {}
    for p in pods:
        if p["status"].get("phase") != "Running":
            continue
        n = sum(int(c.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", 0)) for c in p["spec"]["containers"])
        if n:
            used[p["metadata"]["namespace"]] = used.get(p["metadata"]["namespace"], 0) + n
    return {"slices_in_use": sum(used.values()), "by_namespace": used}


def search_docs(query: str, k: int = 3, qdrant="http://qdrant.llm-serving:6333",
                embed_url="http://bge-m3.llm-serving:8000", embed_model="bge-m3"):
    def post(url, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    try:
        v = post(embed_url + "/v1/embeddings", {"model": embed_model, "input": [query]})["data"][0]["embedding"]
        hits = post(f"{qdrant}/collections/technical-depth/points/search", {"vector": v, "limit": k, "with_payload": True})
        return {"results": [{"path": h["payload"]["path"], "heading": h["payload"]["heading"],
                             "text": h["payload"]["text"][:600]} for h in hits["result"]]}
    except Exception as e:                                                 # noqa: BLE001
        return {"error": f"search unavailable: {e}"}


TOOLS = [
    {"type": "function", "function": {"name": "calculator", "description": "Evaluate an arithmetic expression.",
     "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "gpu_slices", "description": "Count GPU time-slices in use per namespace.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "search_docs", "description": "Search the lab documentation.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
                    "required": ["query"]}}},
]
IMPL = {"calculator": calculator, "gpu_slices": gpu_slices, "search_docs": search_docs}


def chat(url, model, messages):
    body = {"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "temperature": 0.2}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=600).read())["choices"][0]["message"]


def run(url, model, question, max_steps=6):
    msgs = [{"role": "system", "content": "Use tools when they help. Answer concisely."},
            {"role": "user", "content": question}]
    for step in range(max_steps):
        m = chat(url, model, msgs)
        calls = m.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls} if calls else
                    {"role": "assistant", "content": m.get("content") or ""})
        if not calls:
            print(f"\nANSWER: {m.get('content')}")
            return m.get("content")
        for c in calls:
            fn, args = c["function"]["name"], json.loads(c["function"]["arguments"] or "{}")
            if fn not in IMPL:
                result = {"error": f"unknown tool {fn}"}
            else:
                try:
                    result = IMPL[fn](**args)
                except Exception as e:                                     # noqa: BLE001
                    result = {"error": str(e)}
            print(f"[step {step+1}] {fn}({json.dumps(args)}) → {json.dumps(result)[:300]}")
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
    print("stopped: max steps reached")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", default="qwen2.5-7b")
    ap.add_argument("--max-steps", type=int, default=6)
    a = ap.parse_args()
    run(a.url, a.model, a.question, a.max_steps)
