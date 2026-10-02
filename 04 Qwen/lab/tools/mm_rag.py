#!/usr/bin/env python3
"""Multimodal document RAG with Qwen2.5-VL + bge-m3 + Qdrant (Volume 19).

  make-docs : render N synthetic spec-sheet pages (PNG) with facts and a question per page
  ingest    : Qwen2.5-VL transcribes each page image → bge-m3 embeds the transcript →
              Qdrant collection (payload: page path + transcript)
  ask       : embed the question → retrieve the best page → send that page IMAGE and the
              question to Qwen2.5-VL → answer (the model reads the pixels, not our transcript)
  eval      : run every page's question; report retrieval hit@1 and answer accuracy

  python3 mm_rag.py make-docs --dir /tmp/pages -n 12
  python3 mm_rag.py ingest --dir /tmp/pages --vl-url http://localhost:8000 --embed-url http://localhost:8001 --qdrant http://localhost:6333
  python3 mm_rag.py eval   --dir /tmp/pages --vl-url … --embed-url … --qdrant …
Needs Pillow. Reuses 03's rag_demo.py (embeddings + Qdrant REST).
"""
import argparse
import base64
import io
import json
import pathlib
import random
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "03 DeepSeek" / "lab" / "tools"))
sys.path.insert(0, "/app/ds-tools")
import rag_demo  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PIL.PngImagePlugin import PngInfo  # noqa: E402

RACKS = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15", "R16"]


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_docs(a):
    rnd = random.Random(a.seed)
    d = pathlib.Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, rack in enumerate(rnd.sample(RACKS, a.n)):
        kw, pdu, row = rnd.randint(12, 48), f"PDU-{rnd.randint(100, 999)}", rnd.choice("ABCDEFGH")
        lines = [f"Rack {rack} specification", f"Row: {row}", f"Power budget: {kw} kW", f"PDU model: {pdu}",
                 f"Cooling: {'liquid' if kw > 30 else 'air'}"]
        img = Image.new("RGB", (768, 512), "white")
        dr = ImageDraw.Draw(img)
        dr.text((40, 30), lines[0], fill="black", font=font(40))
        for j, line in enumerate(lines[1:]):
            dr.text((40, 120 + j * 70), line, fill="black", font=font(34))
        q = f"What is the power budget of rack {rack}, in kW? Answer with a number only."
        meta = PngInfo()
        meta.add_text("transcript", "\n".join(lines))              # for the CI mock only
        meta.add_text("qa", json.dumps({q: str(kw)}))
        p = d / f"page-{i:02d}.png"
        img.save(p, "PNG", pnginfo=meta)
        manifest.append({"page": p.name, "question": q, "answer": str(kw)})
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {a.n} pages + manifest.json to {d}")


def vl(a, png_bytes, text, max_tokens=256):
    url = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    body = {"model": a.vl_model, "temperature": 0, "max_tokens": max_tokens, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": text}]}]}
    req = urllib.request.Request(a.vl_url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return (json.load(r)["choices"][0]["message"]["content"] or "").strip()


def ingest(a):
    d = pathlib.Path(a.dir)
    pages = sorted(d.glob("page-*.png"))
    col = f"{a.qdrant}/collections/{a.collection}"
    pts = []
    for i, p in enumerate(pages):
        transcript = vl(a, p.read_bytes(), "Transcribe all text on this page exactly, line by line.")
        vec = rag_demo.embed(a, [transcript])[0]
        if i == 0:
            try:
                rag_demo.http("DELETE", col)
            except Exception:                                   # noqa: BLE001 — may not exist
                pass
            rag_demo.http("PUT", col, {"vectors": {"size": len(vec), "distance": "Cosine"}})
        pts.append({"id": i, "vector": vec, "payload": {"page": p.name, "transcript": transcript}})
        print(f"  {p.name}: {transcript.splitlines()[0] if transcript else '(empty)'}")
    rag_demo.http("PUT", f"{col}/points?wait=true", {"points": pts})
    print(f"collection {a.collection}: {len(pts)} pages")


def ask_one(a, question):
    hit = rag_demo.http("POST", f"{a.qdrant}/collections/{a.collection}/points/search",
                        {"vector": rag_demo.embed(a, [question])[0], "limit": 1, "with_payload": True})["result"][0]
    page = hit["payload"]["page"]
    return page, vl(a, (pathlib.Path(a.dir) / page).read_bytes(), question, max_tokens=32)


def evaluate(a):
    man = json.loads((pathlib.Path(a.dir) / "manifest.json").read_text())
    hit = ok = 0
    for m in man:
        page, ans = ask_one(a, m["question"])
        hit += page == m["page"]
        ok += ans.strip().rstrip(".").split()[0:1] == [m["answer"]] if ans else False
        print(f"  {m['page']}  retrieved {page}  answer {ans[:12]!r}  want {m['answer']}")
    print(f"{len(man)} questions  retrieval hit@1 {hit}/{len(man)}  answer accuracy {ok}/{len(man)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("make-docs", "ingest", "ask", "eval"):
        s = sub.add_parser(name)
        s.add_argument("--dir", default="/tmp/pages")
        if name == "make-docs":
            s.add_argument("-n", type=int, default=12)
            s.add_argument("--seed", type=int, default=3)
            continue
        s.add_argument("--vl-url", default="http://vllm.llm-serving:8000")
        s.add_argument("--vl-model", default="qwen2.5-vl-7b")
        s.add_argument("--embed-url", default="http://bge-m3.llm-serving:8000")
        s.add_argument("--embed-model", default="bge-m3")
        s.add_argument("--qdrant", default="http://qdrant.llm-serving:6333")
        s.add_argument("--collection", default="qwen-mm")
        if name == "ask":
            s.add_argument("question")
    a = ap.parse_args()
    if a.cmd == "make-docs":
        make_docs(a)
    elif a.cmd == "ingest":
        ingest(a)
    elif a.cmd == "ask":
        print(ask_one(a, a.question))
    else:
        evaluate(a)


if __name__ == "__main__":
    main()
