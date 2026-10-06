# Volume 21: Triton Inference Server and TensorRT-LLM Integration

```
==================================================================================================
TARGET AUDIENCE: High-Throughput Serving Engineers, MLOps Architects, Low-Latency Systems Leads
PREREQUISITES   : TensorRT-LLM engine compilation, C++ inference backends, Triton model repository
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Convert NeMo `.nemo` checkpoints into optimized TensorRT-LLM engines and deploy them
                  on Triton Inference Server with In-Flight Batching (IFB), Paged KV-cache, and ensembles.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Deploying large language models via raw PyTorch or Python web frameworks (FastAPI) suffers from severe latency bottlenecks: Python GIL contention, un-fused CUDA kernel launches, and static batching that stalls GPU compute while waiting for the slowest generation to complete.

**NVIDIA Triton Inference Server** coupled with **TensorRT-LLM** represents the pinnacle of enterprise serving performance on NVIDIA hardware. By converting `.nemo` checkpoints into compiled TensorRT execution plans and running them inside Triton's C++ **In-Flight Batching (IFB)** architecture, serving throughput increases by **3x to 5x** while cutting P99 latency in half.

```
       ┌───────────────────────────────────────────────────────────────┐
       │   Trained NeMo Checkpoint Container (.nemo)                   │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                                       │ trtllm-build / nemo.export
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Compiled TensorRT-LLM Engine Plan                           │
       │   - Fused GEMM + SwiGLU Kernels   - Blackwell Native FP8      │
       │   - In-Flight Batching Runtime    - Paged KV-Cache Allocator  │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                                       ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │                 Triton Inference Server Architecture              │
  │                                                                   │
  │   ┌────────────────────────┐  ┌───────────────────────────────┐   │
  │   │ Preprocessing (Python) │  │ Postprocessing (Tokenizer Det)│   │
  │   │ Tokenizes Prompt       │  │ Emits SSE / JSON Stream       │   │
  │   └───────────┬────────────┘  └───────────────▲───────────────┘   │
  │               │                               │                   │
  │               └───────────────┬───────────────┘                   │
  │                               │ BLS Ensemble Pipeline             │
  │                               ▼                                   │
  │   ┌───────────────────────────────────────────────────────────┐   │
  │   │  TensorRT-LLM C++ Execution Backend                       │   │
  │   │  - Continuous / In-Flight Iteration Scheduler             │   │
  │   │  - Dynamic Prefill & Decode Slot Interleaving             │   │
  │   └───────────────────────────────────────────────────────────┘   │
  └───────────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The High-Speed Roller Coaster Dispatcher
3. Evolutionary Lineage: From Static Batching to In-Flight Batching (IFB)
4. First-Principles Mathematics & Algorithmic Formulations
   - The Static Batching Inefficiency Ratio
   - In-Flight Batching (IFB) Iteration-Level Scheduling Calculus
   - Triton Model Repository Ensemble Directory Layout
   - KV Cache Memory Virtual Page Sizing
5. Comparative Trade-Off Matrix: LLM Serving Engines
6. Concrete Production Hands-On Lab: In-Flight Batching (IFB) Scheduler Simulator
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The High-Speed Roller Coaster Dispatcher

Imagine a theme park roller coaster:

- **The Static Batching Approach (The Rigid Train)**:
  A 10-car roller coaster train pulls into the station. 10 passengers board. Passenger 1 wants to ride for 1 loop (5 tokens); Passenger 10 wants to ride for 50 loops (500 tokens).
  The entire train must keep running around the track for 50 loops. Passenger 1 finished their ride 49 loops ago, but they are strapped in and cannot exit. Nobody else can board the empty seats in Cars 1–9. The station platform is backed up with a 2-hour waiting line.

- **The Triton In-Flight Batching Approach (Continuous Boarding)**:
  Triton operates an intelligent continuous track.
  On every single loop around the track (**every token generation step**):
  - If Passenger 1 finishes their ride, their seat unbuckles immediately, and they step off (**EOT Token Detected**).
  - A new passenger waiting on the platform steps into Car 1 instantly (**New Request Prefill Injected**).
  The roller coaster **never stops running with empty seats**, saturating the hardware at 100% capacity.

---

## 3. Evolutionary Lineage: From Static Batching to In-Flight Batching (IFB)

```
Generation 1 (2020-2022)      Generation 2 (2022-2023)      Generation 3 (2024-2026)
Static PyTorch Batching       Naive Dynamic Batching        Triton + TensorRT-LLM IFB
──────────────────────────    ──────────────────────────    ─────────────────────────
- Pad batch to max length     - Batch formed at arrival     - Iteration-level scheduling
- Compute wasted on pads      - Train waits for slowest req - Zero padding overhead
- Massive latency spikes      - Memory fragmentation        - Paged KV-cache virtual memory
- Single-client starvation    - High TTFT under load        - Hardware fused GEMM kernels
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### The Static Batching Inefficiency Ratio

Let batch $B$ contain $N$ requests with output sequence lengths $(L_1, L_2, \dots, L_N)$.
Under static batching, the entire batch runs for:

$$L_{\text{max}} = \max_{i \in [1, N]} L_i$$

The total computational FLOPs expended is proportional to $N \times L_{\text{max}}$.
The actual informative FLOPs delivered is proportional to $\sum_{i=1}^N L_i$.
The **Static Computational Waste Fraction** $W_{\text{static}}$ is:

$$W_{\text{static}} = 1.0 - \frac{\sum_{i=1}^N L_i}{N \times \max_{i} L_i}$$

In production workloads where requests vary from short acknowledgments ($L = 10$) to long essays ($L = 1000$), $W_{\text{static}}$ frequently exceeds **70% to 85%**.

### In-Flight Batching (IFB) Iteration-Level Scheduling Calculus

In-Flight Batching executes scheduling decisions at discrete iteration step boundaries $t \in \mathbb{N}$:

$$\mathcal{B}_t = \mathcal{B}_{t-1} \setminus \mathcal{R}_{\text{finished}}(t) \cup \mathcal{R}_{\text{new}}(t)$$

Where:
- $\mathcal{R}_{\text{finished}}(t) = \{ r \in \mathcal{B}_{t-1} \mid y_{r, t} = \text{EOS} \lor \text{len}(y_r) \ge L_{\text{max}} \}$
- $\mathcal{R}_{\text{new}}(t)$ is the set of pending requests admitted from the queue subject to VRAM KV-cache page availability:

$$\sum_{r \in \mathcal{B}_t} \text{Pages}(r) \le \text{MaxGPUKVBlocks}$$

Compute waste under IFB is identically zero ($W_{\text{IFB}} \approx 0\%$).

### Triton Model Repository Ensemble Directory Layout

Triton organizes LLM execution pipelines into an ensemble of cooperating micro-services:

```
triton_model_repository/
├── ensemble/
│   └── config.pbtxt             <-- Maps HTTP/gRPC input -> Pre -> TRT-LLM -> Post
├── preprocessing/
│   ├── 1/model.py               <-- Fast tokenizer (Converts text to token IDs)
│   └── config.pbtxt
├── tensorrt_llm/
│   ├── 1/                       <-- Compiled .engine binary plan
│   │   ├── rank0.engine
│   │   └── model.json
│   └── config.pbtxt             <-- Configures IFB, KV-cache, and GPU IDs
└── postprocessing/
    ├── 1/model.py               <-- Detokenizer (Converts token IDs to SSE text)
    └── config.pbtxt
```

---

## 5. Comparative Trade-Off Matrix: LLM Serving Engines

| Feature Dimension | Raw PyTorch + FastAPI | vLLM (Python Engine) | Triton + TensorRT-LLM |
| :--- | :--- | :--- | :--- |
| **Backend Implementation** | Python | Python + C++ Kernels | **Pure C++ Architecture** |
| **In-Flight Batching** | No (Static padding) | Yes (PagedAttention) | **Yes (TensorRT-LLM IFB)** |
| **Kernel Fusion** | Unfused PyTorch ops | FlashAttention | **Monolithic CUTLASS GEMM+Act**|
| **Multi-Model Orchestration**| Manual code | Single model focus | **Native BLS Ensembles** |
| **Blackwell Native FP8** | Manual hooks | Good | **Hardware Co-Designed (SOTA)**|
| **Throughput (Tokens/sec)** | 150 tok/s | 1,400 tok/s | **2,200 tok/s (Peak)** |

---

## 6. Concrete Production Hands-On Lab: In-Flight Batching (IFB) Scheduler Simulator

This self-contained Python script implements a discrete-event simulator of Triton's In-Flight Batching iteration engine, demonstrating dynamic request admission, step-by-step token decoding, and instant retirement upon EOS detection.

```python
#!/usr/bin/env python3
"""
Triton Inference Server: In-Flight Batching (IFB) Simulator.
Demonstrates iteration-level continuous scheduling, dynamic slot insertion,
and retirement of completed generations.
"""

from typing import List, Dict, Optional

class InferenceRequest:
    def __init__(self, req_id: str, prompt: str, target_length: int):
        self.req_id = req_id
        self.prompt = prompt
        self.target_length = target_length
        self.generated_tokens = 0
        self.is_finished = False

class InFlightBatchScheduler:
    def __init__(self, max_batch_slots: int = 4):
        self.max_batch_slots = max_batch_slots
        self.active_slots: List[Optional[InferenceRequest]] = [None] * max_batch_slots
        self.waiting_queue: List[InferenceRequest] = []
        self.completed_requests: List[InferenceRequest] = []

    def submit_request(self, req: InferenceRequest):
        self.waiting_queue.append(req)

    def step_iteration(self, current_step: int):
        """Simulates a single forward step of the TensorRT-LLM engine."""
        print(f"\n--- Iteration Step {current_step} ---")

        # Step 1: Evict finished requests from slots
        for idx in range(self.max_batch_slots):
            req = self.active_slots[idx]
            if req and req.is_finished:
                print(f"  [EVICT] Slot {idx}: Request '{req.req_id}' finished (Length {req.generated_tokens}).")
                self.completed_requests.append(req)
                self.active_slots[idx] = None

        # Step 2: Admit new requests into empty slots
        for idx in range(self.max_batch_slots):
            if self.active_slots[idx] is None and self.waiting_queue:
                new_req = self.waiting_queue.pop(0)
                self.active_slots[idx] = new_req
                print(f"  [ADMIT] Slot {idx}: Admitted Request '{new_req.req_id}' (Target: {new_req.target_length} tok).")

        # Step 3: Execute 1 token generation step for all active slots
        active_count = sum(1 for r in self.active_slots if r is not None)
        if active_count == 0:
            print("  [IDLE] No active requests in engine.")
            return

        print(f"  [COMPUTE] Executing continuous batch over {active_count} active slots...")
        for idx in range(self.max_batch_slots):
            req = self.active_slots[idx]
            if req:
                req.generated_tokens += 1
                if req.generated_tokens >= req.target_length:
                    req.is_finished = True

# =====================================================================
# VERIFICATION HARNESS
# =====================================================================

def run_ifb_lab():
    print("=" * 80)
    print("TRITON IN-FLIGHT BATCHING (IFB) SCHEDULER SIMULATION")
    print("=" * 80)

    scheduler = InFlightBatchScheduler(max_batch_slots=3)

    # Submit 5 requests of radically different lengths
    requests = [
        InferenceRequest("REQ-1", "Short query", target_length=2),
        InferenceRequest("REQ-2", "Medium query", target_length=5),
        InferenceRequest("REQ-3", "Long query", target_length=8),
        InferenceRequest("REQ-4", "Quick query", target_length=2),
        InferenceRequest("REQ-5", "Final query", target_length=3)
    ]

    for r in requests:
        scheduler.submit_request(r)

    print(f"Submitted 5 Requests to Scheduler Queue. Max Concurrent Slots = 3.")

    # Run 10 iteration cycles
    for step in range(1, 10):
        scheduler.step_iteration(step)
        if len(scheduler.completed_requests) == len(requests):
            print("\n[ALL REQUESTS COMPLETED]")
            break

    print("\nSummary of Completed Generations:")
    for r in scheduler.completed_requests:
        print(f"  {r.req_id}: Generated {r.generated_tokens} tokens.")

    assert len(scheduler.completed_requests) == 5
    print("\n[SUCCESS] Continuous in-flight scheduling and dynamic slot admission verified.")

if __name__ == "__main__":
    run_ifb_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Operating Triton and TensorRT-LLM on the DGX Spark:
1. **Compiling `.nemo` to TensorRT Engine**:
   ```bash
   python -m nemo.export.tensorrt_llm \
     --nemo_checkpoint /workspace/models/nemotron_70b.nemo \
     --engine_dir /workspace/engines/nemotron_70b_fp8 \
     --max_input_len 4096 \
     --max_output_len 2048 \
     --max_batch_size 32 \
     --fp8
   ```

2. **Launching Triton on Blackwell GB10**:
   ```bash
   docker run --gpus all -d --rm \
     --net=host \
     --shm-size=16g \
     -v /workspace/triton_model_repository:/models \
     nvcr.io/nvidia/tritonserver:24.09-trtllm-py3 \
     tritonserver --model-repository=/models
   ```
   Triton binds directly to the Blackwell GB10 GPU, exposing high-performance gRPC (port `8001`) and HTTP (port `8000`) endpoints.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (KV-Cache Page Allocation Calculus)**:
   Given 64 Transformer layers, $d_{\text{head}} = 128$, $H_{\text{KV}} = 8$ heads, and page block size of 16 tokens in FP16. Calculate the exact bytes consumed per virtual memory page block.

2. **Exercise 2 (Triton Ensemble Config)**:
   Write the `ensemble/config.pbtxt` stanza that wires the output tensor `TOKENS` of the `preprocessing` model to the input tensor `input_ids` of the `tensorrt_llm` model.

### Solutions

**Solution for Exercise 1**:
- Bytes per token per layer: $2 \times H_{\text{KV}} \times d_{\text{head}} \times 2\text{ bytes} = 2 \times 8 \times 128 \times 2 = 4,096\text{ bytes} = 4\text{ KB}$.
- Across 64 layers: $64 \times 4\text{ KB} = 256\text{ KB}$ per token.
- For a page block of 16 tokens: $16 \times 256\text{ KB} = 4,096\text{ KB} = \mathbf{4.0\text{ MB per Page Block}}$.

### Troubleshooting FAQ

- **Q: Triton throws `CUDA error: out of memory` during engine initialization.**
  - *Fix*: TensorRT-LLM pre-allocates the KV-cache. In `tensorrt_llm/config.pbtxt`, reduce `kv_cache_free_gpu_mem_fraction` from default `0.90` to `0.80` to preserve memory for CUDA scratchpads and system allocations.

- **Q: Model fails to stream tokens (waits until full generation completes).**
  - *Fix*: You must configure Triton for decoupled streaming mode. In `config.pbtxt`, ensure `model_transaction_policy { decoupled: true }` is enabled so Triton emits intermediate SSE chunks as individual tokens are decoded.
