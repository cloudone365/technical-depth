#!/usr/bin/env python3
"""Pin catalog models to exact Hugging Face commits, and detect upstream changes (Volume 33).

  report : for every entry in models.yaml, ask the Hub for the current commit of the
           repo's main branch and compare with the pinned `revision` (if any)
  pin    : write the current commits into models.yaml as `revision:` (review the diff,
           then `scripts/gen_overlays.py` adds --revision to each overlay)

  python3 catalog_drift.py report                 # exit 1 if any pinned model has moved upstream
  python3 catalog_drift.py pin --only r1-7b r1-32b-fp8
Env: HF_ENDPOINT (default https://huggingface.co), HF_TOKEN for gated repos. Stdlib + PyYAML.
"""
import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

import yaml

LAB = pathlib.Path(__file__).resolve().parent.parent
CATALOG = LAB / "models.yaml"   # --catalog overrides (tests)


def upstream(repo, branch="main"):
    url = f"{os.environ.get('HF_ENDPOINT', 'https://huggingface.co').rstrip('/')}/api/models/{repo}/revision/{branch}"
    h = {"Authorization": f"Bearer {os.environ['HF_TOKEN']}"} if os.environ.get("HF_TOKEN") else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        d = json.load(r)
    return d["sha"], d.get("lastModified", "?")


def report(models):
    moved = 0
    for m in models:
        try:
            sha, when = upstream(m["hf"])
        except urllib.error.HTTPError as e:
            print(f"  ?     {m['name']:18} {m['hf']}  HTTP {e.code} (gated? set HF_TOKEN)")
            continue
        pin = m.get("revision")
        if not pin:
            state = "UNPINNED"
        elif pin == sha:
            state = "ok"
        else:
            state, moved = "MOVED", moved + 1
        print(f"  {state:9} {m['name']:18} pinned {str(pin)[:12]:12}  upstream {sha[:12]} ({when})")
    print(f"{len(models)} models, {moved} moved upstream since pinning")
    return 1 if moved else 0


def pin(models, only, catalog):
    text = catalog.read_text()
    for m in models:
        if only and m["name"] not in only:
            continue
        sha, _ = upstream(m["hf"])
        block = re.compile(rf"(  - name: {re.escape(m['name'])}\n    hf: {re.escape(m['hf'])}\n)(    revision: [0-9a-f]+\n)?")
        text, n = block.subn(lambda mo: mo.group(1) + f"    revision: {sha}\n", text, count=1)
        print(f"  {'pinned' if n else 'NOT FOUND'} {m['name']} → {sha[:12]}")
    catalog.write_text(text)
    print("models.yaml updated — run scripts/gen_overlays.py and review git diff")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["report", "pin"])
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--catalog", type=pathlib.Path, default=CATALOG)
    a = ap.parse_args()
    models = yaml.safe_load(a.catalog.read_text())["models"]
    return report(models) if a.cmd == "report" else pin(models, a.only, a.catalog)


if __name__ == "__main__":
    sys.exit(main())
