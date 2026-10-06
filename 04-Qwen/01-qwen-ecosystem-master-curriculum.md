# 01: Alibaba Qwen Ecosystem Master Curriculum & Technical Specification

This master specification details every critical topic, architectural component, mathematical formula, and operational runbook required to master the **Alibaba Qwen** ecosystem on enterprise infrastructure and the **NVIDIA DGX Spark (Grace Blackwell GB10)**.

---

## 📋 Comprehensive 25-Item Curriculum Roadmap

| Vol | Curriculum Module | Core Concepts & Technical Focus | Hardware / DGX Target |
| :---: | :--- | :--- | :--- |
| **01** | **Qwen2.5 Architecture & Model Spectrum** | Dense (0.5B, 1.5B, 3B, 7B, 14B, 32B, 72B) vs MoE (57B-A14B). Tokenizer (151,643 vocab), SwiGLU activations, RMSNorm pre-norm. | Unified Memory Math |
| **02** | **Attention Engineering: GQA, RoPE & DCA** | Grouped Query Attention (GQA, 8 KV heads), RoPE base $\theta = 1,000,000$, Dual Chunk Attention (DCA) for 128k context windows. | KV Cache Footprint |
| **03** | **Qwen2.5-Coder Deep Dive** | 5.5T code tokens, Fill-In-The-Middle (FIM), multi-file repository reasoning, LiveCodeBench & HumanEval benchmark dominance. | 7B / 14B / 32B Coder |
| **04** | **Qwen2.5-Math & Reasoning** | Chain-of-Thought (CoT), Tool-Integrated Reasoning (TIR), Python interpreter integration, MATH-500 & AIME verification. | Math SFT & RL |
| **05** | **Qwen2-VL & Vision-Language Processing** | Dynamic Resolution NaViT vision encoder, 2D RoPE for spatial positioning, native video comprehension, document OCR. | Multimodal Ingestion |
| **06** | **ModelScope ms-swift Framework Core** | Alibaba's unified LLM training ecosystem. CLI & Python interfaces, dataset pre-processing, 300+ model support. | Multi-GPU Training |
| **07** | **Distributed SFT with ms-swift** | Multi-GPU Supervised Fine-Tuning, Megatron-Deepspeed backend, ZeRO-2/ZeRO-3 integration, gradient checkpointing. | Blackwell Acceleration |
| **08** | **Advanced Alignment: DPO, SimPO & GRPO** | Direct Preference Optimization (DPO), Simple Preference Optimization (SimPO), and Group Relative Policy Optimization (GRPO). | Reasoning Loops |
| **09** | **Parameter-Efficient Tuning (PEFT)** | LoRA, QLoRA (bitsandbytes), DoRA (Weight-Decomposed LoRA), LoRA+, and GaLore memory-efficient pre-training. | DGX Spark 5% Quota |
| **10** | **Synthetic Data Generation & Self-Play** | LLM-as-a-Judge, rejection sampling, instruction back-translation, execution-guided filtering for coding/math data. | Automated Data Pipelines |
| **11** | **High-Throughput Serving with vLLM** | vLLM engine flags, PagedAttention v2, chunked prefill, speculative decoding for Qwen2.5, OpenAI-compatible API. | Production Serving |
| **12** | **SGLang & RadixAttention Deployment** | Radix tree KV cache reuse, multi-turn dialogue speedups, multi-round tool calling acceleration with zero cold KV cost. | Low-Latency Serving |
| **13** | **Quantization Engineering: AWQ, GPTQ & FP8** | 4-bit Activation-aware Weight Quantization (AWQ), GPTQ, Blackwell native FP8 (W8A8), and Marlin FP16xINT4 kernels. | 72B on Single DGX |
| **14** | **TensorRT-LLM Engine Compilation** | Building custom C++ TensorRT-LLM engines, in-flight batching, GEMM auto-tuning for GB10, FP8 execution graphs. | Maximum TFLOPs |
| **15** | **Ollama & llama.cpp Local GGUF** | GGUF quantizations (Q4_K_M, Q5_K_M, Q8_0), unified memory zero-copy offloading, desktop/edge testing. | Local Prototyping |
| **16** | **Qwen-Agent Framework** | Autonomous agentic architecture, planning loops, memory managers, Python code execution sandboxes, tool registries. | Agentic Workflows |
| **17** | **Tool Calling & Function Calling APIs** | Native Hermes/OpenAI function schema support, multi-tool chaining, parallel tool calls, strict JSON output enforcement. | JSON Schema Validation |
| **18** | **Enterprise RAG: Qwen + BGE + Qdrant** | High-precision retrieval augmented generation, BGE-M3 hybrid embeddings, Qdrant vector database, contextual reranking. | Enterprise Knowledge |
| **19** | **Multi-Modal Document & Video RAG** | Parsing complex PDFs, architectural blueprints, financial reports, and timestamped video retrieval using Qwen2-VL. | Multimodal RAG |
| **20** | **Open-WebUI & LiteLLM Gateway Integration** | Centralized proxy gateway, model fallback routing, team quotas, token metering, chat UI with artifact rendering. | Unified Portal |
| **21** | **Kubernetes Production Manifests** | Production K8s Deployments, Services, PVCs, NodeAffinity, tolerations, and resource limits on DGX Spark. | K3s Cluster Ops |
| **22** | **High-Speed Storage & Model Weight Caching** | Local NVMe caching, Hugging Face `hf_transfer` multi-threaded downloads, eliminating Pod cold starts. | Fast Volume Mounts |
| **23** | **DCGM, Prometheus & Grafana Telemetry** | Scraping vLLM Prometheus metrics, DCGM GPU utilization, TTFT (Time to First Token), ITL (Inter-Token Latency). | Real-Time Telemetry |
| **24** | **Master Troubleshooting Playbook** | Triage playbooks for RoPE out-of-bounds, NaN loss during SFT, CUDA OOM, KV cache fragmentation, and token truncation. | Production SRE |
| **25** | **Hands-On Exercises Mastery Workbook** | 25 production-grade challenges: SFT fine-tuning, vLLM deployment, AWQ quantization, Qwen-Agent coding, K8s scaling. | Lab Certification |

---

## 🔬 Architectural Deep Dive: What Makes Qwen Unique?

### 1. Grouped Query Attention (GQA) & Memory Mathematics
In standard Multi-Head Attention (MHA), each query head has its own Key and Value head ($H_q = H_{kv}$). In Qwen2.5, **Grouped Query Attention (GQA)** groups multiple Query heads under a single Key-Value head:

$$\text{Compression Ratio} = \frac{H_{kv}}{H_q}$$

For Qwen2.5-72B:
- $H_q = 64$ Query Heads
- $H_{kv} = 8$ Key/Value Heads
- Group Size = $64 / 8 = 8$ Query heads share one KV head.
- **KV Cache Reduction**: Memory consumption is reduced by $\frac{8}{64} = 87.5\%$ compared to standard MHA.

#### KV Cache Size per Token Formula:
$$\text{KV Size per Token (Bytes)} = 2 \times L \times H_{kv} \times d_k \times P$$
Where:
- $L$ = Number of layers (80 layers for 72B)
- $H_{kv}$ = Number of KV heads (8)
- $d_k$ = Head dimension (128)
- $P$ = Precision in bytes (2 for FP16/BF16, 1 for FP8)

For Qwen2.5-72B in BF16:
$$\text{KV Size} = 2 \times 80 \times 8 \times 128 \times 2 = 327,680 \text{ bytes/token} \approx 0.3125 \text{ MB/token}$$
For a **128,000 token context**:
$$\text{Total KV Cache} = 128,000 \times 0.3125 \text{ MB} \approx 40 \text{ GB}$$
*In FP8 quantization, this drops to just **20 GB**, fitting effortlessly into the DGX Spark unified memory.*

---

### 2. Dual Chunk Attention (DCA) for 128k Long Contexts
To process up to 128k tokens without quadratic compute explosion, Qwen utilizes **Dual Chunk Attention**:
1. Sequences are partitioned into manageable chunks (e.g., 4k tokens).
2. Intra-chunk attention captures dense, local token interactions.
3. Inter-chunk attention captures sparse, long-range dependencies using downsampled or relative RoPE embeddings.
4. Base frequency scaling is set to $\theta = 1,000,000$, ensuring rotary position embeddings do not degrade at position 131,072.

---

## 🛠️ The Alibaba ms-swift Fine-Tuning Runbook

Alibaba's **ms-swift** (Scalable lightWeight Infrastructure for Fine-Tuning) is the official training suite.

### Quickstart: Supervised Fine-Tuning (SFT) Qwen2.5-32B on DGX Spark
```bash
# 1. Install ms-swift
pip install "ms-swift[llm]" -U

# 2. Run LoRA SFT on Qwen2.5-32B-Instruct
swift sft \
    --model_type qwen2_5-32b-instruct \
    --dataset alpaca-en \
    --train_type lora \
    --lora_target_modules ALL \
    --lora_rank 16 \
    --lora_alpha 32 \
    --learning_rate 1e-4 \
    --num_train_epochs 3 \
    --max_length 4096 \
    --gradient_accumulation_steps 4 \
    --batch_size 1 \
    --use_flash_attn true \
    --fp16 false \
    --bf16 true \
    --output_dir ./output_qwen_lora
```

### High-Throughput vLLM Deployment
```bash
python3 -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-32B-Instruct \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.90 \
    --max-model-len 32768 \
    --dtype bfloat16 \
    --enable-chunked-prefill \
    --port 8000
```
