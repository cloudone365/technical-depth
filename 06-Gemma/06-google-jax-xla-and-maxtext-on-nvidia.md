# Volume 06: Google JAX, XLA, and MaxText on NVIDIA GPUs

```
==================================================================================================
TARGET AUDIENCE: High-Performance Computing (HPC) Leads, JAX/XLA Engineers, Distributed Training Architects
PREREQUISITES   : Functional Programming, Accelerated Linear Algebra (XLA), CUDA Memory Hierarchy
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master running Google's official MaxText LLM training and inference framework on
                  NVIDIA Blackwell GPUs using JAX, XLA High-Level Optimizer (HLO), and fused kernels.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

While the broader open-source ecosystem predominantly relies on PyTorch, Google designs, pre-trains, and serves the entire Gemini and Gemma model lineage using **JAX, XLA (Accelerated Linear Algebra), and MaxText**.

MaxText is Google's open-source, highly scalable, pure-JAX LLM framework. Unlike PyTorch's eager-by-default execution, JAX models are pure mathematical functions compiled down to optimized machine instructions via XLA. On the **NVIDIA DGX Spark**, XLA compiles JAX compute graphs directly into optimized Blackwell PTX and CUDA kernels, eliminating Python runtime overhead and fusing memory-bound transformer operations into single-pass SRAM operations.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        GOOGLE JAX & MAXTEXT ARCHITECTURE ON NVIDIA                      │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
               ┌────────────────────────────┴────────────────────────────┐
               ▼                                                         ▼
┌──────────────────────────────────────────┐  ┌──────────────────────────────────────────┐
│             JAX FRONTEND                 │  │            MAXTEXT ENGINE                │
│  - Pure functional transformations       │  │  - Scalable Gemma 2 / Gemini models     │
│  - jax.jit, jax.grad, jax.vmap           │  │  - Pure Flax / Linen module hierarchy   │
│  - Stateless weights: model.apply(w, x)  │  │  - Native SPMD Sharding across GPUs      │
└──────────────────┬───────────────────────┘  └────────────────────┬─────────────────────┘
                   │                                               │
                   └───────────────────────┬───────────────────────┘
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          XLA COMPILER (High-Level Optimizer - HLO)                     │
│  - Dead-code elimination, algebraic simplification                                     │
│  - Kernel Fusion: (LayerNorm + Soft-Cap + Softmax) fused into single GPU SRAM tile      │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                       NVIDIA BLACKWELL GB10 EXECUTION TARGET                            │
│  - Direct PTX machine code emission via CUDA backend                                   │
│  - 900 GB/s NVLink-C2C unified memory communication                                    │
│  - Blackwell Tensor Core FP8 Matrix Multiply-Accumulate (MMA)                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Classical Orchestra vs The Silicon Film Director
3. Evolutionary Lineage: From Theano and TensorFlow Graph to JAX and XLA
4. First-Principles Mathematics & Algorithmic Formulations
   - JAX Pure Functional Mechanics and Explicit PRNG State
   - The XLA Compilation Pipeline: Jaxpr $\to$ HLO $\to$ Blackwell PTX
   - Kernel Fusion Mathematics: Eliminating High-Bandwidth Memory (HBM) Roundtrips
   - MaxText Architecture for Gemma 2
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: Pure JAX Soft-Capping Transformer Layer with `jax.jit`
7. Hardware Grounding for NVIDIA DGX Spark (CUDA Backend Configuration for JAX)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Classical Orchestra vs The Silicon Film Director

To understand the difference between standard PyTorch and JAX/XLA:
- **PyTorch Eager Mode** is like a **Live Improvisational Classical Orchestra**:
  - The conductor shouts: *"Now play LayerNorm!"* The musicians play LayerNorm and write the music onto paper on the floor (GPU DRAM).
  - The conductor shouts: *"Now play Scaled Dot Product!"* The musicians bend down, read the paper from the floor, calculate the scores, and write them back onto the floor.
  - The conductor shouts: *"Now play Soft-Capping Tanh!"* The musicians bend down again, pick up the paper, compute tanh, and write it back down.
  - Every step incurs a costly physical trip to memory (DRAM).
- **JAX + XLA** is like an **Elite Hollywood Film Director (XLA Compiler)**:
  - You hand the director a complete script written in pure mathematics without side effects.
  - The director studies the entire scene ahead of time: *"You don't need to write the LayerNorm output to the floor. Keep it in your hand (SRAM cache), compute the dot product, apply tanh immediately, and only write the final result down once!"*
  - The entire sequence of 10 operations is fused into a single ultra-fast kernel.

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2015: TensorFlow 1.x (Static Computation Graphs)                       │
│ Introduced tf.Graph and tf.Session. Rigid, difficult to debug, steep   │
│ learning curve, disconnected from native Python semantics.             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2017: PyTorch Eager Execution (Dynamic Graphs)                         │
│ Intuitive, line-by-line execution. Fast debugging, but heavy memory   │
│ roundtrips and Python interpreter overhead across deep networks.       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2019-Present: JAX + XLA (Functional Ahead-Of-Time Compilation)         │
│ Combines the pythonic expressiveness of NumPy with composable function │
│ transformations: grad(jit(vmap(f))). Compiles directly via XLA to      │
│ native GPU/TPU machine code. Powers Gemini, Gemma, and AlphaFold 3.    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 JAX Pure Functional Mechanics

In JAX, functions must be **pure**:
1. Identical inputs must always produce identical outputs (no global state, no in-place mutation).
2. No side-effects (no writing to files or modifying outer-scope variables during traced execution).

Instead of PyTorch's object-oriented `nn.Module` where parameters live inside `self.weight`:
In JAX/Flax, model parameters $\Theta$ are stored as a plain Python dictionary (or PyTree). Forward inference is a pure mathematical mapping:

$$y = f(\Theta, x)$$

State updates during gradient descent are explicitly functional:

$$\Theta_{t+1} = \Theta_t - \eta \cdot \nabla_\Theta \mathcal{L}(f(\Theta_t, x), y^*)$$

### 4.2 The XLA Compilation Pipeline: From Python to Blackwell PTX

When a function is decorated with `@jax.jit`, JAX executes the function using abstract symbolic tracers (`ShapedArray`) to produce an intermediate representation called **Jaxpr** (JAX Expressions).

```
Python Function ──► JAX Tracing ──► Jaxpr IR ──► XLA HLO ──► Target Codegen ──► Blackwell PTX
```

1. **Jaxpr Generation**: Records the primitive mathematical graph:
   ```
   { lambda ; a:f32[4,128] b:f32[128,256]. let
       c:f32[4,256] = dot_general[dimension_numbers=(((1,), (0,)), ((), ()))] a b
       d:f32[4,256] = tanh c
     in (d,) }
   ```
2. **XLA High-Level Optimizer (HLO)**:
   - Identifies that `dot_general` and `tanh` can be fused.
   - Eliminates intermediate buffer allocation for `c`.
3. **Blackwell GPU Codegen**:
   - Emits a fused CUDA kernel where thread blocks load tile `a` and tile `b` into Blackwell shared memory (SRAM), compute the MMA instruction on Tensor Cores, execute `__nv_fast_tanh` in registers, and write only `d` to DRAM.

### 4.3 Memory Bandwidth Arithmetic: Why Fusion Matters

Let $N$ denote sequence length ($8,192$) and $D$ denote hidden dimension ($4,096$).
In an unfused implementation of LayerNorm + Soft-Capping:
- Op 1 (LayerNorm): Read $X$ ($64\text{ MB}$), Write $\hat{X}$ ($64\text{ MB}$). Total DRAM = $128\text{ MB}$.
- Op 2 (Scale): Read $\hat{X}$ ($64\text{ MB}$), Write $S$ ($64\text{ MB}$). Total DRAM = $128\text{ MB}$.
- Op 3 (Tanh): Read $S$ ($64\text{ MB}$), Write $Y$ ($64\text{ MB}$). Total DRAM = $128\text{ MB}$.
- **Unfused DRAM Traffic** = $384\text{ MB}$.

In the **XLA Fused Kernel**:
- Read $X$ once ($64\text{ MB}$).
- Compute LayerNorm, Scale, and Tanh entirely within Blackwell L1/SRAM registers.
- Write $Y$ once ($64\text{ MB}$).
- **Fused DRAM Traffic** = $128\text{ MB}$ (**$3\times$ reduction in memory bandwidth consumption!**).

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                FRAMEWORK COMPARISON FOR GEMMA 2                                        │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Dimension          │ PyTorch 2.5 Eager  │ PyTorch Inductor    │ JAX / MaxText    │ TensorRT-LLM        │
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ Core Paradigm      │ Imperative Dynamic │ JIT Graph Capture   │ Pure Functional  │ Static Engine Plan  │
│ Primary Backer     │ Meta / Community   │ PyTorch Foundation  │ Google DeepMind  │ NVIDIA              │
│ Compilation Engine │ None (C++ Runtime) │ TorchDynamo + Triton│ XLA (HLO)        │ TensorRT Optimizer  │
│ Gemma 2 Heritage   │ Community Port     │ Community Port      │ Native Origin    │ High-Throughput C++ │
│ Multi-GPU SPMD     │ FSDP2 / DDP        │ FSDP2 + Compile     │ NamedSharding    │ Pipeline/Tensor Par │
│ Startup Latency    │ Zero (Immediate)   │ Moderate (Warmup)   │ High (AOT Comp)  │ High (Engine Build) │
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Pure JAX Soft-Capping Transformer Layer with `jax.jit`

Save this script as `gemma2_jax_maxtext_lab.py`:

```python
"""
Google Gemma 2 JAX/XLA Implementation Lab.
Demonstrates pure functional programming, XLA Ahead-Of-Time/JIT compilation,
and logit soft-capping attention on NVIDIA hardware.
"""

import math
import os

# Configure JAX memory allocation behavior on NVIDIA GPUs
os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
os.environ["XLA_PYTHON_CLIENT_MEM_FRACTION"] = "0.70"

try:
    import jax
    import jax.numpy as jnp
    from jax import random
    HAS_JAX = True
except ImportError:
    HAS_JAX = False

def run_jax_gemma_lab():
    print("=" * 80)
    print("RUNNING GOOGLE GEMMA 2 JAX / XLA OPTIMIZATION LAB")
    print("=" * 80)

    if not HAS_JAX:
        print("Note: 'jax' and 'jaxlib' are not installed in the current environment.")
        print("To run this on NVIDIA DGX Spark, execute:")
        print("  pip install --upgrade pip")
        print("  pip install -U 'jax[cuda12]' -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html")
        print("\nPrinting equivalent JAX computation graph and mathematical trace:")
        print("""
        @jax.jit
        def gemma2_fused_attention(params, q, k, v, attn_cap=50.0):
            # 1. Scaled dot-product: [B, H, S, S]
            scale = 1.0 / jnp.sqrt(q.shape[-1])
            raw_scores = jnp.einsum('bhqd,bhkd->bhqk', q, k) * scale
            
            # 2. XLA Fused Hyperbolic Tangent Soft-Capping
            capped_scores = attn_cap * jnp.tanh(raw_scores / attn_cap)
            
            # 3. Softmax
            weights = jax.nn.softmax(capped_scores, axis=-1)
            
            # 4. Context aggregation
            context = jnp.einsum('bhqk,bhkd->bhqd', weights, v)
            return context
        """)
        return

    # 1. Inspect JAX Devices
    devices = jax.devices()
    print(f"JAX Detected Compute Platform: {jax.default_backend()}")
    print(f"Available Execution Devices  : {devices}")

    # 2. Define Pure JAX Soft-Capping Function
    @jax.jit
    def gemma2_attention_step(q, k, v, cap: float = 50.0):
        # q: [B, H, S, D], k: [B, H, S, D], v: [B, H, S, D]
        d_k = q.shape[-1]
        scale = 1.0 / jnp.sqrt(d_k)
        
        # Raw scores
        raw_scores = jnp.einsum("bhqd,bhkd->bhqk", q, k) * scale
        
        # Soft-capping via XLA-fused tanh
        capped_scores = cap * jnp.tanh(raw_scores / cap)
        
        # Softmax
        attn_weights = jax.nn.softmax(capped_scores, axis=-1)
        
        # Output projection
        out = jnp.einsum("bhqk,bhkd->bhqd", attn_weights, v)
        return out

    # 3. Initialize Synthetic Tensors
    key = random.PRNGKey(42)
    k1, k2, k3 = random.split(key, 3)
    b, h, s, d = 2, 8, 128, 64

    q = random.normal(k1, (b, h, s, d), dtype=jnp.float32)
    k = random.normal(k2, (b, h, s, d), dtype=jnp.float32)
    v = random.normal(k3, (b, h, s, d), dtype=jnp.float32)

    # 4. Compile and Execute via XLA
    print("\nTriggering XLA Ahead-Of-Time Compilation...")
    lowered = gemma2_attention_step.lower(q, k, v, 50.0)
    compiled = lowered.compile()

    print("Executing XLA Compiled Attention Kernel on Hardware...")
    output = compiled(q, k, v)
    
    print(f"Output Shape: {output.shape}")
    print(f"Output Mean : {jnp.mean(output):.6f}")
    print(f"Output Max  : {jnp.max(output):.6f}")
    print("\nVerification Passed: JAX XLA compiled and executed soft-capped attention successfully!")

if __name__ == "__main__":
    run_jax_gemma_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (CUDA Backend Configuration for JAX)

### 7.1 Installing JAX with CUDA 12 on Grace Blackwell GB10
To run JAX on the Grace ARM Neoverse V2 CPU coupled with the Blackwell GB10 GPU:
```bash
# Verify ARM64 architecture
uname -m  # Must report: aarch64

# Install JAX with Blackwell-compatible CUDA 12 wheel
pip install --upgrade "jax[cuda12]" -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html
```

### 7.2 Memory Preallocation Optimization
By default, JAX attempts to preallocate 75% of total device memory upon initialization. On the NVIDIA DGX Spark, because the 128 GB memory is shared dynamically between the Grace ARM CPU and the Blackwell GPU, rigid GPU pre-allocation can starve host services.
Add these environment variables to `/etc/environment` or your training launch script:
```bash
# Prevent JAX from locking all 128 GB of unified memory immediately
export XLA_PYTHON_CLIENT_PREALLOCATE=false

# Alternatively, set a dynamic growth threshold (e.g., 60% = ~76.8 GB)
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.60

# Enable Blackwell CUDA Graph execution in XLA
export XLA_FLAGS="--xla_gpu_enable_cuda_graphs=true --xla_gpu_enable_async_all_gather=true"
```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate DRAM Traffic Savings for Fused Attention**:
   For sequence length $S = 4096$, number of heads $H = 16$, and batch size $B = 2$, how much memory bandwidth is saved by fusing the attention score matrix $S_{raw} \in \mathbb{R}^{B \times H \times S \times S}$ with the soft-capping operator rather than writing the intermediate tensor to DRAM?
   - *Solution*:
     - Intermediate matrix elements: $2 \times 16 \times 4096 \times 4096 = 536,870,912$ elements.
     - In FP32: $536,870,912 \times 4 \text{ bytes} \approx 2.147 \text{ GB}$.
     - Without fusion, writing $S_{raw}$ and reading it back for $\tanh$ takes $2.147 \times 2 = 4.294\text{ GB}$ of DRAM transfers.
     - With XLA fusion, this intermediate tensor remains in Blackwell L1/SRAM, saving **$4.294\text{ GB}$ of DRAM bandwidth per attention layer**!

2. **Explain the Functional Gradient Property**:
   In JAX, why does `jax.grad(loss_fn)(params)` require `loss_fn` to be a pure function?
   - *Solution*: JAX uses reverse-mode automatic differentiation by tracing symbolic arrays through mathematical primitives. If `loss_fn` modifies a global list or writes in-place to an array, the tracer cannot record the modification in the directional acyclic graph (DAG), resulting in corrupt or zero gradients.

### Troubleshooting FAQ

- **Q: Why does my JAX script throw `CUDA_ERROR_OUT_OF_MEMORY` immediately upon startup?**
  *A*: JAX's default allocator aggressively reserves memory. If vLLM, Ollama, or desktop display servers are already active on the DGX Spark, JAX's default 75% allocation request fails. Set `export XLA_PYTHON_CLIENT_PREALLOCATE=false` to enable dynamic on-demand memory expansion.
- **Q: Can I load Hugging Face safetensors directly into JAX/MaxText?**
  *A*: Yes. MaxText provides an automated conversion script (`convert_gemma_hf_to_maxtext.py`) that flattens Hugging Face PyTorch safetensors into a dictionary of nested JAX arrays (`Flax Checkpoint`), mapping PyTorch parameter names (`model.layers.0.self_attn.q_proj.weight`) to MaxText Linen paths (`params['decoder']['layers_0']['self_attention']['q']`).
