#!/usr/bin/env python3
"""Model memory math for DGX Spark (Volumes 01, 02, 11, 12, 13, 14, 23).

Computes, from a model's architecture:
  * total and *active* parameters (dense, GQA, MLA, DeepSeekMoE)
  * weight memory for bf16 / fp8 / int4 (AWQ/GPTQ) / GGUF-like bit widths
  * KV-cache bytes per token (GQA: 2·L·kv_heads·head_dim; MLA: L·(kv_lora_rank + rope_dim))
  * how many tokens of KV fit in a vLLM --gpu-memory-utilization budget on a GB10
  * whether it fits one Spark, two Sparks, or neither

Usage:
  python3 model_math.py --list
  python3 model_math.py deepseek-r1-distill-qwen-32b --dtype bf16 --util 0.65
  python3 model_math.py deepseek-v3 --dtype fp8 --sparks 2
  python3 model_math.py --config /models/hf/.../config.json --dtype bf16
  python3 model_math.py --compare deepseek-v2-lite qwen2.5-7b llama-3.1-8b      # KV/token table
No third-party dependencies.
"""
import argparse
import json
import sys

GIB = 2**30
SPARK_VISIBLE_GIB = 119.7          # what CUDA reports as total on a GB10 (≈128 GB)
BYTES = {"bf16": 2.0, "fp16": 2.0, "fp8": 1.0, "int8": 1.0, "int4": 0.5, "awq": 0.5, "gptq": 0.5,
         "q4_k_m": 4.85 / 8, "q8_0": 8.5 / 8, "iq1_s": 1.58 / 8, "iq2_xxs": 2.06 / 8}

# Architecture presets (from the published config.json files).
PRESETS = {
    "deepseek-v3": dict(kind="mla-moe", vocab=129280, dim=7168, inter=18432, moe_inter=2048, layers=61,
                        dense_layers=3, heads=128, routed=256, shared=1, active=8, q_lora=1536, kv_lora=512,
                        nope=128, rope=64, v=128, tied=False, mtp_layers=1,
                        hf="deepseek-ai/DeepSeek-V3", note="native FP8 weights; R1 shares this architecture"),
    "deepseek-r1": dict(ref="deepseek-v3", hf="deepseek-ai/DeepSeek-R1"),
    "deepseek-v2-lite": dict(kind="mla-moe", vocab=102400, dim=2048, inter=10944, moe_inter=1408, layers=27,
                             dense_layers=1, heads=16, routed=64, shared=2, active=6, q_lora=0, kv_lora=512,
                             nope=128, rope=64, v=128, tied=False,
                             hf="deepseek-ai/DeepSeek-V2-Lite-Chat", note="MLA + DeepSeekMoE that fits one Spark"),
    "deepseek-coder-v2-lite": dict(ref="deepseek-v2-lite", hf="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct"),
    "qwen2.5-0.5b": dict(kind="gqa", vocab=151936, dim=896, inter=4864, layers=24, heads=14, kv_heads=2,
                         head_dim=64, tied=True, bias=True, hf="Qwen/Qwen2.5-0.5B-Instruct"),
    "deepseek-r1-distill-qwen-1.5b": dict(kind="gqa", vocab=151936, dim=1536, inter=8960, layers=28, heads=12,
                                          kv_heads=2, head_dim=128, tied=False, bias=True,
                                          hf="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"),
    "qwen2.5-7b": dict(kind="gqa", vocab=152064, dim=3584, inter=18944, layers=28, heads=28, kv_heads=4,
                       head_dim=128, tied=False, bias=True, hf="Qwen/Qwen2.5-7B-Instruct"),
    "deepseek-r1-distill-qwen-7b": dict(ref="qwen2.5-7b", hf="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"),
    "qwen2.5-14b": dict(kind="gqa", vocab=152064, dim=5120, inter=13824, layers=48, heads=40, kv_heads=8,
                        head_dim=128, tied=False, bias=True, hf="Qwen/Qwen2.5-14B-Instruct"),
    "deepseek-r1-distill-qwen-14b": dict(ref="qwen2.5-14b", hf="deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"),
    "qwen2.5-32b": dict(kind="gqa", vocab=152064, dim=5120, inter=27648, layers=64, heads=40, kv_heads=8,
                        head_dim=128, tied=False, bias=True, hf="Qwen/Qwen2.5-32B-Instruct"),
    "deepseek-r1-distill-qwen-32b": dict(ref="qwen2.5-32b", hf="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"),
    "llama-3.1-8b": dict(kind="gqa", vocab=128256, dim=4096, inter=14336, layers=32, heads=32, kv_heads=8,
                         head_dim=128, tied=False, hf="meta-llama/Llama-3.1-8B-Instruct"),
    "llama-3.3-70b": dict(kind="gqa", vocab=128256, dim=8192, inter=28672, layers=80, heads=64, kv_heads=8,
                          head_dim=128, tied=False, hf="meta-llama/Llama-3.3-70B-Instruct"),
    "deepseek-r1-distill-llama-70b": dict(ref="llama-3.3-70b", hf="deepseek-ai/DeepSeek-R1-Distill-Llama-70B"),
    "mistral-7b": dict(kind="gqa", vocab=32768, dim=4096, inter=14336, layers=32, heads=32, kv_heads=8,
                       head_dim=128, tied=False, hf="mistralai/Mistral-7B-Instruct-v0.3"),
    "mixtral-8x7b": dict(kind="gqa-moe", vocab=32000, dim=4096, inter=14336, layers=32, heads=32, kv_heads=8,
                         head_dim=128, tied=False, routed=8, active=2, hf="mistralai/Mixtral-8x7B-Instruct-v0.1"),
}


def resolve(name):
    p = dict(PRESETS[name])
    while "ref" in p:
        base = dict(PRESETS[p.pop("ref")])
        base.update(p)
        p = base
    return p


def from_hf_config(cfg):
    """Map a HF config.json (Qwen2/Llama/Mistral/Mixtral/DeepseekV2/V3) onto our schema."""
    arch = (cfg.get("architectures") or [""])[0]
    if "Deepseek" in arch:
        return dict(kind="mla-moe", vocab=cfg["vocab_size"], dim=cfg["hidden_size"], inter=cfg["intermediate_size"],
                    moe_inter=cfg["moe_intermediate_size"], layers=cfg["num_hidden_layers"],
                    dense_layers=cfg.get("first_k_dense_replace", 0), heads=cfg["num_attention_heads"],
                    routed=cfg["n_routed_experts"], shared=cfg.get("n_shared_experts", 0) or 0,
                    active=cfg["num_experts_per_tok"], q_lora=cfg.get("q_lora_rank") or 0,
                    kv_lora=cfg["kv_lora_rank"], nope=cfg["qk_nope_head_dim"], rope=cfg["qk_rope_head_dim"],
                    v=cfg["v_head_dim"], tied=cfg.get("tie_word_embeddings", False),
                    mtp_layers=cfg.get("num_nextn_predict_layers", 0))
    heads = cfg["num_attention_heads"]
    p = dict(kind="gqa", vocab=cfg["vocab_size"], dim=cfg["hidden_size"], inter=cfg["intermediate_size"],
             layers=cfg["num_hidden_layers"], heads=heads, kv_heads=cfg.get("num_key_value_heads", heads),
             head_dim=cfg.get("head_dim") or cfg["hidden_size"] // heads,
             tied=cfg.get("tie_word_embeddings", False), bias="Qwen2" in arch)
    if cfg.get("num_local_experts"):
        p.update(kind="gqa-moe", routed=cfg["num_local_experts"], active=cfg["num_experts_per_tok"])
    return p


def params(p):
    """Return (total, active) parameter counts, ignoring norms (tiny)."""
    emb = p["vocab"] * p["dim"] * (1 if p["tied"] else 2)
    d, L = p["dim"], p["layers"]
    if p["kind"].startswith("mla"):
        qk = p["nope"] + p["rope"]
        q = d * p["q_lora"] + p["q_lora"] * p["heads"] * qk if p["q_lora"] else d * p["heads"] * qk
        kv = d * (p["kv_lora"] + p["rope"]) + p["kv_lora"] * p["heads"] * (p["nope"] + p["v"])
        o = p["heads"] * p["v"] * d
        attn = q + kv + o
    else:
        hd = p["head_dim"]
        attn = d * p["heads"] * hd + 2 * d * p["kv_heads"] * hd + p["heads"] * hd * d
        if p.get("bias"):
            attn += (p["heads"] + 2 * p["kv_heads"]) * hd
    if p["kind"] == "mla-moe":
        dense_mlp = 3 * d * p["inter"]
        expert = 3 * d * p["moe_inter"]
        moe_total = expert * (p["routed"] + p["shared"]) + d * p["routed"]
        moe_active = expert * (p["active"] + p["shared"]) + d * p["routed"]
        nd = p["dense_layers"]
        total = emb + L * attn + nd * dense_mlp + (L - nd) * moe_total
        active = emb + L * attn + nd * dense_mlp + (L - nd) * moe_active
    elif p["kind"] == "gqa-moe":
        expert = 3 * d * p["inter"]
        total = emb + L * (attn + expert * p["routed"] + d * p["routed"])
        active = emb + L * (attn + expert * p["active"] + d * p["routed"])
    else:
        mlp = 3 * d * p["inter"]
        total = active = emb + L * (attn + mlp)
    return total, active


def kv_bytes_per_token(p, kv_dtype_bytes=2.0):
    if p["kind"].startswith("mla"):
        # MLA caches one compressed latent (kv_lora_rank) + the shared RoPE key per layer
        return p["layers"] * (p["kv_lora"] + p["rope"]) * kv_dtype_bytes
    return 2 * p["layers"] * p["kv_heads"] * p["head_dim"] * kv_dtype_bytes


def mha_equivalent_kv(p, kv_dtype_bytes=2.0):
    """KV/token if the same model used plain multi-head attention (for MLA comparisons)."""
    if p["kind"].startswith("mla"):
        return 2 * p["layers"] * p["heads"] * (p["nope"] + p["rope"]) * kv_dtype_bytes
    return 2 * p["layers"] * p["heads"] * p["head_dim"] * kv_dtype_bytes


def report(name, p, dtype, util, sparks, kv_dtype, overhead_gib, ctx):
    total, active = params(p)
    wb = BYTES[dtype]
    w_gib = total * wb / GIB
    kvb = kv_bytes_per_token(p, BYTES[kv_dtype])
    budget = util * SPARK_VISIBLE_GIB * sparks
    kv_gib = budget - w_gib / 1.0 - overhead_gib * sparks
    tokens = int(kv_gib * GIB / kvb) if kv_gib > 0 else 0
    print(f"model            : {name}  ({p.get('hf', 'custom config')})")
    print(f"architecture     : {p['kind']}, {p['layers']} layers, dim {p['dim']}")
    print(f"parameters       : {total/1e9:8.2f} B total, {active/1e9:8.2f} B active per token")
    if p.get("mtp_layers"):
        print(f"                   (+ {p['mtp_layers']} MTP module(s) not counted — speculative-decoding head)")
    print(f"weights ({dtype:>7}) : {w_gib:8.1f} GiB")
    print(f"KV/token ({kv_dtype})  : {kvb/1024:8.1f} KiB   (MHA-equivalent {mha_equivalent_kv(p, BYTES[kv_dtype])/1024:.1f} KiB)")
    print(f"budget           : {util:.2f} × {SPARK_VISIBLE_GIB} GiB × {sparks} Spark(s) = {budget:.1f} GiB "
          f"(− {overhead_gib*sparks:.1f} GiB activations/graphs)")
    if kv_gib <= 0:
        print(f"verdict          : ✗ does not fit — weights alone need {w_gib:.1f} GiB > {budget:.1f} GiB")
        return 1
    print(f"KV cache         : {kv_gib:8.1f} GiB → {tokens:,} tokens "
          f"= {tokens // ctx} concurrent sequences at {ctx:,} context")
    print(f"verdict          : ✓ fits (set --gpu-memory-utilization {util:.2f}"
          f"{', tensor/pipeline parallel across 2 Sparks' if sparks > 1 else ''})")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", nargs="?")
    ap.add_argument("--config", help="path to a HF config.json instead of a preset")
    ap.add_argument("--dtype", default="bf16", choices=sorted(BYTES))
    ap.add_argument("--kv-dtype", default="bf16", choices=["bf16", "fp8"])
    ap.add_argument("--util", type=float, default=0.65, help="vLLM --gpu-memory-utilization")
    ap.add_argument("--sparks", type=int, default=1)
    ap.add_argument("--overhead-gib", type=float, default=3.0, help="activations + CUDA graphs per Spark")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--compare", nargs="+", help="print a KV/token + params table for these presets")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.list:
        for k in PRESETS:
            p = resolve(k)
            t, act = params(p)
            print(f"{k:32} {p['kind']:8} {t/1e9:7.1f}B total {act/1e9:7.1f}B active  {p.get('hf', '')}")
        return 0
    if a.compare:
        print(f"{'model':32} {'kind':8} {'total B':>8} {'active B':>9} {'KV/token':>10} {'MHA-equiv':>10}")
        for k in a.compare:
            p = resolve(k)
            t, act = params(p)
            print(f"{k:32} {p['kind']:8} {t/1e9:8.1f} {act/1e9:9.1f} {kv_bytes_per_token(p)/1024:8.1f}KiB "
                  f"{mha_equivalent_kv(p)/1024:8.1f}KiB")
        return 0
    if a.config:
        p, name = from_hf_config(json.load(open(a.config))), a.config
    elif a.model in PRESETS:
        p, name = resolve(a.model), a.model
    else:
        ap.error(f"unknown model {a.model!r}; use --list or --config")
    if a.json:
        t, act = params(p)
        print(json.dumps({"total": t, "active": act, "kv_bytes_per_token": kv_bytes_per_token(p, BYTES[a.kv_dtype]),
                          "weights_gib": t * BYTES[a.dtype] / GIB}))
        return 0
    return report(name, p, a.dtype, a.util, a.sparks, a.kv_dtype, a.overhead_gib, a.ctx)


if __name__ == "__main__":
    sys.exit(main())
