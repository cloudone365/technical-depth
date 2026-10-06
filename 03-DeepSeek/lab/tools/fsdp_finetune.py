#!/usr/bin/env python3
"""Full-parameter fine-tuning with PyTorch FSDP2 (Volume 26).

Shards parameters, gradients and optimizer state across ranks with
torch.distributed.fsdp.fully_shard, so a model whose Adam state does not fit one
Spark fits two. Uses the chain-of-thought data from sft_lora.py.

  torchrun --nproc-per-node 1 fsdp_finetune.py --model Qwen/Qwen2.5-0.5B-Instruct --steps 50   # 1 Spark
  torchrun --nnodes 2 --node-rank $R --master-addr 192.168.100.11 --nproc-per-node 1 \
      fsdp_finetune.py --model deepseek-ai/DeepSeek-R1-Distill-Qwen-7B --steps 100                # 2 Sparks
Prints per-rank memory so you can see sharding at work.
"""
import argparse
import os
import sys
import time

import torch
import torch.distributed as dist
from torch.distributed.fsdp import MixedPrecisionPolicy, fully_shard

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sft_lora import dataset  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-5)
    a = ap.parse_args()
    from transformers import AutoModelForCausalLM, AutoTokenizer
    dist.init_process_group("nccl")
    rank, world = dist.get_rank(), dist.get_world_size()
    torch.cuda.set_device(0)
    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=torch.float32)
    model.gradient_checkpointing_enable()
    model.to("cuda")                                     # simple path; very large models: init on 'meta' instead
    mp = MixedPrecisionPolicy(param_dtype=torch.bfloat16, reduce_dtype=torch.float32)
    for layer in model.model.layers:                     # shard block by block, then the root
        fully_shard(layer, mp_policy=mp)
    fully_shard(model, mp_policy=mp)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr)
    data = [tok.apply_chat_template(ex["messages"], tokenize=False) for ex in dataset(a.steps * a.batch * world)]
    t0 = time.time()
    for step in range(a.steps):
        chunk = data[(step * world + rank) * a.batch:(step * world + rank + 1) * a.batch]
        enc = tok(chunk, return_tensors="pt", padding=True, truncation=True, max_length=256).to("cuda")
        loss = model(**enc, labels=enc["input_ids"]).loss
        loss.backward()
        opt.step()
        opt.zero_grad(set_to_none=True)
        if step % 10 == 0 or step == a.steps - 1:
            mem = torch.cuda.max_memory_allocated() / 2**30
            print(f"rank {rank}/{world} step {step} loss {loss.item():.3f} peak mem {mem:.1f} GiB", flush=True)
    if rank == 0:
        print(f"done in {time.time() - t0:.0f}s")
    dist.destroy_process_group()


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()
