#!/usr/bin/env python3
"""Eager vs compiled: what a compiler buys you on the GB10 (Chapter 24).

Runs one transformer-style MLP block (GEMM → GELU → GEMM + residual + RMSNorm)
three ways and prints ms/iter:
  eager            PyTorch launches each op as its own kernel
  compile-default  TorchInductor fuses elementwise ops into generated Triton kernels
  compile-max      + CUDA graphs and autotuned GEMM choices ("max-autotune")
Set TORCH_LOGS=output_code to see the generated Triton kernels in the log.
"""
import os
import time

import torch

torch.backends.cuda.matmul.allow_tf32 = True
B, T, D = int(os.environ.get("B", "8")), int(os.environ.get("T", "1024")), int(os.environ.get("D", "4096"))
dev, dt = "cuda", torch.bfloat16


class Block(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.up = torch.nn.Linear(D, 4 * D, bias=False)
        self.down = torch.nn.Linear(4 * D, D, bias=False)
        self.w = torch.nn.Parameter(torch.ones(D))

    def forward(self, x):
        h = self.down(torch.nn.functional.gelu(self.up(x), approximate="tanh")) + x
        return h * torch.rsqrt(h.pow(2).mean(-1, keepdim=True) + 1e-6) * self.w


def bench(fn, x, iters=50):
    for _ in range(5):
        fn(x)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn(x)
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1e3


if __name__ == "__main__":
    m = Block().to(dev, dt).eval()
    x = torch.randn(B, T, D, device=dev, dtype=dt)
    flops = 2 * 2 * B * T * D * 4 * D
    with torch.inference_mode():
        res = {"eager": bench(m, x)}
        t0 = time.perf_counter()
        c1 = torch.compile(m)
        c1(x)
        res["compile-default"] = bench(c1, x)
        res["compile-default (compile s)"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        c2 = torch.compile(m, mode="max-autotune")
        c2(x)
        res["compile-max"] = bench(c2, x)
        res["compile-max (compile s)"] = time.perf_counter() - t0
    print(f"device={torch.cuda.get_device_name(0)} cc={torch.cuda.get_device_capability()} torch={torch.__version__}")
    for k in ("eager", "compile-default", "compile-max"):
        print(f"{k:>16}: {res[k]:7.2f} ms/iter  {flops / res[k] / 1e9:7.1f} TFLOPS")
    print(f"compile time: default {res['compile-default (compile s)']:.0f}s, max-autotune {res['compile-max (compile s)']:.0f}s")
