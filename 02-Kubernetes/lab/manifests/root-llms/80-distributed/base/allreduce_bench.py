#!/usr/bin/env python3
"""torch.distributed all-reduce bus-bandwidth sweep (Chapter 18).

Same maths as nccl-tests: busbw = algbw * 2(n-1)/n.
Launched by torchrun (RANK/WORLD_SIZE/MASTER_ADDR from env).
BACKEND=nccl (default when every rank has its own GPU) or gloo (1 Spark,
2 ranks sharing one GB10 through time-slicing: NCCL refuses duplicate GPUs).
"""
import os
import time

import torch
import torch.distributed as dist

backend = os.environ.get("BACKEND", "nccl")
dist.init_process_group(backend=backend)
rank, world = dist.get_rank(), dist.get_world_size()
dev = torch.device("cuda", 0) if backend == "nccl" else torch.device("cpu")
if rank == 0:
    print(f"backend={backend} world={world} device={dev}", flush=True)
    print(f"{'size':>10} {'time_ms':>9} {'algbw_GB/s':>11} {'busbw_GB/s':>11}", flush=True)

for exp in range(20, 31, 2):                      # 1 MiB … 1 GiB (fp32 elements * 4 B)
    nbytes = 2 ** exp
    t = torch.ones(nbytes // 4, dtype=torch.float32, device=dev)
    for _ in range(3):
        dist.all_reduce(t)
    if dev.type == "cuda":
        torch.cuda.synchronize()
    iters = 10
    t0 = time.perf_counter()
    for _ in range(iters):
        dist.all_reduce(t)
    if dev.type == "cuda":
        torch.cuda.synchronize()
    dt = (time.perf_counter() - t0) / iters
    algbw = nbytes / dt / 1e9
    busbw = algbw * 2 * (world - 1) / world
    if rank == 0:
        print(f"{nbytes:>10} {dt*1e3:>9.2f} {algbw:>11.2f} {busbw:>11.2f}", flush=True)

# correctness: every element must equal world_size ** (warmup+iters) after the sweep? no —
# check a fresh reduction instead
c = torch.full((4,), float(rank + 1), device=dev)
dist.all_reduce(c)
expected = world * (world + 1) / 2
assert torch.allclose(c, torch.full_like(c, expected)), (c, expected)
if rank == 0:
    print(f"correctness OK: sum(1..{world}) = {expected}", flush=True)
dist.destroy_process_group()
