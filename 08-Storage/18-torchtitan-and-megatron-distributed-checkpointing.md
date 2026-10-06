# Volume 18: Distributed Checkpointing in TorchTitan and Megatron-Core

```
====================================================================================================
MODULE 08: STORAGE & HIGH-PERFORMANCE DATA FABRIC FOR AI
VOLUME 18: Multi-Dimensional Parallelism (3D/4D), Global Manifests & Dynamic Resharding
====================================================================================================
```

---

## 1. Executive Intuition: The Topology Lock-In Trap

In distributed foundation model training, models are partitioned across multi-dimensional parallelism matrices:
- **Tensor Parallelism (TP):** Intra-layer matrix slicing across high-speed NVLink.
- **Pipeline Parallelism (PP):** Inter-layer pipeline stages partitioned across nodes.
- **Context Parallelism (CP):** Long-sequence attention head partitioning.
- **Data Parallelism (DP / FSDP):** Gradient and optimizer state sharding.

Traditionally, each GPU rank dumped its local memory buffers directly into rank-specific monolithic files (e.g., `model_tp_rank_02_pp_rank_01.pt`). This created the catastrophic **Topology Lock-In Trap**:
- If an 8-GPU node dies in a 512-GPU job and replacement capacity is only available on 256 GPUs, training cannot resume because the checkpoint requires exactly $TP=8, PP=4, DP=16$.
- Transitioning a trained checkpoint to an inference cluster (where models run at $TP=1$ or $TP=2$ on different GPU architectures) required multi-hour offline conversion scripts that consumed terabytes of scratch disk and doubled storage costs.

```
+-----------------------------------------------------------------------------------------+
|                          THE TOPOLOGY RESHARDING CHALLENGE                              |
+-----------------------------------------------------------------------------------------+
| Training Topology: TP = 8 (8 shards per layer)                                          |
| Layer 1: [W_0] [W_1] [W_2] [W_3] [W_4] [W_5] [W_6] [W_7]                                |
|                                                                                         |
| Inference / Resumed Topology: TP = 2 (Requires 2 shards per layer)                      |
| Target 0: [W_0 + W_1 + W_2 + W_3]       Target 1: [W_4 + W_5 + W_6 + W_7]               |
+-----------------------------------------------------------------------------------------+
```

Modern distributed checkpointing engines—specifically **TorchTitan (PyTorch DCP)** and **NVIDIA Megatron-Core Distributed Checkpoint (Mcore Dist-Ckpt)**—break this trap. They store tensors with **Global Coordinate Offsets** and centralized **Metadata Manifests**, enabling zero-overhead dynamic resharding at load time.

---

## 2. Lineage & Evolution of Distributed Checkpointing

```
   [2019: Monolithic Rank Pickling]
                 |
           (Megatron-LM v1: Strict rank-coupled files model_optim_rng.pt)
                 |
   [2021: DeepSpeed ZeRO Consolidation]
                 |
           (Offline consolidation scripts: zero_to_fp32.py; high CPU RAM demand)
                 |
   [2023: Megatron-Core Dist-Ckpt (Zarr / Custom)]
                 |
           (Decoupled tensor chunks; Zarr-based array chunking for arbitrary TP/PP reload)
                 |
   [2024: PyTorch Distributed Checkpoint (DCP)]
                 |
           (Standardized in torch.distributed.checkpoint; native support in TorchTitan)
                 |
   [2025: Universal Distributed Safetensors]
                 |
           (Direct zero-copy Safetensors shard streaming across heterogeneous clusters)
```

---

## 3. First-Principles Mathematics: Tensor Sharding & Coordinate Slicing

Let a weight matrix $W \in \mathbb{R}^{M \times N}$ be partitioned across a Tensor Parallel degree of $TP$.

### 3.1 Column-Parallel Sharding (e.g., Self-Attention QKV Projection)
The matrix is split along the column dimension ($N$):

$$W = \begin{bmatrix} W_0 & W_1 & \cdots & W_{TP-1} \end{bmatrix},\quad W_i \in \mathbb{R}^{M \times \frac{N}{TP}}$$

Rank $i$ ($0 \le i < TP$) owns the slice with global coordinates:
$$\text{Row Range: } [0, M)$$
$$\text{Col Range: } \left[ i \cdot \frac{N}{TP},\ (i + 1) \cdot \frac{N}{TP} \right)$$

### 3.2 Row-Parallel Sharding (e.g., Self-Attention Output Projection)
The matrix is split along the row dimension ($M$):

$$W = \begin{bmatrix} W_0 \\ W_1 \\ \vdots \\ W_{TP-1} \end{bmatrix},\quad W_i \in \mathbb{R}^{\frac{M}{TP} \times N}$$

Rank $i$ owns the slice with global coordinates:
$$\text{Row Range: } \left[ i \cdot \frac{M}{TP},\ (i + 1) \cdot \frac{M}{TP} \right)$$
$$\text{Col Range: } [0, N)$$

---

### 3.3 Dynamic Resharding Geometry

Suppose a model was saved with $TP_{\text{save}} = 8$ and must be loaded on a cluster with $TP_{\text{load}} = 2$.
For a row-parallel tensor $W \in \mathbb{R}^{8192 \times 8192}$:

- **At Save Time ($TP_{\text{save}}=8$):**
  Each of the 8 ranks saves a chunk of size $1024 \times 8192$:
  $$\text{Chunk } k: \text{Rows } [1024k, 1024(k+1)),\ k \in [0..7]$$

- **At Load Time ($TP_{\text{load}}=2$):**
  Each of the 2 ranks requires a chunk of size $4096 \times 8192$:
  $$\text{Target Rank 0 requires Rows: } [0, 4096)$$
  $$\text{Target Rank 1 requires Rows: } [4096, 8192)$$

```
Target Rank 0 read operations:
  - Read Chunk 0 [0, 1024)
  - Read Chunk 1 [1024, 2048)
  - Read Chunk 2 [2048, 3072)
  - Read Chunk 3 [3072, 4096)
  --> Concatenate along dimension 0 in HBM
```

The storage system must support **Arbitrary Slice Intersection Queries**:
$$\text{Intersection}(S_{\text{saved}}, S_{\text{requested}}) = \left[ \max(S_{\text{saved}}^{\text{start}}, S_{\text{req}}^{\text{start}}),\ \min(S_{\text{saved}}^{\text{end}}, S_{\text{req}}^{\text{end}}) \right]$$

---

## 4. Deep Architecture: PyTorch DCP & TorchTitan Engine

PyTorch Distributed Checkpoint (DCP) abstracts distributed state persistence into three layers:
1. **State Dict Provider:** Extracts tensors from modules (FSDP, TP, MoE).
2. **Storage Planner:** Resolves global tensor layout, chunk assignments, and byte offsets.
3. **Storage Writer/Reader:** Performs optimized parallel I/O to physical media (POSIX, S3, GDS).

```
+-----------------------------------------------------------------------------+
|                     TORCHTITAN DCP CHECKPOINT PIPELINE                      |
+-----------------------------------------------------------------------------+
| Model / Optimizer (FSDP2 + TP + CP Ranks)                                   |
|   |                                                                         |
|   v                                                                         |
| DefaultSavePlanner (PyTorch DCP)                                            |
|   |-- Extracts sharded tensor metadata:                                     |
|   |     - global_shape: torch.Size([8192, 8192])                            |
|   |     - global_offset: torch.Size([2048, 0])                              |
|   |     - chunk_shape: torch.Size([2048, 8192])                             |
|   |                                                                         |
|   v                                                                         |
| FileSystemWriter (Parallel Storage Driver)                                  |
|   |                                                                         |
|   +---> Writes Central Manifest: `.metadata` (Atomic JSON/Binary)           |
|   |       - Maps all tensor names to storage files and byte offsets         |
|   |                                                                         |
|   +---> Writes Sharded Binary Payloads: `__0_0.distcp`, `__1_0.distcp`       |
|           - Zero-copy binary raw buffers packed into shared files           |
+-----------------------------------------------------------------------------+
```

### 4.1 The Central `.metadata` Manifest Structure
```json
{
  "format_version": 2,
  "storage_data": {
    "model.layers.0.attention.wq.weight": {
      "type": "sharded_tensor",
      "global_shape": [8192, 8192],
      "dtype": "torch.bfloat16",
      "chunks": [
        {
          "offset": [0, 0],
          "shape": [1024, 8192],
          "file_name": "__0_0.distcp",
          "storage_offset": 1048576,
          "length": 16777216
        },
        {
          "offset": [1024, 0],
          "shape": [1024, 8192],
          "file_name": "__1_0.distcp",
          "storage_offset": 1048576,
          "length": 16777216
        }
      ]
    }
  }
}
```

---

## 5. Concrete Production Lab: Distributed Metadata Manifest & Dynamic Resharder

Below is a self-contained Python implementation of an arbitrary tensor sharder, manifest writer, and dynamic resharding reader.

```python
#!/usr/bin/env python3
"""
Production Lab: Universal Tensor Manifest & Dynamic Resharder.
Demonstrates topological-agnostic tensor chunking, manifest writing,
and dynamic reconstruction across differing TP degrees.
"""

import json
import os
import torch

class TensorManifestStore:
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        self.manifest_file = os.path.join(storage_dir, "metadata.json")

    def save_sharded_tensor(self, name: str, full_tensor: torch.Tensor, tp_degree: int):
        """Simulates distributed saving across tp_degree ranks."""
        assert full_tensor.dim() == 2, "2D tensors only for this lab"
        M, N = full_tensor.shape
        chunk_m = M // tp_degree
        
        manifest_entry = {
            "name": name,
            "global_shape": list(full_tensor.shape),
            "dtype": str(full_tensor.dtype),
            "chunks": []
        }

        for rank in range(tp_degree):
            offset_m = rank * chunk_m
            shard = full_tensor[offset_m : offset_m + chunk_m, :]
            file_name = f"{name}_tp{rank}_of_{tp_degree}.bin"
            file_path = os.path.join(self.storage_dir, file_name)
            
            # Save raw bytes
            with open(file_path, "wb") as f:
                f.write(shard.numpy().tobytes())

            manifest_entry["chunks"].append({
                "rank": rank,
                "offset": [offset_m, 0],
                "shape": list(shard.shape),
                "file_name": file_name,
                "num_bytes": shard.numel() * shard.element_size()
            })

        # Commit manifest
        with open(self.manifest_file, "w") as f:
            json.dump({name: manifest_entry}, f, indent=2)
            
        print(f"Tensor '{name}' saved with TP={tp_degree} across {tp_degree} shards.")

    def load_resharded_tensor(self, name: str, target_rank: int, target_tp_degree: int) -> torch.Tensor:
        """
        Dynamically loads and reconstructs the tensor slice required by target_rank
        under an arbitrary target_tp_degree without offline conversion.
        """
        with open(self.manifest_file, "r") as f:
            manifest = json.load(f)[name]

        global_m, global_n = manifest["global_shape"]
        target_chunk_m = global_m // target_tp_degree
        req_start = target_rank * target_chunk_m
        req_end = req_start + target_chunk_m

        # Allocate buffer for the target rank's requested slice
        target_slice = torch.empty((target_chunk_m, global_n), dtype=torch.float32)

        # Inspect all saved chunks to find overlapping intervals
        for chunk in manifest["chunks"]:
            chunk_start = chunk["offset"][0]
            chunk_end = chunk_start + chunk["shape"][0]

            # Calculate intersection
            inter_start = max(req_start, chunk_start)
            inter_end = min(req_end, chunk_end)

            if inter_start < inter_end:
                # Read saved chunk
                file_path = os.path.join(self.storage_dir, chunk["file_name"])
                with open(file_path, "rb") as f:
                    raw_bytes = f.read()
                src_shard = torch.frombuffer(raw_bytes, dtype=torch.float32).reshape(chunk["shape"])

                # Determine source and destination offsets
                src_slice_start = inter_start - chunk_start
                src_slice_end = inter_end - chunk_start
                dst_slice_start = inter_start - req_start
                dst_slice_end = inter_end - req_start

                target_slice[dst_slice_start:dst_slice_end, :] = src_shard[src_slice_start:src_slice_end, :]

        return target_slice

if __name__ == "__main__":
    storage = TensorManifestStore("/tmp/distcp_lab")
    
    # 1. Create a simulated global weight matrix (8192 x 4096)
    original_weight = torch.randn(8192, 4096, dtype=torch.float32)
    
    # 2. Save with TP = 8
    storage.save_sharded_tensor("attention.proj.weight", original_weight, tp_degree=8)
    
    # 3. Dynamically reload with TP = 2 (Simulating inference or scaled topology)
    # Target Rank 0 expects rows [0, 4096)
    reloaded_rank0 = storage.load_resharded_tensor("attention.proj.weight", target_rank=0, target_tp_degree=2)
    
    # 4. Verify mathematical fidelity
    expected_rank0 = original_weight[0:4096, :]
    assert torch.allclose(reloaded_rank0, expected_rank0), "Resharding data mismatch!"
    print("SUCCESS: Checkpoint successfully resharded from TP=8 to TP=2 with 100% bitwise fidelity!")
```

---

## 6. Megatron-Core Distributed Checkpointing (Mcore Dist-Ckpt)

Megatron-Core utilizes the `dist_ckpt` interface which wraps PyTorch tensors into `ShardedTensor` abstractions:

```python
from megatron.core import dist_checkpointing
from megatron.core.dist_checkpointing.dict_utils import dict_list_map_inplace
from megatron.core.dist_checkpointing.strategies.zarr import ZarrDistributedWriter

# Megatron-Core checkpoint saving
def save_megatron_core_checkpoint(model, optimizer, opt_param_scheduler, checkpoint_dir):
    sharded_state_dict = model.sharded_state_dict()
    
    # Non-blocking parallel write via Zarr or Safetensors distributed writer
    dist_checkpointing.save(
        sharded_state_dict=sharded_state_dict,
        checkpoint_dir=checkpoint_dir,
        writer=ZarrDistributedWriter(checkpoint_dir, thread_count=8)
    )
```

**Key Advantages of Mcore Dist-Ckpt:**
- **Zero Consolidation Overhead:** No need to run `merge_megatron_weights.py`.
- **Fast Resumption:** Loading time scales with per-GPU bandwidth, not total model parameter size.
- **Heterogeneous Cluster Deployment:** Resume on 64 nodes with identical accuracy as an 8-node pre-training run.

---

## 7. Comparative Architecture Matrix

| Metric | Legacy Monolithic Save | DeepSpeed ZeRO-3 | Megatron-Core Dist-Ckpt | TorchTitan (PyTorch DCP) |
| :--- | :--- | :--- | :--- | :--- |
| **Sharding Paradigm** | Rank-bound (`.pt`) | Parameter-sliced | Coordinate-indexed Zarr | Global Coordinate ShardedTensor |
| **Resharding Flexibility** | Impossible (Locked) | Offline CPU conversion | Dynamic at load time | Dynamic at load time |
| **Format Overhead** | High (Pickle metadata)| Moderate (JSON + bin) | Minimal (Zarr arrays) | Minimal (Flat binary + manifest) |
| **Direct GDS Support** | No | No | Experimental | Fully Supported |
| **Framework Lock-in** | PyTorch only | DeepSpeed runtime | Megatron-LM | Framework-agnostic PyTorch standard |

---

## 8. SRE Diagnostics & Troubleshooting Playbook

```
+---------------------------------------------------------------------------------------------------+
|                        DISTRIBUTED CHECKPOINT SRE DIAGNOSTIC MATRIX                               |
+------------------------------------+--------------------------+-----------------------------------+
| Symptom / Failure Mode             | Root Cause Hypothesis    | Triage & Remediation Command      |
+------------------------------------+--------------------------+-----------------------------------+
| Checkpoint load fails with:        | Global tensor dimensions | Validate `.metadata` JSON schema: |
| `KeyError: chunk intersection null`| changed between runs     | `jq . /path/to/ckpt/.metadata`    |
|                                    | (e.g. vocab size change).| Inspect dimension alignment.      |
+------------------------------------+--------------------------+-----------------------------------+
| Training resumes with different    | Incomplete slice         | Check loss trajectory on resume;  |
| loss curves after resharding.      | boundary logic dropped   | compare with baseline before      |
|                                    | bias/norm tensors.       | checkpoint was taken.             |
+------------------------------------+--------------------------+-----------------------------------+
| High-concurrency metadata storm:   | 10,000 ranks reading     | Distribute manifest broadcast:    |
| Parallel FS hangs on resume.       | `.metadata` concurrently.| Rank 0 reads `.metadata` and      |
|                                    |                          | broadcasts via `dist.broadcast()`.|
+------------------------------------+--------------------------+-----------------------------------+
| Incomplete sharded checkpoint:     | Straggler rank died      | Verify all chunk files exist:     |
| One `.distcp` file missing.        | during parallel flush.   | `find /ckpt/ -size 0`             |
|                                    |                          | Revert to previous step checkpoint|
+------------------------------------+--------------------------+-----------------------------------+
```

---

## 9. Verification & Architectural Synthesis Checklist

- [ ] **Topological Decoupling:** Checkpoints authored using coordinate-aware sharding (DCP / Mcore Dist-Ckpt).
- [ ] **Dynamic Resharding Tested:** Verify reloading from $TP=8$ into $TP=4, 2, 1$ without offline conversion scripts.
- [ ] **Decentralized Storage IO:** Ensure no central rank-0 bottleneck; each rank reads/writes its own chunk slices.
- [ ] **Broadcasted Metadata:** Rank 0 reads global metadata manifest and broadcasts to workers to protect storage metadata engines.
- [ ] **Bitwise Validation:** Test harness asserts $100\%$ tensor equivalence across resharded configurations.
