#!/usr/bin/env python3
"""Watch a real DeepSeekMoE router make decisions (Volume 02, 09) — runs on the Spark.

Loads DeepSeek-V2-Lite-Chat (15.7 B total, ~2.4 B active, MLA + 64 routed + 2
shared experts, top-6) in BF16 (~31 GB of unified memory), hooks every MoE gate,
feeds prompts from different domains and reports:
  * per-domain expert-usage histograms and how much they overlap (specialisation)
  * load imbalance per layer (max/mean) → input for eplb_sim.py --loads
Works with the model's remote code or the native transformers implementation:
any module whose class name contains "Gate"/"Router" is hooked; if it returns
logits instead of indices, top-k is taken here.

  pip install "transformers==4.56.2" accelerate
  python3 moe_router_probe.py --model deepseek-ai/DeepSeek-V2-Lite-Chat --out loads.json
"""
import argparse
import collections
import json
import re

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

DOMAINS = {
    "code": ["def quicksort(xs):\n    if len(xs) <= 1:\n        return xs\n",
             "kubectl apply -f deployment.yaml && kubectl rollout status deploy/vllm",
             "import torch\nx = torch.randn(4096, 4096, device='cuda')\ny = x @ x.T"],
    "math": ["Let x satisfy 3x + 7 = 22. Then x equals", "The integral of x^2 from 0 to 3 is",
             "If a GPU does 1e15 FLOP/s, a 2e18 FLOP job takes this many seconds:"],
    "chat": ["Tell me a short story about a lighthouse keeper.", "What should I cook for dinner tonight?",
             "Write a polite email declining a meeting invitation."],
    "chinese": ["请介绍一下大型语言模型的基本原理。", "北京今天的天气怎么样？", "写一首关于春天的短诗。"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek-ai/DeepSeek-V2-Lite-Chat")
    ap.add_argument("--out", default="loads.json")
    a = ap.parse_args()
    tok = AutoTokenizer.from_pretrained(a.model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=torch.bfloat16, device_map="cuda",
                                                 trust_remote_code=True).eval()
    k = model.config.num_experts_per_tok
    n_exp = model.config.n_routed_experts
    counts = collections.defaultdict(lambda: collections.defaultdict(lambda: torch.zeros(n_exp, dtype=torch.long)))
    current = {"domain": None}

    def hook(name):
        layer = int(re.search(r"layers\.(\d+)", name).group(1))

        def fn(_mod, _inp, out):
            o = out[0] if isinstance(out, tuple) else out
            idx = o if not torch.is_floating_point(o) else o.topk(k, dim=-1).indices
            counts[current["domain"]][layer] += torch.bincount(idx.reshape(-1).cpu(), minlength=n_exp)
        return fn

    hooked = 0
    for name, mod in model.named_modules():
        cls = type(mod).__name__
        if ("Gate" in cls or "Router" in cls) and re.search(r"layers\.\d+", name):
            mod.register_forward_hook(hook(name))
            hooked += 1
    print(f"hooked {hooked} router modules; top-{k} of {n_exp} routed experts")
    with torch.inference_mode():
        for dom, prompts in DOMAINS.items():
            current["domain"] = dom
            for p in prompts:
                ids = tok(p, return_tensors="pt").to("cuda")
                model(**ids)
    layers = sorted(next(iter(counts.values())).keys())
    mid = layers[len(layers) // 2]
    print(f"\nlayer {mid}: top-5 experts per domain")
    tops = {}
    for dom in DOMAINS:
        c = counts[dom][mid]
        tops[dom] = set(c.topk(5).indices.tolist())
        print(f"  {dom:8} {sorted(tops[dom])}")
    doms = list(DOMAINS)
    print("\noverlap of top-5 sets (Jaccard):")
    for i in range(len(doms)):
        for j in range(i + 1, len(doms)):
            s1, s2 = tops[doms[i]], tops[doms[j]]
            print(f"  {doms[i]:8} vs {doms[j]:8}: {len(s1 & s2) / len(s1 | s2):.2f}")
    total = sum(counts[d][mid] for d in DOMAINS)
    print(f"\nlayer {mid} load max/mean = {total.max().item() / total.float().mean().item():.2f}")
    json.dump(total.tolist(), open(a.out, "w"))
    print("LOADS_JSON: " + json.dumps(total.tolist()))           # grab from `kubectl logs` after the pod exits
    print(f"saved per-expert loads of layer {mid} → {a.out} (feed to eplb_sim.py --loads)")


if __name__ == "__main__":
    main()
