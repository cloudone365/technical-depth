#!/usr/bin/env python3
"""Needle-in-a-haystack long-context check (Volume 08).

Builds a long filler document from varied, original sentences, hides one fact
(the "needle") at a chosen depth, and asks the served model to retrieve it.
Sweeps context lengths and depths, reports hit/miss, TTFT and prompt tokens.

  python3 needle_test.py --url http://localhost:8000 --model r1-7b --lengths 4000 16000 64000 --depths 0.1 0.5 0.9
Stdlib only. Context length is approximate (≈1.3 tokens per word for English).
"""
import argparse
import json
import random
import time
import urllib.request

SUBJ = ["The storage team", "A night-shift operator", "The scheduler", "An intern", "The billing service",
        "Rack seven", "The vector database", "A tired SRE", "The weekly report", "The firmware updater"]
VERB = ["reviewed", "rebalanced", "ignored", "archived", "measured", "restarted", "documented", "migrated",
        "benchmarked", "rotated"]
OBJ = ["the cooling logs", "every checkpoint", "the quota table", "an old dashboard", "the backup policy",
       "three dusty cables", "the latency budget", "a stale lease", "the onboarding guide", "the spare SSDs"]
WHEN = ["on Tuesday", "before lunch", "after the outage", "during the audit", "at midnight", "last spring",
        "in the canary window", "while it rained", "without telling anyone", "for the second time"]


def filler(words, rnd):
    out, n = [], 0
    while n < words:
        s = f"{rnd.choice(SUBJ)} {rnd.choice(VERB)} {rnd.choice(OBJ)} {rnd.choice(WHEN)}."
        out.append(s)
        n += len(s.split())
    return out


def ask(url, model, prompt, max_tokens):
    body = {"model": model, "temperature": 0.0, "max_tokens": max_tokens, "stream": True,
            "messages": [{"role": "user", "content": prompt}], "stream_options": {"include_usage": True}}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0, ttft, text, usage = time.perf_counter(), None, "", {}
    with urllib.request.urlopen(req, timeout=1800) as r:
        for line in r:
            if not line.startswith(b"data:") or b"[DONE]" in line:
                continue
            d = json.loads(line[5:])
            if d.get("usage"):
                usage = d["usage"]
            for ch in d.get("choices", []):
                delta = ch.get("delta", {})
                piece = (delta.get("content") or "") + (delta.get("reasoning_content") or "")
                if piece and ttft is None:
                    ttft = time.perf_counter() - t0
                text += delta.get("content") or ""
    return text, ttft or 0.0, usage.get("prompt_tokens", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--lengths", type=int, nargs="+", default=[2000, 8000, 32000], help="approx. tokens")
    ap.add_argument("--depths", type=float, nargs="+", default=[0.1, 0.5, 0.9])
    ap.add_argument("--max-tokens", type=int, default=2048)
    a = ap.parse_args()
    rnd = random.Random(7)
    print(f"{'tokens≈':>8} {'depth':>6} {'prompt_tok':>10} {'TTFT s':>8}  result")
    hits = total = 0
    for L in a.lengths:
        for depth in a.depths:
            code = f"{rnd.randint(1000, 9999)}-{rnd.choice(['AMBER', 'COBALT', 'TEAL', 'ONYX'])}"
            needle = f"The secret maintenance code for spark-01 is {code}."
            sents = filler(int(L / 1.3), rnd)
            sents.insert(int(len(sents) * depth), needle)
            prompt = (" ".join(sents) + "\n\nQuestion: What is the secret maintenance code for spark-01? "
                      "Reply with the code only.")
            text, ttft, ptok = ask(a.url, a.model, prompt, a.max_tokens)
            ok = code in text
            hits += ok
            total += 1
            print(f"{L:8d} {depth:6.1f} {ptok:10d} {ttft:8.2f}  {'✓' if ok else '✗ ' + repr(text.strip()[:60])}")
    print(f"\n{hits}/{total} needles found")


if __name__ == "__main__":
    main()
