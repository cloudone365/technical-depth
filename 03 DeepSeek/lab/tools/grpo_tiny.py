#!/usr/bin/env python3
"""GRPO in miniature: teach a 0.5B model to reason in a format (Volumes 05 and 25).

Group Relative Policy Optimization (DeepSeekMath / R1): for each prompt, sample
G completions, score them with *rule-based* rewards, and use each completion's
reward relative to its group's mean/std as the advantage — no value network.

Task: arithmetic word problems generated on the fly (verifiable answers).
Rewards:
  format   1.0 if the output is  <think>…</think><answer>…</answer>
  correct  2.0 if the number inside <answer> equals the ground truth

  python3 grpo_tiny.py --dry-run                      # dataset + reward self-test, no GPU, no TRL
  python3 grpo_tiny.py --steps 200                    # on the Spark (NGC PyTorch + pip install trl …)
Watch the logged reward/format and reward/correct climb; completions get <think> tags.
"""
import argparse
import random
import re

SYSTEM = ("Solve the problem. First think step by step inside <think></think>, "
          "then give only the final number inside <answer></answer>.")
FMT = re.compile(r"^\s*<think>.+?</think>\s*<answer>\s*(-?\d+)\s*</answer>\s*$", re.S)


def make_problem(rnd):
    a, c = rnd.randint(2, 40), rnd.randint(2, 9)
    b = rnd.randint(2, min(20, a * c - 1))                         # keep answers positive
    kind = rnd.choice(["shop", "gpu", "train"])
    if kind == "shop":
        q = f"A lab buys {a} cables at {c} dollars each and gets a {b} dollar discount. How many dollars does it pay?"
        ans = a * c - b
    elif kind == "gpu":
        q = f"A cluster has {a} nodes with {c} GPUs each. {b} GPUs are drained for maintenance. How many GPUs are available?"
        ans = a * c - b
    else:
        q = f"A training run saves a checkpoint every {c} steps. How many checkpoints exist after {a * c + b} steps?"
        ans = (a * c + b) // c
    return {"prompt": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}], "answer": str(ans)}


def dataset(n, seed=0):
    rnd = random.Random(seed)
    return [make_problem(rnd) for _ in range(n)]


def _text(c):
    return c[0]["content"] if isinstance(c, list) else c


def reward_format(completions, **_):
    return [1.0 if FMT.match(_text(c)) else 0.0 for c in completions]


def reward_correct(completions, answer, **_):
    out = []
    for c, a in zip(completions, answer):
        m = FMT.match(_text(c))
        out.append(2.0 if m and m.group(1) == a else 0.0)
    return out


def self_test():
    ds = dataset(4)
    good = [[{"role": "assistant", "content": f"<think>compute</think><answer>{d['answer']}</answer>"}] for d in ds]
    bad = [[{"role": "assistant", "content": "The answer is 5"}] for _ in ds]
    assert reward_format(good) == [1.0] * 4 and reward_format(bad) == [0.0] * 4
    assert reward_correct(good, answer=[d["answer"] for d in ds]) == [2.0] * 4
    print("example prompt :", ds[0]["prompt"][1]["content"])
    print("ground truth   :", ds[0]["answer"])
    print("✓ rewards behave: format and correctness are verifiable without a model")


def train(a):
    from datasets import Dataset
    from trl import GRPOConfig, GRPOTrainer
    cfg = GRPOConfig(
        output_dir=a.out, max_steps=a.steps, learning_rate=a.lr, logging_steps=5, save_steps=a.steps,
        per_device_train_batch_size=a.group, num_generations=a.group,  # one prompt × G samples per step
        gradient_accumulation_steps=4, max_prompt_length=160, max_completion_length=a.max_len,
        temperature=0.9, beta=0.0, bf16=True, report_to="none", log_completions=True,
        use_vllm=a.vllm, vllm_mode="colocate", vllm_gpu_memory_utilization=0.2,
    )
    trainer = GRPOTrainer(model=a.model, reward_funcs=[reward_format, reward_correct], args=cfg,
                          train_dataset=Dataset.from_list(dataset(a.prompts)))
    trainer.train()
    trainer.save_model(a.out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--steps", type=int, default=200)
    ap.add_argument("--group", type=int, default=8, help="G: completions per prompt")
    ap.add_argument("--prompts", type=int, default=2000)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-6)
    ap.add_argument("--vllm", action="store_true", help="generate with vLLM colocated (faster rollouts)")
    ap.add_argument("--out", default="/ckpt/grpo-tiny")
    a = ap.parse_args()
    self_test() if a.dry_run else train(a)
