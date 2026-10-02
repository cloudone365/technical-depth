#!/usr/bin/env python3
"""Local model evaluation + economics harness (Volumes 11, 13, 34–37, 40, 41).

Runs three small, original, verifiable suites against any OpenAI-compatible
endpoint (vLLM, SGLang, KServe, Ollama, LiteLLM, a cloud API):
  math   40 multi-step word problems          → exact integer match
  code   12 Python functions with unit tests  → tests executed in a subprocess
  json   8 extraction tasks with a schema     → exact field match
and records, per request: latency, completion tokens, reasoning tokens (when the
server returns `reasoning_content`, e.g. vLLM --reasoning-parser deepseek_r1).

Then prices the run: local $/1M output tokens from Spark power draw + electricity
+ amortised hardware, next to an API price you supply.

  python3 eval_harness.py --url http://localhost:8000 --model r1-32b --suites math json
  python3 eval_harness.py --url … --model … --api-price-out 2.19 --out results/r1-32b.json
  python3 eval_harness.py --report results/*.json          # side-by-side table
Stdlib only. The code suite executes model-written code: run it in a pod, not on your laptop.
"""
import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
SYSTEM = {
    "math": "Solve the problem. End your reply with a line of the form 'ANSWER: <integer>'.",
    "code": "Write only the requested Python function in one ```python code block. No explanations.",
    "json": "Extract the fields and reply with one JSON object only, no markdown.",
}


def chat(url, model, system, user, max_tokens, temperature, api_key, timeout=900):
    body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    dt = time.perf_counter() - t0
    msg = d["choices"][0]["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    if not reasoning and "</think>" in content:                     # models without a reasoning parser
        reasoning, content = content.split("</think>", 1)
    usage = d.get("usage", {})
    return content.strip(), reasoning, usage.get("completion_tokens", 0), usage.get("prompt_tokens", 0), dt


def grade_math(item, content):
    m = re.findall(r"ANSWER:\s*(-?[\d,]+)", content) or re.findall(r"(-?\d[\d,]*)", content)
    return bool(m) and int(m[-1].replace(",", "")) == item["answer"]


def grade_code(item, content):
    m = re.search(r"```(?:python)?\n(.*?)```", content, re.S)
    code = (m.group(1) if m else content) + "\n\n" + item["tests"] + "\nprint('OK')\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
    try:
        r = subprocess.run([sys.executable, "-I", f.name], capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and r.stdout.strip().endswith("OK")
    except subprocess.TimeoutExpired:
        return False
    finally:
        os.unlink(f.name)


def grade_json(item, content):
    txt = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        got = json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return False
        try:
            got = json.loads(m.group(0))
        except json.JSONDecodeError:
            return False
    return all(str(got.get(k)).strip().lower() == str(v).strip().lower() for k, v in item["expected"].items())


def prompt_for(suite, item):
    if suite == "math":
        return item["question"]
    if suite == "code":
        return item["prompt"]
    return f"Text: {item['text']}\nReturn JSON with exactly these keys and types: {json.dumps(item['schema'])}"


GRADERS = {"math": grade_math, "code": grade_code, "json": grade_json}


def run(a):
    results = {"model": a.model, "url": a.url, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "suites": {}}
    t_all = time.perf_counter()
    for suite in a.suites:
        items = [json.loads(l) for l in open(DATA / f"{ {'math': 'math_word', 'code': 'code_tasks', 'json': 'json_tasks'}[suite] }.jsonl")]
        items = items[: a.limit] if a.limit else items

        def one(item):
            try:
                c, r, ct, pt, dt = chat(a.url, a.model, SYSTEM[suite], prompt_for(suite, item),
                                        a.max_tokens, a.temperature, a.api_key)
                return {"id": item["id"], "ok": GRADERS[suite](item, c), "latency": dt, "completion_tokens": ct,
                        "prompt_tokens": pt, "reasoning_chars": len(r), "answer_chars": len(c)}
            except Exception as e:                                   # noqa: BLE001 — record and continue
                return {"id": item["id"], "ok": False, "error": str(e)[:200], "latency": 0, "completion_tokens": 0,
                        "prompt_tokens": 0, "reasoning_chars": 0, "answer_chars": 0}

        with cf.ThreadPoolExecutor(a.concurrency) as ex:
            rows = list(ex.map(one, items))
        ok = sum(r["ok"] for r in rows)
        lat = [r["latency"] for r in rows if r["latency"]]
        toks = [r["completion_tokens"] for r in rows]
        results["suites"][suite] = {
            "n": len(rows), "correct": ok, "accuracy": ok / len(rows),
            "latency_p50": statistics.median(lat) if lat else None,
            "latency_p95": sorted(lat)[int(0.95 * (len(lat) - 1))] if lat else None,
            "mean_completion_tokens": statistics.mean(toks) if toks else 0,
            "reasoning_share": (sum(r["reasoning_chars"] for r in rows) /
                                max(1, sum(r["reasoning_chars"] + r["answer_chars"] for r in rows))),
            "errors": [r for r in rows if "error" in r][:3], "rows": rows}
        print(f"{suite:5} {ok:3d}/{len(rows):<3d} acc={ok/len(rows):.2f}  p50={results['suites'][suite]['latency_p50'] or 0:.1f}s  "
              f"mean_out_tokens={results['suites'][suite]['mean_completion_tokens']:.0f}  "
              f"reasoning_share={results['suites'][suite]['reasoning_share']:.2f}", flush=True)
    wall = time.perf_counter() - t_all
    out_tokens = sum(r["completion_tokens"] for s in results["suites"].values() for r in s["rows"])
    results["economics"] = econ(out_tokens, wall, a)
    e = results["economics"]
    print(f"\nrun: {out_tokens} output tokens in {wall:.0f}s = {e['tokens_per_s']:.1f} tok/s aggregate")
    if a.hosted:
        price = a.api_price_out
        print(f"hosted model: cost = provider price ({'$%.2f' % price if price else 'not given'} per 1M output tokens); "
              f"this run ≈ ${(price or 0) * out_tokens / 1e6:.4f} in output tokens")
    else:
        print(f"local cost: ${e['local_usd_per_mtok']:.2f} per 1M output tokens "
              f"(power {a.power_w} W @ ${a.kwh_price}/kWh + hardware ${a.hw_price} over {a.hw_years} y at {a.duty*100:.0f}% duty)")
    if a.api_price_out and not a.hosted:
        print(f"API price: ${a.api_price_out:.2f} per 1M output tokens → local is "
              f"{a.api_price_out / e['local_usd_per_mtok']:.1f}× cheaper at this throughput"
              if e["local_usd_per_mtok"] < a.api_price_out else
              f"API price: ${a.api_price_out:.2f} per 1M output tokens → API is cheaper at this throughput")
    if a.out:
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        json.dump(results, open(a.out, "w"), indent=1)
        print(f"saved {a.out}")


def econ(out_tokens, wall_s, a):
    tps = out_tokens / wall_s if wall_s else 0
    energy_usd_per_s = a.power_w / 1000 * a.kwh_price / 3600
    hw_usd_per_s = a.hw_price / (a.hw_years * 365 * 24 * 3600 * a.duty)
    per_mtok = (energy_usd_per_s + hw_usd_per_s) / tps * 1e6 if tps else float("inf")
    if getattr(a, "hosted", False):            # a hosted API: the price per token IS the cost
        per_mtok = a.api_price_out if a.api_price_out else float("nan")
    return {"tokens_per_s": tps, "local_usd_per_mtok": per_mtok, "hosted": bool(getattr(a, "hosted", False))}


def report(paths):
    rows = [json.load(open(p)) for p in paths]
    suites = sorted({s for r in rows for s in r["suites"]})
    hdr = (f"{'model':34}" + "".join(f"{s+' acc':>10}" for s in suites)
           + f"{'out tok':>9}{'reason%':>9}{'p50 s':>7}{'tok/ok':>8}{'tok/s':>8}{'$/Mtok':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        su = r["suites"].values()
        toks = statistics.mean(s["mean_completion_tokens"] for s in su)
        reas = statistics.mean(s["reasoning_share"] for s in su)
        p50 = statistics.mean(s["latency_p50"] for s in su)
        # output tokens spent per CORRECT answer: what a right answer actually costs
        spent = sum(s["mean_completion_tokens"] * s["n"] for s in su)
        ok = sum(s["correct"] for s in su)
        per_ok = f"{spent / ok:8.0f}" if ok else f"{'inf':>8}"
        print(f"{r['model'][:34]:34}" + "".join(f"{r['suites'].get(s, {}).get('accuracy', float('nan')):10.2f}" for s in suites)
              + f"{toks:9.0f}{reas*100:8.0f}%{p50:7.1f}{per_ok}{r['economics']['tokens_per_s']:8.1f}"
              + f"{r['economics']['local_usd_per_mtok']:8.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "none"))
    ap.add_argument("--suites", nargs="+", default=["math", "code", "json"], choices=list(GRADERS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.6, help="DeepSeek recommends 0.5–0.7 for R1 models")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--power-w", type=float, default=200.0, help="measured Spark wall power under load")
    ap.add_argument("--kwh-price", type=float, default=0.15)
    ap.add_argument("--hw-price", type=float, default=4000.0)
    ap.add_argument("--hw-years", type=float, default=3.0)
    ap.add_argument("--duty", type=float, default=0.5, help="fraction of time the box does useful work")
    ap.add_argument("--hosted", action="store_true",
                    help="the endpoint is a paid API: $/Mtok = --api-price-out, not local power + hardware")
    ap.add_argument("--api-price-out", type=float, help="USD per 1M output tokens of the API you compare with")
    ap.add_argument("--out")
    ap.add_argument("--report", nargs="+")
    a = ap.parse_args()
    if a.report:
        report(a.report)
    else:
        if not a.model:
            ap.error("--model is required")
        run(a)
