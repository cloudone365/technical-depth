# Volume 02: Megatron-Core 3D Parallelism Deep Dive

```
==================================================================================================
TARGET AUDIENCE: Distributed Systems Engineers, HPC Architects, AI Infrastructure Leads
PREREQUISITES   : PyTorch distributed (torch.distributed), collective communications (All-Reduce, All-Gather,
                  Reduce-Scatter), matrix multiplication, Transformer attention mechanics
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master Megatron-Core 3D Parallelism: Column/Row Tensor Parallelism, Sequence Parallelism,
                  Pipeline Parallelism (1F1B schedules), and Distributed Optimizer sharding.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Training and serving frontier foundation models containing tens or hundreds of billions of parameters is impossible on a single GPU. Even when model weights fit within memory, the gradients, optimizer states, and forward activations require multiples of the weight footprint.

**Megatron-Core** is NVIDIA's high-performance library for distributed Transformer architectures. It orchestrates **3D Parallelism**, combining **Tensor Parallelism (TP)**, **Pipeline Parallelism (PP)**, **Sequence Parallelism (SP)**, and **Distributed Data Parallelism (DP)** into a cohesive execution matrix that saturates NVLink and RDMA networks.

```
                            ┌─────────────────────────────────────────┐
                            │      Master 3D Parallelism Grid         │
                            │   Total GPUs = TP × PP × DP × CP        │
                            └────────────────────┬────────────────────┘
                                                 │
         ┌───────────────────────────────────────┼───────────────────────────────────────┐
         ▼                                       ▼                                       ▼
┌───────────────────────────────┐ ┌───────────────────────────────┐ ┌───────────────────────────────┐
│     Tensor Parallel (TP)      │ │    Pipeline Parallel (PP)     │ │    Sequence Parallel (SP)     │
│  - Intra-node NVLink (900GB/s)│ │  - Inter-node Partitioning    │ │  - Shards LayerNorm & Dropout │
│  - Column Parallel: W = [W1 W2] │ │  - 1F1B Bubble Minimization │ │  - Reduce-Scatter / All-Gather│
│  - Row Parallel: W = [W1; W2] │ │  - Virtual Stages             │ │  - Eliminates Activation VRAM │
└───────────────────────────────┘ └───────────────────────────────┘ └───────────────────────────────┘
                                                 │
                                                 ▼
                                  ┌───────────────────────────────┐
                                  │   Megatron Distributed Opt    │
                                  │   (ZeRO-1 Sharded AdamW)      │
                                  └───────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Assembly Line Factory
3. Evolutionary Lineage: From Horovod Data Parallelism to Megatron 3D
4. First-Principles Mathematics & Algorithmic Formulations
   - Column Parallel Linear Formulation
   - Row Parallel Linear Formulation & GEMM Fusion
   - Sequence Parallelism (SP): Reduce-Scatter & All-Gather Mechanics
   - Pipeline Parallelism (PP): 1F1B Bubble Calculus
5. Comparative Trade-Off Matrix: Distributed Parallelism Modes
6. Concrete Production Hands-On Lab: Column/Row Parallel Matrix Multiplication Simulator
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Assembly Line Factory

Imagine manufacturing an Airbus A380 airliner:

- **Data Parallelism Only**:
  You hire 100 individual mechanics. You tell each mechanic: "Build an entire A380 by yourself in your garage." Each mechanic needs a hangar large enough to hold an entire airliner, all raw metal parts, all tooling, and all manuals. If the airplane is too large to fit inside a single garage, zero planes get built.

- **Megatron 3D Parallelism**:
  You build a specialized modular manufacturing plant:
  1. **Pipeline Parallelism (PP)**: The factory floor is split into sequential stations. Station 1 installs the landing gear, Station 2 attaches the fuselage, Station 3 mounts the wings, Station 4 installs the avionics.
  2. **Tensor Parallelism (TP)**: Inside Station 3, the wing is too heavy for one mechanic. Four mechanics lift the wing simultaneously, each holding a specific quarter of the wing, moving in strict sync via walkie-talkies (high-speed NVLink).
  3. **Sequence Parallelism (SP)**: The long assembly instructions (tokens) are split page-by-page so no mechanic has to hold the entire manual at once.
  4. **Distributed Optimizer**: The spare parts catalog (AdamW optimizer states) is divided among all mechanics, so no single worker carries the entire warehouse in their backpack.

---

## 3. Evolutionary Lineage: From Horovod Data Parallelism to Megatron 3D

```
Generation 1 (2017-2019)      Generation 2 (2020-2022)      Generation 3 (2023-2026)
Horovod / PyTorch DDP         Megatron-LM v1 & DeepSpeed    Megatron-Core 3D + NeMo
──────────────────────────    ──────────────────────────    ───────────────────────────────
- Pure Data Parallelism       - Tensor Parallelism (TP)     - Unified Megatron-Core library
- Replicates model on each GPU - Pipeline Parallel (PP 1F1B) - Native Sequence Parallelism (SP)
- Hard limit: Model must fit  - ZeRO Stage 1/2/3 memory     - Context Parallelism (CP) for 1M
  in single GPU memory        - High communication overhead - Overlapped communication kernels
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Column Parallel Linear Formulation

Let input tensor be $\mathbf{X} \in \mathbb{R}^{B \times L \times d_{\text{in}}}$, and weight matrix be $\mathbf{W} \in \mathbb{R}^{d_{\text{in}} \times d_{\text{out}}}$.
In Column Parallelism across $N$ GPUs, $\mathbf{W}$ is sliced along its output columns:

$$\mathbf{W} = \begin{bmatrix} \mathbf{W}_1 & \mathbf{W}_2 & \dots & \mathbf{W}_N \end{bmatrix}, \quad \text{where } \mathbf{W}_i \in \mathbb{R}^{d_{\text{in}} \times \frac{d_{\text{out}}}{N}}$$

Input $\mathbf{X}$ is replicated across all $N$ GPUs. Each GPU computes its local matrix multiplication independently:

$$\mathbf{Y}_i = \mathbf{X} \mathbf{W}_i \in \mathbb{R}^{B \times L \times \frac{d_{\text{out}}}{N}}$$

**Zero communication** is required during the forward pass of Column Parallel Linear.

```
       Input X [B, L, Din] (Replicated across GPU 0 and GPU 1)
               │                               │
               ▼                               ▼
       GPU 0: W1 [Din, Dout/2]         GPU 1: W2 [Din, Dout/2]
               │                               │
               ▼                               ▼
       Output Y1 [B, L, Dout/2]        Output Y2 [B, L, Dout/2]
```

### Row Parallel Linear Formulation & GEMM Fusion

Next, let the intermediate representation be $\mathbf{Y} \in \mathbb{R}^{B \times L \times d_{\text{out}}}$, and the second projection weight be $\mathbf{V} \in \mathbb{R}^{d_{\text{out}} \times d_{\text{final}}}$.
In Row Parallelism across $N$ GPUs, $\mathbf{V}$ is sliced along its input rows:

$$\mathbf{V} = \begin{bmatrix} \mathbf{V}_1 \\ \mathbf{V}_2 \\ \vdots \\ \mathbf{V}_N \end{bmatrix}, \quad \text{where } \mathbf{V}_i \in \mathbb{R}^{\frac{d_{\text{out}}}{N} \times d_{\text{final}}}$$

Each GPU computes its local partial product:

$$\mathbf{Z}_i = \mathbf{Y}_i \mathbf{V}_i \in \mathbb{R}^{B \times L \times d_{\text{final}}}$$

To obtain the true mathematical product $\mathbf{Z} = \mathbf{Y} \mathbf{V}$, an **All-Reduce (SUM)** collective operation is executed across the $N$ GPUs:

$$\mathbf{Z} = \sum_{i=1}^N \mathbf{Z}_i = \text{All-Reduce-Sum}(\mathbf{Z}_i)$$

```
       GPU 0: Y1 [B, L, Dout/2]        GPU 1: Y2 [B, L, Dout/2]
               │                               │
               ▼                               ▼
       GPU 0: V1 [Dout/2, Dfinal]      GPU 1: V2 [Dout/2, Dfinal]
               │                               │
               ▼                               ▼
       GPU 0: Z1 [B, L, Dfinal]        GPU 1: Z2 [B, L, Dfinal]
               │                               │
               └───────────────┬───────────────┘
                               │ All-Reduce (Sum)
                               ▼
                   Z = Z1 + Z2 [B, L, Dfinal]
```

#### The Megatron Fusion Magic
In a standard MLP: $\mathbf{Z} = \text{GeLU}(\mathbf{X} \mathbf{W}) \mathbf{V}$.
By pairing **Column Parallel** for $\mathbf{W}$ and **Row Parallel** for $\mathbf{V}$, the non-linear activation $\text{GeLU}(\cdot)$ operates element-wise on local shards:

$$\mathbf{Y}_i = \text{GeLU}(\mathbf{X} \mathbf{W}_i)$$

The entire MLP block requires **only a single All-Reduce collective operation** at the very end. The intermediate representations are never gathered.

### Sequence Parallelism (SP): Reduce-Scatter & All-Gather Mechanics

In standard Tensor Parallelism, LayerNorm, Dropout, and residual connections are replicated across all TP GPUs, duplicating activation memory.
**Sequence Parallelism (SP)** shards the sequence dimension $L$ across the $N$ TP GPUs during non-tensor-parallel regions:

$$L_{\text{local}} = \frac{L}{N}$$

- Before entering Column Parallel Linear: An **All-Gather** collects tokens along the sequence dimension:
  $$\text{All-Gather}: \mathbb{R}^{B \times \frac{L}{N} \times d} \longrightarrow \mathbb{R}^{B \times L \times d}$$
- After exiting Row Parallel Linear: The All-Reduce (which is functionally an All-Gather + Reduce-Scatter) is decomposed into a **Reduce-Scatter**:
  $$\text{Reduce-Scatter}: \mathbb{R}^{B \times L \times d} \longrightarrow \mathbb{R}^{B \times \frac{L}{N} \times d}$$

This saves $(N-1)/N$ of the activation memory in LayerNorm and Dropout layers without increasing communication volume.

### Pipeline Parallelism (PP): 1F1B Bubble Calculus

Let $p$ be the number of pipeline stages, and $m$ be the number of micro-batches in a global batch.
Under the **1F1B (One Forward, One Backward)** schedule, the pipeline bubble fraction $F_{\text{bubble}}$ is:

$$F_{\text{bubble}} = \frac{p - 1}{m}$$

If $p = 8$ stages and $m = 64$ micro-batches:

$$F_{\text{bubble}} = \frac{8 - 1}{64} = \frac{7}{64} \approx 10.9\%$$

With **Interleaved 1F1B** (where each GPU hosts $v$ virtual stages):

$$F_{\text{bubble, interleaved}} = \frac{p - 1}{v \cdot m}$$

For $v = 4$, the bubble drops from $10.9\%$ down to **$2.7\%$**.

---

## 5. Comparative Trade-Off Matrix: Distributed Parallelism Modes

| Parallelism Dimension | Tensor Parallelism (TP) | Pipeline Parallelism (PP) | Sequence Parallelism (SP) | Context Parallelism (CP) |
| :--- | :--- | :--- | :--- | :--- |
| **Interconnect Target** | **Intra-node NVLink (High BW)**| Inter-node RDMA / InfiniBand | Intra-node NVLink | Inter-node RDMA (Ring Attn)|
| **Communication Type** | All-Reduce / Reduce-Scatter | Point-to-Point P2P (Send/Recv) | All-Gather / Reduce-Scatter | Ring P2P Key-Value passing |
| **Memory Sharded** | Weights, Gradients, Optimizer| Layers partitioned sequentially| LayerNorm Activations | Attention KV Activations |
| **Optimal Scale** | 2, 4, or 8 GPUs per node | 2 to 32 nodes | Equal to TP degree ($N \le 8$) | 2 to 64 GPUs for 1M context |
| **Pipeline Bubble** | 0% | $(p-1)/m$ | 0% | 0% |

---

## 6. Concrete Production Hands-On Lab: Column/Row Parallel Matrix Multiplication Simulator

This self-contained Python script simulates Megatron-Core Column Parallel and Row Parallel linear layers using PyTorch CPU tensors, proving mathematical identity with monolithic matrix multiplication down to machine precision.

```python
#!/usr/bin/env python3
"""
Megatron-Core Tensor Parallelism Simulator.
Mathematically verifies Column Parallel + Row Parallel Linear against monolithic GEMM.
"""

import torch
import torch.nn as nn

# =====================================================================
# 1. MONOLITHIC REFERENCE MLP BLOCK
# =====================================================================

class MonolithicMLP(nn.Module):
    def __init__(self, in_features: int, hidden_features: int, out_features: int):
        super().__init__()
        self.fc1 = nn.Linear(in_features, hidden_features, bias=False)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_features, out_features, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(self.act(self.fc1(x)))

# =====================================================================
# 2. MEGATRON TENSOR PARALLEL SIMULATOR (TP = 2)
# =====================================================================

class TensorParallelSimulator:
    def __init__(self, monolithic_mlp: MonolithicMLP, tp_world_size: int = 2):
        self.tp_world_size = tp_world_size

        # Extract weights
        W_fc1 = monolithic_mlp.fc1.weight.data # Shape: [hidden, in]
        W_fc2 = monolithic_mlp.fc2.weight.data # Shape: [out, hidden]

        hidden_dim = W_fc1.shape[0]
        shard_hidden = hidden_dim // tp_world_size

        # Slice Column Parallel for FC1 (Slice along output features)
        self.fc1_shards = []
        for rank in range(tp_world_size):
            start = rank * shard_hidden
            end = start + shard_hidden
            self.fc1_shards.append(W_fc1[start:end, :].clone())

        # Slice Row Parallel for FC2 (Slice along input features)
        self.fc2_shards = []
        for rank in range(tp_world_size):
            start = rank * shard_hidden
            end = start + shard_hidden
            self.fc2_shards.append(W_fc2[:, start:end].clone())

        self.act = nn.GELU()

    def forward_distributed(self, x: torch.Tensor) -> torch.Tensor:
        """Simulates parallel execution across TP ranks."""
        partial_outputs = []

        for rank in range(self.tp_world_size):
            # Step 1: Column Parallel GEMM on Rank (No communication)
            # x is replicated: shape [B, L, In]
            # fc1_shard: shape [ShardHidden, In]
            h_local = torch.matmul(x, self.fc1_shards[rank].t())
            act_local = self.act(h_local)

            # Step 2: Row Parallel GEMM on Rank (No communication yet)
            # fc2_shard: shape [Out, ShardHidden]
            z_partial = torch.matmul(act_local, self.fc2_shards[rank].t())
            partial_outputs.append(z_partial)

        # Step 3: All-Reduce (SUM) collective communication
        z_final = sum(partial_outputs)
        return z_final

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

def run_tp_simulation():
    print("=" * 80)
    print("MEGATRON-CORE TENSOR PARALLELISM SIMULATION (TP=2)")
    print("=" * 80)

    torch.manual_seed(42)
    B, L, Din, Dhidden, Dout = 2, 8, 64, 256, 64

    x = torch.randn(B, L, Din)

    # 1. Compute Reference Monolithic Output
    ref_mlp = MonolithicMLP(Din, Dhidden, Dout)
    with torch.no_grad():
        ref_out = ref_mlp(x)

    # 2. Compute Megatron TP=2 Output
    tp_sim = TensorParallelSimulator(ref_mlp, tp_world_size=2)
    with torch.no_grad():
        tp_out = tp_sim.forward_distributed(x)

    # 3. Numerical Error Analysis
    max_abs_diff = torch.max(torch.abs(ref_out - tp_out)).item()
    print(f"Max Absolute Error between Monolithic and TP=2: {max_abs_diff:.8e}")

    assert torch.allclose(ref_out, tp_out, atol=1e-6), "Mathematical mismatch in TP execution!"
    print("\n[SUCCESS] Column + Row Parallel Linear identity verified with zero error.")

if __name__ == "__main__":
    run_tp_simulation()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

On the DGX Spark (Blackwell GB10 + Grace ARM):
1. **Intra-Socket NVLink-C2C**:
   Grace and Blackwell communicate over an ultra-wide **900 GB/s NVLink-C2C bus**. In single-node deployments, Tensor Parallelism is not strictly necessary for models $\le 32\text{B}$ parameters since the entire model fits inside the 128 GB unified memory.

2. **When to Enable TP on DGX Spark**:
   - For **Nemotron-4 340B**: Requires multi-node clustering with $\text{TP}=8$ and $\text{PP}=4$.
   - For **Latency-Critical Serving**: Setting $\text{TP}=2$ or $\text{TP}=4$ across Blackwell GPUs reduces compute latency per token by sharding large GEMM dimensions across parallel SM arrays.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Pipeline Bubble Calculation)**:
   A training job uses $p = 16$ pipeline stages and $m = 32$ micro-batches. Calculate the pipeline bubble fraction under standard 1F1B. How many virtual stages $v$ are needed to reduce the bubble below $5\%$?

2. **Exercise 2 (Sequence Parallel Memory Savings)**:
   Given sequence length $L = 65,536$, hidden size $d = 8,192$, and $\text{TP} = 8$, calculate the activation memory saved per LayerNorm layer by enabling Sequence Parallelism.

### Solutions

**Solution for Exercise 1**:
- Standard 1F1B Bubble: $F = \frac{16 - 1}{32} = \frac{15}{32} = 46.875\%$.
- With interleaved 1F1B: $F = \frac{15}{v \cdot 32} < 0.05 \implies v \cdot 32 > 300 \implies v \ge 10$ virtual stages.

### Troubleshooting FAQ

- **Q: Distributed training hangs during the first backward pass.**
  - *Fix*: You likely have an mismatched All-Reduce or All-Gather collective. Ensure that all ranks participate in every collective operation. If a condition branches on rank, non-participating ranks cause a deadlock.

- **Q: Numerical divergence occurs when scaling from TP=1 to TP=4.**
  - *Fix*: Ensure random seeds are synchronized for replicated layers (LayerNorm, embeddings) and partitioned for sharded layers. Megatron-Core manages this automatically via `tensor_parallel.model_parallel_cuda_manual_seed`.
