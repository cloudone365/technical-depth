#!/usr/bin/env python3
"""RAG over this repository, end to end (Volume 29).

  ingest : chunk Markdown by heading → embed (OpenAI /v1/embeddings, e.g. vLLM serving BAAI/bge-m3)
           → upsert into Qdrant (REST) with path/heading payloads
  ask    : embed the question → top-k search → prompt the chat model with numbered sources
           → answer with [n] citations (reasoning models: the <think> part is dropped)

  python3 rag_demo.py ingest --repo ../../.. --dirs "02 Kubernetes" "03 DeepSeek"
  python3 rag_demo.py ask "How do I stop a vLLM rollout from cutting streams?"
Env/flags: --qdrant http://qdrant.llm-serving:6333  --embed-url http://bge-m3.llm-serving:8000
           --embed-model bge-m3  --chat-url http://vllm.llm-serving:8000  --chat-model r1-32b
Stdlib only.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.request


def http(method, url, body=None, timeout=300):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def chunks(md_path, max_chars=1200):
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"```mermaid.*?```", "", text, flags=re.S)             # diagrams embed badly
    sections = re.split(r"(?m)^(#{1,3} .+)$", text)
    heading = md_path.stem
    for part in sections:
        if re.match(r"#{1,3} ", part):
            heading = part.lstrip("# ").strip()
            continue
        body = part.strip()
        for i in range(0, len(body), max_chars):
            piece = body[i:i + max_chars].strip()
            if len(piece) > 80:
                yield heading, piece


def embed(a, texts):
    d = http("POST", a.embed_url.rstrip("/") + "/v1/embeddings", {"model": a.embed_model, "input": texts})
    return [e["embedding"] for e in sorted(d["data"], key=lambda e: e["index"])]


def ingest(a):
    files = [p for d in a.dirs for p in sorted((pathlib.Path(a.repo) / d).rglob("*.md"))]
    items = [(str(p.relative_to(a.repo)), h, t) for p in files for h, t in chunks(p)]
    print(f"{len(files)} files → {len(items)} chunks")
    col = f"{a.qdrant}/collections/{a.collection}"
    first = embed(a, [items[0][2]])[0]
    try:
        http("DELETE", col)
    except Exception:                                                     # noqa: BLE001 — may not exist yet
        pass
    http("PUT", col, {"vectors": {"size": len(first), "distance": "Cosine"}})
    for i in range(0, len(items), a.batch):
        batch = items[i:i + a.batch]
        vecs = embed(a, [f"{h}\n{t}" for _, h, t in batch])
        pts = [{"id": int(hashlib.sha1(f"{p}{h}{t[:64]}".encode()).hexdigest()[:15], 16), "vector": v,
                "payload": {"path": p, "heading": h, "text": t}} for (p, h, t), v in zip(batch, vecs)]
        http("PUT", f"{col}/points?wait=true", {"points": pts})
        print(f"  upserted {min(i + a.batch, len(items))}/{len(items)}", end="\r")
    print(f"\ncollection {a.collection}: {http('GET', col)['result']['points_count']} points, dim {len(first)}")


def ask(a):
    qv = embed(a, [a.question])[0]
    hits = http("POST", f"{a.qdrant}/collections/{a.collection}/points/search",
                {"vector": qv, "limit": a.k, "with_payload": True})["result"]
    ctx = "\n\n".join(f"[{i+1}] ({h['payload']['path']} § {h['payload']['heading']})\n{h['payload']['text']}"
                      for i, h in enumerate(hits))
    if a.show_context:
        print(ctx, "\n" + "=" * 80)
    msgs = [{"role": "system", "content": "Answer only from the numbered sources. Cite them like [1]. "
                                          "If the sources do not contain the answer, say so."},
            {"role": "user", "content": f"Sources:\n{ctx}\n\nQuestion: {a.question}"}]
    d = http("POST", a.chat_url.rstrip("/") + "/v1/chat/completions",
             {"model": a.chat_model, "messages": msgs, "max_tokens": a.max_tokens, "temperature": 0.3})
    ans = d["choices"][0]["message"].get("content") or ""
    ans = ans.split("</think>", 1)[-1].strip()
    print(ans)
    print("\nsources:")
    for i, h in enumerate(hits):
        print(f"  [{i+1}] {h['score']:.3f}  {h['payload']['path']} § {h['payload']['heading']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "ask"):
        s = sub.add_parser(name)
        s.add_argument("--qdrant", default="http://qdrant.llm-serving:6333")
        s.add_argument("--collection", default="technical-depth")
        s.add_argument("--embed-url", default="http://bge-m3.llm-serving:8000")
        s.add_argument("--embed-model", default="bge-m3")
        if name == "ingest":
            s.add_argument("--repo", default=".")
            s.add_argument("--dirs", nargs="+", default=["02 Kubernetes", "03 DeepSeek"])
            s.add_argument("--batch", type=int, default=32)
        else:
            s.add_argument("question")
            s.add_argument("--chat-url", default="http://vllm.llm-serving:8000")
            s.add_argument("--chat-model", default="r1-32b")
            s.add_argument("-k", type=int, default=5)
            s.add_argument("--max-tokens", type=int, default=2048)
            s.add_argument("--show-context", action="store_true")
    a = ap.parse_args()
    sys.exit(ingest(a) if a.cmd == "ingest" else ask(a))
