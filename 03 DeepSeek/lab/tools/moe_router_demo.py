#!/usr/bin/env python3
"""DeepSeekMoE routing, simulated (Volumes 02 and 09).

Implements the DeepSeek-V3 router on synthetic, deliberately skewed token
streams and shows why auxiliary-loss-free balancing matters:

  score_i  = sigmoid(x · e_i)                        (affinity per routed expert)
  select   = top-k over (score_i + b_i), limited to the best `n_limited` of `n_groups`
  weight   = score_i / Σ score_selected · route_scale  (bias NOT used for weights)
  after each step: b_i += γ · sign(mean_load − load_i)   ← aux-loss-free balancing

Prints per-step max/mean expert load with and without the bias update, the
per-"GPU" load when experts are sharded across devices (feeds Vol 09 / EPLB),
and the shared-expert share of compute.

  python3 moe_router_demo.py                     # V3-like: 256 experts, top-8, 8 groups/4
  python3 moe_router_demo.py --experts 64 --topk 6 --groups 1 --limited 1   # V2-Lite-like
"""
import argparse

import torch


def route(x, emb, bias, k, n_groups, n_limited, route_scale):
    scores = torch.sigmoid(x @ emb.T)                                  # (T, E)
    biased = scores + bias
    T, E = scores.shape
    if n_groups > 1:
        g = biased.view(T, n_groups, E // n_groups)
        group_score = g.topk(2, dim=-1).values.sum(-1)                 # (T, G)
        keep = group_score.topk(n_limited, dim=-1).indices
        gmask = torch.zeros(T, n_groups, dtype=torch.bool).scatter_(1, keep, True)
        biased = biased.masked_fill(~gmask.repeat_interleave(E // n_groups, 1), float("-inf"))
    idx = biased.topk(k, dim=-1).indices                               # (T, k)
    w = scores.gather(1, idx)
    w = w / w.sum(-1, keepdim=True) * route_scale
    return idx, w


def run(a, balance):
    torch.manual_seed(0)
    E, d = a.experts, a.dim
    emb = torch.randn(E, d) / d ** 0.5
    centers = torch.randn(4, d)                                         # 4 "topics" → skewed routing
    bias = torch.zeros(E)
    history = []
    for step in range(a.steps):
        topic = torch.multinomial(torch.tensor([0.55, 0.25, 0.15, 0.05]), a.tokens, replacement=True)
        x = centers[topic] + 0.8 * torch.randn(a.tokens, d)
        idx, _ = route(x, emb, bias, a.topk, a.groups, a.limited, a.route_scale)
        load = torch.bincount(idx.flatten(), minlength=E).float()
        if balance:
            bias += a.gamma * torch.sign(load.mean() - load)
        history.append(load)
    return history, bias


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experts", type=int, default=256)
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--groups", type=int, default=8)
    ap.add_argument("--limited", type=int, default=4)
    ap.add_argument("--route-scale", type=float, default=2.5)
    ap.add_argument("--dim", type=int, default=256)
    ap.add_argument("--tokens", type=int, default=4096)
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--gamma", type=float, default=0.002)
    ap.add_argument("--gpus", type=int, default=8, help="devices the experts are sharded over (EP)")
    a = ap.parse_args()
    for balance in (False, True):
        hist, bias = run(a, balance)
        print(f"\n== {'WITH' if balance else 'WITHOUT'} aux-loss-free bias balancing "
              f"({a.experts} experts, top-{a.topk}, groups {a.limited}/{a.groups})")
        print(" step   max/mean expert load   idle experts   max/mean GPU load (EP={})".format(a.gpus))
        for s in sorted({i for i in (0, 9, 19, 39, a.steps - 1) if i < a.steps}):
            load = hist[s]
            per_gpu = load.view(a.gpus, -1).sum(1)
            print(f" {s+1:4d}   {load.max()/load.mean():20.2f}   {int((load == 0).sum()):12d}   "
                  f"{per_gpu.max()/per_gpu.mean():20.2f}")
        if balance:
            print(f" bias range after {a.steps} steps: [{bias.min():.3f}, {bias.max():.3f}]")
    print("\nThe slowest GPU sets the step time: max/mean GPU load is the MoE efficiency tax.")
    print("Bias balancing fixes routing over time; EPLB (Vol 09) fixes placement by replicating hot experts.")


if __name__ == "__main__":
    main()
