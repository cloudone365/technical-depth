# DGX Spark Documentation

Welcome to the **DGX Spark** project documentation.

## Project Structure (01 to 06 Modular Layout)

```text
DGX Spark/
├── dgx-ansible/               # Ansible automation & hardware telemetry
│   ├── ansible.cfg            # Ansible production configuration
│   ├── inventory.ini          # Cluster inventory & host definitions
│   ├── spark-health.yml       # Health check playbook (GPU inspection)
│   ├── gpu-monitor.yml        # GB10 GPU & unified-memory monitoring & alerting
│   └── docs/                  # Ansible-specific documentation
│       └── README.md          # Ansible setup & operational guide
├── 01Ansible/                 # [01] Ansible Core, Tower/AWX & HashiCorp Vault (3 Volumes + Guides)
├── 02 Kubernetes/             # [02] Kubernetes & Hyperscaler AI Infrastructure (26 Volumes)
├── 03 DeepSeek/               # [03] DeepSeek & Frontier AI Architecture Curriculum (41 Volumes)
├── 04 Qwen/                   # [04] Alibaba Qwen Ecosystem & ms-swift Mastery (25 Volumes)
├── 05 NeMo/                   # [05] NVIDIA NeMo & Nemotron Ecosystem Mastery (25 Volumes)
├── 06 Gemma/                  # [06] Google Gemma & JAX/XLA MaxText Mastery (25 Volumes)
├── 07 Nvidia/                 # [07] NVIDIA Hardware, Silicon Interlinks & Low-Level Systems (30 Volumes)
├── 08 Storage/                # [08] Storage & High-Performance Data Fabric for AI (25 Volumes)
├── NVDIA HARDWARE and SOFTWARE STACK.txt
└── README.md                  # Master documentation entrypoint
```

---

## 🚀 [02] NVIDIA AI Infrastructure & Kubernetes Mastery (26 Volumes)

The complete curriculum is indexed in [**02 Kubernetes/README.md**](02%20Kubernetes/README.md).

### Part I: Control Plane & Core Architecture Internals
| # | Guide | File |
| :--- | :--- | :--- |
| 01 | **Core Architecture & Pod Lifecycle** | [01-kubernetes-core-architecture.md](02%20Kubernetes/01-kubernetes-core-architecture.md) |
| 02 | **Kube-API Server Internals (Auth, RBAC, Webhooks, APF)** | [02-kube-apiserver-internals.md](02%20Kubernetes/02-kube-apiserver-internals.md) |
| 03 | **ETCD Database Deep Dive (Raft, WAL, bbolt, Recovery)** | [03-etcd-database-deep-dive.md](02%20Kubernetes/03-etcd-database-deep-dive.md) |
| 04 | **Controller Manager & Informers (Reconciliation Loops)** | [04-kube-controller-manager-and-controllers.md](02%20Kubernetes/04-kube-controller-manager-and-controllers.md) |
| 05 | **Scheduler & AI Batch Scheduling (Taints, Kueue, Gang)** | [05-kube-scheduler-and-ai-batch-scheduling.md](02%20Kubernetes/05-kube-scheduler-and-ai-batch-scheduling.md) |

### Part II: Deep Networking, Ingress, DNS & Discovery
| # | Guide | File |
| :--- | :--- | :--- |
| 06 | **Networking Deep Dive & CNI (Flannel, Calico, Cilium, Multus)** | [06-kubernetes-networking-deep-dive.md](02%20Kubernetes/06-kubernetes-networking-deep-dive.md) |
| 07 | **Kube-Proxy & ClusterIP (iptables, IPVS, Headless Services)** | [07-kube-proxy-and-cluster-ip-mechanics.md](02%20Kubernetes/07-kube-proxy-and-cluster-ip-mechanics.md) |
| 08 | **CoreDNS & Service Discovery (ndots:5 Latency Bug)** | [08-coredns-and-service-discovery.md](02%20Kubernetes/08-coredns-and-service-discovery.md) |
| 09 | **Ingress Controllers & Gateway API (LLM Streaming, gRPC)** | [09-ingress-controllers-and-gateway-api.md](02%20Kubernetes/09-ingress-controllers-and-gateway-api.md) |

### Part III: Workloads, Storage & Multi-Tenancy
| # | Guide | File |
| :--- | :--- | :--- |
| 10 | **Advanced Workloads (StatefulSets, DaemonSets, Indexed Jobs)** | [10-advanced-workload-controllers.md](02%20Kubernetes/10-advanced-workload-controllers.md) |
| 11 | **Storage, CSI & High-IOPS Volumes (Local Path, NVMe, GDS)** | [11-storage-csi-and-high-performance-volumes.md](02%20Kubernetes/11-storage-csi-and-high-performance-volumes.md) |
| 12 | **Multi-Tenancy & cgroups v2 (Strict 5% Resource Math)** | [12-multi-tenancy-resource-quotas-and-cgroups.md](02%20Kubernetes/12-multi-tenancy-resource-quotas-and-cgroups.md) |

### Part IV: NVIDIA Hardware, Drivers & Hands-On Lab
| # | Guide | File |
| :--- | :--- | :--- |
| 13 | **NVIDIA Hardware & Drivers (Grace Blackwell GB10, NVLink-C2C)** | [13-nvidia-hardware-and-driver-stack.md](02%20Kubernetes/13-nvidia-hardware-and-driver-stack.md) |
| 14 | **Container Toolkit & Virtualization (CDI, Time-Slicing, MIG)** | [14-nvidia-container-toolkit-and-gpu-virtualization.md](02%20Kubernetes/14-nvidia-container-toolkit-and-gpu-virtualization.md) |
| 15 | **DGX Spark Data Center Simulation Lab (Dual K3s, 5% Quotas)** | [15-dgx-spark-datacenter-simulation-lab.md](02%20Kubernetes/15-dgx-spark-datacenter-simulation-lab.md) |
| 16 | **NVIDIA GPU Operator & Network Operator (Helm, GFD, DCGM)** | [16-nvidia-gpu-operator-and-network-operator.md](02%20Kubernetes/16-nvidia-gpu-operator-and-network-operator.md) |

### Part V: Large-Scale Distributed AI & Production Diagnostics
| # | Guide | File |
| :--- | :--- | :--- |
| 17 | **Distributed AI Training & NCCL (DDP, FSDP, GPUDirect RDMA)** | [17-distributed-ai-training-and-nccl.md](02%20Kubernetes/17-distributed-ai-training-and-nccl.md) |
| 18 | **Large-Scale SuperPOD & Network Fabrics (InfiniBand, Clos)** | [18-large-scale-superpod-and-network-fabrics.md](02%20Kubernetes/18-large-scale-superpod-and-network-fabrics.md) |
| 19 | **Cluster Diagnostics & Failure Playbook (etcd, Xid Matrix)** | [19-cluster-diagnostics-and-failure-scenarios.md](02%20Kubernetes/19-cluster-diagnostics-and-failure-scenarios.md) |
| 20 | **20 Hands-On Practice Exercises & Mastery Workbook** | [20-hands-on-practice-exercises-workbook.md](02%20Kubernetes/20-hands-on-practice-exercises-workbook.md) |

### Part VI: Production AI Inference, LLM Serving & Model Runtimes
| # | Guide | File |
| :--- | :--- | :--- |
| 21 | **vLLM High-Throughput Serving (PagedAttention, KV Cache, HPA)** | [21-vllm-high-throughput-llm-serving.md](02%20Kubernetes/21-vllm-high-throughput-llm-serving.md) |
| 22 | **NVIDIA Triton Inference Server (Dynamic Batching, Ensembles, gRPC)** | [22-nvidia-triton-inference-server.md](02%20Kubernetes/22-nvidia-triton-inference-server.md) |
| 23 | **LLM Alternatives & KServe (TensorRT-LLM, TGI, SGLang, Ray Serve)** | [23-llm-inference-alternatives-and-kserve.md](02%20Kubernetes/23-llm-inference-alternatives-and-kserve.md) |

### Part VII: Hyperscaler Mega-Scale & Multi-Accelerator Infrastructure
| # | Guide | File |
| :--- | :--- | :--- |
| 24 | **Disaggregated Prefill & Decode Serving (PD Separation, RDMA)** | [24-disaggregated-prefill-and-decode-serving.md](02%20Kubernetes/24-disaggregated-prefill-and-decode-serving.md) |
| 25 | **Hyperscaler Silicon & Compilers (TPU, Trainium vs Blackwell)** | [25-hyperscaler-silicon-and-compilers.md](02%20Kubernetes/25-hyperscaler-silicon-and-compilers.md) |
| 26 | **Ultra-Scale Cluster Resilience & SDC (10k-100k Accelerators)** | [26-ultra-scale-cluster-resilience-and-fault-tolerance.md](02%20Kubernetes/26-ultra-scale-cluster-resilience-and-fault-tolerance.md) |

---

## 🧠 [03] DeepSeek & Frontier AI Architecture Curriculum (41 Volumes)

The complete 41-volume curriculum is indexed in [**03 DeepSeek/README.md**](03%20DeepSeek/README.md).

| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 01 | **Multi-Head Latent Attention (MLA)** | [01-multi-head-latent-attention-mla.md](03%20DeepSeek/01-multi-head-latent-attention-mla.md) | Low-rank joint KV compression, RoPE decoupling, 93% KV cache reduction. |
| 02 | **DeepSeekMoE Architecture** | [02-deepseek-moe-fine-grained-routing.md](03%20DeepSeek/02-deepseek-moe-fine-grained-routing.md) | 256 fine-grained micro-experts, isolated shared experts, auxiliary-loss-free bias balancing. |
| 05 | **DeepSeek-R1 & GRPO Reasoning** | [05-deepseek-r1-and-grpo-reasoning.md](03%20DeepSeek/05-deepseek-r1-and-grpo-reasoning.md) | Group Relative Policy Optimization, zero-critic RL, emergent `<think>` tokens. |
| 08 | **Context Parallelism & Long-Context Attention** | [08-context-parallelism-and-long-context-attention.md](03%20DeepSeek/08-context-parallelism-and-long-context-attention.md) | 5D Parallelism, RingAttention ring shifts, DeepSpeed Ulysses, 1M+ context windows. |
| 25 | **Distributed RL Rollout Infrastructure** | [25-distributed-rl-rollout-infrastructure.md](03%20DeepSeek/25-distributed-rl-rollout-infrastructure.md) | The Actor-Rollout-Learner loop at scale, vLLM rollout engines, code sandboxes, and Ray orchestration. |
| 41 | **Multi-Ecosystem Local Deployment** | [41-multi-ecosystem-qwen-llama-nemo-deployment.md](03%20DeepSeek/41-multi-ecosystem-qwen-llama-nemo-deployment.md) | Complete local runbooks for Alibaba Qwen 2.5 (SWIFT), Meta Llama 3.3, and NVIDIA NeMo on DGX Spark. |

---

## 🌐 [04] Alibaba Qwen Ecosystem & ms-swift Mastery (25 Volumes)

The complete 25-volume curriculum is indexed in [**04 Qwen/README.md**](04%20Qwen/README.md).

| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 01 | **Qwen Ecosystem Master Specification** | [01-qwen-ecosystem-master-curriculum.md](04%20Qwen/01-qwen-ecosystem-master-curriculum.md) | Complete 25-volume blueprint: GQA, RoPE $\theta=1M$, DCA 128k context, ms-swift SFT/DPO/GRPO, and vLLM on DGX Spark. |
| 01 | **Qwen2.5 Architectural Innovations** | [01-qwen25-architecture-and-model-spectrum.md](04%20Qwen/01-qwen25-architecture-and-model-spectrum.md) | Dense & MoE topologies, Dual-Chunk Attention, and extended 128k context mechanics. |
| 02 | **Advanced RoPE & Dual-Chunk Attention** | [02-attention-engineering-gqa-rope-and-dca.md](04%20Qwen/02-attention-engineering-gqa-rope-and-dca.md) | High base frequency $\theta=10^6$, intra-chunk & inter-chunk chunking algorithms. |
| 03 | **Qwen2.5-Coder: Code Intelligence** | [03-qwen25-coder-deep-dive.md](04%20Qwen/03-qwen25-coder-deep-dive.md) | Fill-In-the-Middle (FIM), multi-file context, SWE-bench verified workflows. |
| 04 | **Qwen2.5-Math: Mathematical Reasoning** | [04-qwen25-math-and-reasoning.md](04%20Qwen/04-qwen25-math-and-reasoning.md) | Qwen2.5-Math rule-based data filtering, Chain-of-Thought (CoT), TIR (Tool-Integrated Reasoning). |
| 05 | **Qwen2-VL: Vision-Language Processing** | [05-qwen2-vl-and-vision-language-processing.md](04%20Qwen/05-qwen2-vl-and-vision-language-processing.md) | Dynamic resolution NaViT, 3D M-RoPE, spatial bounding boxes, and video comprehension. |

---

## ⚡ [05] NVIDIA NeMo & Nemotron Ecosystem Mastery (25 Volumes)

The complete 25-volume curriculum is indexed in [**05 NeMo/README.md**](05%20NeMo/README.md).

| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 01 | **NeMo Ecosystem Master Specification** | [01-nemo-ecosystem-master-curriculum.md](05%20NeMo/01-nemo-ecosystem-master-curriculum.md) | Complete 25-volume blueprint: Megatron-Core 3D Parallelism, NeMo Curator, SteerLM multi-attribute alignment, Colang 2.0 Guardrails, and Triton. |
| 02 | **Megatron-Core 3D Parallelism Deep Dive** | [02-megatron-core-3d-parallelism-deep-dive.md](05%20NeMo/02-megatron-core-3d-parallelism-deep-dive.md) | Column/Row Tensor Parallelism, Sequence Parallelism, 1F1B pipelining, Distributed Optimizer. |
| 03 | **Transformer Engine & Native FP8 on Blackwell** | [03-transformer-engine-and-native-fp8-on-blackwell.md](05%20NeMo/03-transformer-engine-and-native-fp8-on-blackwell.md) | Delayed scaling, amax history tracking, FP8 E4M3 vs E5M2, Blackwell Tensor Cores. |
| 04 | **Nemotron Family: 340B, 70B & Minitron** | [04-nemotron-model-family-340b-70b-and-minitron.md](05%20NeMo/04-nemotron-model-family-340b-70b-and-minitron.md) | Base, Instruct, and Reward models, HelpSteer2 dataset, Minitron pruning. |
| 05 | **Minitron: Pruning & Distillation** | [05-minitron-structured-pruning-and-distillation.md](05%20NeMo/05-minitron-structured-pruning-and-distillation.md) | Depth/width pruning via cosine redundancy, Taylor expansion, Knowledge Distillation (KD). |

---

## 🔬 [06] Google Gemma & JAX/XLA MaxText Mastery (25 Volumes)

The complete 25-volume curriculum is indexed in [**06 Gemma/README.md**](06%20Gemma/README.md).

| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 01 | **Gemma Ecosystem Master Specification** | [01-gemma-ecosystem-master-curriculum.md](06%20Gemma/01-gemma-ecosystem-master-curriculum.md) | Complete 25-volume blueprint: Sliding Window Attention (SWA), Logit soft-capping, Gemini distillation, Google JAX/XLA MaxText, and gemma.cpp. |
| 02 | **Sliding Window Attention (SWA) Mechanics** | [02-sliding-window-attention-mechanics.md](06%20Gemma/02-sliding-window-attention-mechanics.md) | Alternating 4k local sliding window and 8k global attention, 50% KV cache savings on even layers. |
| 03 | **Logit Soft-Capping: Math & Implementation** | [03-logit-soft-capping-math-and-implementation.md](06%20Gemma/03-logit-soft-capping-math-and-implementation.md) | Double logit soft-capping (50.0 attention, 30.0 vocab), $\tanh$ analytical gradient, zero NaN loss. |
| 04 | **Gemini Ultra Knowledge Distillation** | [04-gemini-ultra-knowledge-distillation.md](06%20Gemma/04-gemini-ultra-knowledge-distillation.md) | On-policy distillation, KL divergence vs cross-entropy, temperature invariance proof ($\tau^2$). |
| 05 | **Specialized Gemma Architectures** | [05-specialized-gemma-architectures.md](06%20Gemma/05-specialized-gemma-architectures.md) | CodeGemma FIM (`<|fim_prefix|>`), PaliGemma 2 (`<loc0000>`), RecurrentGemma Griffin GLR ($O(1)$ memory). |
| 25 | **Hands-On Gemma Practice Workbook & Mastery Lab** | [25-hands-on-gemma-practice-workbook.md](06%20Gemma/25-hands-on-gemma-practice-workbook.md) | 25 production capstone challenges with self-contained executable Python test harness and solutions. |

---

## ⚡ [07] NVIDIA Hardware, Silicon Interlinks & Low-Level Systems (30 Volumes)

The complete 30-volume curriculum is indexed in [**07 Nvidia/README.md**](07%20Nvidia/README.md).

| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 01 | **Silicon Packaging & NV-HBI Cross-Die Topology** | [01-silicon-fabrication-packaging-and-cross-die-interlinking.md](07%20Nvidia/01-silicon-fabrication-packaging-and-cross-die-interlinking.md) | TSMC 4NP/3nm EUV, reticle limits, CoWoS-L packaging, silicon interposer bridges, and 10 TB/s cross-die NV-HBI. |
| 02 | **SM Microarchitecture, Warps & Tensor Cores** | [02-sm-microarchitecture-warp-schedulers-and-tensor-cores.md](07%20Nvidia/02-sm-microarchitecture-warp-schedulers-and-tensor-cores.md) | 4 processing blocks/SM, warp schedulers, 64KB register files, 4th/5th Gen Tensor Cores, and DPX instructions. |
| 03 | **Memory Subsystem: TMA, Crossbar & HBM3e/HBM4** | [03-memory-subsystem-tma-cache-crossbars-and-hbm.md](07%20Nvidia/03-memory-subsystem-tma-cache-crossbars-and-hbm.md) | TMA 1D–5D async pipelines, configurable SMem (228KB), 50MB L2 crossbar, and 5120-bit/8192-bit HBM subsystems. |
| 04 | **Micro-Precision Math & Transformer Engines** | [04-micro-precision-math-and-transformer-engines.md](07%20Nvidia/04-micro-precision-math-and-transformer-engines.md) | NVFP4 (E2M1 micro-blocks), FP8 (E4M3/E5M2), dynamic format switching, delayed scaling amax history, and compute ceilings. |
| 05 | **Grace & Vera CPUs and NVLink-C2C Superchips** | [05-host-processors-and-nvlink-c2c-superchips.md](07%20Nvidia/05-host-processors-and-nvlink-c2c-superchips.md) | Grace CPU (72 ARM Neoverse V2 cores, SVE2, SCF fabric), Vera CPU, and NVLink-C2C 900 GB/s cache-coherent unified physical memory space. |
| 06 | **Open GPU Kernel Drivers (nvidia.ko) & GSP** | [06-open-gpu-kernel-drivers-and-gsp-firmware.md](07%20Nvidia/06-open-gpu-kernel-drivers-and-gsp-firmware.md) | `nvidia.ko`, `nvidia-modeset.ko`, IOCTL command buffers, and GSP RISC-V firmware execution loops. |
| 07 | **Unified Virtual Memory (UVM) & Page Faults** | [07-unified-virtual-memory-uvm-and-page-faults.md](07%20Nvidia/07-unified-virtual-memory-uvm-and-page-faults.md) | `nvidia-uvm.ko`, hardware page fault engine, ATS/PASID PCIe coherency, and asynchronous prefetching. |
| 08 | **CUDA Driver API vs Runtime API Lifecycles** | [08-cuda-driver-api-vs-runtime-api-execution-lifecycles.md](07%20Nvidia/08-cuda-driver-api-vs-runtime-api-execution-lifecycles.md) | `libcuda.so.1` vs `libcudart.so`, context management, `cuMem*` virtual memory APIs, and CDI container boundaries. |
| 09 | **Low-Level System Telemetry: nvidia-smi & NVML**| [09-low-level-system-telemetry-nvidia-smi-and-nvml.md](07%20Nvidia/09-low-level-system-telemetry-nvidia-smi-and-nvml.md) | XML dumps, persistence daemon, power capping (`-pl`), clock locking (`-lgc`), and Python `pynvml` bindings. |
| 10 | **Hardware Slicing: MIG, vGPU & Multi-Tenancy** | [10-hardware-slicing-mig-vgpu-and-multi-tenancy.md](07%20Nvidia/10-hardware-slicing-mig-vgpu-and-multi-tenancy.md) | Multi-Instance GPU (MIG) physical SM/L2 slicing, GPU/Compute Instances, vGPU on ESXi/KVM, and Run:ai scheduling. |
| 11 | **CUDA Toolchain: nvcc, PTX Virtual ISA & SASS** | [11-cuda-compilation-pipeline-nvcc-ptx-and-sass.md](07%20Nvidia/11-cuda-compilation-pipeline-nvcc-ptx-and-sass.md) | Split compilation, PTX virtual intermediate representation, and SASS native machine code disassembly with `cuobjdump`. |
| 12 | **Memory Debugging with compute-sanitizer** | [12-dynamic-memory-debugging-with-compute-sanitizer.md](07%20Nvidia/12-dynamic-memory-debugging-with-compute-sanitizer.md) | Dynamic binary instrumentation: `memcheck` (OOB), `racecheck` (RAW/WAR/WAW), `synccheck`, and `initcheck`. |
| 13 | **System-Wide Timeline Tracing with Nsight Systems**| [13-system-wide-timeline-tracing-with-nsight-systems.md](07%20Nvidia/13-system-wide-timeline-tracing-with-nsight-systems.md) | Multi-threaded timeline tracing, catching GPU starvation bubbles, stream concurrency, CUDA Graphs, and NVTX. |
| 14 | **SM Microarchitectural Profiling with Nsight Compute** | [14-sm-microarchitectural-profiling-with-nsight-compute.md](07%20Nvidia/14-sm-microarchitectural-profiling-with-nsight-compute.md) | Speed-of-Light roofline model, warp stall taxonomy (`stall_memory_throttle`, `stall_barrier`), and register pressure. |
| 15 | **High-Performance GEMM: CUTLASS 3.x & cuBLASLt**| [15-high-performance-gemm-engines-cutlass-and-cublaslt.md](07%20Nvidia/15-high-performance-gemm-engines-cutlass-and-cublaslt.md) | Warp-specialized persistent producer-consumer GEMMs, TMA async pipelines, and fused SwiGLU/GELU epilogues. |
| 16 | **NVLink Signaling & NVSwitch Silicon Generations** | [16-nvlink-signaling-and-nvswitch-silicon-generations.md](07%20Nvidia/16-nvlink-signaling-and-nvswitch-silicon-generations.md) | 112G/224G PAM4 SerDes, NVSwitch 3/4/6 crossbar routing, and SHARP in-network hardware reduction offload. |
| 17 | **NVSwitch Fabric Manager & Rack Topologies (NVL72)**| [17-nvswitch-fabric-manager-and-rack-topologies.md](07%20Nvidia/17-nvswitch-fabric-manager-and-rack-topologies.md) | `nvidia-fabricmanager` daemon, HGX 8-GPU baseboard meshes, and GB200 NVL72 copper backplane spine. |
| 18 | **Scale-Out Networks: Quantum IB vs Spectrum-X** | [18-scale-out-networks-quantum-infiniband-vs-spectrum-x.md](07%20Nvidia/18-scale-out-networks-quantum-infiniband-vs-spectrum-x.md) | Quantum-2/X800 credit-based flow control vs Spectrum-4/X800 RoCEv2 (Dynamic Packet Spraying, PFC, ECN, DCQCN). |
| 19 | **Direct Memory Pipelines: GPUDirect RDMA, Storage & P2P**| [19-direct-memory-pipelines-gpudirect-rdma-storage-p2p.md](07%20Nvidia/19-direct-memory-pipelines-gpudirect-rdma-storage-p2p.md) | `nvidia-peermem.ko` driver translation, PCIe/NVLink P2P, and GPUDirect Storage (GDS) with `cuFile` bypass. |
| 20 | **NCCL Collectives & nccl-tests Tuning** | [20-nccl-collectives-and-nccl-tests-tuning.md](07%20Nvidia/20-nccl-collectives-and-nccl-tests-tuning.md) | AllReduce/AllGather algorithms (Ring, Tree, NVLS), tuning environment flags, and multi-node benchmarking. |
| 21 | **DPUs & SmartNICs: BlueField-3/4 & DOCA SDK** | [21-dpus-and-smartnics-bluefield-and-doca-sdk.md](07%20Nvidia/21-dpus-and-smartnics-bluefield-and-doca-sdk.md) | BlueField-3/4 hardware, DOCA Flow match-action acceleration, DOCA GPUNetIO, and virtual NVMe-oF SNAP. |
| 22 | **Datacenter Power (54V Busbars) & Liquid Cooling**| [22-datacenter-infrastructure-54v-busbars-and-liquid-cooling.md](07%20Nvidia/22-datacenter-infrastructure-54v-busbars-and-liquid-cooling.md) | 415V 3-phase AC, 54V DC solid copper busbars, PoL VRMs, micro-throttling di/dt mitigation, DLC cold plates, and CDUs. |
| 23 | **Data Center GPU Manager (DCGM) & Monitoring** | [23-data-center-gpu-manager-dcgm-and-monitoring.md](07%20Nvidia/23-data-center-gpu-manager-dcgm-and-monitoring.md) | Background `nv-hostengine` daemon, `dcgmi` client commands, policy alerts, and Prometheus `dcgm-exporter`. |
| 24 | **Hardware Diagnostics (NVVS) & XID Forensics** | [24-hardware-diagnostics-nvvs-and-kernel-xid-forensics.md](07%20Nvidia/24-hardware-diagnostics-nvvs-and-kernel-xid-forensics.md) | NVVS diagnostic levels 1 to 4, forensic root cause triage for XID 31, 43, 45, 62, 79, and 92, and SDC memory scrubbing. |
| 25 | **Fabric Diagnostics & Cluster Recovery Playbook** | [25-fabric-diagnostics-and-cluster-recovery-playbook.md](07%20Nvidia/25-fabric-diagnostics-and-cluster-recovery-playbook.md) | InfiniBand diagnostic toolkit (`ibstat`, `ibdiagnet -r`, `perfquery`), RoCE tools (`ethtool`, `mlnx_qos`), and node recovery runbooks. |
| 26 | **Inference Engines: TensorRT, TRT-LLM & RAPIDS** | [26-inference-engines-tensorrt-and-tensorrt-llm.md](07%20Nvidia/26-inference-engines-tensorrt-and-tensorrt-llm.md) | TensorRT graph fusion, In-Flight Batching, PagedAttention, NVFP4 W4A4 micro-block scaling, and RAPIDS cuDF/cuGraph. |
| 27 | **Distributed Training: Megatron-Core 3D Parallelisms** | [27-distributed-training-megatron-core-and-parallelisms.md](07%20Nvidia/27-distributed-training-megatron-core-and-parallelisms.md) | 5D Parallelism hierarchy (TP, PP 1F1B bubble mechanics, ZeRO-1/2/3, Ring Attention CP, MoE EP), and Selective Checkpointing. |
| 28 | **Serving Microservices: Triton Architecture & NIM** | [28-serving-microservices-triton-and-nvidia-nim.md](07%20Nvidia/28-serving-microservices-triton-and-nvidia-nim.md) | Triton C++ multi-backend, Dynamic Batching priority queues (`max_queue_delay`), BLS, and NVIDIA NIM OCI container runtime. |
| 29 | **Cloud-Native Orchestration: GPU/Net Operators & BCM**| [29-cloud-native-orchestration-gpu-network-operators-and-bcm.md](07%20Nvidia/29-cloud-native-orchestration-gpu-network-operators-and-bcm.md) | Kubernetes GPU Operator, CDI device injection, Network Operator (MOFED/Multus RDMA), and Base Command Manager bare-metal PXE. |
| 30 | **Advanced Fabric Management: UFM Cyber-AI & MST** | [30-advanced-fabric-management-ufm-cyber-ai-and-mst.md](07%20Nvidia/30-advanced-fabric-management-ufm-cyber-ai-and-mst.md) | UFM Enterprise fat-tree routing, UFM Cyber-AI predictive optical cable degradation, MST `mstflint`/`mstconfig`, and RoCE DCQCN. |

---

## 💾 [08] Storage & High-Performance Data Fabric for AI (25 Volumes)

The complete 25-volume curriculum is indexed in [**08 Storage/README.md**](08%20Storage/README.md).

### Part I: Low-Level Storage Architectures, NVMe-oF & Kernel Bypass
| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 01 | **The Storage Wall & Ingestion Bandwidth Math** | [01-storage-wall-and-ingestion-bandwidth-math.md](08%20Storage/01-storage-wall-and-ingestion-bandwidth-math.md) | Ingestion rooflines, multimodal $64\text{ GB/s}$ requirements, small-file poison, and dataloader worker starvation math. |
| 02 | **NVMe Internals, PCIe Gen5 & Flash Physics** | [02-nvme-internals-pcie-gen5-and-flash-physics.md](08%20Storage/02-nvme-internals-pcie-gen5-and-flash-physics.md) | SQ/CQ paired queues, doorbell registers, SLC/TLC/QLC physics, Write Amplification Factor (WAF), and EDSFF E1.S/E3.S. |
| 03 | **NVMe-oF: RDMA vs. TCP Transport Mechanics** | [03-nvme-of-rdma-vs-tcp-transport-mechanics.md](08%20Storage/03-nvme-of-rdma-vs-tcp-transport-mechanics.md) | Capsule commands, NVMe/RDMA vs NVMe/TCP, BDP queue depth formula, and SPDK polled-mode user-space drivers. |
| 04 | **GPUDirect Storage (GDS) & cuFile Architecture** | [04-gpudirect-storage-gds-and-cufile-architecture.md](08%20Storage/04-gpudirect-storage-gds-and-cufile-architecture.md) | Direct DMA from NVMe to GPU HBM, `nvidia-fs.ko`, `libcufile.so`, C++ API, and `gdscheck` hardware verification. |
| 05 | **Linux VFS, Page Cache & Direct I/O Mechanics** | [05-linux-vfs-page-cache-and-direct-io-mechanics.md](08%20Storage/05-linux-vfs-page-cache-and-direct-io-mechanics.md) | Page Cache double-buffering trap, dirty ratio flush stalls, `O_DIRECT` 4KB alignment, and high-performance `io_uring`. |

### Part II: Modern AI Parallel File Systems
| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 06 | **WekaFS Architecture & Matrix Distributed Engine** | [06-wekafs-architecture-and-matrix-distributed-engine.md](08%20Storage/06-wekafs-architecture-and-matrix-distributed-engine.md) | User-space microkernel, distributed hash table metadata, Snap-to-Object tiering, and native GPUDirect Storage. |
| 07 | **VAST Data Universal Storage & DASE Architecture** | [07-vast-data-universal-storage-and-dase-architecture.md](08%20Storage/07-vast-data-universal-storage-and-dase-architecture.md) | Disaggregated Shared Everything (DASE), stateless C-nodes, D-node JBOFs, SCM write buffers, and similarity dedup. |
| 08 | **Lustre File System at Exascale (MDS, OSS, LNet)** | [08-lustre-file-system-at-exascale-mds-oss-and-lnet.md](08%20Storage/08-lustre-file-system-at-exascale-mds-oss-and-lnet.md) | MDS/MDT metadata targets, OSS/OST object storage, `lfs setstripe` striping math, and LNet multi-rail RDMA. |
| 09 | **IBM Spectrum Scale (GPFS) & Network Shared Disks**| [09-ibm-spectrum-scale-gpfs-and-network-shared-disks.md](08%20Storage/09-ibm-spectrum-scale-gpfs-and-network-shared-disks.md) | NSD block servers, token management byte-range locking, failure groups, and ILM policy-driven flash tiering. |
| 10 | **Comparative Benchmark: Weka vs VAST vs Lustre vs GPFS**| [10-comparative-benchmark-weka-vast-lustre-gpfs.md](08%20Storage/10-comparative-benchmark-weka-vast-lustre-gpfs.md) | Definitive architectural comparison matrix, `ior`/`mdtest` performance ceilings, and Model Flops Utilization (MFU) penalties. |

### Part III: Distributed Object Storage & Cloud-Native Storage Fabrics
| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 11 | **High-Performance Object Storage: MinIO & SIMD** | [11-high-performance-object-storage-minio-and-simd.md](08%20Storage/11-high-performance-object-storage-minio-and-simd.md) | Zero-database metadata, Reed-Solomon $GF(2^8)$ AVX-512 SIMD erasure coding, and S3 multipart streaming. |
| 12 | **Ceph Distributed Storage: RADOS, BlueStore & CRUSH**| [12-ceph-distributed-storage-rados-bluestore-crush.md](08%20Storage/12-ceph-distributed-storage-rados-bluestore-crush.md) | RADOS cluster architecture, CRUSH mathematical placement algorithm, and BlueStore raw block storage engine. |
| 13 | **Kubernetes Storage & Cloud-Native CSI for AI** | [13-kubernetes-cloud-native-storage-csi-for-ai.md](08%20Storage/13-kubernetes-cloud-native-storage-csi-for-ai.md) | CSI gRPC lifecycle, RWO local NVMe vs RWX parallel filesystems, and `WaitForFirstConsumer` volume binding. |
| 14 | **Data Caching Engines: Alluxio, JuiceFS & GPU Cache**| [14-data-caching-alluxio-juicefs-and-gpu-cache.md](08%20Storage/14-data-caching-alluxio-juicefs-and-gpu-cache.md) | Decoupled metadata engines (Redis/TiKV), NVMe chunk caching, effective bandwidth math, and KvikIO/DALI cache. |
| 15 | **Flash-Optimized Data Loaders: WebDataset & DALI** | [15-flash-optimized-data-loaders-webdataset-and-dali.md](08%20Storage/15-flash-optimized-data-loaders-webdataset-and-dali.md) | Streaming `.tar` sharding, Megatron binary mmap (`.bin`/`.idx`), and NVIDIA DALI hardware GPU image/video decoding. |

### Part IV: Exascale Checkpointing & Fault Resilience
| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 16 | **The Checkpointing Wall in Foundation Model Training**| [16-the-checkpointing-wall-in-foundation-model-training.md](08%20Storage/16-the-checkpointing-wall-in-foundation-model-training.md) | Model state sizing ($16\Phi$ bytes), cluster MTBF scaling laws, and Young & Daly optimal checkpoint interval equations. |
| 17 | **Asynchronous & Non-Blocking Checkpointing Engines**| [17-asynchronous-and-non-blocking-checkpointing-engine.md](08%20Storage/17-asynchronous-and-non-blocking-checkpointing-engine.md) | Dedicated CUDA side-streams, host pinned memory staging (<500ms barrier), and PyTorch DCP `async_save`. |
| 18 | **Distributed Checkpointing in TorchTitan & Megatron** | [18-torchtitan-and-megatron-distributed-checkpointing.md](08%20Storage/18-torchtitan-and-megatron-distributed-checkpointing.md) | 3D/4D parallelism sharding, global `.metadata` manifests, and dynamic resharding across differing TP topologies. |
| 19 | **Storage Fault Tolerance & Automated Recovery** | [19-storage-fault-tolerance-and-automated-recovery.md](08%20Storage/19-storage-fault-tolerance-and-automated-recovery.md) | Exascale bit-rot physics, SIMD $xxHash64$ integrity, `renameat2` atomic two-phase commit, and self-healing rollbacks. |
| 20 | **Disaster Recovery, CoW Snapshots & Multi-Region Sync**| [20-disaster-recovery-snapshots-and-multi-region-sync.md](08%20Storage/20-disaster-recovery-snapshots-and-multi-region-sync.md) | Redirect-on-Write (RoW) metadata snapshots, sustained WAN replication bandwidth math, and immutable WORM locks. |

### Part V: Production SRE, Performance Tuning & Forensics
| # | Master Guide | File | Focus Area |
| :--- | :--- | :--- | :--- |
| 21 | **Storage Benchmarking Masterclass: fio, IOR & mdtest**| [21-storage-benchmarking-masterclass-fio-ior-mdtest.md](08%20Storage/21-storage-benchmarking-masterclass-fio-ior-mdtest.md) | Little's Law queue depth scaling, MPI-IO clustered streaming (FPP vs SSF), and extreme metadata stress profiling. |
| 22 | **Storage Network Tuning: RoCEv2, PFC, ECN & MTU 9000** | [22-storage-network-tuning-rocev2-pfc-ecn-jumbo-frames.md](08%20Storage/22-storage-network-tuning-rocev2-pfc-ecn-jumbo-frames.md) | Lossless Ethernet fabrics, PFC headroom buffer sizing, DCQCN congestion loop, and MTU 9000 jumbo frames. |
| 23 | **Production Storage Telemetry: Prometheus & eBPF** | [23-production-storage-telemetry-prometheus-and-ebpf.md](08%20Storage/23-production-storage-telemetry-prometheus-and-ebpf.md) | The Tail-at-Scale Law in AI training, microsecond eBPF kernel block tracing (`biolatency`), and straggler detection. |
| 24 | **Storage Forensics: Drive Flaps, Bit-Rot & Split-Brain**| [24-storage-forensics-drive-flaps-bit-rot-split-brain.md](08%20Storage/24-storage-forensics-drive-flaps-bit-rot-split-brain.md) | PCIe Gen5 to Gen1 link flapping ($252\times$ collapse), NVMe SMART forensics, and kernel D-state hung task call traces. |
| 25 | **Hands-On Storage Mastery Lab & Test Harness** | [25-hands-on-storage-mastery-lab-and-test-harness.md](08%20Storage/25-hands-on-storage-mastery-lab-and-test-harness.md) | Capstone verification lab, architecture synthesis, and automated 25-challenge Python test harness guide. |

### 🛠️ Storage Verification & Diagnostic Tooling
- **Automated Verification Harness:** `08 Storage/storage_systems_mastery_harness.py` (25/25 challenges passing, 100% mathematical & architectural validation).
- **Production Straggler Detector:** `08 Storage/storage_straggler_detector.py` (Real-time detection of slow drives, PCIe flaps, hung D-states, and dirty page thrashing).

---

## 📚 [01] Ansible & Configuration Management (Deep Dives)

The complete curriculum is indexed in [**01Ansible/README.md**](01Ansible/README.md).

| # | Topic | File |
| :--- | :--- | :--- |
| 01 | Ansible Core — Architecture, Modules, Playbooks, Roles, Variables, Templates | [01-ansible-core-deep-dive.md](01Ansible/01-ansible-core-deep-dive.md) |
| 02 | Ansible Tower / AWX — Installation, RBAC, Credentials, Workflows, API | [02-ansible-tower-awx-deep-dive.md](01Ansible/02-ansible-tower-awx-deep-dive.md) |
| 03 | HashiCorp Vault — Secrets Engines, Auth, Policies, AppRole, Integration | [03-hashicorp-vault-deep-dive.md](01Ansible/03-hashicorp-vault-deep-dive.md) |

## 🗺️ Roadmaps & Quick References

- [Ansible Step-by-Step Beginner's Guide](01%20Ansible/00-ansible-step-by-step-guide.md)
- [Ansible, Tower & HashiCorp Vault Roadmap](01Ansible/ansible-tower-vault-roadmap.md)

## 🔧 Project Files

- [Ansible Setup & Inventory Documentation](dgx-ansible/docs/README.md)
- [Inventory Configuration](dgx-ansible/inventory.ini)
- [Ansible Config](dgx-ansible/ansible.cfg)
- [GPU Monitor Playbook](dgx-ansible/gpu-monitor.yml)
- [Spark Health Playbook](dgx-ansible/spark-health.yml)
