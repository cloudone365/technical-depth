# 01: NVIDIA NeMo & Nemotron Ecosystem Master Curriculum & Technical Specification

This master specification details every critical topic, architectural component, mathematical formulation, and operational runbook required to master the **NVIDIA NeMo** enterprise stack on modern hardware and the **NVIDIA DGX Spark (Grace Blackwell GB10)**.

---

## 📋 Comprehensive 25-Item Curriculum Roadmap

| Vol | Curriculum Module | Core Concepts & Technical Focus | Hardware / DGX Target |
| :---: | :--- | :--- | :--- |
| **01** | **NeMo Framework Architecture & Core Abstractions** | Neural Modules, ModelPT, OmegaConf/Hydra configs, PyTorch Lightning integration, NGC container registry. | Single-Node & Multi-Node |
| **02** | **Megatron-Core 3D Parallelism Deep Dive** | Tensor Parallelism (column/row linear), Pipeline Parallelism (1F1B schedule), Sequence Parallelism, Distributed Optimizer. | Inter-GPU NVLink & RDMA |
| **03** | **Transformer Engine & Native FP8 on Blackwell** | Delayed scaling factors, FP8 E4M3 (forward) vs E5M2 (backward), GEMM autotuning, numerical stability guardrails. | GB10 Tensor Cores |
| **04** | **Nemotron Model Family: 340B, 70B & Minitron** | Nemotron-4 340B (Base, Instruct, Reward), Llama-3.1-Nemotron-70B, synthetic data generation leaderboards. | Model Weights & Shards |
| **05** | **Minitron: Structured Pruning & Distillation** | Layer pruning, hidden dimension reduction, embedding shrinking, and teacher-student knowledge distillation. | 8B $\to$ 4B Compact Models |
| **06** | **NeMo Curator: GPU-Accelerated Petabyte Ingestion** | Dask/Ray on GPUs, modular pipeline design, web scraping parsing (WARC, Common Crawl), high-throughput tokenization. | Multi-NVMe High IOPS |
| **07** | **MinHash LSH Deduplication & Exact String Matching** | Jaccard similarity estimation, MinHash locality-sensitive hashing, connected components graph clustering, exact substring removal. | Petabyte-Scale Cleaning |
| **08** | **Heuristic Quality Filtering & Domain Classifiers** | FastText language identification, rule-based quality heuristics, toxicity classifiers, high-perplexity removal. | Training Set Filtering |
| **09** | **PII Redaction & Enterprise Data Sanitization** | Named Entity Recognition (NER), regex-based PII masking (SSN, credit cards, emails), GDPR/HIPAA compliance engines. | Enterprise Data Clean |
| **10** | **Synthetic Data Generation with Nemotron-4 340B** | Instruction generation, prompt expansion, response generation, rejection sampling, creating high-density SFT data. | Automated SFT Generation |
| **11** | **NeMo Customizer: Distributed SFT & PEFT** | Supervised fine-tuning at scale, Megatron data loaders, packing sequences with FlashAttention, gradient accumulation. | Distributed SFT |
| **12** | **SteerLM: Multi-Attribute Conditioned Alignment** | Dynamic attribute conditioning (Helpfulness, Correctness, Tone), eliminating RLHF instability, runtime attribute steering. | Controllable Generation |
| **13** | **Direct Preference Optimization (DPO) at Scale** | Implicit reward formulation, reference policy regularization, pairwise log-odds optimization with Megatron-Core. | Preference Alignment |
| **14** | **Distributed RLHF with PPO & Nemotron Reward** | 4-model cluster topology (Actor, Critic, Reference, Reward), PPO clipping, GAE (Generalized Advantage Estimation). | Multi-GPU RLHF Cluster |
| **15** | **Parameter-Efficient Tuning (PEFT)** | Low-Rank Adaptation (LoRA), P-Tuning v2, Prefix Tuning, Adapters, selective freezing of Megatron transformer layers. | 5% Resource Envelope |
| **16** | **NeMo Guardrails & Colang 2.0 Programming** | Programmable runtime rails, flow-based dialog control, event-driven architecture, LLM agent boundaries. | Safe Generative AI |
| **17** | **Input, Output & Dialog Flow Safety Rails** | Masking prompt injections, input moderation, PII output scrubbers, deterministic state machine branching. | Security Gateways |
| **18** | **Hallucination Detection & Jailbreak Prevention** | Self-check fact verification rails, retrieval consistency checkers, adversarial jailbreak classification. | Enterprise Compliance |
| **19** | **NeMo Retriever: NV-Embed & Neural Rerankers** | State-of-the-art dense embeddings (NV-Embed-v2), neural cross-encoder rerankers (NV-Rerank), Milvus/Qdrant integration. | Low-Latency RAG |
| **20** | **Enterprise Production RAG Architectures** | Hybrid search (dense + sparse BM25), metadata filtering, context compression, citation verification, LangChain integration. | Enterprise Knowledge |
| **21** | **Triton Inference Server + TensorRT-LLM Integration** | Exporting NeMo `.nemo` checkpoints to TensorRT-LLM engines, Triton ensemble pipelines, in-flight batching. | Sub-Millisecond Serving |
| **22** | **NVIDIA NGC Container Deployment & PyTorch 2.5** | Docker container orchestration, NGC API authentication, CUDA 12.6+ base images, automated volume mounting. | Containerized Ops |
| **23** | **Kubernetes & Slurm Orchestration for NeMo** | Volcano and Kueue batch schedulers on K8s, Slurm Pyxis/Enroot job scripts, multi-node InfiniBand MPI jobs. | HPC & Cloud-Native |
| **24** | **Cluster Diagnostics, NCCL RDMA & Xid Recovery** | NCCL all-reduce bus bandwidth tests, InfiniBand link validation, diagnosing GPU Xid errors, DCGM health alerts. | SRE Resilience |
| **25** | **Hands-On NeMo Mastery Practice Workbook** | 25 production challenges: Curator pipeline, Megatron TP pretraining, SteerLM fine-tuning, Colang guardrails, Triton deploy. | Certification Lab |

---

## 🔬 Architectural Deep Dive: What Makes NeMo Unique?

### 1. Megatron-Core 3D Parallelism Mathematics
To train models exceeding hundreds of billions of parameters, NeMo leverages **3D Parallelism**:

$$\text{Total GPUs} = \text{TP} \times \text{PP} \times \text{DP}$$

1. **Tensor Parallelism (TP)**: Splits individual weight matrices across GPUs within the same node (connected via high-speed NVLink at 900 GB/s on DGX Spark).
   - *Column Parallel Linear*: $\text{Split } W \text{ column-wise across } N \text{ GPUs}: Y_i = X W_i$. Outputs are concatenated via All-Gather or passed to Row Parallel.
   - *Row Parallel Linear*: $\text{Split } W \text{ row-wise}: Y = \sum_{i=1}^N X_i W_i$. Outputs are summed via All-Reduce.
2. **Pipeline Parallelism (PP)**: Partitions the model layers sequentially across nodes.
   - *1F1B Schedule (One Forward, One Backward)*: Interleaves forward and backward passes to reduce the pipeline bubble:
     $$\text{Bubble Fraction} = \frac{p - 1}{m}$$
     Where $p$ is the pipeline depth and $m$ is the number of micro-batches.
3. **Context Parallelism (CP)**: Shards the sequence dimension across GPUs for ultra-long context windows (128k to 1M tokens), overlapping ring communication with attention computation.

---

### 2. SteerLM: Multi-Attribute Alignment Paradigm
Traditional RLHF collapses human feedback into a single scalar reward. **SteerLM** trains the model conditioned on explicit, continuous attributes:

$$P(Y \mid X, a_1, a_2, \dots, a_k)$$

Where $a_i \in [0, 4]$ represents attributes such as:
- $a_1$: **Helpfulness** (Degree of task completion)
- $a_2$: **Correctness** (Factual accuracy)
- $a_3$: **Coherence** (Logical consistency)
- $a_4$: **Complexity** (Sophistication of vocabulary/depth)
- $a_5$: **Verbosity** (Conciseness vs detail)

At inference time, developers can "steer" the model by setting:
`[Helpfulness: 4, Correctness: 4, Verbosity: 1]` for concise expert summaries, or `[Verbosity: 4]` for step-by-step educational explanations.

---

### 3. NeMo Guardrails (Colang 2.0 Syntax Example)
```colang
# Define sensitive financial topics
define user ask off_topic
  "Can you write a poem about flowers?"
  "Who won the soccer match yesterday?"

define bot refuse off_topic
  "I am an enterprise financial assistant. I cannot assist with non-financial inquiries."

# Define deterministic flow
define flow handle off_topic
  user ask off_topic
  bot refuse off_topic
  stop

# Define input jailbreak rail
define flow check input
  $is_safe = execute check_jailbreak(input=$user_message)
  if not $is_safe
    bot inform cannot respond
    stop
```

---

## 🛠️ NeMo Framework Runbook on DGX Spark

### Launching the NeMo NGC Container
```bash
docker run --gpus all -it --rm \
    --ipc=host \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    -v /home/sundarjadhav/models:/workspace/models \
    -v /home/sundarjadhav/data:/workspace/data \
    nvcr.io/nvidia/nemo:24.09
```

### Running PEFT LoRA Fine-Tuning with NeMo
```python
import nemo.collections.nlp as nemo_nlp
from nemo.collections.nlp.models.language_modeling.megatron_gpt_model import MegatronGPTModel

# Load pre-trained NeMo model
model = MegatronGPTModel.restore_from(restore_path="/workspace/models/nemotron_340b.nemo")

# Configure LoRA adapter
peft_config = {
    'target_modules': ['attention.dense_h_to_4h', 'attention.dense_4h_to_h'],
    'adapter_dim': 16,
    'adapter_dropout': 0.1
}

# Attach adapter and begin training
model.add_adapter(peft_config)
print("NeMo LoRA adapter initialized successfully on Grace Blackwell GB10.")
```
