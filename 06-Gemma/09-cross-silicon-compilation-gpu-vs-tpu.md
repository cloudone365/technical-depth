# Volume 09: Cross-Silicon Compilation: GPU vs TPU

```
==================================================================================================
TARGET AUDIENCE: Hardware Architects, Foundation Model Engineers, Silicon Performance Specialists
PREREQUISITES   : Computer Architecture, Roofline Model, Matrix Multiplication Units (MMA/MXU)
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Analyze XLA cross-silicon compilation targeting NVIDIA Blackwell GB10 vs Google TPU
                  v5p/v6e Trillium. Master systolic arrays, tensor cores, and memory hierarchy trade-offs.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Google Gemma 2 is uniquely engineered to compile natively across two of the world's most advanced AI computing platforms:
1. **Google Cloud TPUs (TPU v5p and TPU v6e Trillium)**: Custom ASICs built around 2D systolic **Matrix Multiply Units (MXUs)** and **Optical Circuit Switches (OCS)**.
2. **NVIDIA GPUs (Blackwell GB10 and B200)**: Massively parallel architectures centered around **5th-Generation Tensor Cores**, asynchronous warp-specialized execution, and **NVLink** interconnects.

Because Google's primary compiler infrastructure is **XLA (Accelerated Linear Algebra)**, the exact same high-level Gemma 2 mathematical graph targets either silicon substrate without changing a single line of model logic. However, the underlying hardware scheduling, memory access patterns, and arithmetic intensities differ drastically.

```
                           ┌──────────────────────────────────────────────┐
                           │        UNIFIED GEMMA 2 JAX/MAXTEXT CODE      │
                           └──────────────────────┬───────────────────────┘
                                                  │
                                     Compiles via XLA (HLO)
                                                  │
                 ┌────────────────────────────────┴────────────────────────────────┐
                 ▼                                                                 ▼
┌──────────────────────────────────────────────┐  ┌──────────────────────────────────────────────┐
│        NVIDIA BLACKWELL GB10 GPU TARGET      │  │        GOOGLE CLOUD TPU V6E (TRILLIUM)       │
├──────────────────────────────────────────────┤  ├──────────────────────────────────────────────┤
│ - Architecture: SIMT + Tensor Cores (MMA)    │  │ - Architecture: Systolic Array (MXU 256x256) │
│ - Memory: 128 GB Unified LPDDR5X/HBM3e       │  │ - Memory: High-Bandwidth Memory (HBM)        │
│ - Interconnect: 900 GB/s NVLink-C2C          │  │ - Interconnect: Optical Circuit Switch (ICI) │
│ - Precision: Native FP8 (E4M3), TF32, BF16   │  │ - Precision: Native BF16, Int8               │
│ - Execution: Dynamic Warp Dispatching        │  │ - Execution: Statically Scheduled VLIW Pipeline│
└──────────────────────────────────────────────┘  └──────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Assembly Line Factory vs The Automated Drone Fleet
3. Evolutionary Lineage: From TPU v1 and Volta to TPU v6e and Blackwell GB10
4. First-Principles Mathematics & Algorithmic Formulations
   - Systolic Array (TPU MXU) vs Tensor Core (NVIDIA MMA) Compute Mechanics
   - The Roofline Model: Arithmetic Intensity and Operational Flops
   - Memory Subsystems: Unified NVLink-C2C vs Discrete HBM
   - Compiler Lowering: How XLA Emits PTX vs TPU Bytecode
5. Alternative Industry Approaches & Comparative Architectural Matrix
6. Concrete Production Hands-On Lab: Roofline Performance Profiler for Gemma 2 Operations
7. Hardware Grounding for NVIDIA DGX Spark (Extracting Peak GB10 TFLOPs)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Assembly Line Factory vs The Automated Drone Fleet

To understand the difference between a Google TPU and an NVIDIA Blackwell GPU:
- **Google TPU (Systolic Array)** is a **Gigantic Industrial Factory Assembly Line**:
  - Raw materials (matrix weights) flow rhythmically from top to bottom, while intermediate products (activations) slide from left to right along fixed conveyor belts.
  - At every station, a worker performs an exact multiply-accumulate operation at clock ticks without any re-routing or decision making.
  - If your workload perfectly matches the conveyor dimensions ($256 \times 256$ matrix tiles), the efficiency is extraordinary, with minimal control logic overhead.
- **NVIDIA Blackwell GPU (Tensor Core SIMT)** is a **Swarm of Autonomous Flying Delivery Drones**:
  - Independent thread blocks (warps) can change direction, fetch memory asynchronously via Tensor Memory Accelerator (TMA), and dynamically balance workloads.
  - If a sequence has irregular padding, sliding windows, or dynamic conditional branches (like MoE or speculative decoding), the swarm adapts instantaneously without stalling.

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2016-2017: The Dawn of Accelerators                                    │
│ Google TPU v1 (Inference only, 8-bit MXU) vs NVIDIA Volta V100 (1st gen│
│ Tensor Cores, introduced mixed-precision FP16 computing).              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2020-2022: Scale-Up Networking & bfloat16                              │
│ Google TPU v4 (Optical Circuit Switch 3D Torus) vs NVIDIA Hopper H100  │
│ (Transformer Engine, FP8, 900 GB/s NVLink 4).                          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2024-Present: Frontier Era                                             │
│ Google TPU v6e Trillium (Dual 256x256 MXU, 4.7x perf/watt) vs NVIDIA   │
│ Blackwell GB10/GB200 (Grace-Blackwell NVLink-C2C, 5th-gen Tensor Cores,│
│ native FP4/FP8, 128 GB Unified Memory on DGX Spark).                   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 Compute Engine Mechanics: Systolic MXU vs Tensor Core MMA

#### 1. Google TPU Systolic Matrix Multiply Unit (MXU):
A TPU v6e MXU is a 2D spatial grid of $256 \times 256$ multiply-accumulate processing elements.
Data flows lockstep across clock cycles:

$$C_{i, j}^{(t)} = C_{i, j}^{(t-1)} + A_{i, k} \cdot B_{k, j}$$

Because intermediate results pass directly to neighboring cells via register-to-register wires, the MXU does not read or write intermediate values to register files or SRAM, achieving world-class energy efficiency ($\approx 4.7\times$ performance per watt). However, if matrix dimensions are not multiples of $128$ or $256$, padding waste occurs.

#### 2. NVIDIA Blackwell Tensor Core (MMA):
Blackwell Tensor Cores execute warp-level Matrix Multiply-Accumulate instructions:
$$\text{MMA.M16N8K32} \implies D = A \cdot B + C$$
Warps dynamically load tiles from Blackwell Distributed Shared Memory (DSMEM) using asynchronous hardware pipelines (**Tensor Memory Accelerator - TMA**). This offers extreme flexibility: non-square matrices, arbitrary batch sizes, and dynamic sliding windows run without structural padding overhead.

### 4.2 The Roofline Model: Arithmetic Intensity

The maximum achievable throughput $P$ (FLOPs/sec) of an operation is governed by the Roofline Model:

$$P = \min\left( P_{\text{peak}}, \; I \times \text{BW} \right)$$

Where:
- $P_{\text{peak}}$ is the peak theoretical computational capability of the chip (FLOPs/s).
- $\text{BW}$ is the memory bandwidth (Bytes/s).
- $I = \frac{\text{Operational FLOPs}}{\text{DRAM Bytes Transferred}}$ is the **Arithmetic Intensity** (FLOPs/Byte).

The critical inflection point, or **Ridge Point** $I^*$, defines where an operation transitions from memory-bandwidth-bound to compute-bound:

$$I^* = \frac{P_{\text{peak}}}{\text{BW}}$$

#### Ridge Point Comparison:
| Hardware Target | Peak Compute ($P_{\text{peak}}$ FP16/BF16) | Memory Bandwidth ($\text{BW}$) | Ridge Point ($I^*$) |
| :--- | :--- | :--- | :--- |
| **Google TPU v6e (Trillium)** | $\approx 918 \text{ TFLOPs}$ | $\approx 1,600 \text{ GB/s}$ (HBM) | $\approx 573 \text{ FLOPs/Byte}$ |
| **NVIDIA Blackwell GB10** | $\approx 1,000 \text{ TFLOPs}$ | $\approx 900 \text{ GB/s}$ (NVLink-C2C/Unified) | $\approx 1,111 \text{ FLOPs/Byte}$ |

**Architectural Implication**:
- Autoregressive token generation (decoding phase, batch size 1) has an arithmetic intensity of $I \approx 2 \text{ FLOPs/Byte} \ll I^*$. It is **100% memory bandwidth bound** on both platforms.
- Prompt prefill (batch size $\times$ sequence length $> 2048$) has an arithmetic intensity of $I > 1,500 \text{ FLOPs/Byte} \gg I^*$. It is **100% compute bound**, where Blackwell's higher TFLOP density and FP8 capabilities dominate.

---

## 5. Alternative Industry Approaches & Comparative Architectural Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              BLACKWELL GB10 VS TPU V6E ARCHITECTURAL MATRIX                            │
├──────────────────────┬──────────────────────────────────────────┬──────────────────────────────────────┤
│ Feature              │ NVIDIA Blackwell GB10 (DGX Spark)        │ Google Cloud TPU v6e (Trillium)      │
├──────────────────────┼──────────────────────────────────────────┼──────────────────────────────────────┤
│ Compute Primitive    │ 5th-Gen Tensor Cores (MMA)               │ 2D Systolic Array (MXU 256x256)      │
│ Memory Capacity      │ 128 GB Unified (CPU + GPU shared)        │ 32 GB HBM per chip                   │
│ Inter-Chip Link      │ 900 GB/s NVLink-C2C (Local)              │ 1,600 Gbps ICI (Optical Switch Mesh) │
│ FP8 Support          │ Native E4M3 & E5M2 Hardware Acceleration │ Emulated / Partial Int8 focus        │
│ Dynamic Branching    │ Zero penalty (Warp Scheduler)            │ Stalls VLIW pipeline                 │
│ Gemma 2 Optimization │ Fused CUDA Soft-Cap Kernels              │ XLA Fused HLO Graph                  │
│ Framework Ecosystem  │ PyTorch 2.5, JAX, vLLM, TensorRT-LLM     │ JAX, MaxText, TensorFlow             │
└──────────────────────┴──────────────────────────────────────────┴──────────────────────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Roofline Performance Profiler for Gemma 2 Operations

Save this script as `gemma2_roofline_profiler.py`:

```python
"""
Gemma 2 Roofline Model Performance Profiler.
Analyzes arithmetic intensity and determines whether operations are
memory-bandwidth bound or compute-bound on Blackwell GB10 vs TPU v6e.
"""

import math

class HardwareTarget:
    def __init__(self, name: str, peak_tflops: float, bandwidth_gb_s: float):
        self.name = name
        self.peak_flops = peak_tflops * 1e12
        self.bandwidth_bytes = bandwidth_gb_s * 1e9
        self.ridge_point = self.peak_flops / self.bandwidth_bytes

    def profile_operation(self, op_name: str, flops: float, bytes_transferred: float):
        intensity = flops / bytes_transferred
        if intensity < self.ridge_point:
            achievable_flops = intensity * self.bandwidth_bytes
            bound_type = "MEMORY-BOUND (Bandwidth Limited)"
        else:
            achievable_flops = self.peak_flops
            bound_type = "COMPUTE-BOUND (Tensor Core / MXU Limited)"
        
        utilization = (achievable_flops / self.peak_flops) * 100.0
        return {
            "op_name": op_name,
            "intensity": intensity,
            "bound_type": bound_type,
            "achievable_tflops": achievable_flops / 1e12,
            "utilization": utilization
        }

def run_roofline_benchmark():
    print("=" * 80)
    print("GEMMA 2 ROOFLINE ANALYSIS: NVIDIA BLACKWELL GB10 VS GOOGLE TPU V6E")
    print("=" * 80)

    # Hardware Specs (BF16 Tensor / MXU performance)
    gb10 = HardwareTarget("NVIDIA Blackwell GB10", peak_tflops=1000.0, bandwidth_gb_s=900.0)
    tpu_v6e = HardwareTarget("Google Cloud TPU v6e", peak_tflops=918.0, bandwidth_gb_s=1600.0)

    print(f"{'Target Silicon':<25} | {'Peak TFLOPs':<12} | {'Bandwidth':<12} | {'Ridge Point':<16}")
    print("-" * 72)
    for hw in [gb10, tpu_v6e]:
        print(f"{hw.name:<25} | {hw.peak_flops/1e12:<12.1f} | {hw.bandwidth_bytes/1e9:<12.1f} | {hw.ridge_point:<16.2f} FLOP/B")

    # Workload 1: Autoregressive Token Generation (Batch=1, Hidden=4096)
    # Reading weight matrix W: 4096 x 4096 in BF16 = 33.55 MB
    # Math: 2 * 4096 * 4096 = 33.55 MFLOPs
    flops_decode = 2.0 * 4096 * 4096
    bytes_decode = (4096 * 4096 * 2) + (4096 * 2) + (4096 * 2)

    # Workload 2: Prompt Prefill GEMM (Batch=1, SeqLen=2048, Hidden=4096)
    # Math: 2 * 2048 * 4096 * 4096 = 68.7 GFLOPs
    flops_prefill = 2.0 * 2048 * 4096 * 4096
    bytes_prefill = (4096 * 4096 * 2) + (2048 * 4096 * 2)

    workloads = [
        ("Gemma 2 Single-Token Decode", flops_decode, bytes_decode),
        ("Gemma 2 2048-Token Prefill", flops_prefill, bytes_prefill)
    ]

    print("\n" + "=" * 80)
    for op_name, flops, data_bytes in workloads:
        print(f"\nWorkload: {op_name}")
        print(f"Total FLOPs: {flops/1e9:.3f} GFLOPs | Memory Transferred: {data_bytes/(1024*1024):.2f} MB")
        print("-" * 80)
        for hw in [gb10, tpu_v6e]:
            res = hw.profile_operation(op_name, flops, data_bytes)
            print(f"[{hw.name}]")
            print(f"  Arithmetic Intensity : {res['intensity']:.2f} FLOPs/Byte")
            print(f"  Operational Regime   : {res['bound_type']}")
            print(f"  Max Achievable Perf  : {res['achievable_tflops']:.2f} TFLOPs ({res['utilization']:.2f}% of peak)")

if __name__ == "__main__":
    run_roofline_benchmark()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (Extracting Peak GB10 TFLOPs)

### 7.1 Exploiting the Blackwell Asynchronous Copy Engine
When running prefill workloads on the DGX Spark, memory bandwidth is maximized by keeping data entirely within the L2 cache and shared memory:
- **TMA (Tensor Memory Accelerator)**: Hardware block that transfers 5D tensor tiles directly between unified memory and Blackwell shared memory (SRAM) without consuming CUDA warp cycles.
- In JAX/XLA, setting `XLA_FLAGS="--xla_gpu_enable_pipelined_p2p=true"` ensures that XLA generates TMA asynchronous instructions during Gemma 2 matrix multiplications.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate Decoding Phase Memory Limit**:
   If a model has $27.2 \times 10^9$ parameters in FP16 ($54.4\text{ GB}$), what is the maximum theoretical generation speed (tokens per second) on a memory subsystem with $900\text{ GB/s}$ bandwidth, assuming batch size 1?
   - *Solution*: Each generated token requires reading all $54.4\text{ GB}$ of weights from memory.
     $$\text{Max Throughput} = \frac{900 \text{ GB/s}}{54.4 \text{ GB/token}} \approx 16.54 \text{ tokens/second}$$
     (To achieve higher throughput, quantization to FP8 ($27.2\text{ GB}$) doubles generation speed to $\approx 33.1\text{ tokens/s}$).

2. **Differentiate Static VLIW vs Dynamic Warp Execution**:
   Why is dynamic sliding window attention easier to optimize on Blackwell than on TPU v6e?
   - *Solution*: Blackwell schedules warps dynamically. When alternating between 4k sliding window and 8k global attention, the GPU can issue different tile sizes to different streaming multiprocessors without recompilation. TPU v6e relies on static VLIW instruction bundling; altering attention masks across layers can force XLA to generate multiple distinct execution programs.

### Troubleshooting FAQ

- **Q: Does JAX code compiled on a TPU run without modifications on Blackwell?**
  *A*: Yes. The high-level JAX code (`model.apply()`, `jax.jit()`, `NamedSharding`) is 100% portable. When executing on NVIDIA, XLA invokes the `se_gpu` (StreamExecutor GPU) backend, emitting PTX instead of TPU VLIW assembly.
- **Q: What is the primary difference in bfloat16 handling between Google TPU and NVIDIA Blackwell?**
  *A*: Both architectures implement identical IEEE 754 bfloat16 bit representations (1 sign bit, 8 exponent bits, 7 mantissa bits). Computations yield bit-identical numerical results across both platforms.
