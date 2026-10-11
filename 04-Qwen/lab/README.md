# Qwen Lab · Runnable Companion to Chapters 01–14

A lean lab for the [Qwen step-by-step guide](../00-qwen-step-by-step-guide.md): a Qwen model catalog that fits one DGX Spark, vLLM overlays generated from it, ms-swift fine-tuning Jobs, and three CPU-testable tools for function calling, synthetic data and agents. Everything else is reused, not copied:

- **the platform** from [02-Kubernetes](../../02-Kubernetes/lab/README.md) (built by [01-Ansible](../../01-Ansible/lab/README.md)): the kubeadm root cluster on **dgx-spark-1** (192.168.0.100) with GPU Operator **15 time-slices**, Kueue, KEDA, Traefik at 192.168.0.115; the vLLM base Deployment (`02-Kubernetes/lab/manifests/root-llms/90-serving/vllm`), the `model-cache` PVC, the prefetch Job, the training window and `scripts/lib.sh`;
- **the application stack** from [03-DeepSeek](../../03-DeepSeek/lab/README.md): Open WebUI, LiteLLM, BGE-M3 + Qdrant RAG (`03-DeepSeek/lab/k8s/apps`, `03-DeepSeek/lab/tools/rag_demo.py`) and the telemetry (`03-DeepSeek/lab/observability`). This lab only tells them which Qwen model to use.

Three kubectl contexts in `01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml`: **`spark-root`** (runs the models: `llm-serving`, training: `batch`), `dev-lab` (tenants) and **`dev-llms`** (the LLM sandbox, namespace `llm-sandbox`, 2 CPU · 4 Gi · 2 slices). The scripts source 02's `scripts/lib.sh`, so `k` always means `kubectl --context spark-root` and `kdl` means `kubectl --context dev-llms`.

## The rules the catalog follows

The root's LLM stack shares one ~90 GiB memory pool ([02-Kubernetes Chapter 14](../../02-Kubernetes/14-multi-tenancy-resource-quotas-and-cgroups.md)). `scripts/gen_overlays.py --check` enforces in CI:

1. **Memory request = limit** on every GPU container, and `mem ≥ util × 121.7 GiB + 3.5 GiB`.
2. **Σ `--gpu-memory-utilization` ≲ 0.72** over the engines running at the same time (0.20 ≈ 24.3 GiB). The `sets:` in `models.yaml` are checked against it, with the 03 app stack (BGE-M3 at 0.06, ~19 Gi) counted where a set includes it.
3. **Everyday models stay ≤ 32 Gi**, so two of them fit `serving-budget` (90 Gi) next to the apps. The 30B-class models (56–64 Gi) run alone or with the app stack.
4. **Training runs in the training window**: each Job in `batch` is ≤ 8 CPU · ≤ 80 Gi · 1 slice, request = limit.

| name | Hugging Face repo | util | pod memory | context | flags worth knowing |
|---|---|---|---|---|---|
| `qwen3-0.6b` | `Qwen/Qwen3-0.6B` | 0.05 (sandbox 0.02) | 10 Gi (sandbox 3 Gi) | 8K | reasoning parser `qwen3`; has a dev-llms overlay |
| `qwen3-4b-instruct` | `Qwen/Qwen3-4B-Instruct-2507` | 0.12 | 20 Gi | 32K | tools: `hermes` (non-thinking) |
| `qwen3-4b-thinking` | `Qwen/Qwen3-4B-Thinking-2507` | 0.15 | 24 Gi | 32K | reasoning parser `deepseek_r1` |
| `qwen3-8b` | `Qwen/Qwen3-8B` | 0.20 | 32 Gi | 32K | reasoning `qwen3` + tools `hermes` (hybrid thinking) |
| `qwen2.5-math-7b` | `Qwen/Qwen2.5-Math-7B-Instruct` | 0.15 | 24 Gi | 4K | CoT / tool-integrated math |
| `qwen3-vl-8b` | `Qwen/Qwen3-VL-8B-Instruct` | 0.20 | 32 Gi | 16K | `--limit-mm-per-prompt` 2 images |
| `qwen3-coder-30b-fp8` | `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` | 0.40 | 56 Gi | 64K | tools: `qwen3_coder` |
| `qwen3.6-35b-fp8` | `Qwen/Qwen3.6-35B-A3B-FP8` | 0.45 | 64 Gi | 64K | reasoning `qwen3` + tools `qwen3_coder`, vision |

All are Apache-2.0 and not gated. QwQ-32B is served from 03's catalog (`03-DeepSeek/lab/k8s/models/qwq-32b-awq`). The overlays switch the image to `VLLM_IMAGE` from `versions.env`: the 02 base pins an older vLLM that predates Qwen3-VL and Qwen3.6.

## Layout

```
lab/
├── README.md
├── versions.env            # vLLM + PyTorch NGC images, ms-swift and its pins, qwen-agent (optional)
├── models.yaml             # Qwen catalog: repo, util, pod memory, context, flags; budget; sets that run together
├── scripts/
│   ├── lib.sh              # sources 02's lib.sh; model_field, side_render, running_util
│   ├── gen_overlays.py     # models.yaml → k8s/models/<name>/ (and <name>/sandbox/); --check for CI
│   └── serve-model.sh      # budget check → prefetch → apply → rollout → smoke test (main, --side, --sandbox)
├── k8s/
│   ├── models/<name>/      # GENERATED kustomize overlays of 02's vLLM base (one per catalog entry)
│   └── jobs/
│       ├── swift-sft.yaml  # ms-swift LoRA SFT, Qwen3-4B-Instruct-2507, Alpaca-GPT4 2k
│       ├── swift-dpo.yaml  # ms-swift LoRA DPO (or SimPO/ORPO/CPO), orca_dpo_pairs 2k
│       └── swift-grpo.yaml # ms-swift LoRA GRPO, Qwen2.5-1.5B-Instruct, NuminaMath-TIR, accuracy + format rewards
├── tools/                  # stdlib (+ PyYAML), any OpenAI-compatible endpoint
│   ├── fc_check.py         # tool-call / JSON-schema checker with a pass-rate gate
│   ├── synth_data.py       # self-instruct generation, dedup (ROUGE-L), quality filters, judge, DPO pairs
│   └── qwen_agent_demo.py  # Qwen-Agent style tool loop with three local tools and an audit log
└── tests/
    ├── mock_openai.py      # deterministic OpenAI endpoint: tool calls, JSON mode, instructions, judge
    └── run-local-checks.sh # everything CI runs (.github/workflows/qwen-lab-ci.yml)
```

## Quick start

Static checks and the tools on CPU, no cluster needed:

```bash
# ▶ MacBook · 04-Qwen/lab
pip install pyyaml yamllint shellcheck-py      # plus kubectl and kubeconform on the PATH
tests/run-local-checks.sh                      # ends with: ALL LOCAL CHECKS PASSED
scripts/serve-model.sh list                    # the catalog and the sets that fit together
```

Try the smallest model in the sandbox first, then serve the everyday set on the root:

```bash
# ▶ MacBook · 04-Qwen/lab
scripts/serve-model.sh qwen3-0.6b --sandbox            # dev-llms / llm-sandbox, 3 Gi, eager mode
kubectl --context dev-llms -n llm-sandbox delete -k k8s/models/qwen3-0.6b/sandbox
scripts/serve-model.sh qwen3-8b                        # main model behind svc/vllm
scripts/serve-model.sh qwen3-4b-instruct --side        # next to it: svc/vllm-qwen3-4b-instruct
kubectl --context spark-root -n llm-serving get deploy -L model
```

Each run checks Σ util against 0.72 before it applies anything, prefetches the weights into `model-cache` with 02's prefetch Job, waits for the rollout and asks the model for 12 × 12. Edit `models.yaml`, then regenerate the overlays:

```bash
# ▶ MacBook · 04-Qwen/lab
python3 scripts/gen_overlays.py && python3 scripts/gen_overlays.py --check
```

Check function calling and run the agent against the served model:

```bash
# ▶ MacBook · 04-Qwen/lab
kubectl --context spark-root -n llm-serving port-forward svc/vllm 8000:8000 &
python3 tools/fc_check.py run --url http://127.0.0.1:8000 --model qwen3-8b --min 0.8
python3 tools/qwen_agent_demo.py "How many GiB does qwen3-coder-30b-fp8 reserve? Look up 'qwen3-coder-30b-fp8' in the model catalog." \
  --url http://127.0.0.1:8000 --model qwen3-8b --audit out/agent.jsonl
python3 tools/synth_data.py --url http://127.0.0.1:8000 --model qwen3-8b --no-think \
  --rounds 4 --per-round 10 --judge --out out/sft.jsonl --dpo out/dpo.jsonl
```

Fine-tune in the training window (park serving, lend the pool to `batch`, run one Job at a time, give it back):

```bash
# ▶ MacBook · 04-Qwen/lab
kubectl --context spark-root -n llm-serving annotate scaledobject vllm autoscaling.keda.sh/paused-replicas=0 --overwrite
kubectl --context spark-root apply -k ../../02-Kubernetes/lab/manifests/root-llms/training-window
kubectl --context spark-root -n batch create configmap qwen-sft-data --from-file=train.jsonl=out/sft.jsonl   # optional: own data
kubectl --context spark-root apply -f k8s/jobs/swift-sft.yaml
kubectl --context spark-root -n batch get workloads                # Kueue admits it into spark-cq
kubectl --context spark-root -n batch logs -f job/swift-sft
kubectl --context spark-root apply -k ../../02-Kubernetes/lab/manifests/root-llms/10-tenancy
kubectl --context spark-root apply -k ../../02-Kubernetes/lab/manifests/root-llms/20-scheduling
kubectl --context spark-root -n llm-serving annotate scaledobject vllm autoscaling.keda.sh/paused-replicas-
```

The Jobs share the PVC `qwen-train` in `batch` (Hugging Face cache and outputs): `model-cache` lives in `llm-serving`, and a PVC cannot be mounted from another namespace. The Hugging Face token is optional for these models; if you use one, sync `kv/spark-lab/hf` (field `token`) from vault01 into Secret `hf-token` in `llm-serving` and, for the Jobs, in `batch`.

## Using 03's application stack with a Qwen model

Deploy the apps once from 03 (`kubectl --context spark-root apply -k ../../03-DeepSeek/lab/k8s/apps`). They reach models by Service name and served model name, so a Qwen model needs only a LiteLLM alias. Add entries like these to the `model_list` in `03-DeepSeek/lab/k8s/apps/litellm.yaml` (or `kubectl --context spark-root -n llm-serving edit configmap litellm-config`), then restart LiteLLM:

```yaml
- model_name: qwen                       # everyday chat (thinking on by default)
  litellm_params: {model: hosted_vllm/qwen3-8b, api_base: http://vllm.llm-serving:8000/v1, api_key: none}
- model_name: agent                      # the --side model with the hermes parser
  litellm_params: {model: hosted_vllm/qwen3-4b-instruct, api_base: http://vllm-qwen3-4b-instruct.llm-serving:8000/v1, api_key: none}
```

```bash
# ▶ MacBook · 04-Qwen/lab
kubectl --context spark-root -n llm-serving rollout restart deploy/litellm
```

Open WebUI lists whatever LiteLLM serves, so `qwen` and `agent` appear in its model picker. RAG keeps BGE-M3 and Qdrant and swaps only the chat model:

```bash
# ▶ MacBook · 04-Qwen/lab
kubectl --context spark-root -n llm-serving port-forward svc/qdrant 6333:6333 &
kubectl --context spark-root -n llm-serving port-forward svc/bge-m3 8001:8000 &
python3 ../../03-DeepSeek/lab/tools/rag_demo.py ask "Why does a vLLM pod set memory request equal to limit?" \
  --qdrant http://127.0.0.1:6333 --embed-url http://127.0.0.1:8001 --chat-url http://127.0.0.1:8000 --chat-model qwen3-8b
```

The Grafana dashboard and alert rules in `03-DeepSeek/lab/observability` read vLLM's own metrics, which carry the served model name, so they show the Qwen engines as they are.

## Where each piece is explained

| Chapter | Lab files |
|---|---|
| [01 · Architecture & model family](../01-qwen-architecture-and-model-family.md), [02 · Coder, Math & reasoning](../02-coder-math-and-reasoning-models.md), [03 · Vision-language](../03-vision-language-models.md) | `models.yaml` |
| [04 · Serving with vLLM & SGLang](../04-serving-qwen-with-vllm-and-sglang.md), [06 · Kubernetes deployment](../06-kubernetes-deployment-and-weight-caching.md) | `scripts/serve-model.sh`, `scripts/gen_overlays.py`, `k8s/models/` |
| [07 · ms-swift & SFT](../07-ms-swift-and-sft.md), [08 · PEFT & alignment](../08-peft-and-alignment.md) | `k8s/jobs/swift-*.yaml` |
| [09 · Synthetic data](../09-synthetic-data-and-self-play.md) | `tools/synth_data.py` |
| [10 · Qwen-Agent & function calling](../10-qwen-agent-and-function-calling.md) | `tools/fc_check.py`, `tools/qwen_agent_demo.py` |
| [11 · RAG](../11-rag-with-qwen-bge-and-qdrant.md), [12 · Open WebUI & LiteLLM](../12-open-webui-and-litellm.md), [13 · Telemetry](../13-telemetry-and-troubleshooting.md) | 03's apps, `rag_demo.py`, observability |
| [14 · Hands-on workbook](../14-hands-on-workbook.md) | `tests/run-local-checks.sh` |
