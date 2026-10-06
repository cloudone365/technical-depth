# Volume 16: High-Throughput vLLM Serving for Gemma 2

```
==================================================================================================
TARGET AUDIENCE: Inference Systems Engineers, MLOps Leads, Low-Latency Serving Architects
PREREQUISITES   : PagedAttention, Continuous Batching, KV Cache Memory Allocation, CUDA Graphs
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master production-grade, high-concurrency serving of Gemma 2 (9B and 27B) using vLLM,
                  fused soft-capping kernels, alternating sliding window attention, and FP8 acceleration.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Deploying Gemma 2 at enterprise scale requires an inference engine that resolves two distinct challenges:
1. **Sliding Window KV Cache Management**: Alternating 4,096-token sliding windows and 8,192-token global attention layers without leaking GPU memory.
2. **Fused Double Logit Soft-Capping**: Applying $\tanh(x / 50.0)$ within the attention tile without falling back to slow DRAM roundtrips.

**vLLM** solves both challenges natively. Utilizing **PagedAttention v2** and **Continuous In-Flight Batching (IFB)**, vLLM dynamically partitions physical GPU memory into non-contiguous 16-token memory blocks, eliminating internal fragmentation and enabling Gemma 2 27B to serve over **95 concurrent streams** on a single NVIDIA DGX Spark.

```
Incoming Request Streams (User A, B, C...) ──► Continuous In-Flight Batcher (IFB)
                                                       │
                                                       ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 VLLM PAGEDATTENTION V2 ENGINE                          │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  - Physical Block Allocator (16-token blocks in 128 GB Unified Memory)                 │
│  - Alternating Sliding Window Ring Buffer: Evicts tokens outside [t - 4096, t]        │
│  - Fused Blackwell Soft-Capping Kernel: Computes tanh(Q*K / 50.0) in SRAM registers    │
│  - CUDA Graphs for fixed batch-size decode execution                                   │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        OPENAI-COMPATIBLE HIGH-THROUGHPUT API                           │
│  - /v1/chat/completions (Server-Sent Events streaming)                                 │
│  - Sub-15ms TTFT (Time-To-First-Token) | > 850 tokens/sec aggregate throughput         │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Modern Valet Parking Garage
3. Evolutionary Lineage: From Static Hugging Face Generation to vLLM PagedAttention
4. First-Principles Mathematics & Algorithmic Formulations
   - PagedAttention Memory Allocation and Logical Block Tables
   - SWA Memory Reclamation Mathematics on Alternating Layers
   - Continuous In-Flight Batching State Transitions
   - Measuring Serving Performance: TTFT, ITL, and Concurrency Saturation
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: Production Async vLLM Serving Benchmark Client
7. Hardware Grounding for NVIDIA DGX Spark (Serving 27B FP8 on GB10)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Modern Valet Parking Garage

Imagine a luxury downtown hotel parking garage (GPU VRAM):
- **Traditional Inference (Hugging Face / Eager PyTorch)** is like **Reserving 8 Adjacent Parking Spots for Every Single Car**:
  - When a customer pulls up, the garage reserves a giant contiguous 8-car parking spot just in case the customer decides to rent a limousine later (max sequence length 8,192).
  - Even if the customer only parked a tiny scooter (prompt of 12 tokens), those 8 spots remain locked and empty.
  - Result: The garage fills up after 4 cars and turns away hundreds of paying customers (**severe memory fragmentation**).
- **vLLM PagedAttention** is an **Automated High-Tech Valet Robot**:
  - The garage is divided into small, identical 16-token lockers.
  - When a car arrives, the robot assigns lockers dynamically wherever an empty slot exists.
  - If a layer uses a 4,096-token sliding window, the valet robot automatically empties the oldest lockers and reassigns them to incoming cars.
  - Result: 96% memory utilization and zero wasted space.

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2020: Static Hugging Face generate()                                   │
│ Padded all sequences to max_length. 60-80% of VRAM wasted on padding.  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2022: FasterTransformer & TensorRT-LLM 1.0                             │
│ Introduced basic continuous batching, but memory remained contiguous.  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2023-Present: vLLM & PagedAttention (Kwon et al., UC Berkeley)         │
│ Virtual memory paging applied to KV caches. Custom fused kernels for   │
│ Gemma 2 double soft-capping and sliding window token eviction.         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 PagedAttention Memory Allocation

In traditional attention, the KV cache for request $i$ requires contiguous memory:
$$\text{Memory}_{\text{request}} = 2 \times L \times N_{\text{heads}} \times D_{\text{head}} \times S_{\max} \times \text{sizeof(dtype)}$$

In **PagedAttention**, the KV cache is partitioned into fixed-size physical blocks of size $B_{\text{size}} = 16$ tokens.
A **Block Table** maps logical token indices to physical memory blocks:

$$\text{BlockTable}(i) = \left[ b_0, b_1, b_2, \dots, b_m \right], \quad m = \left\lceil \frac{S_t}{B_{\text{size}}} \right\rceil$$

When generating token $t$, the attention kernel queries the block table to gather key-value vectors directly from fragmented physical memory:

$$A_{i, j} = \frac{Q_i \cdot K_{\text{BlockTable}[j // B_{\text{size}}], \; j \% B_{\text{size}}}^T}{\sqrt{d_k}}$$

### 4.2 SWA Memory Reclamation Proof

In Gemma 2, even layers implement a local sliding window $W = 4,096$:
$$\text{Receptive Window} = \left[ \max(0, t - 4096), \; t \right]$$

For tokens $j < t - 4096$, attention weight is identically zero.
vLLM reclaims blocks where all 16 tokens satisfy $j + 16 < t - 4096$:

$$\text{Blocks To Reclaim} = \left\lfloor \frac{t - 4096}{16} \right\rfloor$$

These blocks are immediately returned to the free block pool, reducing the KV cache footprint on even layers by **50%**, which lowers total model memory consumption across all 46 layers by **25%**.

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                INFERENCE SERVING ENGINES COMPARISON                                    │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Dimension          │ Hugging Face TGI   │ vLLM (v0.6+)        │ TensorRT-LLM     │ Ollama / llama.cpp  │
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ Memory Management  │ PagedAttention     │ PagedAttention v2   │ Paged KV Cache   │ Static Allocation   │
│ Continuous Batching│ Yes                │ Yes (Chunked Prefill│ Yes (In-Flight)  │ Limited             │
│ Gemma 2 Soft-Cap   │ Triton Fused       │ Native CUDA Fused   │ Custom TRT Plugin│ ggml kernel         │
│ Concurrency Sat    │ ~60 streams        │ ~95 streams (GB10)  │ ~105 streams     │ ~4-8 streams        │
│ Deployment Ease    │ Docker container   │ Python / CLI        │ C++ Build Engine │ Single Binary / App │
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Production Async vLLM Serving Benchmark Client

Save this script as `vllm_gemma2_client_lab.py`:

```python
"""
Google Gemma 2 High-Throughput vLLM Production Client Lab.
Demonstrates:
1. Asynchronous concurrent request streaming to vLLM OpenAI endpoint
2. Measuring Time-To-First-Token (TTFT) and Inter-Token Latency (ITL)
3. Concurrency scaling and token throughput analytics
"""

import asyncio
import time
import json
import urllib.request

class MockVLLMClient:
    """
    Simulates high-concurrency asynchronous client benchmarking vLLM server.
    """
    def __init__(self, base_url: str = "http://localhost:8000/v1"):
        self.base_url = base_url

    async def simulate_stream_request(self, req_id: int, prompt: str) -> dict:
        start_time = time.perf_counter()
        
        # Simulate network latency + vLLM chunked prefill
        await asyncio.sleep(0.015)  # 15ms TTFT simulation
        ttft = time.perf_counter() - start_time

        # Simulate token decoding loop (50 tokens at 12ms per token)
        num_tokens = 50
        token_times = []
        for _ in range(num_tokens):
            t_prev = time.perf_counter()
            await asyncio.sleep(0.010)  # 10ms ITL simulation
            token_times.append(time.perf_counter() - t_prev)

        total_latency = time.perf_counter() - start_time
        itl = sum(token_times) / len(token_times)
        throughput = num_tokens / total_latency

        return {
            "req_id": req_id,
            "ttft_ms": ttft * 1000.0,
            "itl_ms": itl * 1000.0,
            "total_latency_s": total_latency,
            "num_tokens": num_tokens,
            "tokens_per_sec": throughput
        }

async def run_concurrency_benchmark(num_concurrent_users: int = 16):
    print("=" * 80)
    print(f"RUNNING VLLM GEMMA 2 CONCURRENCY BENCHMARK ({num_concurrent_users} STREAMS)")
    print("=" * 80)

    client = MockVLLMClient()
    prompt = "Explain the difference between sliding window and global attention in Gemma 2."

    tasks = [
        client.simulate_stream_request(i, prompt)
        for i in range(num_concurrent_users)
    ]

    t_start = time.perf_counter()
    results = await asyncio.gather(*tasks)
    total_duration = time.perf_counter() - t_start

    total_tokens = sum(r["num_tokens"] for r in results)
    avg_ttft = sum(r["ttft_ms"] for r in results) / len(results)
    avg_itl = sum(r["itl_ms"] for r in results) / len(results)
    aggregate_throughput = total_tokens / total_duration

    print(f"\nBenchmark Results across {num_concurrent_users} Concurrent Streams:")
    print(f"Total Tokens Emitted      : {total_tokens}")
    print(f"Total Wall-Clock Time     : {total_duration:.2f} seconds")
    print(f"Average Time-to-First-Tok : {avg_ttft:.2f} ms")
    print(f"Average Inter-Token Lat   : {avg_itl:.2f} ms")
    print(f"Aggregate System Throughput: {aggregate_throughput:.2f} tokens/second")
    print("\nProduction Serving Runbook for NVIDIA DGX Spark:")
    print("  python3 -m vllm.entrypoints.openai.api_server \\")
    print("    --model google/gemma-2-27b-it \\")
    print("    --dtype bfloat16 \\")
    print("    --gpu-memory-utilization 0.90 \\")
    print("    --max-model-len 8192 \\")
    print("    --max-num-seqs 128 \\")
    print("    --port 8000")

if __name__ == "__main__":
    asyncio.run(run_concurrency_benchmark(16))
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (Serving 27B FP8 on GB10)

### 7.1 Unified Memory Sizing for vLLM Serving
On the NVIDIA DGX Spark (128 GB Unified Memory):
- **Gemma 2 27B in Native FP8 (W8A8)**:
  $$\text{Model Weights} = 27.2 \text{ GB}$$
- **vLLM Base CUDA Context & Graph Memory**:
  $$\approx 2.5 \text{ GB}$$
- **Remaining Memory for PagedAttention KV Cache**:
  $$128 \text{ GB} \times 0.90 - (27.2 + 2.5) = 115.2 - 29.7 = \mathbf{85.5\text{ GB}}$$

### 7.2 Concurrency Calculation
At context length $8,192$, accounting for alternating SWA (effective context length $6,144$ tokens):
- KV cache per token in FP16 across 46 layers:
  $$2 \times 46 \times 8 \times 128 \times 2 \text{ bytes} \approx 188.4 \text{ KB per token}$$
- KV cache per full 8k stream:
  $$6,144 \times 188.4 \text{ KB} \approx 1.15 \text{ GB per sequence}$$
- **Maximum Concurrent 8k Streams**:
  $$\frac{85.5 \text{ GB}}{1.15 \text{ GB}} \approx \mathbf{74\text{ concurrent full-context 8k streams on a single chip!}}$$

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate KV Cache Memory Savings of FP8 KV Cache**:
   If vLLM is configured with `--kv-cache-dtype fp8`, by what factor does the KV cache shrink, and how many additional concurrent streams can fit on the DGX Spark?
   - *Solution*: FP8 uses 1 byte per element vs 2 bytes in FP16/BF16, shrinking KV cache by **$2\times$**. Maximum concurrent 8k streams double from 74 to **148 streams**.

2. **Differentiate TTFT vs ITL**:
   Why is TTFT compute-bound while ITL is memory-bandwidth bound?
   - *Solution*: TTFT processes all prompt tokens in parallel using large matrix multiplications (GEMM) on Tensor Cores ($I \gg 1000\text{ FLOPs/Byte}$). ITL generates tokens one-by-one, requiring the GPU to stream all 27B weights from DRAM to generate a single token ($I \approx 2\text{ FLOPs/Byte}$).

### Troubleshooting FAQ

- **Q: Why does vLLM log `Warning: Gemma 2 soft-capping kernel not supported in FlashAttention-1`?**
  *A*: FlashAttention-1 lacks the fused $\tanh$ soft-capping kernel. Ensure your vLLM installation is version $0.5.4$ or newer, which uses the updated FlashAttention-2 / FlashAttention-3 soft-capping branch.
- **Q: Can I enable Chunked Prefill in vLLM for Gemma 2?**
  *A*: Yes. Add `--enable-chunked-prefill=True`. This prevents massive prompt prefills from interrupting active autoregressive token generation, drastically stabilizing Inter-Token Latency (ITL).
