#!/usr/bin/env python3
"""Measure TTFT with and without a shared prompt prefix (Volume 23, 21).

Works against any OpenAI-compatible endpoint (vLLM, SGLang, KServe, mock-llm).
  python3 ttft_probe.py --url http://localhost:8000 --model qwen2.5-0.5b
Sends N requests whose prompt = <long shared system prompt> + <short unique
question>, then N with a *unique* long prefix each, and prints median TTFT for
both. A large gap means prefix caching (vLLM APC / SGLang RadixAttention) works.
"""
import argparse
import json
import statistics
import time
import urllib.request
import uuid

SHARED = ("You are the DGX Spark lab assistant. " + "The GB10 superchip couples a Grace CPU and a "
          "Blackwell GPU over NVLink-C2C and both share 128 GB of LPDDR5x unified memory. ") * 60


def ttft(url, model, system, question, max_tokens):
    body = json.dumps({"model": model, "stream": True, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": question}]}).encode()
    req = urllib.request.Request(url + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            if line.startswith(b"data:") and b'"content"' in line:
                first = time.perf_counter() - t0
                for _ in r:          # drain the rest of the stream
                    pass
                return first
    return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", default="qwen2.5-0.5b")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=8)
    a = ap.parse_args()
    shared = [ttft(a.url, a.model, SHARED, f"Question {i}: summarise in 5 words.", a.max_tokens) for i in range(a.n)]
    unique = [ttft(a.url, a.model, str(uuid.uuid4()) + SHARED, f"Question {i}: summarise in 5 words.", a.max_tokens)
              for i in range(a.n)]
    print(f"prompt ≈ {len(SHARED.split())} words")
    print(f"first request (cold)      : {shared[0]*1000:8.1f} ms")
    print(f"shared prefix  median TTFT: {statistics.median(shared[1:])*1000:8.1f} ms")
    print(f"unique prefix  median TTFT: {statistics.median(unique)*1000:8.1f} ms")
    print(f"speed-up from prefix cache: {statistics.median(unique)/statistics.median(shared[1:]):.1f}x")


if __name__ == "__main__":
    main()
