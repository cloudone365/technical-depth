#!/usr/bin/env python3
"""Streaming probe for reasoning models, hop by hop (Volume 21).

Sends one streamed chat completion and times every SSE chunk, separating the
reasoning phase (delta.reasoning_content) from the answer phase (delta.content):

  time to first byte        connection + queueing + prefill
  time to first reasoning   first thinking token
  time to first answer      when the user sees the answer start (after </think>)
  inter-token latency       p50 / p99 gap between chunks
  finish_reason             stop | length (truncated chain of thought)

and flags a hop that BUFFERS the stream (all chunks arrive in one burst at the end),
which is what a misconfigured proxy or middleware does to SSE.

  python3 stream_probe.py --url http://localhost:8000 --model r1-7b
  python3 stream_probe.py --url http://api.lab.local --api-key sk-… --model reasoning
  python3 stream_probe.py --url http://192.168.0.100 --host api.lab.local --api-key sk-… --model reasoning --json
Stdlib only.
"""
import argparse
import json
import statistics
import time
import urllib.error
import urllib.request

PROMPT = "A train leaves at 09:40 and arrives at 13:05. How many minutes is the trip? End with 'ANSWER: <n>'."


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def probe(url, model, api_key=None, host=None, max_tokens=2048, temperature=0.6, prompt=PROMPT, timeout=900):
    body = json.dumps({"model": model, "stream": True, "max_tokens": max_tokens, "temperature": temperature,
                       "stream_options": {"include_usage": True},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body, headers=headers)
    t0 = time.perf_counter()
    r = {"url": url, "model": model, "ttfb_s": None, "first_reasoning_s": None, "first_answer_s": None,
         "chunks": 0, "reasoning_chunks": 0, "answer_chunks": 0, "finish_reason": None, "usage": None,
         "answer": "", "error": None}
    arrivals = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                now = time.perf_counter() - t0
                if r["ttfb_s"] is None:
                    r["ttfb_s"] = now
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                ev = json.loads(data)
                if ev.get("usage"):
                    r["usage"] = ev["usage"]
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    if ch.get("finish_reason"):
                        r["finish_reason"] = ch["finish_reason"]
                    rc = d.get("reasoning_content") or d.get("reasoning")
                    c = d.get("content")
                    if rc:
                        r["reasoning_chunks"] += 1
                        if r["first_reasoning_s"] is None:
                            r["first_reasoning_s"] = now
                    if c:
                        r["answer_chunks"] += 1
                        r["answer"] += c
                        if r["first_answer_s"] is None:
                            r["first_answer_s"] = now
                    if rc or c:
                        r["chunks"] += 1
                        arrivals.append(now)
    except urllib.error.HTTPError as e:
        r["error"] = f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
    except Exception as e:  # noqa: BLE001 — a probe reports, it doesn't crash
        r["error"] = f"{type(e).__name__}: {e}"
    r["total_s"] = time.perf_counter() - t0
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    r["itl_p50_ms"] = pct(gaps, 50) * 1000
    r["itl_p99_ms"] = pct(gaps, 99) * 1000
    span = (arrivals[-1] - arrivals[0]) if len(arrivals) > 1 else 0.0
    # Buffered: many chunks, but they all landed in a burst that is a tiny part of the request time.
    r["buffered"] = bool(len(arrivals) >= 10 and r["total_s"] > 0.5 and span < 0.05 * r["total_s"])
    r["tok_per_s"] = (r["chunks"] / span) if span > 0 and not r["buffered"] else 0.0
    r["answer"] = r["answer"][-120:]
    return r


def show(r):
    f = lambda v: "   —   " if v is None else f"{v * 1000:7.0f}"  # noqa: E731
    print(f"{r['url']}  model={r['model']}")
    if r["error"]:
        print(f"  ERROR {r['error']}  (after {r['total_s']:.1f}s)")
        return
    print(f"  ttfb {f(r['ttfb_s'])} ms | first reasoning {f(r['first_reasoning_s'])} ms | "
          f"first answer {f(r['first_answer_s'])} ms | total {r['total_s']:.2f} s")
    print(f"  chunks {r['chunks']} (reasoning {r['reasoning_chunks']}, answer {r['answer_chunks']}) | "
          f"ITL p50 {r['itl_p50_ms']:.1f} ms p99 {r['itl_p99_ms']:.1f} ms | ≈{r['tok_per_s']:.0f} chunks/s")
    print(f"  finish_reason={r['finish_reason']}  usage={r['usage']}")
    if r["buffered"]:
        print("  BUFFERED: every chunk arrived in one burst at the end — a hop is buffering SSE (Vol 21 §7)")
    if r["finish_reason"] == "length":
        print("  TRUNCATED: hit max_tokens inside the chain of thought — raise max_tokens (Vol 05, alert ReasoningTruncated)")
    if r["reasoning_chunks"] and not r["answer_chunks"]:
        print("  NO ANSWER: only reasoning arrived")
    if not r["reasoning_chunks"] and "</think>" in r["answer"]:
        print("  REASONING IN CONTENT: server has no reasoning parser (drill D01)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", default="r1-7b")
    ap.add_argument("--api-key")
    ap.add_argument("--host", help="Host header, for calling the gateway by IP")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of the table")
    a = ap.parse_args()
    r = probe(a.url, a.model, a.api_key, a.host, a.max_tokens, a.temperature, a.prompt, a.timeout)
    if a.json:
        print(json.dumps(r))
    else:
        show(r)
    raise SystemExit(1 if r["error"] else 0)


if __name__ == "__main__":
    main()
