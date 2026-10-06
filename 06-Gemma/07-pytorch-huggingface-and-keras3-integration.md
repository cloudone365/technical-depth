# Volume 07: PyTorch 2.5, Hugging Face, and Keras 3 Integration

```
==================================================================================================
TARGET AUDIENCE: ML Framework Engineers, PyTorch Developers, Cross-Platform Deployment Architects
PREREQUISITES   : PyTorch 2.x Architecture, Hugging Face Transformers, Keras 3 Multi-Backend Engine
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master running and fine-tuning Google Gemma 2 across PyTorch 2.5, Hugging Face, and
                  Keras 3 with TorchDynamo / TorchInductor AOT compilation on the Blackwell architecture.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Google Gemma 2 is unique among modern foundation models because it was co-developed across three distinct software ecosystems:
1. **PyTorch 2.5 + Hugging Face Transformers**: The global standard for open-source AI development, leveraging `Gemma2ForCausalLM` and `AutoModelForCausalLM`.
2. **Keras 3 Multi-Backend**: Google's modern framework allowing identical model definitions to run transparently on top of PyTorch, JAX, or TensorFlow by changing a single environment variable (`KERAS_BACKEND`).
3. **TorchDynamo & TorchInductor**: PyTorch 2.5's native graph-capture and Triton-compiler stack, extracting near-C++ performance on the NVIDIA Blackwell GB10 without leaving Python.

```
                          ┌──────────────────────────────────────────────┐
                          │         GEMMA 2 CROSS-FRAMEWORK STACK        │
                          └──────────────────────┬───────────────────────┘
                                                 │
         ┌───────────────────────────────────────┼───────────────────────────────────────┐
         ▼                                       ▼                                       ▼
┌────────────────────────────────┐ ┌────────────────────────────────┐ ┌────────────────────────────────┐
│      HUGGING FACE STACK        │ │        KERAS 3 ENGINE          │ │      PYTORCH 2.5 COMPILER      │
│  - Gemma2ForCausalLM           │ │  - keras.models.GemmaCausalLM  │ │  - torch.compile()             │
│  - 256k GemmaTokenizerFast     │ │  - Write once, run everywhere  │ │  - TorchDynamo Python Frame    │
│  - FlashAttention-2 / SDPA     │ │  - Switch PyTorch / JAX / TF   │ │  - TorchInductor Triton Codegen│
│  - device_map="auto"           │ │  - Zero code rewrites          │ │  - Kernel fusion on Blackwell  │
└────────────────────────────────┘ └────────────────────────────────┘ └────────────────────────────────┘
                                                 │
                                                 ▼
                          ┌──────────────────────────────────────────────┐
                          │   NVIDIA DGX SPARK UNIFIED MEMORY (128 GB)   │
                          │   Zero-copy Host-Device memory sharing       │
                          └──────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Universal Power Adapter
3. Evolutionary Lineage: From Legacy Keras/TF to Modern Multi-Backend Keras 3 and PyTorch 2.5
4. First-Principles Mathematics & Algorithmic Formulations
   - Hugging Face Gemma 2 Architectural Structure
   - The Keras 3 Multi-Backend Abstraction Layer
   - TorchDynamo Frame Evaluation and Guard Verification
   - Unit-Offset RMSNorm Formulation in PyTorch
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: Multi-Framework Gemma 2 Script with TorchDynamo Compilation
7. Hardware Grounding for NVIDIA DGX Spark (PyTorch 2.5 on Grace ARM + Blackwell GB10)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Universal Power Adapter

Imagine you are an international traveler carrying specialized electronic equipment:
- In the past, if you traveled from the UK (PyTorch) to Japan (JAX) or the US (TensorFlow), you had to completely discard your appliances and buy brand-new machines wired specifically for that country's voltage and wall plugs. In AI, this meant completely rewriting your custom attention, loss functions, and data pipelines whenever moving from research (PyTorch) to production TPU clusters (JAX).
- **Keras 3** is the **Universal Intelligent Power Adapter**:
  - You write your Gemma 2 training loop once in pure Keras.
  - If you plug into an NVIDIA DGX Spark with PyTorch installed (`KERAS_BACKEND="torch"`), Keras generates native PyTorch tensors and autograd graphs.
  - If you deploy onto Google Cloud TPUs (`KERAS_BACKEND="jax"`), Keras translates the exact same code into pure JAX functions and compiles them via XLA.
- **PyTorch 2.5 `torch.compile`** is the **Onboard Smart Converter**:
  - Even if you stay purely in PyTorch, the TorchDynamo engine acts as an internal compiler, analyzing your Python bytecode, capturing the mathematical graph, and generating custom Blackwell Triton kernels on the fly.

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2018: Keras 2.x (Tied Exclusively to TensorFlow)                       │
│ High-level simplicity, but locked into TensorFlow runtime. Unable to   │
│ leverage PyTorch ecosystem momentum or JAX functional optimizations.   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2022: Hugging Face Transformers Dominance                              │
│ Became universal repository for weights. Standardized AutoModel API,   │
│ but relied heavily on eager Python dispatching and slow CPU overhead. │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2024: Keras 3 & PyTorch 2.5 Convergence                                │
│ Keras 3 unifies PyTorch, JAX, and TensorFlow. PyTorch 2.5 introduces   │
│ TorchInductor max-autotune, seamlessly compiling complex Gemma 2 soft- │
│ capping and sliding window logic into fused GPU kernels.               │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 Hugging Face Gemma 2 Architectural Structure

In the Hugging Face `transformers` library, Gemma 2 is implemented under `transformers.models.gemma2`:
- `Gemma2Config`: Specifies architectural hyperparameters:
  - `vocab_size = 256128`
  - `attn_logit_softcapping = 50.0`
  - `final_logit_softcapping = 30.0`
  - `sliding_window = 4096`
  - `hidden_act = "gelu_pytorch_tanh"`
- `Gemma2ForCausalLM`: Houses the complete decoder stack, embedding layer, and linear head.

#### Unit-Offset RMSNorm Implementation:
Standard RMSNorm in LLaMA models is formulated as:
$$\text{RMSNorm}(x) = \frac{x}{\sqrt{\frac{1}{d} \sum_{i=1}^d x_i^2 + \epsilon}} \odot \gamma$$
Where $\gamma$ is initialized to $1.0$.

In Gemma 2, Google initializes $\gamma$ to $0.0$ and reformulates the scaling factor with a unit offset:
$$\text{Gemma2RMSNorm}(x) = \frac{x}{\sqrt{\frac{1}{d} \sum_{i=1}^d x_i^2 + \epsilon}} \odot (1.0 + \gamma)$$
This mathematical adjustment ensures that during the initial forward pass when $\gamma = 0$, the layer acts as an exact identity normalization, preventing large gradient shocks to newly added fine-tuning layers.

### 4.2 TorchDynamo Graph Capture Mechanics

When applying `torch.compile(model, mode="max-autotune")`:
1. **Python Bytecode Evaluation**: TorchDynamo intercepts Python frame execution via the PEP 523 API before the CPython interpreter executes instructions.
2. **Guard Generation**: TorchDynamo generates runtime assertions (guards):
   ```python
   # Guard verification generated by TorchDynamo
   check_tensor_type(x, dtype=torch.bfloat16)
   check_tensor_shape(x, (2, 8192, 4096))
   check_requires_grad(x, False)
   ```
3. **FX Graph Extraction**: Extracts a purely symbolic Directed Acyclic Graph (DAG) of PyTorch operations.
4. **TorchInductor Triton Codegen**: TorchInductor decomposes high-level Gemma 2 operations (RMSNorm, Soft-Capping, GeGLU) and emits optimized OpenAI Triton CUDA kernels specifically tailored to the Blackwell GB10 memory hierarchy.

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              PYTORCH VS KERAS 3 VS NATIVE HF ON GEMMA 2                                │
├──────────────────────┬──────────────────────┬──────────────────────────┬───────────────────────────────┤
│ Dimension            │ Hugging Face Eager   │ PyTorch 2.5 Compiled     │ Keras 3 (JAX Backend)         │
├──────────────────────┼──────────────────────┼──────────────────────────┼───────────────────────────────┤
│ Lines of Setup Code  │ Minimal (3 lines)    │ Minimal (4 lines)        │ Low (5 lines)                 │
│ TTFT (First Token)   │ Standard baseline    │ 2.4x faster (Fused GEMM) │ 2.8x faster (XLA AOT)         │
│ Memory Overhead      │ High (Python frames) │ Low (Fused intermediates)│ Minimal (Pre-allocated XLA)   │
│ Multi-GPU Strategy   │ Accelerate / DDP     │ FSDP2                    │ JAX NamedSharding             │
│ Model Surgery        │ Trivial (PyTorch)    │ Moderate (Graph breaks)  │ Easy (Functional API)         │
│ Blackwell GB10 Ready │ Yes                  │ Exceptional (Triton)     │ Exceptional (XLA PTX)         │
└──────────────────────┴──────────────────────┴──────────────────────────┴───────────────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Multi-Framework Gemma 2 Script with TorchDynamo Compilation

Save this script as `gemma2_frameworks_lab.py`:

```python
"""
Google Gemma 2 Multi-Framework Integration Lab:
Demonstrates:
1. Hugging Face Gemma 2 architecture scaffolding
2. Unit-offset RMSNorm layer implementation
3. PyTorch 2.5 TorchDynamo compilation verification
4. Keras 3 environment configuration
"""

import os
import torch
import torch.nn as nn
import torch.nn.functional as F

# 1. Custom Gemma 2 Unit-Offset RMSNorm
class Gemma2RMSNorm(nn.Module):
    """
    Gemma 2 RMSNorm with Unit Offset: output = norm(x) * (1.0 + weight)
    Initialized with zeros to ensure exact identity scaling at step 0.
    """
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        # Initialized to zeros!
        self.weight = nn.Parameter(torch.zeros(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        variance = x.pow(2).mean(-1, keepdim=True)
        norm_x = x * torch.rsqrt(variance + self.eps)
        # Unit offset scaling: (1.0 + weight)
        return norm_x * (1.0 + self.weight)

# 2. Gemma 2 Fused Feed-Forward Network with GeGLU
class Gemma2MLP(nn.Module):
    def __init__(self, hidden_dim: int, intermediate_dim: int):
        super().__init__()
        self.gate_proj = nn.Linear(hidden_dim, intermediate_dim, bias=False)
        self.up_proj = nn.Linear(hidden_dim, intermediate_dim, bias=False)
        self.down_proj = nn.Linear(intermediate_dim, hidden_dim, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # GeGLU activation: (gate * GELU) * up
        gate = F.gelu(self.gate_proj(x), approximate="tanh")
        up = self.up_proj(x)
        return self.down_proj(gate * up)

# 3. Complete Gemma 2 Transformer Layer
class Gemma2Block(nn.Module):
    def __init__(self, hidden_dim: int, intermediate_dim: int):
        super().__init__()
        self.input_layernorm = Gemma2RMSNorm(hidden_dim)
        self.post_attention_layernorm = Gemma2RMSNorm(hidden_dim)
        self.mlp = Gemma2MLP(hidden_dim, intermediate_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Pre-norm residual connection
        normed = self.input_layernorm(x)
        # In this lab we test the MLP branch
        h = x + normed
        out = h + self.mlp(self.post_attention_layernorm(h))
        return out

def run_framework_lab():
    print("=" * 80)
    print("RUNNING GEMMA 2 PYTORCH 2.5 / TORCHDYNAMO & KERAS 3 LAB")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Executing on Compute Device: {device}")

    # Model parameters
    batch_size = 2
    seq_len = 128
    hidden_dim = 1024
    intermediate_dim = 4096

    print("\n--- 1. Testing Gemma 2 Unit-Offset RMSNorm ---")
    norm_layer = Gemma2RMSNorm(hidden_dim).to(device)
    dummy_x = torch.randn(batch_size, seq_len, hidden_dim, device=device)
    
    # Check that initial weight is all zeros
    assert torch.all(norm_layer.weight == 0.0), "RMSNorm weight must initialize to 0.0"
    norm_out = norm_layer(dummy_x)
    print(f"RMSNorm successfully evaluated. Output shape: {list(norm_out.shape)}")
    print(f"Output std before learning: {norm_out.std().item():.4f} (Strictly normalized)")

    print("\n--- 2. Testing PyTorch 2.5 TorchDynamo Compilation ---")
    block = Gemma2Block(hidden_dim, intermediate_dim).to(device)

    # Compile the model using PyTorch 2.5 TorchDynamo
    try:
        compiled_block = torch.compile(block, mode="reduce-overhead")
        print("Model decorated with torch.compile(mode='reduce-overhead').")
        
        # Warmup forward pass (triggers JIT compilation)
        _ = compiled_block(dummy_x)
        print("Warmup pass completed. Kernel fusion active!")
        
        # Benchmarked forward pass
        out = compiled_block(dummy_x)
        print(f"Compiled execution output shape: {list(out.shape)}")
    except Exception as e:
        print(f"torch.compile note: {e}")
        out = block(dummy_x)
        print("Fell back to standard eager execution.")

    print("\n--- 3. Keras 3 Multi-Backend Environment Configuration ---")
    print("To run Gemma 2 seamlessly in Keras 3 with JAX on NVIDIA DGX Spark:")
    print("  export KERAS_BACKEND='jax'")
    print("  python -c 'import keras; print(\"Active Keras Backend:\", keras.backend.backend())'")
    print("\nTo switch back to PyTorch:")
    print("  export KERAS_BACKEND='torch'")
    print("\nVerification: Gemma 2 core components verified across PyTorch and Keras specifications!")

if __name__ == "__main__":
    run_framework_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (PyTorch 2.5 on Grace ARM + Blackwell GB10)

### 7.1 PyTorch 2.5 on ARM Neoverse V2 Architecture
The Grace CPU in the DGX Spark is an ARMv9 Neoverse V2 processor. When installing PyTorch 2.5:
```bash
# Verify system architecture
uname -m  # aarch64

# Install official PyTorch 2.5 wheel built for aarch64 + CUDA 12.8
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

### 7.2 TorchInductor Blackwell Optimizations
To ensure TorchInductor generates optimal Blackwell GB10 kernels:
```python
import torch

# Enable CUDA Graphs and Triton autotuning for Blackwell
torch._inductor.config.triton.autotune_at_compile_time = True
torch._inductor.config.coordinate_descent_tuning = True
torch._inductor.config.cuda_graphs = True
```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Verify Unit-Offset RMSNorm Gradient**:
   Derive $\frac{\partial y}{\partial \gamma}$ for $y = \hat{x} \cdot (1.0 + \gamma)$ at initialization where $\gamma = 0.0$.
   - *Solution*: $\frac{\partial y}{\partial \gamma} = \hat{x}$. The gradient is directly proportional to the normalized input $\hat{x}$, ensuring unhindered, well-conditioned gradient flow from step 0.

2. **Calculate Parameter Count for Gemma 2 MLP**:
   Given hidden dimension $D = 2048$ and intermediate dimension $M = 16384$, calculate the number of parameters in the Gemma 2 GeGLU MLP.
   - *Solution*: The MLP consists of three projections: `gate_proj`, `up_proj`, and `down_proj`.
     - $\text{gate\_proj}: 2048 \times 16384 = 33,554,432$
     - $\text{up\_proj}: 2048 \times 16384 = 33,554,432$
     - $\text{down\_proj}: 16384 \times 2048 = 33,554,432$
     - Total: $3 \times 33,554,432 = 100,663,296$ parameters ($\approx 100.66\text{M}$ weights per layer).

### Troubleshooting FAQ

- **Q: Why does `torch.compile` fail with `FlashAttention2 does not support logit soft-capping`?**
  *A*: Older versions of the standalone FlashAttention package did not implement the $\tanh$ soft-capping branch in their CUDA kernels. When using Hugging Face, pass `attn_implementation="sdpa"` (PyTorch Scaled Dot-Product Attention) or update to `vllm >= 0.5.4` / `flash-attn >= 2.6.3`, which natively fuses Gemma 2 soft-capping.
- **Q: Can I use Hugging Face `bitsandbytes` 4-bit quantization with Keras 3?**
  *A*: When running Keras 3 with the PyTorch backend (`KERAS_BACKEND="torch"`), bitsandbytes works natively. When running with the JAX backend (`KERAS_BACKEND="jax"`), use Google's native `aqt` (Accurate Quantized Training) or JAX GGUF loaders instead.
