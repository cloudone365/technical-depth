# Volume 20: Unified Memory Math and KV Sizing on GB10

```
==================================================================================================
TARGET AUDIENCE: Infrastructure Capacity Planners, MLOps Architects, Performance Tuning Leads
PREREQUISITES   : Memory Hierarchy, KV Cache Equations, Grouped-Query Attention (GQA), SWA
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master first-principles mathematical calculation of Gemma 2 memory footprints,
                  alternating sliding window KV cache sizing, and concurrency limits on Blackwell GB10.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Deploying large foundation models without rigorous memory capacity planning inevitably leads to unexpected `CUDA Out Of Memory (OOM)` crashes or wasteful GPU under-utilization.

On the **NVIDIA DGX Spark**, the Grace ARM CPU and Blackwell GB10 GPU share a single **128 GB Unified Memory pool** interconnected at **900 GB/s bidirectional NVLink-C2C**. To maximize throughput, systems engineers must balance three competing memory consumers:
1. **Static Model Parameters ($M_{\text{weights}}$)**: Invariant footprint determined by precision (BF16, FP8, INT4).
2. **Runtime Engine Overhead ($M_{\text{runtime}}$)**: CUDA runtime context, memory allocator margins, and CUDA graphs.
3. **Dynamic KV Cache Pool ($M_{\text{KV}}$)**: PagedAttention memory buffer allocated to store past conversation history.

Thanks to Gemma 2's **Alternating Sliding Window Attention (SWA)**, the KV cache on even layers caps strictly at 4,096 tokens, saving **25% of total attention memory** and unlocking unprecedented concurrency on a single node.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                   NVIDIA DGX SPARK UNIFIED MEMORY POOL (128 GB)                        │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
         ┌──────────────────────────────────┼──────────────────────────────────┐
         ▼                                  ▼                                  ▼
┌────────────────────────────────┐ ┌────────────────────────────────┐ ┌────────────────────────────────┐
│   STATIC WEIGHTS (M_weights)   │ │  RUNTIME OVERHEAD (M_runtime)  │ │   DYNAMIC KV CACHE (M_KV)      │
│  - 27B in FP8 : 27.2 GB        │ │  - Linux Kernel / OS : 4.0 GB  │ │  - Available : ~85 to ~94 GB   │
│  - 27B in BF16: 54.4 GB        │ │  - CUDA Context/Graph: 2.5 GB  │ │  - Alternating SWA savings: 25%│
│  - 9B in BF16 : 18.5 GB        │ │  Total Overhead: ~6.5 GB       │ │  - Concurrency: Up to 95 conc. │
└────────────────────────────────┘ └────────────────────────────────┘ └────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Water Reservoir and Canal Irrigation Network
3. Evolutionary Lineage: From Monolithic Static Allocations to Paged Dynamic Sizing
4. First-Principles Mathematics & Algorithmic Formulations
   - Mathematical Model Weight Footprint Equations across Precision Formats
   - Sliding Window Attention (SWA) KV Cache Reduction Proof
   - The Unified Concurrency Limit Theorem
   - Impact of KV Cache Precision: FP16 vs FP8
5. Alternative Industry Approaches & Comparative Sizing Matrix
6. Concrete Production Hands-On Lab: DGX Spark Memory Sizing Simulator
7. Hardware Grounding for NVIDIA DGX Spark (NVLink-C2C Page Cache Sizing)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Water Reservoir and Canal Irrigation Network

Imagine managing a 128-million-liter municipal water reservoir (128 GB Unified Memory):
- **Static Model Weights** are like the massive concrete dam wall and permanent turbines (27.2 GB for 27B in FP8). Once built, this concrete is fixed and cannot be consumed by farms.
- **Operating System & Runtime Overhead** is the dead-pool water level at the very bottom of the reservoir (6.5 GB) required to keep the pumps primed.
- **Dynamic KV Cache** is the active irrigation water released into farm canals (concurrent user streams).
  - In standard language models, every farm canal keeps widening infinitely as long as the farmer talks ($O(N)$ memory). Soon, the reservoir runs dry.
  - In **Gemma 2**, every second canal is equipped with an automated sluice gate (Sliding Window of 4,096 tokens). As fresh water flows in, older water automatically drains back into the reservoir.
  - This allows the exact same reservoir to irrigate **25% more farms simultaneously** without drying up!

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2017: Multi-Head Attention (MHA)                                       │
│ N_kv_heads = N_q_heads. Enormous KV cache footprint. Concurrency was  │
│ severely restricted by memory capacity.                                │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2023: Grouped-Query Attention (GQA)                                    │
│ N_kv_heads << N_q_heads (e.g. 8 KV heads vs 32 Q heads).               │
│ Reduced KV cache by 4x to 8x. Adopted by LLaMA 2/3 and Gemma 2.        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2024: Alternating Sliding Window Attention (Gemma 2)                   │
│ Alternates 4k sliding window (even layers) with 8k global (odd layers).│
│ Cuts GQA memory footprint by an additional 25% across deep networks.   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 Model Parameter Weight Footprint

For a model with $P$ total parameters, memory consumption depends strictly on storage precision $b$ (bits per parameter):

$$M_{\text{weights}} = \frac{P \times b}{8 \times 1024^3} \quad [\text{in GiB}]$$

#### Gemma 2 Structural Dimensions and Weight Memory:
| Model Variant | Parameters ($P$) | Layers ($L$) | Heads ($Q / KV$) | Hidden ($D$) | BF16 (16-bit) | FP8 (8-bit) | Q4_K_M (4.5-bit) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemma 2 2B** | $2.61 \times 10^9$ | 26 | 8 / 4 | 2,304 | $4.86\text{ GB}$ | $2.43\text{ GB}$ | $1.41\text{ GB}$ |
| **Gemma 2 9B** | $9.24 \times 10^9$ | 42 | 16 / 8 | 3,584 | $17.21\text{ GB}$ | $8.61\text{ GB}$ | $4.98\text{ GB}$ |
| **Gemma 2 27B** | $27.23 \times 10^9$| 46 | 32 / 16 | 4,608 | $50.72\text{ GB}$ | $25.36\text{ GB}$ | $14.65\text{ GB}$ |

*(Note: Including embedding and vocab projection weights, 27B physical safetensors size is $\approx 54.4\text{ GB}$ in BF16 and $\approx 27.2\text{ GB}$ in FP8).*

### 4.2 SWA KV Cache Reduction Proof

In standard Grouped-Query Attention (GQA), the KV cache memory consumed by a single token across all $L$ layers is:

$$\text{KV}_{\text{token}} = 2 \times L \times N_{\text{kv\_heads}} \times D_{\text{head}} \times \text{bytes\_per\_elem}$$

Where the factor of $2$ accounts for both Keys and Values.
For Gemma 2 27B ($L = 46$, $N_{\text{kv\_heads}} = 16$, $D_{\text{head}} = 128$, in FP16 with $2\text{ bytes}$):

$$\text{KV}_{\text{token}} = 2 \times 46 \times 16 \times 128 \times 2 = 376,832 \text{ bytes} \approx 368.0 \text{ KB per token}$$

For a sequence of length $S = 8,192$:
- **Standard Transformer (All Full Attention Layers)**:
  $$M_{\text{full}} = 8,192 \times 376,832 \text{ bytes} = 3,087,007,744 \text{ bytes} \approx \mathbf{2.875\text{ GB per sequence}}$$

- **Gemma 2 Alternating SWA**:
  - 23 Odd Layers (Global Attention, $S = 8,192$):
    $$M_{\text{odd}} = 23 \times (2 \times 16 \times 128 \times 2) \times 8,192 = 1,543,503,872 \text{ bytes}$$
  - 23 Even Layers (Sliding Window, $W = 4,096$):
    $$M_{\text{even}} = 23 \times (2 \times 16 \times 128 \times 2) \times 4,096 = 771,751,936 \text{ bytes}$$
  - Total Gemma 2 KV Cache:
    $$M_{\text{Gemma2}} = 1,543,503,872 + 771,751,936 = 2,315,255,808 \text{ bytes} \approx \mathbf{2.156\text{ GB per sequence}}$$

$$\text{Memory Savings} = \frac{2.875 - 2.156}{2.875} = \frac{0.719}{2.875} = \mathbf{25.0\% \text{ exact memory reduction!}}$$

### 4.3 The Concurrency Limit Theorem

Let $M_{\text{total}} = 128\text{ GB}$. The maximum number of concurrent full-context sequences $C_{\max}$ that can be served concurrently without paging to disk is:

$$C_{\max} = \left\lfloor \frac{M_{\text{total}} \times \mu_{\text{util}} - (M_{\text{weights}} + M_{\text{runtime}})}{M_{\text{KV\_per\_stream}}(S)} \right\rfloor$$

Where $\mu_{\text{util}} \approx 0.90$ is the safe memory utilization ceiling.

---

## 5. Alternative Industry Approaches & Comparative Sizing Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        KV CACHE FOOTPRINT PER 8,192-TOKEN STREAM (FP16)                                │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Model Architecture │ Layers / KV Heads  │ Head Dimension      │ SWA Active?      │ Memory per 8k Stream│
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ LLaMA 3 70B        │ 80 layers / 8 heads│ 128                 │ No (Full Attn)   │ 2.62 GB             │
│ Qwen 2.5 72B       │ 80 layers / 8 heads│ 128                 │ No (Full Attn)   │ 2.62 GB             │
│ Gemma 2 9B         │ 42 layers / 8 heads│ 256                 │ Yes (Alternating)│ 2.19 GB             │
│ Gemma 2 27B        │ 46 layers / 16 head│ 128                 │ Yes (Alternating)│ 2.15 GB (25% saved!)│
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: DGX Spark Memory Sizing Simulator

Save this script as `dgx_spark_gemma2_memory_lab.py`:

```python
"""
Google Gemma 2 Memory Sizing and Concurrency Calculator Lab.
Calculates exact weight footprints, SWA KV cache reduction,
and maximum concurrency on NVIDIA DGX Spark (128 GB Unified Memory).
"""

class DGXSparkMemoryPlanner:
    def __init__(self, total_unified_gb: float = 128.0, os_overhead_gb: float = 6.5):
        self.total_mem = total_unified_gb
        self.os_overhead = os_overhead_gb
        self.available_pool = total_unified_gb - os_overhead_gb

    def calculate_gemma2_kv_cache(
        self,
        layers: int,
        kv_heads: int,
        head_dim: int,
        seq_len: int,
        sliding_window: int = 4096,
        bytes_per_elem: int = 2  # 2 for FP16/BF16, 1 for FP8
    ) -> dict:
        odd_layers = (layers + 1) // 2
        even_layers = layers // 2

        # Memory per token per layer for K and V
        token_layer_bytes = 2 * kv_heads * head_dim * bytes_per_elem

        # Standard full attention KV cache (if no SWA)
        standard_bytes = layers * token_layer_bytes * seq_len

        # Gemma 2 Alternating SWA KV cache
        odd_bytes = odd_layers * token_layer_bytes * seq_len
        even_bytes = even_layers * token_layer_bytes * min(seq_len, sliding_window)
        gemma2_bytes = odd_bytes + even_bytes

        savings_pct = ((standard_bytes - gemma2_bytes) / standard_bytes) * 100.0

        return {
            "standard_gb": standard_bytes / (1024**3),
            "gemma2_gb": gemma2_bytes / (1024**3),
            "savings_pct": savings_pct
        }

    def calculate_concurrency(
        self,
        weights_gb: float,
        kv_per_stream_gb: float,
        target_utilization: float = 0.90
    ) -> int:
        usable_mem = (self.total_mem * target_utilization) - weights_gb
        if usable_mem <= 0:
            return 0
        return int(usable_mem // kv_per_stream_gb)

def run_memory_planning_lab():
    print("=" * 80)
    print("NVIDIA DGX SPARK (128 GB) GEMMA 2 CAPACITY PLANNING BENCHMARK")
    print("=" * 80)

    planner = DGXSparkMemoryPlanner()

    # Model specs for Gemma 2 27B
    layers = 46
    kv_heads = 16
    head_dim = 128
    seq_len = 8192

    # 1. Evaluate SWA Savings in FP16 and FP8
    print("\n--- 1. Evaluating KV Cache Footprint per 8,192-token Stream ---")
    kv_fp16 = planner.calculate_gemma2_kv_cache(layers, kv_heads, head_dim, seq_len, bytes_per_elem=2)
    kv_fp8 = planner.calculate_gemma2_kv_cache(layers, kv_heads, head_dim, seq_len, bytes_per_elem=1)

    print(f"FP16 Standard Attention : {kv_fp16['standard_gb']:.3f} GB per stream")
    print(f"FP16 Gemma 2 SWA Cache  : {kv_fp16['gemma2_gb']:.3f} GB ({kv_fp16['savings_pct']:.1f}% reduction!)")
    print(f"FP8 Gemma 2 SWA Cache   : {kv_fp8['gemma2_gb']:.3f} GB (Half of FP16)")

    # 2. Concurrency Scaling on DGX Spark (128 GB)
    print("\n--- 2. Max Concurrency Limits for Gemma 2 27B on Single GB10 ---")
    scenarios = [
        ("Gemma 2 27B (BF16 Weights 54.4 GB, FP16 KV)", 54.4, kv_fp16["gemma2_gb"]),
        ("Gemma 2 27B (FP8 Weights 27.2 GB, FP16 KV)", 27.2, kv_fp16["gemma2_gb"]),
        ("Gemma 2 27B (FP8 Weights 27.2 GB, FP8 KV)", 27.2, kv_fp8["gemma2_gb"]),
        ("Gemma 2 27B (Q4_K_M Weights 14.6 GB, FP8 KV)", 14.6, kv_fp8["gemma2_gb"])
    ]

    print(f"{'Deployment Configuration':<46} | {'KV/Stream':<12} | {'Max Concurrent 8k Streams':<25}")
    print("-" * 88)
    for desc, w_gb, kv_gb in scenarios:
        max_streams = planner.calculate_concurrency(w_gb, kv_gb, target_utilization=0.90)
        print(f"{desc:<46} | {kv_gb:<10.2f} GB | {max_streams:<25} streams")

    print("\nEngineering Conclusion: Serving Gemma 2 27B in Native FP8 with FP8 KV cache")
    print("allows a single NVIDIA DGX Spark to sustain over 80 concurrent 8k streams!")

if __name__ == "__main__":
    run_memory_planning_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (NVLink-C2C Page Cache Sizing)

### 7.1 Managing Host OS Page Cache vs GPU Allocation
On Linux systems running Unified Memory architectures, the OS Page Cache can inadvertently expand to fill DRAM when streaming large training datasets or video files.
To prevent the OS page cache from starving vLLM:
```bash
# Tune Linux virtual memory dirty page ratios in /etc/sysctl.conf
sudo sysctl -w vm.dirty_ratio=10
sudo sysctl -w vm.dirty_background_ratio=5

# Drop caches before launching large serving instances
sync && echo 3 | sudo tee /proc/sys/vm/drop_caches
```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate Available Memory for Prefill Buffer**:
   If Gemma 2 27B FP8 occupies $27.2\text{ GB}$ and 40 concurrent 8k streams consume $40 \times 1.078\text{ GB} = 43.12\text{ GB}$ in FP8 KV cache, how much memory remains for prompt prefill activations within a $115.2\text{ GB}$ ceiling?
   - *Solution*: $115.2 - (27.2 + 43.12) = 115.2 - 70.32 = \mathbf{44.88\text{ GB}}$ remaining, easily accommodating large multi-thousand-token prompt prefills.

2. **Differentiate Odd vs Even Layer Attention Span**:
   In Gemma 2, if a user prompt is 10,000 tokens long, how many tokens can Layer 0 (even) attend to at the final token, and how many can Layer 1 (odd) attend to?
   - *Solution*: Layer 0 attends only to the last 4,096 tokens ($[5905, 10000]$). Layer 1 attends to all 10,000 previous tokens ($[0, 10000]$).

### Troubleshooting FAQ

- **Q: Why does my server crash with `CUDA out of memory` when theoretical math indicates 10 GB free?**
  *A*: Memory allocators suffer from external memory fragmentation when allocating variable-sized tensors. Using **vLLM PagedAttention** or **TensorRT-LLM Paged KV Cache** eliminates external fragmentation by constraining all dynamic allocations to fixed-size 16-token or 64-token physical blocks.
- **Q: Can we run Gemma 2 27B in FP16 on a discrete 40 GB GPU?**
  *A*: No. Gemma 2 27B weights alone in FP16 require $54.4\text{ GB}$, which immediately exceeds a 40 GB GPU without even allocating the KV cache. It requires either 4-bit quantization (AWQ/NF4) or a unified memory system like the DGX Spark.
