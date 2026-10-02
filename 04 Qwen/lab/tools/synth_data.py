#!/usr/bin/env python3
"""Verified synthetic training data (Volume 10): generate → verify → deduplicate → mine
hard cases → write SFT and DPO files in ms-swift's format.

  teacher  : any served model writes a word problem as JSON with a Python arithmetic
             `expression` and the `answer` (schema-constrained via response_format)
  verify   : the expression is evaluated by an ast whitelist; kept only if it equals the answer
  dedup    : exact (normalised text) and near-duplicate (5-gram Jaccard ≥ 0.8)
  student  : (optional) a smaller model answers; problems it gets WRONG are the valuable ones
  output   : sft.jsonl  {"messages": [user, assistant]}
             dpo.jsonl  {"messages": [user, assistant=chosen], "rejected_response": student's wrong answer}

  python3 synth_data.py --url http://localhost:8000 --teacher qwen2.5-32b-awq -n 200 --out data/synth
  python3 synth_data.py … --student-url http://localhost:8001 --student qwen2.5-0.5b
Stdlib only.
"""
import argparse
import ast
import hashlib
import json
import operator
import pathlib
import random
import re
import urllib.request

TOPICS = ["GPU time-slices shared by teams", "checkpoint sizes and disk space", "tokens per second and batch time",
          "KV cache per sequence", "power draw and energy cost", "rack units and cabling", "dataset shards and workers",
          "learning-rate warmup steps", "network bandwidth and transfer time", "queue wait and job throughput"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["question", "expression", "answer", "solution"],
          "properties": {"question": {"type": "string"}, "expression": {"type": "string"},
                         "answer": {"type": "integer"}, "solution": {"type": "string"}}}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv,
       ast.Mod: operator.mod, ast.Pow: operator.pow, ast.Div: operator.truediv, ast.USub: operator.neg}


def safe_eval(expr):
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.operand))
        raise ValueError("not arithmetic")
    return ev(ast.parse(expr, mode="eval").body)


def chat(url, model, messages, schema=None, temperature=0.8, max_tokens=512):
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "problem", "schema": schema}}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["choices"][0]["message"]["content"] or ""


def norm(t):
    return re.sub(r"\W+", " ", t.lower()).strip()


def shingles(t, k=5):
    w = norm(t).split()
    return {" ".join(w[i:i + k]) for i in range(max(1, len(w) - k + 1))}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--teacher", default="qwen2.5-32b-awq")
    ap.add_argument("--student-url")
    ap.add_argument("--student")
    ap.add_argument("-n", type=int, default=50, help="generation attempts")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="data/synth")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    stats = dict(generated=0, bad_json=0, failed_verify=0, exact_dup=0, near_dup=0, kept=0, student_wrong=0)
    kept, seen, shingle_sets = [], set(), []
    for _ in range(a.n):
        topic = rnd.choice(TOPICS)
        prompt = (f"Write ONE new multi-step arithmetic word problem about {topic} in a GPU datacenter. "
                  "Give a Python arithmetic `expression` (numbers and + - * // % ** only) that computes the integer "
                  "`answer`, and a short step-by-step `solution`. Reply as JSON.")
        raw = chat(a.url, a.teacher, [{"role": "user", "content": prompt}], schema=SCHEMA)
        stats["generated"] += 1
        try:
            item = json.loads(raw)
            q, expr, ans, sol = item["question"], item["expression"], int(item["answer"]), item["solution"]
        except (ValueError, KeyError, TypeError):
            stats["bad_json"] += 1
            continue
        try:
            ok = safe_eval(expr) == ans
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
            ok = False
        if not ok:
            stats["failed_verify"] += 1
            continue
        h = hashlib.sha1(norm(q).encode()).hexdigest()
        if h in seen:
            stats["exact_dup"] += 1
            continue
        sh = shingles(q)
        if any(len(sh & o) / max(1, len(sh | o)) >= 0.8 for o in shingle_sets):
            stats["near_dup"] += 1
            continue
        seen.add(h)
        shingle_sets.append(sh)
        rec = {"question": q, "answer": ans, "solution": sol, "rejected": None}
        if a.student:
            got = chat(a.student_url or a.url, a.student,
                       [{"role": "user", "content": q + "\nEnd with 'ANSWER: <integer>'."}], temperature=0.6)
            m = re.findall(r"ANSWER:\s*(-?\d+)", got)
            if not m or int(m[-1]) != ans:
                rec["rejected"] = got
                stats["student_wrong"] += 1
        kept.append(rec)
        stats["kept"] += 1
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "sft.jsonl", "w") as f:
        for r in kept:
            f.write(json.dumps({"messages": [{"role": "user", "content": r["question"]},
                                             {"role": "assistant", "content": f"{r['solution']}\nANSWER: {r['answer']}"}]}) + "\n")
    with open(out / "dpo.jsonl", "w") as f:
        for r in kept:
            if r["rejected"]:
                f.write(json.dumps({"messages": [{"role": "user", "content": r["question"]},
                                                 {"role": "assistant", "content": f"{r['solution']}\nANSWER: {r['answer']}"}],
                                    "rejected_response": r["rejected"]}) + "\n")
    print(" ".join(f"{k}={v}" for k, v in stats.items()))
    print(f"wrote {out / 'sft.jsonl'} ({stats['kept']} rows) and {out / 'dpo.jsonl'} ({stats['student_wrong']} pairs)")


if __name__ == "__main__":
    main()
