#!/usr/bin/env python3
"""Docs checks for the curriculum folders.

  python3 .github/scripts/docs_check.py "02 Kubernetes" [--render] [--png OUTDIR]

* every relative link / image in *.md resolves to a file (anchors ignored)
* every ```mermaid block parses and renders (with --render; needs mmdc)
* no leftover placeholders (TODO, TBD, lorem)
Exit 1 on any failure.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

args = [a for a in sys.argv[1:] if not a.startswith("--")]
render = "--render" in sys.argv
png_dir = None
if "--png" in sys.argv:
    png_dir = sys.argv[sys.argv.index("--png") + 1]
    args = [a for a in args if a != png_dir]
fails = 0
LINK = re.compile(r"\]\(([^)\s]+)\)")
MERMAID = re.compile(r"```mermaid\n(.*?)```", re.S)
PLACEHOLDER = re.compile(r"\b(TODO|TBD|lorem ipsum|FIXME)\b", re.I)

puppeteer = None
if render:
    cfg = {"args": ["--no-sandbox"]}
    for exe in ("/opt/pw-browsers/chromium", os.environ.get("PUPPETEER_EXECUTABLE_PATH", "")):
        if exe and os.path.exists(exe):
            cfg["executablePath"] = exe
            break
    puppeteer = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(cfg, puppeteer)
    puppeteer.close()

n_md = n_links = n_mmd = 0
def md_files(roots):
    for root in roots:
        if os.path.isfile(root):
            yield os.path.dirname(root) or ".", os.path.basename(root)
            continue
        for dirpath, _, files in os.walk(root):
            for f in sorted(files):
                if f.endswith(".md"):
                    yield dirpath, f


if True:
    for dirpath, f in md_files(args):
            path = os.path.join(dirpath, f)
            text = open(path, encoding="utf-8").read()
            n_md += 1
            # strip fenced code before link checks
            prose = re.sub(r"```.*?```", "", text, flags=re.S)
            for m in LINK.finditer(prose):
                target = m.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = urllib.parse.unquote(target.split("#")[0])
                if not target:
                    continue
                n_links += 1
                if not os.path.exists(os.path.normpath(os.path.join(dirpath, target))):
                    print(f"BROKEN LINK  {path}: {m.group(1)}")
                    fails += 1
            for m in PLACEHOLDER.finditer(prose):
                print(f"PLACEHOLDER  {path}: {m.group(0)}")
                fails += 1
            for i, block in enumerate(MERMAID.findall(text)):
                n_mmd += 1
                if not render:
                    continue
                with tempfile.TemporaryDirectory() as td:
                    src = os.path.join(td, "d.mmd")
                    open(src, "w").write(block)
                    out = os.path.join(png_dir, f"{f[:-3]}-{i}.png") if png_dir else os.path.join(td, "d.svg")
                    if png_dir:
                        os.makedirs(png_dir, exist_ok=True)
                    r = subprocess.run(["mmdc", "-p", puppeteer.name, "-i", src, "-o", out, "-b", "white"]
                                       + (["-s", "2"] if png_dir else []),
                                       capture_output=True, text=True, timeout=180)
                    if r.returncode != 0 or not os.path.exists(out):
                        print(f"MERMAID FAIL {path} block {i}: {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
                        fails += 1
print(f"{n_md} markdown files, {n_links} relative links, {n_mmd} mermaid blocks"
      f"{' rendered' if render else ''}, {fails} problems")
sys.exit(1 if fails else 0)
