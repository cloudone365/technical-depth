#!/usr/bin/env python3
"""LoRA SFT: teach the <think>…</think><answer>…</answer> format (Volumes 23, 24).

Builds a synthetic chain-of-thought dataset from the same verifiable problem
generator as grpo_tiny.py (explicit, correct reasoning steps), then fine-tunes
a small model with TRL SFTTrainer + PEFT LoRA. Typical pipeline (R1 recipe in
miniature): SFT for format  →  GRPO (grpo_tiny.py) for correctness.

  python3 sft_lora.py --dry-run                                  # show 2 training examples
  python3 sft_lora.py --model deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --steps 300 --rank 16
  python3 sft_lora.py --merge                                    # also write merged weights for vLLM
  python3 sft_lora.py --export /ckpt/data/gpu_math.json          # write the dataset for Unsloth / LLaMA-Factory (Vol 24)
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from grpo_tiny import SYSTEM  # noqa: E402


def cot_example(rnd):
    a, c = rnd.randint(2, 40), rnd.randint(2, 9)
    b = rnd.randint(2, min(20, a * c - 1))                         # keep answers positive
    q = f"A cluster has {a} nodes with {c} GPUs each. {b} GPUs are drained for maintenance. How many GPUs are available?"
    think = f"Total GPUs = {a} × {c} = {a*c}. Drained = {b}. Available = {a*c} − {b} = {a*c-b}."
    return {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q},
                         {"role": "assistant", "content": f"<think>{think}</think><answer>{a*c-b}</answer>"}]}


def dataset(n, seed=1):
    rnd = random.Random(seed)
    return [cot_example(rnd) for _ in range(n)]


def train(a):
    from datasets import Dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer
    cfg = SFTConfig(output_dir=a.out, max_steps=a.steps, per_device_train_batch_size=8, gradient_accumulation_steps=2,
                    learning_rate=2e-4, lr_scheduler_type="cosine", warmup_ratio=0.05, logging_steps=10,
                    save_steps=a.steps, bf16=True, gradient_checkpointing=True, max_length=512,
                    assistant_only_loss=False, report_to="none")
    peft = LoraConfig(r=a.rank, lora_alpha=2 * a.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    tr = SFTTrainer(model=a.model, args=cfg, train_dataset=Dataset.from_list(dataset(a.n)), peft_config=peft)
    tr.model.print_trainable_parameters()
    import time
    import torch
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    tr.train()
    dt = time.time() - t0
    print("STATS " + json.dumps({"engine": "trl+peft", "steps": a.steps, "seconds": round(dt, 1),
                                 "steps_per_s": round(a.steps / dt, 3),
                                 "peak_cuda_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2)}))
    tr.save_model(a.out)                                            # adapter only (MiBs)
    if a.merge:
        merged = tr.model.merge_and_unload()
        merged.save_pretrained(a.out + "-merged")
        tr.processing_class.save_pretrained(a.out + "-merged")
        print(f"merged model → {a.out}-merged (serve it with vLLM, or keep the adapter and use --enable-lora)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B")
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--out", default="/ckpt/sft-lora")
    ap.add_argument("--export", metavar="FILE", help="write the dataset as ShareGPT/OpenAI-messages JSON and exit")
    a = ap.parse_args()
    if a.export:
        os.makedirs(os.path.dirname(os.path.abspath(a.export)), exist_ok=True)
        with open(a.export, "w") as f:
            json.dump(dataset(a.n), f, ensure_ascii=False)
        print(f"wrote {a.n} examples → {a.export}")
    elif a.dry_run:
        for ex in dataset(2):
            print(ex["messages"][1]["content"], "\n  →", ex["messages"][2]["content"])
    else:
        train(a)
