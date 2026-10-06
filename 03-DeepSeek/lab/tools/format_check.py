#!/usr/bin/env python3
"""Format + correctness check for the <think>/<answer> contract (Volumes 23–25).

Sends N fresh arithmetic problems (same generator as grpo_tiny.py, different seed
from training) to any OpenAI-compatible endpoint and scores the replies with the
same rules GRPO rewards: format = <think>…</think><answer>N</answer>, correct = N
matches. When the server splits reasoning out (vLLM --reasoning-parser), the
thinking is put back around the content before scoring, so base model, LoRA
adapter and GRPO checkpoint are compared on equal terms.

  python3 format_check.py --url http://localhost:8000 --model r1-1.5b -n 50
  python3 format_check.py --url http://localhost:8000 --model r1-sft -n 50      # the LoRA adapter
Stdlib only.
"""
import argparse
import concurrent.futures as cf
import json
import random
import sys
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from grpo_tiny import FMT, make_problem  # noqa: E402


def ask(url, model, prompt, max_tokens, temperature, api_key):
    body = json.dumps({"model": model, "messages": prompt, "max_tokens": max_tokens, "temperature": temperature}).encode()
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body, headers=h)
    with urllib.request.urlopen(req, timeout=600) as r:
        m = json.load(r)["choices"][0]["message"]
    content, reasoning = m.get("content") or "", m.get("reasoning_content") or m.get("reasoning") or ""
    return f"<think>{reasoning}</think>{content}" if reasoning and "<think>" not in content else content


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("-n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--api-key")
    ap.add_argument("--show", type=int, default=1, help="print this many raw replies")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    probs = [make_problem(rnd) for _ in range(a.n)]
    with cf.ThreadPoolExecutor(a.concurrency) as ex:
        outs = list(ex.map(lambda p: ask(a.url, a.model, p["prompt"], a.max_tokens, a.temperature, a.api_key), probs))
    fmt = cor = 0
    for p, o in zip(probs, outs):
        m = FMT.match(o)
        fmt += bool(m)
        cor += bool(m and m.group(1) == p["answer"])
    for o in outs[:a.show]:
        print("sample:", o[-200:].replace("\n", " "))
    print(f"model {a.model}: format {fmt}/{a.n} ({fmt / a.n:.0%})  correct {cor}/{a.n} ({cor / a.n:.0%})")


if __name__ == "__main__":
    main()
