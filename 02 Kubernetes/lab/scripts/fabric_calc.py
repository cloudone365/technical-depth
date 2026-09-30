#!/usr/bin/env python3
"""Rail-optimised fat-tree sizing calculator (Volume 18).

Given N nodes with G GPUs (one NIC per GPU = one rail per GPU index) and a
switch radix R, compute a non-blocking 2-tier (leaf/spine) or 3-tier design:
switch counts, cables, and per-GPU bisection bandwidth.

  python3 fabric_calc.py --nodes 32 --gpus 8 --radix 64 --link-gbps 400
  python3 fabric_calc.py --nodes 2 --gpus 1 --radix 2 --link-gbps 200   # two Sparks, back-to-back
"""
import argparse
import math


def plan(nodes, gpus, radix, link_gbps):
    endpoints = nodes * gpus                       # one NIC port per GPU
    down = radix // 2                              # non-blocking: half ports down, half up
    out = {"endpoints": endpoints, "rails": gpus}
    if nodes <= 1:
        out.update(tiers=0, note="single node: GPUs talk over NVLink / C2C, no fabric")
        return out
    if endpoints <= radix and gpus == 1:
        out.update(tiers=1 if endpoints > 2 else 0, leaves=1 if endpoints > 2 else 0, spines=0,
                   cables=endpoints if endpoints > 2 else 1,
                   note="back-to-back cable" if endpoints == 2 else "one switch")
    else:
        # rail-optimised: each rail r connects GPU r of every node to the same leaf group
        leaves_per_rail = math.ceil(nodes / down)
        leaves = leaves_per_rail * gpus
        if leaves == gpus:                         # each rail fits on one leaf: no spine needed for same-rail traffic
            spines = math.ceil(leaves * down / radix) if gpus > 1 else 0
            tiers = 2 if spines else 1
        else:
            spines = math.ceil(leaves * down / radix)
            tiers = 2
            if spines > radix:                     # leaf/spine exhausted → 3-tier (super-spine)
                tiers = 3
        out.update(tiers=tiers, leaves=leaves, spines=spines,
                   cables=endpoints + leaves * down, note="rail-optimised, non-blocking (1:1)")
    out["per_gpu_gbps"] = link_gbps
    out["bisection_tbps"] = round(endpoints * link_gbps / 2 / 1000, 1)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nodes", type=int, required=True)
    ap.add_argument("--gpus", type=int, default=8, help="GPUs (= rails) per node")
    ap.add_argument("--radix", type=int, default=64, help="switch ports (e.g. 64 for QM9700 NDR)")
    ap.add_argument("--link-gbps", type=int, default=400)
    a = ap.parse_args()
    for k, v in plan(a.nodes, a.gpus, a.radix, a.link_gbps).items():
        print(f"{k:>16}: {v}")
