#!/usr/bin/env python3
"""RAG over this repository, end to end (Volume 29).

  ingest   : chunk Markdown by heading → embed (OpenAI /v1/embeddings, e.g. vLLM serving BAAI/bge-m3)
             → upsert into a NEW Qdrant collection <name>-<timestamp> → atomically point alias <name>
             at it (blue/green: readers never see a half-built index) → keep the previous one for rollback
  ask      : embed the question → top-k search → prompt the chat model with numbered sources
             → answer with [n] citations (reasoning models: the <think> part is dropped)
  eval     : retrieval quality on data/rag_gold.jsonl — hit@1, hit@k, MRR, and the misses
  rollback : point the alias back at the previous collection

  python3 rag_demo.py ingest --repo ../../.. --dirs "02-Kubernetes" "03-DeepSeek"
  python3 rag_demo.py ask "How do I stop a vLLM rollout from cutting streams?"
  python3 rag_demo.py eval -k 5
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
import time
import urllib.error
import urllib.request

GOLD = pathlib.Path(__file__).resolve().parent.parent / "data" / "rag_gold.jsonl"


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
    alias = a.collection
    existing = {c["name"] for c in http("GET", f"{a.qdrant}/collections")["result"]["collections"]}
    while (phys := f"{alias}-{time.strftime('%Y%m%d%H%M%S')}") in existing:   # two ingests in one second
        time.sleep(1)
    col = f"{a.qdrant}/collections/{phys}"
    first = embed(a, [items[0][2]])[0]
    http("PUT", col, {"vectors": {"size": len(first), "distance": "Cosine"}})
    for i in range(0, len(items), a.batch):
        batch = items[i:i + a.batch]
        vecs = embed(a, [f"{h}\n{t}" for _, h, t in batch])
        pts = [{"id": int(hashlib.sha1(f"{p}{h}{t[:64]}".encode()).hexdigest()[:15], 16), "vector": v,
                "payload": {"path": p, "heading": h, "text": t}} for (p, h, t), v in zip(batch, vecs)]
        http("PUT", f"{col}/points?wait=true", {"points": pts})
        if sys.stdout.isatty():
            print(f"  upserted {min(i + a.batch, len(items))}/{len(items)}", end="\r")
    print(f"\ncollection {phys}: {http('GET', col)['result']['points_count']} points, dim {len(first)}")
    if a.gate > 0:                                        # quality gate: evaluate the NEW index before it goes live
        g = argparse.Namespace(**{**vars(a), "collection": phys, "k": 5, "min_hit": a.gate, "gold": a.gold})
        if evaluate(g):
            http("DELETE", col)
            print(f"GATE FAILED: hit@5 below {a.gate} — {phys} deleted, alias {alias} unchanged")
            return 1
        print("gate passed")
    switch_alias(a, alias, phys)
    return 0


def collections(a, alias):
    names = [c["name"] for c in http("GET", f"{a.qdrant}/collections")["result"]["collections"]]
    return sorted(n for n in names if re.fullmatch(re.escape(alias) + r"-\d{14}", n))


def current(a, alias):
    al = http("GET", f"{a.qdrant}/aliases")["result"]["aliases"]
    return next((x["collection_name"] for x in al if x["alias_name"] == alias), None)


def switch_alias(a, alias, target, keep=2):
    names = [c["name"] for c in http("GET", f"{a.qdrant}/collections")["result"]["collections"]]
    if alias in names:                                    # an old-style real collection with the alias's name
        http("DELETE", f"{a.qdrant}/collections/{alias}")
        print(f"migrated: removed plain collection {alias} so the name can become an alias")
    before = current(a, alias)
    acts = ([{"delete_alias": {"alias_name": alias}}] if before else []) + \
           [{"create_alias": {"collection_name": target, "alias_name": alias}}]
    http("POST", f"{a.qdrant}/collections/aliases", {"actions": acts})
    print(f"alias {alias}: {before or '(none)'} → {target}")
    for old in collections(a, alias)[:-keep]:             # keep the live one and the previous one
        if old != target:
            http("DELETE", f"{a.qdrant}/collections/{old}")
            print(f"pruned {old}")


def rollback(a):
    alias, cols = a.collection, collections(a, a.collection)
    live = current(a, alias)
    older = [c for c in cols if c < (live or "")]
    if not older:
        print(f"no collection older than {live} to roll back to")
        return 1
    switch_alias(a, alias, older[-1], keep=len(cols))
    return 0


def evaluate(a):
    gold = [json.loads(line) for line in open(a.gold) if line.strip()]
    if a.dirs:
        gold = [g for g in gold if any(g["expect"].startswith(d + "/") for d in a.dirs)]
    hit1 = hitk = rr = 0.0
    for g in gold:
        hits = http("POST", f"{a.qdrant}/collections/{a.collection}/points/search",
                    {"vector": embed(a, [g["question"]])[0], "limit": a.k, "with_payload": True})["result"]
        paths = [h["payload"]["path"] for h in hits]
        rank = next((i + 1 for i, p in enumerate(paths) if p == g["expect"]), None)
        hit1 += rank == 1
        hitk += rank is not None
        rr += 1 / rank if rank else 0
        if not rank:
            print(f"  MISS  {g['question'][:70]:70}  want {g['expect']}  got {paths[0] if paths else '-'}")
    n = len(gold) or 1
    print(f"{len(gold)} questions  hit@1 {hit1 / n:.2f}  hit@{a.k} {hitk / n:.2f}  MRR {rr / n:.2f}")
    return 0 if hitk / n >= a.min_hit else 1


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
    for name in ("ingest", "ask", "eval", "rollback"):
        s = sub.add_parser(name)
        s.add_argument("--qdrant", default="http://qdrant.llm-serving:6333")
        s.add_argument("--collection", default="technical-depth")
        s.add_argument("--embed-url", default="http://bge-m3.llm-serving:8000")
        s.add_argument("--embed-model", default="bge-m3")
        if name == "ingest":
            s.add_argument("--repo", default=".")
            s.add_argument("--dirs", nargs="+", default=["02-Kubernetes", "03-DeepSeek"])
            s.add_argument("--batch", type=int, default=32)
            s.add_argument("--gate", type=float, default=0.0, metavar="MIN_HIT",
                           help="only switch the alias if hit@5 on the gold set (within --dirs) ≥ MIN_HIT")
            s.add_argument("--gold", default=str(GOLD))
        elif name == "eval":
            s.add_argument("--gold", default=str(GOLD))
            s.add_argument("--dirs", nargs="*", help="only questions whose answer lives in these dirs")
            s.add_argument("-k", type=int, default=5)
            s.add_argument("--min-hit", type=float, default=0.0, help="exit 1 if hit@k is below this")
        elif name == "ask":
            s.add_argument("question")
            s.add_argument("--chat-url", default="http://vllm.llm-serving:8000")
            s.add_argument("--chat-model", default="r1-32b")
            s.add_argument("-k", type=int, default=5)
            s.add_argument("--max-tokens", type=int, default=2048)
            s.add_argument("--show-context", action="store_true")
    a = ap.parse_args()
    sys.exit({"ingest": ingest, "ask": ask, "eval": evaluate, "rollback": rollback}[a.cmd](a))
