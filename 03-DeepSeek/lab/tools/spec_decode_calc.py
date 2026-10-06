#!/usr/bin/env python3
"""Speculative decoding / MTP speed-up estimator (Volume 03).

With a draft that proposes k tokens and a per-token acceptance rate α (tokens
accepted independently), the expected tokens produced per target forward pass is
    E = (1 − α^(k+1)) / (1 − α)
Speed-up ≈ E / (1 + k·c), where c = cost of one draft step relative to one
target step (≈0 for n-gram lookup, ~0.05–0.15 for a small draft model, and for
DeepSeek-V3 MTP a single extra module per token).

  python3 spec_decode_calc.py --alpha 0.8 --k 1 --draft-cost 0.05   # V3-style MTP (1 extra token)
  python3 spec_decode_calc.py --sweep                              # table over α and k
Measure α on your server: vllm:spec_decode_num_accepted_tokens / vllm:spec_decode_num_draft_tokens.
"""
import argparse


def expected_tokens(alpha, k):
    return (1 - alpha ** (k + 1)) / (1 - alpha) if alpha < 1 else k + 1


def speedup(alpha, k, c):
    return expected_tokens(alpha, k) / (1 + k * c)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=0.8)
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--draft-cost", type=float, default=0.05)
    ap.add_argument("--sweep", action="store_true")
    a = ap.parse_args()
    if a.sweep:
        ks = [1, 2, 3, 4, 6]
        print("alpha  " + "".join(f"  k={k}:tok/speed" for k in ks))
        for al in (0.5, 0.6, 0.7, 0.8, 0.9):
            print(f" {al:.1f}  " + "".join(f"  {expected_tokens(al, k):4.2f}/{speedup(al, k, a.draft_cost):4.2f}x " for k in ks))
    else:
        print(f"α={a.alpha} k={a.k} c={a.draft_cost}: {expected_tokens(a.alpha, a.k):.2f} tokens/step, "
              f"≈{speedup(a.alpha, a.k, a.draft_cost):.2f}× decode speed-up")
