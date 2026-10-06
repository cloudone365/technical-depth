#!/usr/bin/env python3
"""Expert-parallel load balancing, simulated (Volume 09).

Given per-expert token loads (measured or synthetic), compare:
  naive      experts 0..E-1 placed contiguously, E/G per GPU
  eplb-lite  replicate the hottest experts into R redundant slots, then pack
             replicas onto GPUs with longest-processing-time-first (LPT)
  upstream   DeepSeek's eplb.py (github.com/deepseek-ai/EPLB) if --upstream PATH is given

  python3 eplb_sim.py                                   # zipf loads, 256 experts, 32 GPUs, 32 spare slots
  python3 eplb_sim.py --experts 64 --gpus 8 --redundant 8 --loads loads.json   # loads from moe_router_probe.py
Metric: max/mean GPU load. 1.00 is perfect; the step time follows the max.
"""
import argparse
import heapq
import importlib.util
import json
import random


def zipf_loads(n, s=1.1, tokens=1_000_000, seed=0):
    rnd = random.Random(seed)
    w = [1 / (i + 1) ** s for i in range(n)]
    rnd.shuffle(w)
    tot = sum(w)
    return [int(tokens * x / tot) for x in w]


def naive(loads, gpus):
    per = len(loads) // gpus
    return [sum(loads[g * per:(g + 1) * per]) for g in range(gpus)]


def eplb_lite(loads, gpus, redundant):
    # 1) replication: give a replica to whichever expert currently has the highest load *per replica*
    reps = [1] * len(loads)
    heap = [(-l, e) for e, l in enumerate(loads)]
    heapq.heapify(heap)
    for _ in range(redundant):
        _, e = heapq.heappop(heap)
        reps[e] += 1
        heapq.heappush(heap, (-loads[e] / reps[e], e))
    items = sorted(((loads[e] / reps[e], e) for e in range(len(loads)) for _ in range(reps[e])), reverse=True)
    # 2) packing: LPT onto GPUs with equal slot counts
    slots = len(items) // gpus
    bins = [[0.0, g, 0] for g in range(gpus)]                          # load, gpu, used slots
    placement = {g: [] for g in range(gpus)}
    for load, e in items:
        b = min((b for b in bins if b[2] < slots), key=lambda b: b[0])
        b[0] += load
        b[2] += 1
        placement[b[1]].append(e)
    return [b[0] for b in sorted(bins, key=lambda b: b[1])], reps, placement


def upstream(path, loads, gpus, redundant, nodes):
    spec = importlib.util.spec_from_file_location("eplb", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import torch
    w = torch.tensor([loads], dtype=torch.float64)
    phy2log, _, logcnt = mod.rebalance_experts(w, len(loads) + redundant, max(1, nodes), nodes, gpus)
    per = (len(loads) + redundant) // gpus
    gpu_load = [0.0] * gpus
    for slot, e in enumerate(phy2log[0].tolist()):
        gpu_load[slot // per] += loads[e] / logcnt[0][e].item()
    return gpu_load


def ratio(x):
    return max(x) / (sum(x) / len(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experts", type=int, default=256)
    ap.add_argument("--gpus", type=int, default=32)
    ap.add_argument("--redundant", type=int, default=32)
    ap.add_argument("--nodes", type=int, default=4)
    ap.add_argument("--loads", help="JSON list of per-expert token counts")
    ap.add_argument("--upstream", help="path to DeepSeek's eplb.py")
    a = ap.parse_args()
    loads = json.load(open(a.loads)) if a.loads else zipf_loads(a.experts)
    assert len(loads) % a.gpus == 0 and (len(loads) + a.redundant) % a.gpus == 0
    n = naive(loads, a.gpus)
    lite, reps, _ = eplb_lite(loads, a.gpus, a.redundant)
    print(f"{len(loads)} experts on {a.gpus} GPUs, {a.redundant} redundant slots; hottest expert = "
          f"{max(loads) / (sum(loads) / len(loads)):.1f}× mean")
    print(f"  naive contiguous placement : max/mean GPU load = {ratio(n):.2f}")
    print(f"  eplb-lite (replicate + LPT): max/mean GPU load = {ratio(lite):.2f}   "
          f"(replicated experts: {sum(1 for r in reps if r > 1)})")
    if a.upstream:
        print(f"  DeepSeek eplb.py           : max/mean GPU load = {ratio(upstream(a.upstream, loads, a.gpus, a.redundant, a.nodes)):.2f}")
    print(f"  → step-time gain vs naive ≈ {ratio(n) / ratio(lite):.2f}×")


if __name__ == "__main__":
    main()
