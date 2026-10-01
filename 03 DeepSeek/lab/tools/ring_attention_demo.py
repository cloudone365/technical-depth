#!/usr/bin/env python3
"""Context parallelism with ring attention, verified (Volume 08).

Splits one long sequence across W ranks. Each rank keeps its own query chunk
and passes K/V chunks around a ring; partial results are merged with the
online-softmax (log-sum-exp) trick, the same maths FlashAttention uses across
tiles. The result must equal full causal attention computed on one device.

  python3 ring_attention_demo.py --world 2 --seq 4096        # gloo on CPU: laptop, CI, one Spark
  torchrun --nnodes 2 … ring_attention_demo.py --backend nccl  (2 Sparks, see volume)
Memory per rank for K/V is seq/W instead of seq, which is the whole point.
"""
import argparse
import math
import os

import torch
import torch.distributed as dist
import torch.multiprocessing as mp


def full_attention(q, k, v):
    return torch.nn.functional.scaled_dot_product_attention(q, k, v, is_causal=True)


def ring_attention(q, k, v, rank, world):
    """q,k,v: (heads, chunk, dim) for this rank's chunk of the sequence."""
    H, C, D = q.shape
    scale = 1 / math.sqrt(D)
    dev = q.device
    m = torch.full((H, C, 1), float("-inf"), dtype=q.dtype, device=dev)   # running max
    l = torch.zeros((H, C, 1), dtype=q.dtype, device=dev)                 # running denominator
    o = torch.zeros_like(q)                                                # running numerator
    q_pos = torch.arange(rank * C, (rank + 1) * C, device=dev)
    kv = torch.stack([k, v])
    for step in range(world):
        src = (rank - step) % world                                  # whose K/V we hold now
        k_blk, v_blk = kv[0], kv[1]
        if src <= rank:                                              # causal: future chunks contribute nothing
            k_pos = torch.arange(src * C, (src + 1) * C, device=dev)
            s = (q @ k_blk.transpose(-1, -2)) * scale
            s = s.masked_fill(k_pos[None, None, :] > q_pos[None, :, None], float("-inf"))
            m_new = torch.maximum(m, s.amax(-1, keepdim=True))
            p = torch.exp(s - m_new)
            corr = torch.exp(m - m_new)
            l = l * corr + p.sum(-1, keepdim=True)
            o = o * corr + p @ v_blk
            m = m_new
        if step < world - 1:                                         # pass K/V to the next rank
            recv = torch.empty_like(kv)
            reqs = [dist.isend(kv.contiguous(), (rank + 1) % world), dist.irecv(recv, (rank - 1) % world)]
            for r in reqs:
                r.wait()
            kv = recv
    return o / l


def worker(rank, world, seq, heads, dim, port, backend="gloo"):
    if "RANK" not in os.environ:                                     # mp.spawn mode (one machine)
        os.environ.update(MASTER_ADDR="127.0.0.1", MASTER_PORT=str(port))
        dist.init_process_group(backend, rank=rank, world_size=world)
    else:                                                            # torchrun mode (e.g. two Sparks)
        dist.init_process_group(backend)
    dev = torch.device("cuda", 0) if backend == "nccl" else torch.device("cpu")
    dtype = torch.float32 if backend == "nccl" else torch.float64
    torch.manual_seed(0)                                             # same full tensors on every rank
    q, k, v = (torch.randn(heads, seq, dim, dtype=dtype).to(dev) for _ in range(3))
    C = seq // world
    sl = slice(rank * C, (rank + 1) * C)
    out = ring_attention(q[:, sl], k[:, sl], v[:, sl], rank, world)
    gathered = [torch.empty_like(out) for _ in range(world)]
    dist.all_gather(gathered, out)
    if rank == 0:
        ring = torch.cat(gathered, dim=1)
        ref = full_attention(q, k, v)
        err = (ring - ref).abs().max().item()
        kv_full = 2 * heads * seq * dim * 2 / 2**20
        print(f"world={world} seq={seq} heads={heads} dim={dim}")
        tol = 1e-8 if dtype == torch.float64 else 1e-4
        print(f"backend={backend} device={dev}")
        print(f"max |ring - full| = {err:.2e}  → {'✓ identical' if err < tol else '✗ MISMATCH'}")
        print(f"K/V held per rank (bf16): {kv_full / world:.1f} MiB instead of {kv_full:.1f} MiB")
        assert err < tol
    dist.destroy_process_group()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", type=int, default=2)
    ap.add_argument("--seq", type=int, default=2048)
    ap.add_argument("--heads", type=int, default=4)
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--port", type=int, default=29533)
    ap.add_argument("--backend", default="gloo", choices=["gloo", "nccl"])
    a = ap.parse_args()
    if "RANK" in os.environ:                                         # launched by torchrun
        worker(int(os.environ["RANK"]), int(os.environ["WORLD_SIZE"]), a.seq, a.heads, a.dim, a.port, a.backend)
    else:
        assert a.seq % a.world == 0
        mp.spawn(worker, args=(a.world, a.seq, a.heads, a.dim, a.port, a.backend), nprocs=a.world, join=True)
