#!/usr/bin/env python3
"""
capstone_scorecard.py — grade the Module 01 capstone from EVIDENCE in lab/.cache/.
Each challenge passes only if the artifact produced by the real run exists and says so.

  python3 tools/capstone_scorecard.py            # table
  python3 tools/capstone_scorecard.py --json
"""
import argparse
import glob
import json
import os
import re

CACHE = os.environ.get("SPARK_LAB_CACHE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache")


def p(*parts):
    return os.path.join(CACHE, *parts)


def jload(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def latest(pattern):
    files = sorted(glob.glob(p(pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


CHECKS = []


def check(vol, title):
    def deco(fn):
        CHECKS.append((vol, title, fn))
        return fn
    return deco


@check("01", "Runs are logged (ansible.log has >= 10 playbook runs)")
def _():
    n = len(re.findall(r"PLAY RECAP", read(p("ansible.log"))))
    return n >= 10, f"{n} runs logged"


@check("01", "Fact cache populated with ansible_local.spark for every Spark")
def _():
    files = [f for f in glob.glob(p("facts", "*")) if not f.endswith("localhost")]
    ok = [f for f in files if (jload(f) or {}).get("ansible_local", {}).get("spark", {}).get("gpu", {}).get("present")]
    return len(files) > 0 and len(ok) == len(files), f"{len(ok)}/{len(files)} hosts with GPU facts"


@check("02", "Fleet benchmark recorded (>= 7 runs in bench.csv)")
def _():
    rows = [line for line in read(p("bench.csv")).splitlines() if "," in line]
    return len(rows) >= 7, f"{len(rows)} rows"


@check("06-25", "End-to-end validation passes on every Spark")
def _():
    reports = glob.glob(p("validation", "*.json"))
    bad = [os.path.basename(r) for r in reports
           if not all((jload(r) or {}).get("results", {"x": False}).values())]
    return len(reports) > 0 and not bad, f"{len(reports)} reports, failing: {bad or 'none'}"


@check("10", "Firmware consistent across nodes (no mismatches)")
def _():
    inv = jload(p("firmware-inventory.json"))
    if inv is None:
        return False, "no firmware-inventory.json"
    return inv.get("mismatch") == {}, f"mismatch={inv.get('mismatch')}"


@check("22", "Latest drift check is clean")
def _():
    f = latest("drift/check-*.md") or latest("drift/recheck-*.md")
    if not f:
        return False, "no drift report"
    rows = re.findall(r"^\| (\S+) \| (\d+) \| (\d+) \|$", read(f), re.M)
    dirty = [h for h, d, fl in rows if int(d) or int(fl)]
    return bool(rows) and not dirty, f"{os.path.basename(f)} dirty={dirty or 'none'}"


@check("22", "Self-heal proven (a recheck report exists)")
def _():
    f = latest("drift/recheck-*.md")
    return f is not None, os.path.basename(f) if f else "never healed"


@check("24", "Incident drill produced an evidence bundle")
def _():
    b = glob.glob(p("incidents", "*.tgz"))
    return len(b) > 0, f"{len(b)} bundles"


@check("16", "kubeconfig fetched from the kubeadm root cluster")
def _():
    k = glob.glob(p("kubeconfig-*.yaml"))
    ok = any("https://127.0.0.1" not in read(x) and "server: https://" in read(x) for x in k)
    return ok, ", ".join(os.path.basename(x) for x in k) or "none"


@check("03B/19", "vault01 CA copied; no Vault token or AppRole secret left in the cache")
def _():
    ca = os.path.exists(p("vault-ca.crt"))
    leaks = [os.path.basename(x) for x in glob.glob(p("*")) if re.search(r"(vault-init|approle|token)", os.path.basename(x))]
    return ca and not leaks, f"ca={ca} leaks={leaks or 'none'}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    results = []
    for vol, title, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 — scorecard must never crash
            ok, detail = False, f"error: {e}"
        results.append({"volume": vol, "challenge": title, "pass": bool(ok), "evidence": detail})
    score = sum(r["pass"] for r in results)
    if a.json:
        print(json.dumps({"score": score, "total": len(results), "results": results}, indent=2))
    else:
        for r in results:
            print(f"[{'PASS' if r['pass'] else 'FAIL'}] Vol {r['volume']:<6} {r['challenge']}\n         evidence: {r['evidence']}")
        print(f"\nScore: {score}/{len(results)}")
    raise SystemExit(0 if score == len(results) else 1)


if __name__ == "__main__":
    main()
