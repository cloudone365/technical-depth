# 01: Google Gemma Ecosystem Master Curriculum & Technical Specification

This master specification details every critical topic, architectural component, mathematical formulation, and operational runbook required to master the **Google Gemma** ecosystem on enterprise infrastructure and the **NVIDIA DGX Spark (Grace Blackwell GB10)**.

---

## 📋 Comprehensive 25-Item Curriculum Roadmap

| Vol | Curriculum Module | Core Concepts & Technical Focus | Hardware / DGX Target |
| :---: | :--- | :--- | :--- |
| **01** | **Gemma 2 Architecture: 2B, 9B & 27B** | Model parameters, 256k vocabulary (Gemini tokenizer), GeGLU activations, RMSNorm with unit offset, RoPE embeddings. | Single GB10 Memory |
| **02** | **Sliding Window Attention (SWA) Mechanics** | Alternating local sliding window (4k) and global full attention (8k) layers, 50% KV cache memory reduction, linear attention speedups. | KV Cache Footprint |
| **03** | **Logit Soft-Capping: Math & Implementation** | Attention logit soft-capping (50.0) and final vocabulary projection soft-capping (30.0). Hyperbolic tangent math, preventing training blowups. | Numerical Stability |
| **04** | **Gemini Ultra Knowledge Distillation** | On-policy distillation loss, teacher logits matching, KL divergence vs cross-entropy, why Gemma 2 9B punches above its weight class. | High-Fidelity Distillation |
| **05** | **Specialized Gemma Architectures** | CodeGemma (FIM & code completion), PaliGemma 2 (SigLIP-So400M vision encoder), RecurrentGemma (Griffin hybrid linear recurrence). | Multimodal & Code |
| **06** | **Google JAX, XLA & MaxText on NVIDIA GPUs** | MaxText scalable LLM engine, JAX pure functions, XLA ahead-of-time/JIT compilation for NVIDIA Blackwell Tensor Cores. | JAX on CUDA |
| **07** | **PyTorch 2.5, Hugging Face & Keras 3** | Keras 3 multi-backend engine (Torch, JAX, TensorFlow), Hugging Face Transformers integration, TorchDynamo compilation. | Cross-Framework |
| **08** | **SPMD Sharding & Mesh Parallelism in JAX** | Single Program Multiple Data (SPMD), NamedSharding, Mesh axes (data, model, tensor), automated collective communications. | Distributed Sharding |
| **09** | **Cross-Silicon Compilation: GPU vs TPU** | Comparing XLA compilation targets: NVIDIA Blackwell GB10 vs Google Cloud TPU v5p/v6e Trillium, memory bandwidth differences. | Silicon Benchmarking |
| **10** | **gemma.cpp: Zero-Dependency C++ Inference** | Google's standalone C++ inference implementation using Google Highway (portable SIMD), sub-second cold starts, CPU/GPU execution. | Edge & Microservice |
| **11** | **Parameter-Efficient Tuning: LoRA & QLoRA** | PEFT on Gemma 2, targeting `q_proj`, `v_proj`, `gate_proj`, gradient checkpointing, rank/alpha optimization on DGX Spark. | 5% Resource Envelope |
| **12** | **Post-Training: SFT, DPO & Gemma-2-Ataraxy** | Supervised fine-tuning, Direct Preference Optimization (DPO), and RLHF utilizing Google's Gemma-2-Ataraxy reward models. | Alignment Protocols |
| **13** | **Model Merging: Warp, TIES & DARE** | Weight Averaging (Warp), Truncate-Iterate-Eliminate-Shift (TIES), Drop And REscale (DARE) without retraining. | Post-Training Merging |
| **14** | **Safety Tuning: Responsible AI & ShieldGemma** | ShieldGemma content moderation models (2B, 9B), detecting hate speech, harassment, self-harm, sexually explicit content. | Enterprise Guardrails |
| **15** | **Vision-Language Fine-Tuning with PaliGemma 2** | Image captioning, object detection, document visual question answering (DocVQA), spatial bounding box tokenization. | Multimodal Ingestion |
| **16** | **High-Throughput vLLM Serving for Gemma 2** | vLLM integration, custom CUDA soft-capping kernels, PagedAttention v2, chunked prefill, OpenAI-compatible endpoint. | High-Throughput Serving |
| **17** | **Quantization: bitsandbytes, AWQ & Native FP8** | 4-bit NF4 quantization, Activation-aware Weight Quantization (AWQ), Blackwell native FP8 (W8A8) execution. | 27B on Single GB10 |
| **18** | **TensorRT-LLM Engine Compilation for Gemma** | Custom TensorRT-LLM plugins for Gemma 2 soft-capping and sliding window attention, maximum GPU TFLOPs extraction. | C++ High Performance |
| **19** | **Ollama & llama.cpp Local GGUF Deployment** | GGUF quantized models (Q4_K_M, Q8_0), unified memory zero-copy offloading on Grace ARM CPU + Blackwell GPU. | Local Prototyping |
| **20** | **Unified Memory Math & KV Sizing on GB10** | Mathematical calculation of Gemma 2 27B memory footprint, sliding window KV buffer sizing, and concurrency limits. | DGX Memory Math |
| **21** | **Multimodal Document RAG with PaliGemma 2** | End-to-end multimodal pipeline: indexing PDF pages as image-embeddings, visual document retrieval, and grounded answering. | Visual Enterprise RAG |
| **22** | **Enterprise Tool Calling & Function Calling APIs** | Structuring tool definitions, strict JSON mode generation, function dispatching, and agentic workflows with Gemma 2. | Autonomous Agents |
| **23** | **Kubernetes Production Manifests & KServe** | Production K8s Deployments, Services, and PVC manifests tailored to the DGX Spark 5% or full-host resource envelopes. | K3s Cluster Ops |
| **24** | **Master Troubleshooting: Soft-Cap Overflows & OOM** | Debugging NaN losses during fine-tuning, resolving vLLM soft-capping kernel bugs, handling CUDA out of memory errors. | SRE Diagnostics |
| **25** | **Hands-On Gemma Practice Workbook & Mastery Lab** | 25 production challenges: SWA memory calculation, JAX MaxText training, LoRA SFT, vLLM serving, ShieldGemma integration. | Lab Certification |

---

## 🔬 Architectural Deep Dive: What Makes Gemma 2 Unique?

### 1. Double Logit Soft-Capping Mathematics
In standard transformers, logits can grow arbitrarily large:

$$\text{logits} = \frac{Q K^T}{\sqrt{d_k}}$$

When values in $Q K^T$ become large, the softmax output becomes extremely peaky (over-confident), leading to gradient vanishing, loss spikes, and numerical overflow in mixed-precision training.

Gemma 2 solves this by passing all logits through a bounded hyperbolic tangent ($\tanh$) function:

$$\text{soft\_capped\_logits} = \text{cap} \times \tanh\left(\frac{\text{logits}}{\text{cap}}\right)$$

Where:
1. **Attention Logits Cap**: $\text{cap} = 50.0$ applied to $\frac{Q K^T}{\sqrt{d_k}}$ before softmax.
2. **Output Vocab Projection Cap**: $\text{cap} = 30.0$ applied to final hidden states $h \times W_{\text{vocab}}$ before softmax.

Because $\tanh(z) \in (-1, 1)$, the soft-capped attention logits are mathematically bounded to $(-50, 50)$, and output logits to $(-30, 30)$, guaranteeing numerical stability in FP16 and FP8.

#### PyTorch Implementation of Logit Soft-Capping:
```python
import torch
import torch.nn as nn
import torch.nn.functional as F

class Gemma2AttentionSoftCapping(nn.Module):
    def __init__(self, attn_cap: float = 50.0):
        super().__init__()
        self.attn_cap = attn_cap

    def forward(self, q: torch.Tensor, k: torch.Tensor, scale: float) -> torch.Tensor:
        # q: [B, H, S_q, D], k: [B, H, S_k, D]
        raw_scores = torch.matmul(q, k.transpose(-1, -2)) * scale
        # Apply hyperbolic tangent soft-capping
        soft_capped_scores = self.attn_cap * torch.tanh(raw_scores / self.attn_cap)
        return soft_capped_scores

# Verification
if __name__ == "__main__":
    capper = Gemma2AttentionSoftCapping(attn_cap=50.0)
    extreme_logits = torch.tensor([[-200.0, 0.0, 50.0, 200.0]])
    capped = capper.attn_cap * torch.tanh(extreme_logits / capper.attn_cap)
    print("Extreme logits:", extreme_logits)
    print("Soft-capped logits:", capped)
    # Output is strictly bounded between -50.0 and +50.0
```

---

### 2. Sliding Window Attention (SWA) Architecture
In Gemma 2, attention alternates layer-by-layer:
- **Even Layers (Layer 0, 2, 4, ...)**: Local Sliding Window Attention with window size $W = 4,096$ tokens. Token $i$ only attends to tokens in $[i - W, i]$.
- **Odd Layers (Layer 1, 3, 5, ...)**: Full Global Attention with window size $W = 8,192$ tokens. Token $i$ attends to all previous tokens $[0, i]$.

This architecture reduces KV cache memory consumption by **50%** on the alternating layers, allowing a 27B parameter model with 8k context to fit within strict memory envelopes while retaining global receptive field capabilities through the alternating global layers.

---

## 🛠️ Gemma 2 Runbook on DGX Spark

### Launching High-Throughput vLLM for Gemma 2 27B
```bash
python3 -m vllm.entrypoints.openai.api_server \
    --model google/gemma-2-27b-it \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.90 \
    --max-model-len 8192 \
    --dtype bfloat16 \
    --enforce-eager \
    --port 8000
```

### Running Gemma with Google JAX and MaxText
```bash
# Clone Google Cloud MaxText repository
git clone https://github.com/google/maxtext.git
cd maxtext

# Run Gemma 2 9B inference via JAX on NVIDIA GPU with XLA compilation
python3 MaxText/decode.py MaxText/configs/gemma2-9b.yml \
    run_name=gemma2_test \
    load_parameters_path=gs://maxtext-gemma/gemma2-9b \
    prompt="Explain the difference between Sliding Window Attention and Multi-Head Attention in deep learning."
```
