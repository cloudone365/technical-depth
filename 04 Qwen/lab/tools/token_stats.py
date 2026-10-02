#!/usr/bin/env python3
"""How many tokens does the same content cost in different tokenizers? (Volume 01)

Tokens drive everything on a GB10: KV cache, context limits, decode time, cost per answer.
Qwen's byte-level BPE (≈151.6K entries) was built for Chinese and code as well as English;
this prints chars/token for each sample so you can see what that means for your data.

  python3 token_stats.py                                   # Qwen2.5 vs Llama 3.1 vs Mistral
  python3 token_stats.py --models Qwen/Qwen2.5-7B-Instruct deepseek-ai/DeepSeek-R1-Distill-Qwen-7B --file my.txt
Downloads tokenizer files only (a few MB each). Llama is gated: set HF_TOKEN.
"""
import argparse
import pathlib

SAMPLES = {
    "english": "The DGX Spark couples a Grace CPU and a Blackwell GPU over NVLink-C2C, and both share 128 GB of unified memory.",
    "chinese": "DGX Spark 通过 NVLink-C2C 将 Grace CPU 与 Blackwell GPU 连接在一起，两者共享 128 GB 统一内存。",
    "python": "def kv_bytes(layers, kv_heads, head_dim, dtype_bytes=2):\n    return 2 * layers * kv_heads * head_dim * dtype_bytes\n",
    "json": '{"model": "qwen2.5-7b", "util": 0.30, "max_len": 32768, "args": ["--enable-auto-tool-choice", "--tool-call-parser=hermes"]}',
    "yaml": "resources:\n  requests: {cpu: \"4\", memory: 16Gi, nvidia.com/gpu: \"1\"}\n  limits: {memory: 48Gi, nvidia.com/gpu: \"1\"}\n",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["Qwen/Qwen2.5-7B-Instruct", "meta-llama/Llama-3.1-8B-Instruct",
                                                    "mistralai/Mistral-7B-Instruct-v0.3"])
    ap.add_argument("--file", help="add your own text file as a sample")
    a = ap.parse_args()
    from transformers import AutoTokenizer
    samples = dict(SAMPLES)
    if a.file:
        samples[pathlib.Path(a.file).name] = pathlib.Path(a.file).read_text(errors="ignore")
    toks = {}
    for m in a.models:
        try:
            toks[m] = AutoTokenizer.from_pretrained(m)
        except Exception as e:                                    # noqa: BLE001 — gated or offline
            print(f"skip {m}: {type(e).__name__}")
    print(f"{'sample':10} {'chars':>6} " + " ".join(f"{m.split('/')[-1][:22]:>24}" for m in toks))
    for name, text in samples.items():
        cells = []
        for t in toks.values():
            n = len(t.encode(text, add_special_tokens=False))
            cells.append(f"{n:>6} tok {len(text) / n:5.2f} c/t   ")
        print(f"{name:10} {len(text):>6} " + " ".join(f"{c:>24}" for c in cells))
    for m, t in toks.items():
        print(f"vocab {m}: {len(t):,} entries")


if __name__ == "__main__":
    main()
