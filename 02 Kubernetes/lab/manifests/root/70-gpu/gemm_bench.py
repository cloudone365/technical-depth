#!/usr/bin/env python3
"""BF16 GEMM throughput + UMA facts (Steps 15, 16, 17; Step 27 ex. 19).

Prints one JSON line per run so results can be grepped / compared:
  {"pod": ..., "tflops": ..., "mem_total_gib": ..., "cc": "12.1"}
Env: N (matrix size, default 8192), ITERS (default 50), LOOP=1 to repeat forever.
"""
import json
import os
import socket
import time

import torch

N = int(os.environ.get("N", "8192"))
ITERS = int(os.environ.get("ITERS", "50"))


def run_once():
    a = torch.randn(N, N, device="cuda", dtype=torch.bfloat16)
    b = torch.randn(N, N, device="cuda", dtype=torch.bfloat16)
    for _ in range(3):
        a @ b
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(ITERS):
        a @ b
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    free, total = torch.cuda.mem_get_info()
    major, minor = torch.cuda.get_device_capability()
    print(json.dumps({
        "pod": socket.gethostname(),
        "device": torch.cuda.get_device_name(0),
        "cc": f"{major}.{minor}",
        "n": N,
        "tflops": round(2 * N**3 * ITERS / dt / 1e12, 1),
        "mem_total_gib": round(total / 2**30, 1),
        "mem_free_gib": round(free / 2**30, 1),
    }), flush=True)


if __name__ == "__main__":
    run_once()
    while os.environ.get("LOOP") == "1":
        run_once()
