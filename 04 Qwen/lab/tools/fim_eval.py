#!/usr/bin/env python3
"""Fill-in-the-middle evaluation for Qwen2.5-Coder (Volume 03).

Each task in data/fim_tasks.jsonl is a Python function with a hole. The model gets
  <|fim_prefix|>{prefix}<|fim_suffix|>{suffix}<|fim_middle|>
on /v1/completions (base models are trained for this), we splice its output back in,
and run the task's unit test in a subprocess (timeout, no network needed). Reports
pass@1, exact match and latency. --mode chat asks an instruct model the same thing
in plain words, for comparison.

  python3 fim_eval.py --url http://localhost:8000 --model qwen2.5-coder-7b-base
  python3 fim_eval.py --url http://localhost:8000 --model qwen2.5-coder-7b --mode chat
Executes model-written code: run it in a pod (k8s/jobs/fim-eval.yaml), not on your laptop.
"""
import argparse
import json
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "fim_tasks.jsonl"
STOP = ["<|endoftext|>", "<|fim_pad|>", "<|file_sep|>", "<|im_end|>", "<|fim_prefix|>", "<|repo_name|>"]


def post(url, path, body, api_key=None):
    h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})}
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def complete(a, t):
    if a.mode == "fim":
        prompt = f"<|fim_prefix|>{t['prefix']}<|fim_suffix|>{t['suffix']}<|fim_middle|>"
        d = post(a.url, "/v1/completions", {"model": a.model, "prompt": prompt, "max_tokens": a.max_tokens,
                                            "temperature": 0, "stop": STOP}, a.api_key)
        return d["choices"][0]["text"]
    msg = ("Fill in the missing middle of this Python function. Reply with ONLY the missing lines in one "
           "```python block, keeping the indentation.\n\n### before\n```python\n" + t["prefix"] +
           "```\n### after\n```python\n" + t["suffix"] + "```")
    d = post(a.url, "/v1/chat/completions", {"model": a.model, "temperature": 0, "max_tokens": a.max_tokens,
                                             "messages": [{"role": "user", "content": msg}]}, a.api_key)
    text = d["choices"][0]["message"]["content"] or ""
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text)


def passes(t, middle, timeout=5):
    code = t["prefix"] + middle + ("" if middle.endswith("\n") else "\n") + t["suffix"] + "\n" + t["test"] + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
    try:
        r = subprocess.run([sys.executable, "-I", f.name], capture_output=True, timeout=timeout)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    finally:
        pathlib.Path(f.name).unlink(missing_ok=True)


def norm(s):
    return "\n".join(line.rstrip() for line in s.strip("\n").splitlines())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", default="qwen2.5-coder-7b-base")
    ap.add_argument("--mode", choices=["fim", "chat"], default="fim")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--api-key")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    tasks = [json.loads(line) for line in open(DATA)]
    ok = exact = 0
    lat = []
    for t in tasks:
        t0 = time.perf_counter()
        mid = complete(a, t)
        lat.append(time.perf_counter() - t0)
        p, e = passes(t, mid), norm(mid) == norm(t["middle"])
        ok += p
        exact += e
        print(f"  {t['id']} {t['name']:18} {'PASS' if p else 'fail'}{'  exact' if e else ''}")
        if a.show and not p:
            print("    got:\n" + "\n".join("      " + x for x in mid.splitlines()))
    print(f"model {a.model} mode {a.mode}: pass@1 {ok}/{len(tasks)} ({ok / len(tasks):.0%})  "
          f"exact {exact}/{len(tasks)}  p50 latency {statistics.median(lat) * 1000:.0f} ms")


if __name__ == "__main__":
    main()
