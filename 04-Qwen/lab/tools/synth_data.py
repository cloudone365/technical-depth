#!/usr/bin/env python3
"""Self-instruct style synthetic data with filtering (Chapter 09).

  1. generate  ask the model for new instructions, shown a few examples from the pool
  2. filter    length bounds, a blocklist (multimodal / attachments), exact duplicates and
               near-duplicates (ROUGE-L F ≥ 0.7 against everything kept so far, as in Self-Instruct)
  3. answer    one response per instruction; drop empty, short, refusing and repetitive
               answers (distinct 3-gram ratio < 0.5)
  4. judge     optional LLM-as-judge score 1-10 (--judge), keep ≥ --min-score
  5. pairs     optional self-play preference pairs (--dpo): a second, high-temperature answer,
               both judged, the better one is "chosen"

Writes ms-swift's dataset format, ready for k8s/jobs/swift-sft.yaml / swift-dpo.yaml:
  SFT  {"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}
  DPO  {"messages": [... chosen ...], "rejected_response": "..."}

  python3 tools/synth_data.py --url http://127.0.0.1:8000 --model qwen3-4b-instruct \
      --rounds 4 --per-round 10 --judge --out out/sft.jsonl --dpo out/dpo.jsonl
Stdlib only. Use a non-thinking model (or --no-think) so answers carry no <think> trace.
"""
import argparse
import json
import pathlib
import random
import re
import sys
import urllib.request

SEEDS = [
    "Explain why a vLLM pod on a DGX Spark sets its memory request equal to its limit.",
    "Write a bash one-liner that shows the five largest files in the model cache.",
    "Summarise what grouped-query attention changes compared with multi-head attention.",
    "Give three checks to run before starting a fine-tuning job in a shared cluster.",
    "Describe how a Kubernetes readiness probe protects clients during a model rollout.",
    "Convert 0.20 of 121.7 GiB to GiB and explain what that budget must hold.",
]
BLOCK = re.compile(r"\b(image|picture|photo|video|audio|attached|attachment|file you sent)\b", re.I)
REFUSAL = re.compile(r"\b(I'm sorry|I am sorry|I can't help|I cannot help|as an AI)\b", re.I)


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def rouge_l(a, b):
    """ROUGE-L F1 between two strings (word LCS)."""
    x, y = tokens(a), tokens(b)
    if not x or not y:
        return 0.0
    prev = [0] * (len(y) + 1)
    for xi in x:
        cur = [0]
        for j, yj in enumerate(y):
            cur.append(prev[j] + 1 if xi == yj else max(prev[j + 1], cur[j]))
        prev = cur
    lcs = prev[-1]
    if lcs == 0:
        return 0.0
    p, r = lcs / len(x), lcs / len(y)
    return 2 * p * r / (p + r)


def distinct3(text):
    t = tokens(text)
    grams = [tuple(t[i:i + 3]) for i in range(len(t) - 2)]
    return len(set(grams)) / len(grams) if grams else 1.0


class Client:
    def __init__(self, url, model, key, no_think):
        self.url, self.model, self.key, self.no_think = url.rstrip("/"), model, key, no_think

    def chat(self, prompt, temperature=0.7, max_tokens=1024):
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        if self.no_think:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        req = urllib.request.Request(self.url + "/v1/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        msg = json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]
        return re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()


def instruction_problem(text, pool, max_sim):
    n = len(text.split())
    if n < 4:
        return "too short"
    if n > 60:
        return "too long"
    if BLOCK.search(text):
        return "needs a non-text input"
    norm = " ".join(tokens(text))
    for p in pool:
        if norm == " ".join(tokens(p)):
            return "duplicate"
    for p in pool:
        if rouge_l(text, p) >= max_sim:
            return "near-duplicate"
    return None


def answer_problem(text):
    if REFUSAL.search(text):
        return "refusal"
    if len(text.split()) < 8:
        return "answer too short"
    if distinct3(text) < 0.5:
        return "repetitive"
    return None


def judge(client, question, answer):
    out = client.chat("Rate the answer from 1 to 10 for correctness and helpfulness. "
                      "Reply only with 'Score: N'.\n\nQuestion: " + question + "\n\nAnswer: " + answer,
                      temperature=0, max_tokens=512)
    m = re.search(r"Score:\s*(\d+)", out)
    return int(m[1]) if m else 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--model", default="qwen3-4b-instruct")
    p.add_argument("--api-key", default="none")
    p.add_argument("--seeds", help="text file, one seed instruction per line (default: built-in)")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--per-round", type=int, default=8)
    p.add_argument("--examples", type=int, default=3, help="pool examples shown per generation prompt")
    p.add_argument("--max-sim", type=float, default=0.7, help="ROUGE-L F at or above this = near-duplicate")
    p.add_argument("--judge", action="store_true", help="score answers with the model, keep >= --min-score")
    p.add_argument("--min-score", type=int, default=6)
    p.add_argument("--dpo", help="also write preference pairs (ms-swift rejected_response format) here")
    p.add_argument("--no-think", action="store_true", help="send enable_thinking=false (Qwen3 hybrid models)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="out/sft.jsonl")
    a = p.parse_args()

    rng = random.Random(a.seed)
    client = Client(a.url, a.model, a.api_key, a.no_think)
    seeds = [x.strip() for x in open(a.seeds)] if a.seeds else list(SEEDS)
    pool, new, reasons = list(seeds), [], {}

    for _ in range(a.rounds):
        shown = rng.sample(pool, min(a.examples, len(pool)))
        prompt = ("Here are example tasks for an assistant that helps engineers run LLMs on Kubernetes:\n"
                  + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(shown))
                  + f"\n\nWrite {a.per_round} new instructions that are different from the examples. "
                    "One per line, numbered, text only.")
        for line in client.chat(prompt, temperature=1.0).splitlines():
            m = re.match(r"^\s*\d+[.)]\s*(.+?)\s*$", line)
            if not m:
                continue
            why = instruction_problem(m[1], pool, a.max_sim)
            if why:
                reasons[why] = reasons.get(why, 0) + 1
                continue
            pool.append(m[1])
            new.append(m[1])
    generated = len(new) + sum(reasons.values())

    sft, dpo = [], []
    for q in new:
        ans = client.chat(q)
        why = answer_problem(ans)
        score = judge(client, q, ans) if (a.judge or a.dpo) and not why else None
        if not why and a.judge and score < a.min_score:
            why = "low judge score"
        if why:
            reasons[why] = reasons.get(why, 0) + 1
            continue
        sft.append({"messages": [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]})
        if a.dpo:
            alt = client.chat(q, temperature=1.3)
            alt_score = judge(client, q, alt) if alt.strip() else 0
            if alt_score != score:
                chosen, rejected = (ans, alt) if score > alt_score else (alt, ans)
                dpo.append({"messages": [{"role": "user", "content": q}, {"role": "assistant", "content": chosen}],
                            "rejected_response": rejected})

    for path, rows in ((a.out, sft), (a.dpo, dpo)):
        if path:
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print(f"generated {generated} instructions → {len(new)} new after filtering, {len(sft)} SFT rows kept"
          + (f", {len(dpo)} DPO pairs" if a.dpo else ""))
    for k, v in sorted(reasons.items()):
        print(f"  dropped {v:3}  {k}")
    print(f"wrote {a.out}" + (f" and {a.dpo}" if a.dpo else ""))
    return 0 if sft else 1


if __name__ == "__main__":
    sys.exit(main())
