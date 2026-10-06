#!/usr/bin/env python3
"""LoRA / QLoRA / full fine-tune memory sizing for DGX Spark (Volumes 23, 24, 26).

Uses the architecture presets from model_math.py.
  trainable LoRA params = Σ over targeted matrices of r·(d_in + d_out)
  memory ≈ frozen weights (bf16 or 4-bit) + trainable params × (weights + grads + Adam 2×fp32 [+ master fp32])
           + activations (≈ tokens × layers × hidden × bytes × factor; gradient checkpointing lowers the factor)

  python3 lora_calc.py deepseek-r1-distill-qwen-7b --method lora --rank 16
  python3 lora_calc.py deepseek-r1-distill-qwen-32b --method qlora --rank 64 --seq 4096 --batch 1
  python3 lora_calc.py qwen2.5-7b --method full
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from model_math import GIB, SPARK_VISIBLE_GIB, params, resolve  # noqa: E402


def linear_shapes(p):
    """(name, d_in, d_out) of the per-layer linear maps LoRA usually targets (dense GQA models)."""
    d, hd = p["dim"], p["head_dim"]
    return [("q_proj", d, p["heads"] * hd), ("k_proj", d, p["kv_heads"] * hd), ("v_proj", d, p["kv_heads"] * hd),
            ("o_proj", p["heads"] * hd, d), ("gate_proj", d, p["inter"]), ("up_proj", d, p["inter"]),
            ("down_proj", p["inter"], d)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("--method", choices=["lora", "qlora", "full"], default="lora")
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--targets", default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj")
    ap.add_argument("--seq", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--grad-ckpt", action="store_true", default=True)
    ap.add_argument("--no-grad-ckpt", dest="grad_ckpt", action="store_false")
    a = ap.parse_args()
    p = resolve(a.model)
    if p["kind"] != "gqa":
        sys.exit("lora_calc handles dense GQA models (Qwen/Llama/Mistral/R1-distills); use model_math for MoE sizing")
    total, _ = params(p)
    tgt = set(a.targets.split(","))
    lora = sum(a.rank * (i + o) for n, i, o in linear_shapes(p) if n in tgt) * p["layers"]
    if a.method == "full":
        frozen_b, trainable = 0, total
        per_trainable = 2 + 2 + 8 + 4          # bf16 weight, bf16 grad, Adam m+v fp32, fp32 master
    else:
        frozen_b = total * (0.55 if a.method == "qlora" else 2.0)   # nf4 incl. quant constants ≈ 0.55 B/param
        trainable = lora
        per_trainable = 2 + 2 + 8 + 4
    act_factor = 2 if a.grad_ckpt else 16
    acts = a.batch * a.seq * p["layers"] * p["dim"] * 2 * act_factor
    logits = a.batch * a.seq * p["vocab"] * 4                        # fp32 logits for the loss (often chunked)
    tot = frozen_b + trainable * per_trainable + acts + logits
    print(f"model {a.model}: {total/1e9:.2f} B params, method={a.method}, rank={a.rank if a.method != 'full' else '-'}")
    print(f"  trainable params : {trainable/1e6:10.1f} M  ({trainable/total*100:.3f} % of the model)")
    print(f"  frozen weights   : {frozen_b/GIB:10.1f} GiB")
    print(f"  train state      : {trainable*per_trainable/GIB:10.1f} GiB  (weights+grads+Adam+master)")
    print(f"  activations      : {acts/GIB:10.1f} GiB  (seq {a.seq} × batch {a.batch}, grad-ckpt {'on' if a.grad_ckpt else 'off'})")
    print(f"  logits           : {logits/GIB:10.1f} GiB")
    print(f"  total ≈ {tot/GIB:.1f} GiB of {SPARK_VISIBLE_GIB} GiB unified memory → "
          f"{'✓ fits one Spark' if tot/GIB < 0.85 * SPARK_VISIBLE_GIB else '✗ needs 2 Sparks (FSDP/ZeRO-3) or a lighter method'}")
    print(f"  LoRA adapter file: {trainable*2/2**20:.0f} MiB (bf16)" if a.method != "full" else "")


if __name__ == "__main__":
    main()
