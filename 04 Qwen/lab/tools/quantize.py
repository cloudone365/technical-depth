#!/usr/bin/env python3
"""Quantise a Qwen checkpoint with llm-compressor for vLLM (Volume 13).

  fp8     FP8_DYNAMIC: per-channel FP8 weights, per-token dynamic FP8 activations.
          No calibration data needed. ~½ the bytes of BF16; vLLM runs it natively.
  w4a16   GPTQ 4-bit weights (group size 128), 16-bit activations. Needs calibration
          samples (here: chat-formatted text built from this repo's own data, so no
          download). ~¼ the bytes of BF16; vLLM uses Marlin kernels.
Output: a compressed-tensors checkpoint vLLM loads directly (/models/quantized/<name>).

  python3 quantize.py --model Qwen/Qwen2.5-7B-Instruct --scheme fp8   --out /models/quantized/qwen2.5-7b-fp8
  python3 quantize.py --model Qwen/Qwen2.5-7B-Instruct --scheme w4a16 --out /models/quantized/qwen2.5-7b-w4a16
GPU only (k8s/jobs/quantize.yaml).
"""
import argparse
import json
import pathlib
import time

CALIB = [pathlib.Path(__file__).resolve().parent.parent / "data" / f for f in ("fim_tasks.jsonl",)]


def calibration_texts(tok, n):
    texts = []
    for p in CALIB:
        if p.exists():
            for line in open(p):
                t = json.loads(line)
                texts.append(t["prefix"] + t["middle"] + t["suffix"])
    base = ["Explain how a GPU time-slice is shared between two inference servers.",
            "Write a Python function that parses Kubernetes memory quantities like 16Gi.",
            "A training job saves a 30 GB checkpoint every 500 steps. How much disk is used after 4,000 steps?",
            "Summarise the trade-offs between FP8 and 4-bit weight quantization for LLM serving."]
    for i in range(n):
        q = base[i % len(base)] + f" (variant {i})"
        texts.append(tok.apply_chat_template([{"role": "user", "content": q}], tokenize=False, add_generation_prompt=True))
    return texts[:n]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--scheme", choices=["fp8", "w4a16"], default="fp8")
    ap.add_argument("--samples", type=int, default=256)
    ap.add_argument("--seq", type=int, default=2048)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    import torch
    from datasets import Dataset
    from llmcompressor import oneshot
    from llmcompressor.modifiers.quantization import GPTQModifier, QuantizationModifier
    from transformers import AutoModelForCausalLM, AutoTokenizer

    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=torch.bfloat16, device_map="auto")
    if a.scheme == "fp8":
        oneshot(model=model, recipe=QuantizationModifier(targets="Linear", scheme="FP8_DYNAMIC", ignore=["lm_head"]))
    else:
        ds = Dataset.from_dict({"text": calibration_texts(tok, a.samples)})
        ds = ds.map(lambda b: tok(b["text"], truncation=True, max_length=a.seq, padding=False), remove_columns=["text"])
        oneshot(model=model, dataset=ds, max_seq_length=a.seq, num_calibration_samples=len(ds),
                recipe=GPTQModifier(targets="Linear", scheme="W4A16", ignore=["lm_head"]))
    model.save_pretrained(a.out, save_compressed=True)
    tok.save_pretrained(a.out)
    size = sum(p.stat().st_size for p in pathlib.Path(a.out).glob("*.safetensors")) / 2**30
    print(f"STATS {json.dumps({'scheme': a.scheme, 'out': a.out, 'gib_on_disk': round(size, 2), 'minutes': round((time.time() - t0) / 60, 1)})}")


if __name__ == "__main__":
    main()
