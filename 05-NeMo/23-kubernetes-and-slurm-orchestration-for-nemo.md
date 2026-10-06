# Volume 23: Kubernetes and Slurm Orchestration for NeMo

```
==================================================================================================
TARGET AUDIENCE: HPC Cluster Administrators, Cloud Infrastructure Leads, Supercomputing Architects
PREREQUISITES   : Slurm sbatch/srun, Kubernetes Custom Resource Definitions (CRDs), MPI, InfiniBand
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Orchestrate multi-node Megatron-Core and NeMo workloads at scale using Slurm (Pyxis/Enroot)
                  and Kubernetes (Kueue, Volcano, MPI-Operator) with topology-aware scheduling.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Training and fine-tuning multi-hundred-billion parameter models (such as `Nemotron-4 340B`) requires orchestrating dozens or hundreds of GPU nodes over high-speed InfiniBand/RoCE fabrics. In distributed deep learning, standard single-container scheduling paradigms fail:
1. **The Distributed Deadlock Problem**: If 63 out of 64 required pods start, but Pod 64 is stuck waiting for resources, all 63 pods idle indefinitely, burning \$10,000s in wasted compute (**The Gang Scheduling Mandate**).
2. **Topology Awareness**: Slicing Tensor Parallel ranks across different network switches destroys all-reduce bandwidth. TP ranks must reside on the same NVLink domain, while PP/DP ranks must map to shared leaf switches.

This volume covers both enterprise supercomputing paradigms: **HPC Slurm with Pyxis/Enroot** and **Cloud-Native Kubernetes with Kueue and Volcano**.

```
                         [Distributed NeMo Training Job]
                                        │
           ┌────────────────────────────┴────────────────────────────┐
           ▼                                                         ▼
┌───────────────────────────────────────┐ ┌───────────────────────────────────────┐
│     HPC Slurm Orchestration           │ │    Cloud-Native Kubernetes (K8s)      │
│  - Pyxis / Enroot Container Runtime   │ │  - Volcano / Kueue Gang Scheduler     │
│  - #SBATCH Topology-Aware Placement   │ │  - MPI-Operator / KubeRay CRDs        │
│  - Direct InfiniBand SRUN Dispatch    │ │  - NodeAffinity & RDMA Device Plugins │
└──────────────────┬────────────────────┘ └──────────────────┬────────────────────┘
                   │                                         │
                   └────────────────────┬────────────────────┘
                                        │
                                        ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Topology-Aware Hardware Placement Matrix                    │
       │   - Tensor Parallel (TP=8): Pinned to Intra-Node NVLink       │
       │   - Pipeline Parallel (PP=4): Pinned to Same Leaf Switch      │
       │   - Data Parallel (DP): Distributed across Core Spines        │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Symphony Orchestra vs. The Commuter Bus
3. Evolutionary Lineage: From Ad-Hoc Shell Scripts to Gang-Scheduled AI Fabric
4. First-Principles Mathematics & Algorithmic Formulations
   - Gang Scheduling (All-or-Nothing) State Transitions
   - Network Bisection Bandwidth & Topology Tree Penalty
   - InfiniBand GPUDirect RDMA Environment Geometry
5. Comparative Trade-Off Matrix: Slurm vs Kubernetes
6. Concrete Production Hands-On Lab: Topology-Aware Gang Scheduler Simulator
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Symphony Orchestra vs. The Commuter Bus

Imagine organizing a public service vs. an orchestra concert:

- **The Commuter Bus Approach (Standard Kubernetes Scheduling)**:
  Passengers get on the bus whenever they arrive at the bus stop. If 5 passengers arrive, the bus drives. If 1 passenger is missing, nobody cares; the bus still runs. This works for microservices (web servers, databases) because each container is independent.

- **The Symphony Orchestra Approach (Gang-Scheduled Slurm / Volcano)**:
  You are performing Beethoven's 9th Symphony with 100 musicians (100 GPU nodes).
  If 99 musicians are in their seats on stage, but the solo cellist is stuck in traffic, **the symphony cannot begin**. If the conductor raises the baton anyway, the concert is ruined.
  **Gang Scheduling enforces all-or-nothing admission**: Either all 100 musicians are on stage with their instruments tuned (**All-or-Nothing Gang Allocation**), or the concert start time is held in queue.
  Furthermore, the violins must sit next to each other so they can hear each other without acoustic delay (**Topology-Aware Placement**).

---

## 3. Evolutionary Lineage: From Ad-Hoc Shell Scripts to Gang-Scheduled AI Fabric

```
Generation 1 (2015-2018)      Generation 2 (2018-2022)      Generation 3 (2023-2026)
Manual SSH Loops & Hostfiles  Standard Slurm & Basic K8s    Cloud-Native AI Fabric (Kueue/Enroot)
──────────────────────────    ──────────────────────────    ─────────────────────────────────────
- Manual bash loops           - Slurm sbatch script queues  - Volcano / Kueue gang scheduling
- Uncontained bare-metal      - Containerized Pyxis/Enroot  - Automated network topology packing
- Failed nodes hung jobs      - Partial K8s pod deadlock    - Dynamic fault recovery & restart
- No network topology awareness- High operational friction  - Unified hybrid Cloud + Slurm HPC
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Gang Scheduling (All-or-Nothing) State Transitions

Let a distributed training job $\mathcal{J}$ require $N$ compute pods.
Under traditional schedulers, pod allocation is incremental:

$$P_{\text{allocated}}(t) = P_{\text{allocated}}(t-1) + \Delta P$$

If $\Delta P < N$ and cluster resources become exhausted, $\mathcal{J}$ enters a **Partial Allocation Deadlock**.
Gang scheduling defines an atomic admission gate:

$$\text{Schedule}(\mathcal{J}) = \begin{cases} \text{DISPATCH ALL } N \text{ PODS} & \text{if } \text{AvailableNodes} \ge N \\ \text{HOLD IN QUEUE} & \text{if } \text{AvailableNodes} < N \end{cases}$$

This guarantees zero GPU resource stranding.

### Network Bisection Bandwidth & Topology Tree Penalty

In multi-node clusters, inter-node communication latency is governed by network tree depth.
Let the communication cost between node $u$ and node $v$ be:

$$C(u, v) = \alpha + \beta \cdot d(u, v)$$

Where:
- $\alpha$ is base InfiniBand latency ($\approx 1.2\ \mu\text{s}$).
- $d(u, v)$ is the tree distance (number of network switch hops).
- $d(u, v) = 0$: Same node (NVLink: 900 GB/s, latency $< 0.1\ \mu\text{s}$).
- $d(u, v) = 2$: Same leaf switch (InfiniBand NDR: 400 Gbps, latency $\approx 1.5\ \mu\text{s}$).
- $d(u, v) = 4$: Traversing spine switch (Oversubscription penalty, latency $\approx 4.5\ \mu\text{s}$).

Topology-aware schedulers solve an integer linear program minimizing total hop cost:

$$\min_{\mathcal{M}} \sum_{(u, v) \in \mathcal{E}_{\text{NCCL}}} C(\mathcal{M}(u), \mathcal{M}(v))$$

Where $\mathcal{E}_{\text{NCCL}}$ is the communication graph of the distributed model.

---

## 5. Comparative Trade-Off Matrix: Slurm vs Kubernetes

| Dimension | HPC Slurm (Pyxis / Enroot) | Kubernetes with Kueue / Volcano |
| :--- | :--- | :--- |
| **Primary Industry** | Supercomputing Centers, National Labs | Enterprise Cloud, FinTech, Web-Scale |
| **Container Runtime** | NVIDIA Enroot (Zero daemon overhead) | Containerd / Docker Engine |
| **Gang Scheduling** | Native (`#SBATCH --nodes=N`) | Custom Controller (Kueue / Volcano CRD)|
| **Topology Awareness** | Native (`#SBATCH --topology=tree`) | Requires Topology-Aware Scheduling Plugin|
| **Multi-Tenancy** | Fair-share user queues | Namespaces, ResourceQuotas, RBAC |
| **DGX Spark Deployment** | **Standard in HPC DGX Clusters** | **Standard in Enterprise Cloud DGX** |

---

## 6. Concrete Production Hands-On Lab: Topology-Aware Gang Scheduler Simulator

This self-contained Python script implements:
1. A multi-node network topology tree (Leaf Switches and Compute Nodes).
2. Gang scheduling logic with all-or-nothing admission.
3. Topology-aware node packing to minimize network hops for Tensor Parallel groups.

```python
#!/usr/bin/env python3
"""
Topology-Aware Gang Scheduler Simulator for NeMo Clusters.
Demonstrates all-or-nothing job admission and locality-optimized node placement.
"""

from typing import List, Dict, Optional, Tuple

class ComputeNode:
    def __init__(self, node_id: str, switch_id: str, total_gpus: int = 1):
        self.node_id = node_id
        self.switch_id = switch_id
        self.total_gpus = total_gpus
        self.free_gpus = total_gpus

class DistributedJob:
    def __init__(self, job_id: str, required_gpus: int, tp_degree: int):
        self.job_id = job_id
        self.required_gpus = required_gpus
        self.tp_degree = tp_degree
        self.allocated_nodes: List[str] = []

class TopologyGangScheduler:
    def __init__(self, nodes: List[ComputeNode]):
        self.nodes = {n.node_id: n for n in nodes}
        # Map switch_id -> list of node_ids
        self.switches: Dict[str, List[str]] = {}
        for n in nodes:
            self.switches.setdefault(n.switch_id, []).append(n.node_id)

    def schedule_job(self, job: DistributedJob) -> bool:
        """
        Attempts atomic gang allocation.
        Prefers packing all GPUs under the same leaf switch to minimize network hops.
        """
        total_available = sum(n.free_gpus for n in self.nodes.values())
        if total_available < job.required_gpus:
            print(f"[GANG REJECT] Job '{job.job_id}' requires {job.required_gpus} GPUs, but only {total_available} free.")
            return False

        # Strategy 1: Attempt to fit under a single leaf switch
        for switch_id, node_ids in self.switches.items():
            switch_free = sum(self.nodes[nid].free_gpus for nid in node_ids)
            if switch_free >= job.required_gpus:
                print(f"[PLACEMENT SUCCESS] Pinned Job '{job.job_id}' to Leaf Switch '{switch_id}' (Optimal Topology)!")
                self._commit_allocation(job, node_ids)
                return True

        # Strategy 2: Multi-switch fallback (Cross-spine communication)
        print(f"[PLACEMENT FALLBACK] Job '{job.job_id}' spans multiple switches (Higher latency).")
        all_node_ids = list(self.nodes.keys())
        self._commit_allocation(job, all_node_ids)
        return True

    def _commit_allocation(self, job: DistributedJob, candidate_node_ids: List[str]):
        needed = job.required_gpus
        for nid in candidate_node_ids:
            node = self.nodes[nid]
            if node.free_gpus > 0:
                take = min(node.free_gpus, needed)
                node.free_gpus -= take
                needed -= take
                job.allocated_nodes.append(f"{nid} ({take} GPUs)")
                if needed == 0:
                    break

# =====================================================================
# VERIFICATION HARNESS
# =====================================================================

def run_scheduler_lab():
    print("=" * 80)
    print("NVIDIA NEMO TOPOLOGY-AWARE GANG SCHEDULER SIMULATION")
    print("=" * 80)

    # 4 Nodes distributed across 2 Leaf Switches
    cluster_nodes = [
        ComputeNode("dgx-spark-01", switch_id="leaf-sw-A", total_gpus=1),
        ComputeNode("dgx-spark-02", switch_id="leaf-sw-A", total_gpus=1),
        ComputeNode("dgx-spark-03", switch_id="leaf-sw-B", total_gpus=1),
        ComputeNode("dgx-spark-04", switch_id="leaf-sw-B", total_gpus=1),
    ]

    scheduler = TopologyGangScheduler(cluster_nodes)

    # Job 1: 2 GPUs (Should fit completely on leaf-sw-A)
    job1 = DistributedJob("NeMo-Pretrain-Job-01", required_gpus=2, tp_degree=2)
    s1 = scheduler.schedule_job(job1)
    print(f"  Allocated Nodes: {job1.allocated_nodes}\n")

    # Job 2: 4 GPUs (Cannot fit, requires all remaining 2 GPUs + 2 more -> GANG REJECT!)
    job2 = DistributedJob("Nemotron-340B-Job-02", required_gpus=4, tp_degree=4)
    s2 = scheduler.schedule_job(job2)
    assert not s2, "Job 2 should have been rejected by gang admission control!"

    # Job 3: 2 GPUs (Fits on leaf-sw-B)
    job3 = DistributedJob("FineTune-SFT-Job-03", required_gpus=2, tp_degree=2)
    s3 = scheduler.schedule_job(job3)
    print(f"  Allocated Nodes: {job3.allocated_nodes}\n")

    assert s1 and s3
    print("[SUCCESS] All-or-nothing gang scheduling and topology tree placement verified.")

if __name__ == "__main__":
    run_scheduler_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Production cluster job scripts:

### 1. Slurm Batch Script (`submit_nemo_job.sh`)
```bash
#!/bin/bash
#SBATCH --job-name=nemo_megatron_pretrain
#SBATCH --nodes=4
#SBATCH --gpus-per-node=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --topology=tree
#SBATCH --exclusive

export NCCL_DEBUG=INFO
export NCCL_IB_HCA=mlx5_0:1,mlx5_1:1
export NCCL_NET_GDR_LEVEL=5
export TORCH_DISTRIBUTED_DEBUG=DETAIL

srun --container-image=nvcr.io/nvidia/nemo:24.09 \
     --container-mounts=/mnt/nvme/data:/workspace/data,/mnt/nvme/models:/workspace/models \
     python -m nemo.collections.nlp.models.language_modeling.megatron_gpt_pretraining \
     --config-path=/workspace/models \
     --config-name=megatron_gpt_config
```

### 2. Kubernetes Volcano Gang Job (`volcano_nemo_job.yaml`)
```yaml
apiVersion: batch.volcano.sh/v1alpha1
kind: Job
metadata:
  name: nemo-distributed-sft
spec:
  minAvailable: 4
  schedulerName: volcano
  tasks:
    - replicas: 4
      name: worker
      template:
        spec:
          nodeSelector:
            nvidia.com/gpu.product: Blackwell-GB10
          containers:
            - name: nemo-worker
              image: nvcr.io/nvidia/nemo:24.09
              resources:
                limits:
                  nvidia.com/gpu: "1"
                  memory: "110Gi"
```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (InfiniBand GDR Check)**:
   Explain what `NCCL_NET_GDR_LEVEL=5` achieves. What happens if this is set on a system without GPUDirect RDMA kernel modules?

2. **Exercise 2 (Kueue Cohort Allocation)**:
   Author a Kubernetes `Kueue` ResourceFlavor manifest that defines a shared cluster quota between the "Research" cohort and the "Production" cohort with preemption rules.

### Solutions

**Solution for Exercise 1**:
`NCCL_NET_GDR_LEVEL=5` enables maximum GPUDirect RDMA: allowing network cards (ConnectX-7/8) to directly read and write GPU High-Bandwidth Memory across the PCIe/NVLink bus without bouncing data through host CPU memory buffers. If set without GPUDirect kernel modules (`nvidia-peermem`), NCCL logs a warning and falls back to socket-based memory copies.

### Troubleshooting FAQ

- **Q: Distributed jobs hang indefinitely with zero CPU or GPU utilization.**
  - *Fix*: You suffered a Gang Deadlock. The job dispatched without all ranks running. Ensure your Slurm script uses `#SBATCH --wait-all-nodes=1` or your Kubernetes manifest uses Volcano/Kueue with `minAvailable` matching total replicas.

- **Q: Multi-node training runs at 10% of expected speed.**
  - *Fix*: NCCL is routing over standard 1 Gbps Ethernet instead of InfiniBand. Force NCCL to bind to high-speed RDMA adapters: `export NCCL_SOCKET_IFNAME=ib0` or `export NCCL_IB_DISABLE=0`.
