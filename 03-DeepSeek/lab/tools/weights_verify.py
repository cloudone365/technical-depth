#!/usr/bin/env python3
"""Pin and verify model weights on disk (Volume 33).

  weights_verify.py snapshot <model-dir> [--out manifest.json]   # sha256 of every file + HF revision
  weights_verify.py verify   <model-dir> manifest.json            # exit 1 on any mismatch/missing/extra file
Model dirs are Hugging Face cache snapshots, e.g.
  /models/hf/models--deepseek-ai--DeepSeek-R1-Distill-Qwen-32B/snapshots/<revision>/
Symlinks into blobs/ are followed. Large files are hashed in 16 MiB chunks.
"""
import hashlib
import json
import os
import pathlib
import sys
import time


def sha256(path, chunk=16 * 2**20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def scan(root):
    root = pathlib.Path(root)
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*")) if p.is_file() and not p.name.startswith(".")}


def snapshot(root, out):
    files = scan(root)
    t0 = time.time()
    manifest = {"root": str(root), "revision": pathlib.Path(root).resolve().name, "created": time.strftime("%FT%T"),
                "files": {k: {"sha256": sha256(p), "bytes": os.path.getsize(p)} for k, p in files.items()}}
    total = sum(f["bytes"] for f in manifest["files"].values())
    json.dump(manifest, open(out, "w"), indent=1)
    print(f"{len(files)} files, {total/2**30:.1f} GiB hashed in {time.time()-t0:.0f}s → {out}")


def verify(root, manifest_path):
    m = json.load(open(manifest_path))
    files = scan(root)
    bad = 0
    for name, meta in m["files"].items():
        p = files.pop(name, None)
        if p is None:
            print(f"MISSING  {name}"); bad += 1
        elif os.path.getsize(p) != meta["bytes"] or sha256(p) != meta["sha256"]:
            print(f"CHANGED  {name}"); bad += 1
    for name in files:
        print(f"EXTRA    {name}"); bad += 1
    print("✓ weights match manifest" if not bad else f"✗ {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("snapshot", "verify"):
        sys.exit(__doc__)
    if sys.argv[1] == "snapshot":
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "weights-manifest.json"
        snapshot(sys.argv[2], out)
    else:
        sys.exit(verify(sys.argv[2], sys.argv[3]))
