#!/usr/bin/env python3
"""A Qwen-Agent style tool loop against any OpenAI-compatible endpoint (Chapter 10).

The loop every agent framework runs, in ~100 lines: send the conversation plus the
tool schemas → if the model returns tool_calls, run each one locally, append the
results as role "tool" messages → repeat until the model answers in text (or
--max-steps). Bad arguments are returned to the model as an error result, so it
can correct itself instead of crashing the loop.

Local tools: calculator (safe arithmetic), uma_budget (util → GiB on a GB10),
catalog_lookup (an entry of this lab's models.yaml).

  python3 tools/qwen_agent_demo.py "How many GiB does a util of 0.45 reserve?" \
      --url http://127.0.0.1:8000 --model qwen3-8b [--audit out/agent.jsonl] [--no-think]

Needs a model served with --enable-auto-tool-choice and a tool-call parser
(models.yaml: qwen3-8b, qwen3-4b-instruct, qwen3-coder-30b-fp8, qwen3.6-35b-fp8).

The same agent with the Qwen-Agent library (pip install qwen-agent, versions.env
PIP_QWEN_AGENT) — it adds its own function-calling prompt, RAG and code tools:
    from qwen_agent.agents import Assistant
    bot = Assistant(llm={"model": "qwen3-8b", "model_server": "http://127.0.0.1:8000/v1", "api_key": "none"},
                    function_list=["code_interpreter"])
    for responses in bot.run(messages=[{"role": "user", "content": "..."}]): pass
This script needs only the standard library and PyYAML.
"""
import argparse
import ast
import json
import operator
import pathlib
import re
import sys
import urllib.request

import yaml

CATALOG = pathlib.Path(__file__).resolve().parent.parent / "models.yaml"
GIB_VISIBLE = 121.7

TOOLS = [
    {"type": "function", "function": {
        "name": "calculator", "description": "Evaluate an arithmetic expression (+ - * / ** % and parentheses)",
        "parameters": {"type": "object", "required": ["expression"],
                       "properties": {"expression": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "uma_budget",
        "description": "GiB of unified memory that a vLLM --gpu-memory-utilization fraction (util) reserves on a GB10",
        "parameters": {"type": "object", "required": ["util"],
                       "properties": {"util": {"type": "number", "minimum": 0, "maximum": 1}}}}},
    {"type": "function", "function": {
        "name": "catalog_lookup", "description": "Look up a model in the lab catalog: Hugging Face id, util, memory, context",
        "parameters": {"type": "object", "required": ["name"],
                       "properties": {"name": {"type": "string", "description": "catalog name, e.g. qwen3-8b"}}}}},
]

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only numbers and + - * / ** % are allowed")


def calculator(expression):
    return {"expression": expression, "result": round(_eval(ast.parse(expression, mode="eval").body), 6)}


def uma_budget(util):
    return {"util": util, "gib": round(util * GIB_VISIBLE, 2),
            "note": "weights + KV cache must fit; keep the sum over running engines <= 0.72"}


def catalog_lookup(name):
    models = {m["name"]: m for m in yaml.safe_load(open(CATALOG))["models"]}
    if name not in models:
        return {"error": f"unknown model {name}", "known": sorted(models)}
    m = models[name]
    return {k: m[k] for k in ("name", "hf", "util", "mem", "max_len", "args")}


IMPL = {"calculator": calculator, "uma_budget": uma_budget, "catalog_lookup": catalog_lookup}


def call_tool(name, raw_args):
    try:
        args = json.loads(raw_args or "{}")
    except ValueError as e:
        return {"error": f"arguments are not valid JSON ({e}); send a JSON object"}
    if name not in IMPL:
        return {"error": f"unknown tool {name}; available: {sorted(IMPL)}"}
    try:
        return IMPL[name](**args)
    except (TypeError, ValueError, SyntaxError, ZeroDivisionError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def chat(a, messages):
    body = {"model": a.model, "messages": messages, "tools": TOOLS, "tool_choice": "auto",
            "temperature": 0, "max_tokens": a.max_tokens}
    if a.no_think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(a.url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {a.api_key}"})
    return json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--model", default="qwen3-8b")
    p.add_argument("--api-key", default="none")
    p.add_argument("--max-steps", type=int, default=5)
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--no-think", action="store_true", help="send enable_thinking=false (Qwen3 hybrid models)")
    p.add_argument("--audit", help="append one JSON line per tool call (tool, args, result)")
    a = p.parse_args()

    messages = [{"role": "system", "content": "You are a lab assistant for a DGX Spark. Use the tools for numbers "
                                              "and catalog facts; never guess them."},
                {"role": "user", "content": a.question}]
    if a.audit:
        pathlib.Path(a.audit).parent.mkdir(parents=True, exist_ok=True)
    audit = open(a.audit, "a") if a.audit else None
    for step in range(1, a.max_steps + 1):
        msg = chat(a, messages)
        calls = msg.get("tool_calls") or []
        if not calls:
            answer = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
            print(f"answer (step {step}): {answer}")
            return 0
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name, raw = c["function"]["name"], c["function"]["arguments"]
            result = call_tool(name, raw)
            print(f"  step {step} → {name}({raw})\n           ← {json.dumps(result)}")
            if audit:
                audit.write(json.dumps({"step": step, "tool": name, "arguments": raw, "result": result}) + "\n")
            messages.append({"role": "tool", "tool_call_id": c.get("id", name), "name": name,
                             "content": json.dumps(result)})
    print(f"no final answer after {a.max_steps} steps")
    return 1


if __name__ == "__main__":
    sys.exit(main())
