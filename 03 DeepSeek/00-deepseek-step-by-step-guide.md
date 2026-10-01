# Step-by-Step: DeepSeek on the DGX Spark, from Architecture to a Served, Streamed Reasoning Model

> **Module 03 companion guide.** The shortest correct path through the DeepSeek lab, in the order that avoids rework. Each step lists the commands, what "done" looks like, and the volume that explains it. Prerequisite: the [02 Kubernetes step-by-step guide](../02%20Kubernetes/00-kubernetes-step-by-step-guide.md) through its Step 15 (vLLM serving on k3s).
>
> **Status:** this guide covers volumes 01–21. Steps for volumes 22–41 (autoscaling, fine-tuning, RL, apps, RAG, agents, automation, comparisons, telemetry and the workbook) are added as those volumes are rewritten.

```mermaid
flowchart LR
  subgraph W1["Week 1 · Architecture, proved in code"]
    A0["0 Lab setup"] --> A1["1 MLA"] --> A2["2 MoE routing"] --> A3["3 MTP · spec decode"] --> A4["4 FP8"] --> A5["5 R1 · GRPO"]
  end
  subgraph W2["Week 2 · Kernels and scale"]
    B1["6 FlashMLA · DeepGEMM"] --> B2["7 Long context"] --> B3["8 EPLB · 3FS"]
  end
  subgraph W3["Week 3 · Models on one GB10"]
    C1["9 Memory math"] --> C2["10 32B models"] --> C3["11 Coder · Math"] --> C4["12 V3/R1 671B (2×)"]
  end
  subgraph W4["Week 4 · Serving platform"]
    D1["13 vLLM catalog"] --> D2["14 SGLang · llama.cpp · TRT-LLM"] --> D3["15 Manifests · admission"] --> D4["16 Weight cache"] --> D5["17 Streaming gateway"]
  end
  W1 --> W2 --> W3 --> W4
  classDef ctrl fill:#1f6feb,stroke:#0b3d91,color:#fff
  classDef gpu fill:#76b900,stroke:#3d6000,color:#000
  classDef store fill:#bf8700,stroke:#7a5600,color:#fff
  classDef net fill:#8250df,stroke:#4c2889,color:#fff
  class A0,A1,A2,A3,A4,A5 ctrl
  class B1,B2,B3 store
  class C1,C2,C3,C4 gpu
  class D1,D2,D3,D4 gpu
  class D5 net
```

**One Spark or two?** Everything works on one. Steps marked **(2×)** have an optional second part that needs spark-02 and the QSFP cable.

---

## Step 0 · Lab setup (30 min) → [lab README](lab/README.md)

```bash
cd "technical-depth/03 DeepSeek/lab"
tests/run-local-checks.sh                 # CPU-only: schemas, tools, mocks — must end "ALL LOCAL CHECKS PASSED"
kubectl apply -k .                        # tool + eval-data ConfigMaps
scripts/verify.sh platform                # 02 platform present, model-cache PVC, ConfigMaps
```

**Done when:** `verify.sh platform` is all `PASS`.

## Step 1 · MLA (60 min) → [01](01-multi-head-latent-attention-mla.md)

```bash
python3 tools/mla_attention_demo.py                       # three forms agree
python3 tools/model_math.py --compare deepseek-v3 qwen2.5-32b
```

**Done when:** you can state the KV bytes per token for MLA vs MHA and why the absorbed form decodes faster.

## Step 2 · DeepSeekMoE routing (60 min) → [02](02-deepseek-moe-fine-grained-routing.md)

```bash
python3 tools/moe_router_demo.py --steps 200
kubectl apply -f k8s/jobs/gpu-probes.yaml                 # includes the real V2-Lite router probe
```

**Done when:** the aux-loss-free bias balances expert load in the simulation, and you have per-expert loads from the real router.

## Step 3 · MTP and speculative decoding (45 min) → [03](03-multi-token-prediction-mtp.md)

```bash
python3 tools/spec_decode_calc.py --sweep
kubectl apply -k k8s/spec-decode/r1-7b-draft              # then r1-7b-ngram; compare tok/s
```

## Step 4 · FP8 (45 min) → [04](04-fp8-mixed-precision-framework.md)

```bash
python3 tools/fp8_blockscale.py
```

**Done when:** you can explain why 128×128 block scales survive outliers that break per-tensor scaling.

## Step 5 · R1 and GRPO (90 min) → [05](05-deepseek-r1-and-grpo-reasoning.md)

```bash
python3 tools/grpo_tiny.py --dry-run
kubectl apply -f k8s/jobs/train-common.yaml -f k8s/jobs/grpo.yaml   # Kueue admits it in batch/train
```

**Done when:** reward rises over the run and the model's outputs follow the `<think>/<answer>` format.

## Step 6 · FlashMLA and DeepGEMM on sm_121 (60 min) → [06](06-flash-mla-decoding-kernel.md), [07](07-deepgemm-fp8-library.md)

```bash
python3 tools/mla_attention_demo.py --decode-bench 8192 --batch 8 --device cuda
python3 tools/grouped_gemm_bench.py
```

**Done when:** you know which DeepSeek kernels target SM90/SM100 only, and what the GB10 uses instead.

## Step 7 · Long context (60 min) → [08](08-context-parallelism-and-long-context-attention.md)

```bash
torchrun --nproc-per-node 2 tools/ring_attention_demo.py --seq 4096
kubectl apply -k k8s/long-context/r1-7b-128k
python3 tools/needle_test.py --url http://localhost:8000 --model r1-7b-128k
```

## Step 8 · EPLB and 3FS (60 min) → [09](09-eplb-expert-parallelism-load-balancer.md), [10](10-3fs-fire-flyer-file-system.md)

```bash
python3 tools/eplb_sim.py --experts 256 --gpus 32 --redundant 32
```

## Step 9 · Memory math (30 min) → [12](12-memory-math-for-30b-32b-on-gb10.md)

```bash
python3 tools/model_math.py deepseek-r1-distill-qwen-32b --util 0.70 --ctx 16384
```

**Done when:** you can predict, before deploying, whether a model and context fit at a given `--gpu-memory-utilization`.

## Step 10 · 32B models (75 min) → [11](11-deepseek-r1-32b-and-qwen-32b-models.md)

```bash
scripts/serve-model.sh r1-32b-fp8
python3 tools/eval_harness.py --url http://localhost:8000 --model r1-32b-fp8 --suites math json --out results/r1-32b-fp8.json
```

## Step 11 · Coder and Math models (60 min) → [13](13-deepseek-coder-v2-and-math-models.md)

```bash
scripts/serve-model.sh coder-v2-lite
kubectl apply -f k8s/jobs/eval.yaml                       # code suite runs sandboxed in a pod
```

## Step 12 · V3/R1 671B across two Sparks (2×, 2–3 h) → [14](14-deepseek-v3-671b-moe-sharding.md)

```bash
kubectl apply -f k8s/llamacpp/rpc-2spark.yaml             # IQ1_S GGUF over llama.cpp RPC
```

On one Spark: do the sharding math in §3 and skip the deployment.

## Step 13 · vLLM catalog (90 min) → [15](15-vllm-serving-deepseek-and-qwen.md)

```bash
python3 scripts/gen_overlays.py --check
scripts/serve-model.sh r1-7b
scripts/breakfix.sh inject D01                            # then D02, D03
```

**Done when:** you can add a model to `models.yaml` and serve it with one command, and D01–D03 are solved.

## Step 14 · Other engines (3 h) → [16](16-sglang-and-radix-attention-serving.md), [17](17-ollama-and-llamacpp-local-gguf.md), [18](18-tensorrt-llm-compilation-for-deepseek.md)

Run the same weights and eval suites on SGLang, llama.cpp and TensorRT-LLM. Fill in the comparison tables in each volume.

## Step 15 · Manifests and admission (60 min) → [19](19-kubernetes-manifests-for-deepseek.md)

```bash
API=1 tests/run-local-checks.sh                           # schema → server dry-run → pod templates
```

## Step 16 · Weight cache (75 min) → [20](20-nvme-local-storage-and-weight-caching.md)

```bash
kubectl apply -f k8s/ops/weights-verify.yaml
kubectl -n llm-serving create job --from=cronjob/weights-verify wv-now
```

## Step 17 · Streaming gateway (60 min) → [21](21-ingress-and-realtime-streaming-gateways.md)

```bash
kubectl apply -k k8s/apps
python3 tools/stream_probe.py --url http://api.lab.local --api-key "$KEY" --model reasoning-fast
scripts/breakfix.sh inject D04
```

**Done when:** TTFT, time-to-first-answer and ITL are recorded at every hop, and D04 is solved.
