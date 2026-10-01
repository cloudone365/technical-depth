#!/usr/bin/env python3
"""Why MoE needs grouped GEMMs (Volume 07).

An MoE layer multiplies a different, variable-size slice of tokens by each
expert's weights. Three ways to do it:
  loop     one GEMM per expert (many small launches; what a naive implementation does)
  padded   pad every expert's tokens to the max and run one batched GEMM (wastes FLOPs on padding)
  grouped  sort tokens by expert, one launch over contiguous groups (DeepGEMM's "contiguous layout";
           here via torch._grouped_mm when available, else reported as unavailable)
Also checks all three produce the same result.

  python3 grouped_gemm_bench.py                          # CPU, small sizes
  python3 grouped_gemm_bench.py --experts 64 --tokens 8192 --dim 2048 --ffn 1408   # V2-Lite-like, on the GB10
"""
import argparse
import time

import torch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experts", type=int, default=16)
    ap.add_argument("--tokens", type=int, default=2048)
    ap.add_argument("--topk", type=int, default=6)
    ap.add_argument("--dim", type=int, default=512)
    ap.add_argument("--ffn", type=int, default=256)
    ap.add_argument("--skew", type=float, default=1.2, help="zipf exponent of expert popularity")
    ap.add_argument("--iters", type=int, default=20)
    a = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    dt = torch.bfloat16 if dev == "cuda" else torch.float32
    torch.manual_seed(0)
    pop = torch.tensor([1 / (i + 1) ** a.skew for i in range(a.experts)])
    assign = torch.multinomial(pop / pop.sum(), a.tokens * a.topk, replacement=True)
    counts = torch.bincount(assign, minlength=a.experts)
    x = torch.randn(int(counts.sum()), a.dim, device=dev, dtype=dt)       # tokens already sorted by expert
    w = torch.randn(a.experts, a.dim, a.ffn, device=dev, dtype=dt) / a.dim ** 0.5
    offs = torch.cumsum(counts, 0).to(dev)
    starts = torch.cat([torch.zeros(1, dtype=torch.long, device=dev), offs[:-1]])

    def loop():
        return torch.cat([x[s:e] @ w[i] for i, (s, e) in enumerate(zip(starts.tolist(), offs.tolist()))])

    mx = int(counts.max())

    def padded():
        xp = torch.zeros(a.experts, mx, a.dim, device=dev, dtype=dt)
        for i, (s, e) in enumerate(zip(starts.tolist(), offs.tolist())):
            xp[i, : e - s] = x[s:e]
        yp = torch.bmm(xp, w)
        return torch.cat([yp[i, : int(counts[i])] for i in range(a.experts)])

    grouped = None
    if hasattr(torch, "_grouped_mm") and dev == "cuda":
        def grouped():
            return torch._grouped_mm(x, w, offs=offs.to(torch.int32))

    def timeit(fn):
        for _ in range(3):
            fn()
        if dev == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(a.iters):
            fn()
        if dev == "cuda":
            torch.cuda.synchronize()
        return (time.perf_counter() - t0) / a.iters * 1e3

    ref = loop()
    flops = 2 * x.shape[0] * a.dim * a.ffn
    print(f"device={dev} experts={a.experts} routed tokens={x.shape[0]} (top-{a.topk}) "
          f"busiest expert={mx} quietest={int(counts.min())} padding waste={(mx * a.experts) / x.shape[0] - 1:.0%}")
    for name, fn in (("loop", loop), ("padded", padded), ("grouped", grouped)):
        if fn is None:
            print(f"  {name:8}: unavailable in this PyTorch build (torch._grouped_mm needs CUDA + recent PyTorch)")
            continue
        try:
            out = fn()
            err = (out.float() - ref.float()).abs().max().item()
            ms = timeit(fn)
            print(f"  {name:8}: {ms:8.2f} ms  {flops / ms / 1e9:8.1f} TFLOPS (useful)   max|Δ| vs loop {err:.1e}")
        except RuntimeError as e:
            print(f"  {name:8}: failed on this device/build: {str(e).splitlines()[0][:120]}")


if __name__ == "__main__":
    main()
