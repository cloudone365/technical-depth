#!/usr/bin/env python3
"""Cluster MTBF, optimal checkpoint interval and goodput (Chapter 25).

  python3 mtbf_calc.py --gpus 16384 --gpu-mtbf-h 50000 --ckpt-s 60 --restart-s 600
Assumes independent failures (exponential): cluster MTBF = component MTBF / N.
Checkpoint interval uses the Young/Daly approximation  τ ≈ sqrt(2·C·M)
(C = blocking checkpoint cost, M = cluster MTBF). Expected lost work per
failure ≈ τ/2 + restart time.
"""
import argparse
import math


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpus", type=int, required=True)
    ap.add_argument("--gpu-mtbf-h", type=float, default=50000, help="per-GPU+host MTBF in hours (all causes)")
    ap.add_argument("--ckpt-s", type=float, default=60, help="blocking time per checkpoint (s)")
    ap.add_argument("--restart-s", type=float, default=600, help="detect + reschedule + reload (s)")
    a = ap.parse_args()
    m = a.gpu_mtbf_h * 3600 / a.gpus                       # cluster MTBF, seconds
    tau = math.sqrt(2 * a.ckpt_s * m)
    overhead = a.ckpt_s / tau + (tau / 2 + a.restart_s) / m  # fraction of time lost
    print(f"cluster MTBF          : {m/3600:10.2f} h  ({86400/m:.1f} interruptions/day)")
    print(f"optimal ckpt interval : {tau/60:10.1f} min (Young/Daly)")
    print(f"expected goodput      : {max(0.0, 1 - overhead)*100:10.1f} %")
    for c in (a.ckpt_s, a.ckpt_s / 10):
        t = math.sqrt(2 * c * m)
        g = 1 - (c / t + (t / 2 + a.restart_s) / m)
        print(f"  with ckpt cost {c:6.1f}s → interval {t/60:6.1f} min, goodput {max(0, g)*100:5.1f} %")


if __name__ == "__main__":
    main()
