#!/usr/bin/env python3
"""Vision-language probe for Qwen2.5-VL (Volume 05).

Draws small synthetic images with known answers (bar chart, serial-number label, shapes
to count, price table), sends them as base64 data URLs in the OpenAI vision format, and
scores exact answers. Also prints how many vision tokens each image should cost:
Qwen2.5-VL patches are 14 px, merged 2×2 → one token per 28×28 px after resizing to
multiples of 28 within [min_pixels, max_pixels].

  python3 vl_eval.py --url http://localhost:8000 --model qwen2.5-vl-7b
  python3 vl_eval.py … --size 1024 --save /tmp/vl      # bigger images, keep PNGs to look at
Needs Pillow. Images are generated locally; nothing is executed.
"""
import argparse
import base64
import io
import json
import math
import pathlib
import random
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

MIN_PIX, MAX_PIX = 4 * 28 * 28, 16384 * 28 * 28


def vision_tokens(w, h, factor=28, min_pix=MIN_PIX, max_pix=MAX_PIX):
    """Qwen2.5-VL smart_resize, then one token per 28×28 block."""
    hb, wb = max(factor, round(h / factor) * factor), max(factor, round(w / factor) * factor)
    if hb * wb > max_pix:
        beta = math.sqrt(h * w / max_pix)
        hb, wb = math.floor(h / beta / factor) * factor, math.floor(w / beta / factor) * factor
    elif hb * wb < min_pix:
        beta = math.sqrt(min_pix / (h * w))
        hb, wb = math.ceil(h * beta / factor) * factor, math.ceil(w * beta / factor) * factor
    return (hb // factor) * (wb // factor), (wb, hb)


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                       # Pillow < 10.1
        return ImageFont.load_default()


def make(kind, rnd, size):
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    s = size / 512
    if kind == "bars":
        vals = rnd.sample(range(1, 10), 4)
        for i, (lab, v) in enumerate(zip("ABCD", vals)):
            x = int((60 + i * 110) * s)
            d.rectangle([x, int((460 - v * 40) * s), x + int(70 * s), int(460 * s)], fill=(31, 111, 235))
            d.text((x + int(25 * s), int(470 * s)), lab, fill="black", font=font(int(28 * s)))
        q, ans = "Which bar is the tallest? Answer with the letter only.", "ABCD"[vals.index(max(vals))]
    elif kind == "serial":
        ans = f"{rnd.randint(1000, 9999)}-{rnd.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rnd.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
        d.rectangle([int(40 * s), int(180 * s), int(470 * s), int(330 * s)], outline="black", width=max(1, int(4 * s)))
        d.text((int(70 * s), int(225 * s)), f"SN: {ans}", fill="black", font=font(int(56 * s)))
        q = "What is the serial number after 'SN:'? Answer with the serial number only."
    elif kind == "count":
        n = rnd.randint(2, 7)
        for i in range(n):
            x, y = int((70 + (i % 4) * 110) * s), int((120 + (i // 4) * 170) * s)
            d.ellipse([x, y, x + int(80 * s), y + int(80 * s)], fill=(118, 185, 0), outline="black")
        q, ans = "How many green circles are in the image? Answer with a number only.", str(n)
    else:  # table
        rows = [(name, rnd.randint(2, 49)) for name in rnd.sample(["cable", "NIC", "SSD", "fan", "PSU", "rail"], 3)]
        f = font(int(36 * s))
        d.text((int(60 * s), int(80 * s)), "Item      Price", fill="black", font=f)
        for i, (name, p) in enumerate(rows):
            d.text((int(60 * s), int((150 + i * 70) * s)), f"{name:<9} {p}", fill="black", font=f)
        q, ans = "What is the sum of the Price column? Answer with a number only.", str(sum(p for _, p in rows))
    meta = PngInfo()
    meta.add_text("answer", ans)            # ground truth for the CI mock; the model only sees pixels
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=meta)
    return buf.getvalue(), q, ans


def ask(a, png, q):
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    body = {"model": a.model, "temperature": 0, "max_tokens": 32, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": q}]}]}
    h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {a.api_key}"} if a.api_key else {})}
    req = urllib.request.Request(a.url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(), headers=h)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    return (d["choices"][0]["message"]["content"] or "").strip(), d.get("usage", {}).get("prompt_tokens"), time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", default="qwen2.5-vl-7b")
    ap.add_argument("--n", type=int, default=5, help="images per kind")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--save", help="directory to keep the generated PNGs")
    ap.add_argument("--api-key")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    est, _ = vision_tokens(a.size, a.size)
    print(f"{a.size}×{a.size} px → ≈{est} vision tokens per image (Qwen2.5-VL 28-px blocks)")
    score, lat, ptoks = {}, [], []
    for kind in ("bars", "serial", "count", "table"):
        for i in range(a.n):
            png, q, ans = make(kind, rnd, a.size)
            if a.save:
                pathlib.Path(a.save).mkdir(parents=True, exist_ok=True)
                pathlib.Path(a.save, f"{kind}-{i}.png").write_bytes(png)
            got, pt, dt = ask(a, png, q)
            ok = got.strip().rstrip(".").upper() == ans.upper()
            score.setdefault(kind, []).append(ok)
            lat.append(dt)
            if pt:
                ptoks.append(pt)
            print(f"  {kind:6} #{i}  want {ans:8} got {got[:20]:20} {'ok' if ok else 'BAD'}")
    total = sum(sum(v) for v in score.values())
    n = sum(len(v) for v in score.values())
    print("accuracy  " + "  ".join(f"{k} {sum(v)}/{len(v)}" for k, v in score.items()) + f"  total {total}/{n}")
    if ptoks:
        print(f"prompt tokens per request: median {sorted(ptoks)[len(ptoks) // 2]} (vision ≈{est} + text)")
    print(f"latency p50 {sorted(lat)[len(lat) // 2] * 1000:.0f} ms")


if __name__ == "__main__":
    main()
