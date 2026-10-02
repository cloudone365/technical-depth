#!/usr/bin/env python3
"""The Vol 23 LoRA SFT, re-done with Unsloth (Volume 24) — same data, same rank, same steps,
so wall time and peak memory can be compared with sft_lora.py (plain TRL + PEFT).

Unsloth patches the model with fused Triton kernels (RoPE, RMSNorm, cross-entropy, LoRA
matmuls) and its own gradient checkpointing; the trainer is still TRL's SFTTrainer.
  python3 unsloth_sft.py --model deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --steps 300 --rank 16
  python3 unsloth_sft.py --qlora …          # 4-bit base (bitsandbytes NF4)
GPU only. Prints steps/s and peak CUDA memory at the end.
"""
import argparse
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sft_lora import dataset  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B")
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--qlora", action="store_true")
    ap.add_argument("--out", default="/ckpt/unsloth-lora")
    a = ap.parse_args()

    from unsloth import FastLanguageModel  # import before transformers/trl so its patches apply
    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    model, tok = FastLanguageModel.from_pretrained(model_name=a.model, max_seq_length=512,
                                                   load_in_4bit=a.qlora, dtype=None)
    model = FastLanguageModel.get_peft_model(
        model, r=a.rank, lora_alpha=2 * a.rank, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=1)
    ds = Dataset.from_list(dataset(a.n)).map(
        lambda ex: {"text": tok.apply_chat_template(ex["messages"], tokenize=False)}, remove_columns=["messages"])
    cfg = SFTConfig(output_dir=a.out, max_steps=a.steps, per_device_train_batch_size=8, gradient_accumulation_steps=2,
                    learning_rate=2e-4, lr_scheduler_type="cosine", warmup_ratio=0.05, logging_steps=10,
                    save_steps=a.steps, bf16=True, max_length=512, dataset_text_field="text", report_to="none")
    tr = SFTTrainer(model=model, processing_class=tok, train_dataset=ds, args=cfg)
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    tr.train()
    dt = time.time() - t0
    model.save_pretrained(a.out)
    tok.save_pretrained(a.out)
    stats = {"engine": "unsloth", "qlora": a.qlora, "steps": a.steps, "seconds": round(dt, 1),
             "steps_per_s": round(a.steps / dt, 3), "peak_cuda_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2)}
    print("STATS " + json.dumps(stats))


if __name__ == "__main__":
    main()
