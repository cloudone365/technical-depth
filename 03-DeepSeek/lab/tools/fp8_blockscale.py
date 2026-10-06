#!/usr/bin/env python3
"""FP8 with fine-grained (block) scaling vs per-tensor scaling (Volumes 04 and 07).

DeepSeek-V3 trains and stores weights in FP8 E4M3 with one scale per 128×128
weight block (activations: per 1×128 tile). This script shows why:

  1. quantisation error — per-tensor vs 128×128-block scaling on a weight with
     a few large outliers (typical of LLM weights/activations)
  2. GEMM throughput on the GB10 — BF16 vs FP8 (torch._scaled_mm, per-tensor scales)
     when CUDA + FP8 tensor cores are available

  python3 fp8_blockscale.py                   # error study (CPU is fine)
  python3 fp8_blockscale.py --gemm --n 8192   # + FP8 GEMM timing (Spark)
"""
import argparse
import time

import torch

E4M3_MAX = 448.0


def q_per_tensor(w):
    s = w.abs().max() / E4M3_MAX
    return (w / s).to(torch.float8_e4m3fn).to(torch.float32) * s


def q_block(w, b=128):
    M, N = w.shape
    wb = w.view(M // b, b, N // b, b)
    s = wb.abs().amax(dim=(1, 3), keepdim=True) / E4M3_MAX
    s = torch.where(s == 0, torch.ones_like(s), s)
    return ((wb / s).to(torch.float8_e4m3fn).to(torch.float32) * s).view(M, N)


def rel_err(a, b):
    return ((a - b).norm() / b.norm()).item()


def error_study(n):
    """Sweep the outlier magnitude: E4M3 has ~18 binades of range, so per-tensor
    scaling survives moderate outliers but collapses when max/typical grows."""
    torch.manual_seed(0)
    base = torch.randn(n, n) * 0.02
    idx = torch.randint(0, n * n, (n // 64,))
    bulk = torch.ones(n * n, dtype=torch.bool)
    bulk[idx] = False
    x = torch.randn(256, n)
    print(f"weight {n}x{n} ~ N(0, 0.02) with {n // 64} outliers; error measured on the non-outlier weights")
    print(f"{'outlier':>9} {'max/typical':>12} | {'per-tensor err':>14} {'flushed→0':>10} | {'128x128 err':>12} {'flushed→0':>10}")
    for mag in (1.0, 10.0, 100.0, 1000.0, 5000.0):
        w = base.clone()
        w.view(-1)[idx] = mag
        row = []
        for fn in (q_per_tensor, q_block):
            flat = fn(w).view(-1)
            row += [rel_err(flat[bulk], w.view(-1)[bulk]), (flat[bulk] == 0).float().mean().item() * 100]
        print(f"{mag:9.0f} {mag / 0.02:12.0f} | {row[0]:14.4f} {row[1]:9.1f}% | {row[2]:12.4f} {row[3]:9.1f}%")
    print("-> one global scale lets the largest value set the step size: small weights lose precision,")
    print("   then underflow to zero. Block scales confine each outlier to its own 128x128 tile.")


def gemm_bench(n, iters=50):
    if not torch.cuda.is_available():
        print("CUDA not available: skipping GEMM timing")
        return
    dev = "cuda"
    a = torch.randn(n, n, device=dev, dtype=torch.bfloat16)
    b = torch.randn(n, n, device=dev, dtype=torch.bfloat16)

    def timeit(fn):
        for _ in range(5):
            fn()
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(iters):
            fn()
        torch.cuda.synchronize()
        return (time.perf_counter() - t0) / iters

    t_bf16 = timeit(lambda: a @ b)
    sa = (a.abs().max() / E4M3_MAX).float()
    sb = (b.abs().max() / E4M3_MAX).float()
    a8 = (a / sa).to(torch.float8_e4m3fn)
    b8 = (b / sb).to(torch.float8_e4m3fn).t().contiguous().t()      # column-major second operand
    try:
        t_fp8 = timeit(lambda: torch._scaled_mm(a8, b8, scale_a=sa, scale_b=sb, out_dtype=torch.bfloat16))
    except (RuntimeError, AttributeError) as e:
        print(f"FP8 _scaled_mm unavailable on this build/GPU: {e}")
        t_fp8 = None
    flop = 2 * n ** 3
    print(f"{torch.cuda.get_device_name(0)}  cc={torch.cuda.get_device_capability()}  n={n}")
    print(f"  BF16 GEMM : {flop / t_bf16 / 1e12:7.1f} TFLOPS")
    if t_fp8:
        print(f"  FP8  GEMM : {flop / t_fp8 / 1e12:7.1f} TFLOPS  ({t_bf16 / t_fp8:.2f}× BF16)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1024)
    ap.add_argument("--gemm", action="store_true")
    a = ap.parse_args()
    error_study(min(a.n, 2048))
    if a.gemm:
        gemm_bench(a.n)
