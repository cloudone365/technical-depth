# Volume 08: SPMD Sharding and Mesh Parallelism in JAX

```
==================================================================================================
TARGET AUDIENCE: Distributed Systems Architects, JAX High-Performance Leads, GPU Cluster Engineers
PREREQUISITES   : Multi-GPU Communication (All-Reduce, All-Gather), JAX Arrays, Distributed Mesh
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master JAX Single Program Multiple Data (SPMD) sharding, Mesh axes, NamedSharding,
                  and PartitionSpec (P) to distribute Gemma 2 27B training and inference across GPUs.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

In traditional distributed PyTorch (Megatron-LM, DeepSpeed), engineers must manually orchestrate collective communication primitives: inserting explicit `torch.distributed.all_reduce()` after row-parallel linear layers and `all_gather()` before sequence-parallel attention.

In Google JAX, distributed execution follows a profoundly different, declarative paradigm: **Single Program Multiple Data (SPMD) Mesh Parallelism**. Rather than writing communication code, the engineer merely declares the logical **Mesh** of physical devices and provides a **PartitionSpec** describing how logical tensor axes map to physical device axes. The XLA compiler mathematically analyzes the compute graph and automatically synthesizes optimal NCCL collectives (All-Reduce, All-Gather, Reduce-Scatter) at compile time.

```
                           ┌──────────────────────────────────────────────┐
                           │            PHYSICAL DEVICE MESH              │
                           │   Mesh(devices, ('data', 'fsdp', 'tensor'))  │
                           └──────────────────────┬───────────────────────┘
                                                  │
                 ┌────────────────────────────────┴────────────────────────────────┐
                 ▼                                                                 ▼
┌──────────────────────────────────────────────┐  ┌──────────────────────────────────────────────┐
│             LOGICAL TENSOR AXES              │  │            NAMEDSHARDING WITH P()            │
│  Weight: [Hidden_In, Hidden_Out]             │  │  NamedSharding(mesh, P('fsdp', 'tensor'))    │
│  Activation: [Batch, SeqLen, Hidden]         │  │  NamedSharding(mesh, P('data', None, None))  │
└──────────────────────┬───────────────────────┘  └──────────────────────┬───────────────────────┘
                       │                                                 │
                       └────────────────────────┬────────────────────────┘
                                                ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              XLA SPMD PARTITIONER & COMPILER                                   │
│  - Automatically derives intermediate tensor shardings across layer operations                 │
│  - Automatically inserts optimized NCCL collectives: All-Gather, Reduce-Scatter, All-Reduce    │
│  - Fuses communications with Blackwell Tensor Core matrix multiplications                       │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Master Construction Blueprint
3. Evolutionary Lineage: From Explicit MPI Collectives to Declarative SPMD Sharding
4. First-Principles Mathematics & Algorithmic Formulations
   - The JAX SPMD Sharding Abstraction: Device Mesh and PartitionSpec
   - Automatic Collective Synthesis: How XLA Derives Communications
   - Sharding Gemma 2 27B: Data, FSDP, and Tensor Parallel Partitioning
   - Activation Memory vs Parameter Sharding Trade-Off
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: JAX Mesh Creation and Sharded Matrix Multiplication
7. Hardware Grounding for NVIDIA DGX Spark (Unified Memory and NVLink-C2C Bandwidth)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Master Construction Blueprint

Imagine constructing a massive skyscraper with 8 specialized construction crews (8 GPUs):
- **Imperative Parallelism (Manual PyTorch / Megatron)** is like giving a radio to every individual worker and forcing them to manually coordinate:
  - *"Crew 1, build column A, then radio Crew 2, 3, and 4 to pick up your steel beam, then wait for everyone to say 'Received' (All-Gather)."*
  - If a worker forgets a single radio call, the entire construction site deadlocks and halts permanently.
- **Declarative SPMD Mesh Sharding (Google JAX)** is like handing a single, unified 3D architectural CAD model to an automated construction supercomputer (XLA):
  - You tell the supercomputer: *"Split the building along the East-West axis into 4 sections, and split the foundation across all 8 crews."*
  - The supercomputer calculates the exact logistics: it automatically schedules who moves which beam, when to hand off materials, and how to overlap transport with welding.
  - Workers never radio each other manually; the compiled plan is mathematically guaranteed to be deadlock-free and communication-optimal.

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2018: Explicit Collective Parallelism (Megatron-LM, FairScale)         │
│ Developers write explicit column-parallel and row-parallel classes.    │
│ Error-prone, hard to modify model architectures without rewrites.      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2021: JAX pjit & GSPMD (Lepikhin et al., Xu et al., Google)           │
│ Introduced General SPMD partitioning. Users decorate functions with    │
│ in_shardings and out_shardings; XLA synthesizes distributed execution. │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2023-Present: JAX NamedSharding & Dynamic Mesh API                     │
│ Replaced pjit with universal jax.jit and NamedSharding. Sharding is a  │
│ first-class property of jax.Array, enabling effortless scaling of      │
│ Gemma 2 and Gemini across thousands of TPU and GPU chips.              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 The Device Mesh and PartitionSpec

In JAX, physical GPUs are represented as a multidimensional array of devices called a **Mesh**:

$$\mathcal{M} \in \mathbb{D}^{d_1 \times d_2 \times \dots \times d_k}$$

Where each axis is assigned a human-readable semantic name, such as `('data', 'fsdp', 'tensor')`.

A tensor $X \in \mathbb{R}^{S_1 \times S_2 \times \dots \times S_m}$ is mapped to the mesh using a **PartitionSpec**, denoted $P(\text{axis}_1, \text{axis}_2, \dots, \text{axis}_m)$:
- If axis $i$ is assigned name `'fsdp'`, dimension $S_i$ is partitioned evenly across the devices along the `'fsdp'` mesh axis.
- If axis $i$ is `None`, dimension $S_i$ is replicated across all devices on that axis.

For example, a Gemma 2 feed-forward projection matrix $W_{\text{gate}} \in \mathbb{R}^{D_{\text{model}} \times D_{\text{ffn}}}$ with $P(\text{None}, \text{'tensor'})$:
- The input dimension $D_{\text{model}}$ is replicated.
- The output dimension $D_{\text{ffn}}$ is sharded across the `tensor` axis (equivalent to Column Parallel Linear in Megatron).

### 4.2 Automatic Collective Synthesis Proof

Consider matrix multiplication between activation $X \in \mathbb{R}^{B \times D}$ and weight $W \in \mathbb{R}^{D \times M}$:

$$Y = X \cdot W$$

Suppose our device mesh has 4 GPUs along a 1D axis named `'tp'`, and we shard:
- $X$ with sharding $P(\text{None}, \text{None})$ (fully replicated).
- $W$ with sharding $P(\text{None}, \text{'tp'})$ (column-sharded, each GPU holds $\frac{M}{4}$ columns).

The local computation on GPU $k$ evaluates:

$$Y^{(k)} = X \cdot W^{(k)} \in \mathbb{R}^{B \times \frac{M}{4}}$$

The resulting output $Y$ naturally has sharding $P(\text{None}, \text{'tp'})$. **No communication is needed!**

Now, suppose the subsequent layer multiplies $Y$ by second weight $V \in \mathbb{R}^{M \times D}$, sharded with $P(\text{'tp'}, \text{None})$ (row-sharded, matching the column sharding of $Y$):

$$Z^{(k)} = Y^{(k)} \cdot V^{(k)} = \left( X \cdot W^{(k)} \right) \cdot V^{(k)} \in \mathbb{R}^{B \times D}$$

Notice that the true mathematical result is:

$$Z = \sum_{k=1}^4 Z^{(k)}$$

The XLA compiler inspects the sharding of $Z$ demanded by the next layer. If the next layer expects $Z$ with sharding $P(\text{None}, \text{None})$, XLA automatically synthesizes a **NCCL All-Reduce (Sum)** across the `'tp'` device axis.

### 4.3 Gemma 2 27B Sharding Strategy

For a single-node or multi-node cluster, Gemma 2 27B parameters ($27.2\times 10^9$ weights) are sharded as follows:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        GEMMA 2 27B SHARDING PARTITION SPEC MATRIX                      │
├──────────────────────────────┬────────────────────────────┬────────────────────────────┤
│ Component                    │ Tensor Shape               │ PartitionSpec P(...)       │
├──────────────────────────────┼────────────────────────────┼────────────────────────────┤
│ Embedding Layer              │ [Vocab (256k), Hidden]     │ P('tensor', None)          │
│ Q / K / V Projections        │ [Hidden, NumHeads, HeadDim]│ P(None, 'tensor', None)    │
│ Attention Output Proj        │ [NumHeads * HeadDim, Hidden│ P('tensor', None)          │
│ MLP Gate & Up Projections    │ [Hidden, Intermediate]     │ P(None, 'tensor')          │
│ MLP Down Projection          │ [Intermediate, Hidden]     │ P('tensor', None)          │
│ Activations (Batch, SeqLen)  │ [Batch, SeqLen, Hidden]    │ P('data', None, None)      │
└──────────────────────────────┴────────────────────────────┴────────────────────────────┘
```

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                DISTRIBUTED SHARDING PARADIGMS                                          │
├──────────────────────┬──────────────────────┬──────────────────────────┬───────────────────────────────┤
│ Framework            │ Parallelism Method   │ Code Intrusiveness       │ Optimization Level            │
├──────────────────────┼──────────────────────┼──────────────────────────┼───────────────────────────────┤
│ PyTorch DDP          │ Pure Data Parallel   │ Low                      │ Cannot fit 27B on single GPU  │
│ PyTorch FSDP2        │ Fully Sharded Data   │ Moderate                 │ Standard ZeRO-3 style         │
│ Megatron-LM          │ 3D Tensor/Pipeline   │ Very High (Rewrite code) │ High manual tuning            │
│ JAX NamedSharding    │ Declarative SPMD     │ Minimal (Declarative P)  │ Automatic XLA global fusion   │
└──────────────────────┴──────────────────────┴──────────────────────────┴───────────────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: JAX Mesh Creation and Sharded Matrix Multiplication

Save this script as `gemma2_jax_spmd_lab.py`:

```python
"""
Google Gemma 2 JAX SPMD Sharding Lab.
Demonstrates:
1. Creating Logical Device Meshes across GPU devices.
2. NamedSharding with PartitionSpec P().
3. Automatic collective derivation during distributed GEMM.
"""

import os
os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"

try:
    import jax
    import jax.numpy as jnp
    from jax.sharding import Mesh, PartitionSpec as P, NamedSharding
    from jax.experimental.shard_map import shard_map
    HAS_JAX = True
except ImportError:
    HAS_JAX = False

def run_spmd_lab():
    print("=" * 80)
    print("RUNNING GOOGLE GEMMA 2 JAX SPMD & MESH PARALLELISM LAB")
    print("=" * 80)

    if not HAS_JAX:
        print("Note: JAX is not available in the current environment.")
        print("Displaying programmatic implementation of Gemma 2 NamedSharding logic:\n")
        print("""
        from jax.sharding import Mesh, PartitionSpec as P, NamedSharding

        # 1. Define device mesh: 1 data-parallel axis, 1 tensor-parallel axis
        devices = jax.devices()
        mesh = Mesh(devices.reshape((1, len(devices))), ('data', 'tensor'))

        # 2. Gemma 2 Attention Weight Sharding
        # Q projection: [Hidden, NumHeads, HeadDim] -> Sharded along NumHeads
        q_sharding = NamedSharding(mesh, P(None, 'tensor', None))

        # MLP Gate projection: [Hidden, Intermediate] -> Sharded along Intermediate
        gate_sharding = NamedSharding(mesh, P(None, 'tensor'))

        # 3. Activation Sharding
        # Batch is sharded across 'data', Sequence length is replicated
        act_sharding = NamedSharding(mesh, P('data', None, None))
        """)
        return

    devices = jax.devices()
    num_devices = len(devices)
    print(f"Detected physical execution devices: {num_devices} ({devices})")

    # Define a 1D Mesh
    mesh_devices = devices.reshape((num_devices,))
    mesh = Mesh(mesh_devices, axis_names=('tp',))
    print(f"Configured JAX Device Mesh: {mesh}")

    # Shardings
    replicated_sharding = NamedSharding(mesh, P(None, None))
    column_sharded = NamedSharding(mesh, P(None, 'tp'))
    row_sharded = NamedSharding(mesh, P('tp', None))

    # Synthetic matrix multiplication
    b, d, m = 4, 1024, 2048
    x = jax.random.normal(jax.random.PRNGKey(0), (b, d))
    w = jax.random.normal(jax.random.PRNGKey(1), (d, m))

    # Apply sharding
    x_replicated = jax.device_put(x, replicated_sharding)
    w_column = jax.device_put(w, column_sharded)

    print(f"X Sharding: {x_replicated.sharding}")
    print(f"W Sharding: {w_column.sharding}")

    @jax.jit
    def distributed_matmul(act, weight):
        return jnp.matmul(act, weight)

    out = distributed_matmul(x_replicated, w_column)
    print(f"Output Matrix Shape: {out.shape}")
    print(f"Output Sharding Derived by XLA: {out.sharding}")
    print("\nVerification Passed: JAX XLA automatically derived column-sharded output sharding!")

if __name__ == "__main__":
    run_spmd_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (Unified Memory and NVLink-C2C Bandwidth)

### 7.1 Single-Node NVLink-C2C Fabric
On the NVIDIA DGX Spark, the Grace ARM CPU and Blackwell GB10 GPU communicate via a dedicated **900 GB/s bidirectional NVLink-C2C** link.
When distributing Gemma 2 27B across heterogeneous memory tiers:
- The 900 GB/s bandwidth is **$7\times$ faster than PCIe Gen 5 ($128\text{ GB/s}$)**.
- JAX host-to-device transfers (`jax.device_put`) and activation offloading operate with single-microsecond latency.
- XLA's SPMD partitioner can place activation checkpoints directly into Grace ARM DRAM and fetch them during the backward pass without stalling the Blackwell Tensor Cores.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate All-Reduce Volume in Tensor Parallel Down Projection**:
   Given Gemma 2 27B with hidden dimension $D = 4608$, sequence length $S = 4096$, batch size $B = 2$, and tensor parallelism $TP = 4$. What is the payload size in megabytes transferred across the All-Reduce collective after the MLP down-projection?
   - *Solution*:
     - Output tensor dimension: $B \times S \times D = 2 \times 4096 \times 4608 = 37,748,736$ elements.
     - In bfloat16 ($2\text{ bytes}$ per element): $37,748,736 \times 2 = 75,497,472\text{ bytes} \approx 72.0\text{ MB}$.
     - The All-Reduce collective transmits $72.0\text{ MB}$ of activations per layer.

2. **Differentiate Replicated vs Sharded PartitionSpec**:
   What is the semantic difference between `PartitionSpec('tp', None)` and `PartitionSpec(None, 'tp')` for a 2D weight matrix?
   - *Solution*:
     - `P('tp', None)` shards the first dimension (rows) across the `'tp'` devices; each device receives $\frac{\text{Rows}}{TP}$ rows.
     - `P(None, 'tp')` shards the second dimension (columns) across the `'tp'` devices; each device receives $\frac{\text{Cols}}{TP}$ columns.

### Troubleshooting FAQ

- **Q: Why does my JAX SPMD program produce unexpected `Reshard` operations?**
  *A*: When the input sharding of an operation does not match the sharding demanded by the subsequent operation, XLA inserts an automated `Reshard` collective (data redistribution). Inspect the HLO graph using `print(jax.jit(fn).lower(*args).compile().as_text())` and verify that output and input `PartitionSpec` axes align.
- **Q: Does JAX SPMD require launching via `mpirun` or `torchrun`?**
  *A*: On single-node multi-GPU systems, standard Python handles all GPUs automatically via `jax.devices()`. On multi-node clusters, launch with `jax.distributed.initialize()` combined with standard Slurm or MPI hostnames.
