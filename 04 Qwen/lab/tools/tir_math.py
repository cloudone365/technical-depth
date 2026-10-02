#!/usr/bin/env python3
"""Chain-of-thought vs tool-integrated reasoning (TIR) for Qwen2.5-Math (Volume 04).

CoT : "Please reason step by step, and put your final answer within \\boxed{}."
TIR : "Please integrate natural language reasoning with programs to solve the problem
       above, and put your final answer within \\boxed{}."
In TIR mode the model writes ```python blocks; we run each block (subprocess, timeout,
isolated interpreter), append its stdout as ```output, and let the model continue —
up to --max-rounds — until it writes \\boxed{answer}. Scored on 03's 40 word problems.

  python3 tir_math.py --url http://localhost:8000 --model qwen2.5-math-7b --mode cot
  python3 tir_math.py --url http://localhost:8000 --model qwen2.5-math-7b --mode tir
Executes model-written code: run it in a pod (k8s/jobs/tir-eval.yaml), not on your laptop.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

DATA = pathlib.Path(__file__).resolve().parents[3] / "03 DeepSeek" / "lab" / "data" / "math_word.jsonl"
LOCAL = pathlib.Path(__file__).resolve().parent.parent / "data" / "math_word.jsonl"   # in-cluster copy
PROMPT = {
    "cot": "Please reason step by step, and put your final answer within \\boxed{}.",
    "tir": "Please integrate natural language reasoning with programs to solve the problem above, "
           "and put your final answer within \\boxed{}.",
}
CODE = re.compile(r"```python\n(.*?)```", re.S)
BOXED = re.compile(r"\\boxed\{([^{}]*)\}")


def chat(a, messages, stop=None):
    body = {"model": a.model, "messages": messages, "max_tokens": a.max_tokens, "temperature": 0}
    if stop:
        body["stop"] = stop
    if messages[-1]["role"] == "assistant":          # continue the same assistant turn after ```output
        body.update(continue_final_message=True, add_generation_prompt=False)
    req = urllib.request.Request(a.url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"] or "", d.get("usage", {}).get("completion_tokens", 0)


def run_code(src, timeout=5):
    try:
        r = subprocess.run([sys.executable, "-I", "-c", src], capture_output=True, text=True, timeout=timeout)
        return (r.stdout + (r.stderr[-300:] if r.returncode else "")).strip()[:1000]
    except subprocess.TimeoutExpired:
        return "TimeoutError"


def solve(a, q):
    msgs = [{"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": f"{q}\n{PROMPT[a.mode]}"}]
    transcript, tokens, calls = "", 0, 0
    for _ in range(a.max_rounds if a.mode == "tir" else 1):
        text, n = chat(a, msgs + ([{"role": "assistant", "content": transcript}] if transcript else []),
                       stop=["```output"] if a.mode == "tir" else None)
        tokens += n
        transcript += text
        if BOXED.search(text) or a.mode == "cot":
            break
        blocks = CODE.findall(text)
        if not blocks:
            break
        calls += 1
        transcript += f"\n```output\n{run_code(blocks[-1])}\n```\n"
    m = BOXED.findall(transcript)
    return (m[-1].strip() if m else None), tokens, calls


def as_int(s):
    try:
        return int(float(s.replace(",", "").replace("$", "").strip()))
    except (AttributeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", default="qwen2.5-math-7b")
    ap.add_argument("--mode", choices=["cot", "tir"], default="tir")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--max-rounds", type=int, default=4)
    a = ap.parse_args()
    data = LOCAL if LOCAL.exists() else DATA
    items = [json.loads(line) for line in open(data)][: a.limit or None]
    ok = toks = calls = 0
    t0 = time.perf_counter()
    for it in items:
        ans, n, c = solve(a, it["question"])
        good = as_int(ans) == it["answer"]
        ok += good
        toks += n
        calls += c
        print(f"  {it['id']}  {'ok ' if good else 'BAD'}  boxed={ans!s:>10}  want={it['answer']}  code_runs={c}")
    n = len(items)
    print(f"model {a.model} mode {a.mode}: {ok}/{n} correct ({ok / n:.0%}), {toks / n:.0f} completion tokens/problem, "
          f"{calls / n:.1f} code runs/problem, {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
