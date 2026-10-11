#!/usr/bin/env python3
"""Function-calling and JSON-output checker for an OpenAI-compatible endpoint (Chapter 10).

Sends a fixed suite of prompts with tool schemas (and one structured-output
request) and checks every answer the way an agent runtime would have to:

  1. the right tool(s) were called (or none, when no tool fits)
  2. `arguments` is valid JSON
  3. the arguments validate against the tool's JSON schema (types, required,
     enum, minimum/maximum, no extra keys)
  4. the values match what the prompt asked for

  run       python3 tools/fc_check.py run --url http://127.0.0.1:8000 --model qwen3-8b [--min 0.9]
  validate  python3 tools/fc_check.py validate --schema tool.json --args '{"city": "Lisbon"}'

In vLLM the parsing is done by --tool-call-parser (hermes for Qwen2.5/Qwen3,
qwen3_coder for Qwen3-Coder/3.5/3.6): a wrong parser shows up here as "no tool
call" or "arguments not JSON". Stdlib only.
"""
import argparse
import json
import re
import sys
import urllib.request

TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather", "description": "Current weather for a city",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["city"],
                       "properties": {"city": {"type": "string", "minLength": 1},
                                      "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}}}},
    {"type": "function", "function": {
        "name": "calculator", "description": "Evaluate an arithmetic expression",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["expression"],
                       "properties": {"expression": {"type": "string", "minLength": 1}}}}},
    {"type": "function", "function": {
        "name": "set_gpu_util", "description": "Set the vLLM gpu memory utilization for a catalog model",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["model", "util"],
                       "properties": {"model": {"type": "string"},
                                      "util": {"type": "number", "minimum": 0.01, "maximum": 0.72}}}}},
]

ENTRY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["model", "util", "max_len"],
                "properties": {"model": {"type": "string"}, "util": {"type": "number", "minimum": 0, "maximum": 1},
                               "max_len": {"type": "integer", "minimum": 512}}}

CASES = [
    {"id": "single", "prompt": "What's the weather in 'Lisbon' in celsius?",
     "expect": [{"name": "get_weather", "args": {"city": "Lisbon", "unit": "celsius"}}]},
    {"id": "arithmetic", "prompt": "Calculate 17 * 23 for me.",
     "expect": [{"name": "calculator", "args": {}}]},
    {"id": "typed-args", "prompt": "Set the gpu memory utilization of 'qwen3-8b' to 0.2",
     "expect": [{"name": "set_gpu_util", "args": {"model": "qwen3-8b", "util": 0.2}}]},
    {"id": "parallel", "prompt": "Get the weather in 'Austin' in fahrenheit and calculate 2 + 2",
     "expect": [{"name": "get_weather", "args": {"city": "Austin", "unit": "fahrenheit"}},
                {"name": "calculator", "args": {}}]},
    {"id": "no-tool", "prompt": "Say hello to the team.", "expect": []},
    {"id": "json-schema", "json_schema": ENTRY_SCHEMA,
     "prompt": "Return the catalog entry for model 'qwen3-vl-8b' with util 0.2 and max_len 16384 as JSON.",
     "expect_json": {"model": "qwen3-vl-8b", "util": 0.2, "max_len": 16384}},
]

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def validate(schema, value, path="$"):
    """Minimal JSON-schema check (the subset tool schemas use). Returns a list of problems."""
    errs, typ = [], schema.get("type")
    if typ in ("number", "integer"):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if typ == "integer":
            ok = ok and float(value).is_integer()
        if not ok:
            return [f"{path}: expected {typ}, got {type(value).__name__}"]
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    elif typ in TYPES and not isinstance(value, TYPES[typ]):
        return [f"{path}: expected {typ}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    if typ == "string" and len(value) < schema.get("minLength", 0):
        errs.append(f"{path}: shorter than {schema['minLength']}")
    if typ == "object":
        props = schema.get("properties", {})
        errs += [f"{path}.{k}: required" for k in schema.get("required", []) if k not in value]
        for k, v in value.items():
            if k in props:
                errs += validate(props[k], v, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: not allowed")
    if typ == "array" and "items" in schema:
        for i, v in enumerate(value):
            errs += validate(schema["items"], v, f"{path}[{i}]")
    return errs


def chat(url, model, key, body):
    body = {"model": model, "temperature": 0, "max_tokens": 1024, **body}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    return json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]


def strip_think(text):
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def same(a, b):
    return abs(a - b) < 1e-6 if isinstance(a, (int, float)) and isinstance(b, (int, float)) else str(a).lower() == str(b).lower()


def check_case(case, msg, stats):
    """Return a list of problems for one case (empty = pass)."""
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
    if "expect_json" in case:
        try:
            obj = json.loads(strip_think(msg.get("content")))
        except ValueError:
            return ["content is not JSON"]
        probs = validate(case["json_schema"], obj)
        probs += [f"{k}={obj.get(k)!r}, want {v!r}" for k, v in case["expect_json"].items() if not same(obj.get(k), v)]
        return probs
    calls = msg.get("tool_calls") or []
    if not case["expect"]:
        return [f"unexpected tool call {c['function']['name']}" for c in calls]
    if not calls:
        return ["no tool call (check --enable-auto-tool-choice and --tool-call-parser)"]
    probs, got = [], {}
    for c in calls:
        stats["calls"] += 1
        name = c["function"]["name"]
        try:
            args = json.loads(c["function"]["arguments"] or "{}")
            stats["json"] += 1
        except ValueError:
            probs.append(f"{name}: arguments not JSON: {c['function']['arguments'][:60]!r}")
            continue
        if name not in schemas:
            probs.append(f"unknown tool {name}")
            continue
        errs = validate(schemas[name], args)
        stats["schema"] += not errs
        probs += [f"{name}: {e}" for e in errs]
        got[name] = args
    for want in case["expect"]:
        if want["name"] not in got:
            if not any(p.startswith(want["name"]) for p in probs):
                probs.append(f"missing call {want['name']}")
            continue
        probs += [f"{want['name']}.{k}={got[want['name']].get(k)!r}, want {v!r}"
                  for k, v in want["args"].items() if not same(got[want["name"]].get(k), v)]
    return probs


def run(a):
    cases = CASES if not a.cases else [json.loads(x) for x in open(a.cases) if x.strip()]
    stats, passed = {"calls": 0, "json": 0, "schema": 0}, 0
    for case in cases:
        if "json_schema" in case:
            body = {"messages": [{"role": "user", "content": case["prompt"]}],
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "entry", "schema": case["json_schema"]}}}
        else:
            body = {"messages": [{"role": "user", "content": case["prompt"]}], "tools": TOOLS, "tool_choice": "auto"}
        try:
            msg = chat(a.url, a.model, a.api_key, body)
            probs = check_case(case, msg, stats)
        except OSError as e:
            probs = [f"request failed: {e}"]
        passed += not probs
        print(f"{'PASS' if not probs else 'FAIL'}  {case['id']:12} {'; '.join(probs)}")
    rate = passed / len(cases)
    print(f"fc_check: {passed}/{len(cases)} cases passed ({rate:.0%})  tool calls: valid JSON "
          f"{stats['json']}/{stats['calls']}, schema-valid {stats['schema']}/{stats['calls']}")
    if rate < a.min:
        print(f"GATE FAILED: {rate:.0%} < {a.min:.0%}")
        return 1
    return 0


def validate_cmd(a):
    schema = json.load(open(a.schema))
    schema = schema.get("function", schema).get("parameters", schema)   # accept a whole tool definition
    try:
        errs = validate(schema, json.loads(a.args))
    except ValueError as e:
        errs = [f"arguments not JSON: {e}"]
    print("\n".join(errs) if errs else "valid")
    return 1 if errs else 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the suite against an endpoint")
    r.add_argument("--url", default="http://127.0.0.1:8000")
    r.add_argument("--model", default="qwen3-8b")
    r.add_argument("--api-key", default="none")
    r.add_argument("--cases", help="JSONL with cases in the CASES format (default: built-in suite)")
    r.add_argument("--min", type=float, default=0.0, help="exit 1 if the pass rate is below this")
    v = sub.add_parser("validate", help="validate one arguments string against a schema file")
    v.add_argument("--schema", required=True)
    v.add_argument("--args", required=True)
    a = p.parse_args()
    return run(a) if a.cmd == "run" else validate_cmd(a)


if __name__ == "__main__":
    sys.exit(main())
