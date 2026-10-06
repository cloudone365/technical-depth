# Volume 03: Logit Soft-Capping Mathematics and Implementation

```
==================================================================================================
TARGET AUDIENCE: Deep Learning Researchers, Kernel Engineers, Distributed Training Specialists
PREREQUISITES   : Self-Attention Mechanics, Hyperbolic Trigonometry, Backpropagation, Floating-Point FP16/FP8
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master the theory, first-principles mathematics, PyTorch/CUDA implementations, and
                  numerical stability benefits of Gemma 2 Double Logit Soft-Capping (50.0 and 30.0).
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

In large-scale autoregressive language model training, numerical instability—manifesting as sudden loss spikes, divergent gradients, and catastrophic `NaN` generation—is frequently triggered by runaway logits in two sensitive calculation junctions:
1. **The Attention Score Calculation**: $S = \frac{Q K^T}{\sqrt{d_k}}$ before the softmax operator.
2. **The Final Vocabulary Projection**: $Z = H W_{\text{vocab}}$ before computing cross-entropy loss or sampling.

Google Gemma 2 introduced an elegant, mathematically bounded mechanism termed **Double Logit Soft-Capping**. Instead of abruptly truncating runaway activations (hard clipping), Gemma 2 continuously and smoothly squashes extreme values using scaled hyperbolic tangent functions.

```
                  ┌─────────────────────────────────────────────────────────┐
                  │           GEMMA 2 DOUBLE LOGIT SOFT-CAPPING             │
                  └────────────────────────────┬────────────────────────────┘
                                               │
               ┌───────────────────────────────┴───────────────────────────────┐
               ▼                                                               ▼
┌─────────────────────────────────────────────┐ ┌─────────────────────────────────────────────┐
│     ATTENTION LOGIT SOFT-CAPPING (50.0)     │ │     VOCAB LOGIT SOFT-CAPPING (30.0)         │
│                                             │ │                                             │
│   S_raw = (Q * K^T) / sqrt(d_k)             │ │   Z_raw = HiddenState * W_vocab             │
│   S_capped = 50.0 * tanh(S_raw / 50.0)      │ │   Z_capped = 30.0 * tanh(Z_raw / 30.0)      │
│   Softmax(S_capped) in Attention Head       │ │   Softmax(Z_capped) for Next Token Loss     │
│   Bounds scores to (-50.0, +50.0)           │ │   Bounds logits to (-30.0, +30.0)           │
└─────────────────────────────────────────────┘ └─────────────────────────────────────────────┘
               │                                                               │
               └───────────────────────────────┬───────────────────────────────┘
                                               ▼
                              ┌─────────────────────────────────┐
                              │  NUMERICAL BENEFIT              │
                              │  - Strictly bounds exp(logit)   │
                              │  - Eliminates under/overflow    │
                              │  - Non-zero gradient everywhere │
                              │  - Native FP8 stability on GB10 │
                              └─────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Hydraulic Pressure Relief Valve
3. Evolutionary Lineage & Predecessors: From Hard Clamping to Bounded Transcendental Functions
4. First-Principles Mathematics & Algorithmic Formulations
   - Mathematical Definition of Hyperbolic Tangent Soft-Capping
   - Analytical Gradient and Backpropagation Dynamics
   - Numerical Stability in Half-Precision (FP16/BF16/FP8)
   - Why Gemma 2 Uses 50.0 for Attention and 30.0 for Vocab
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: Custom Autograd and Triton Soft-Capping Kernels
7. Hardware Grounding for NVIDIA DGX Spark (Grace Blackwell GB10 FP8 Execution)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Hydraulic Pressure Relief Valve

Imagine a high-pressure industrial hydraulic pipeline transporting pressurized fluid. Under standard conditions, fluid pressure fluctuates safely between 10 and 40 bars. However, during rapid valve switching (analogous to rare token combinations or high-entropy prompts), pressure spikes can instantaneously surge to 500 bars.

If you install a **rigid mechanical stopper (Hard Clamping)**, any pressure above 50 bars slams violently against the stopper:
- The fluid stops abruptly at 50 bars.
- But the sensor reports zero feedback about whether the incoming pressure was 51 bars or 500 bars.
- The derivative is zero; the control system cannot sense how hard the fluid is pushing. In neural network terms, **the gradient vanishes entirely**, freezing the model weights.

Alternatively, if you have **no limiter at all (Standard Transformers)**:
- A 500-bar spike ruptures the pipes.
- In neural networks, computing $e^{500}$ in 16-bit floating point immediately exceeds the maximum representable number ($65,504$ in FP16), resulting in `+Inf`. In the next subtraction, `Inf - Inf` produces `NaN`, corrupting the entire model weights across thousands of GPUs.

Google Gemma 2 implements a **Continuous Hydraulic Progressive Spring Valve (Soft-Capping)**:
- When pressure is within the normal operating zone (e.g., $-10$ to $+10$), the spring resistance is negligible: fluid moves naturally with near-perfect linearity ($f(x) \approx x$).
- As pressure rises toward 50, the spring progressively stiffens, smoothly absorbing the kinetic energy.
- Even at 200 bars, the output smoothly approaches 50 without ever exceeding it.
- Crucially, the spring still yields slightly; the derivative is non-zero, allowing the control system (backpropagation) to learn precisely how to correct the pressure upstream.

---

## 3. Evolutionary Lineage & Predecessors

The quest to tame activation spikes in deep networks has spanned multiple generations:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2017: Vanilla Attention (Vaswani et al.)                              │
│ S = (Q * K^T) / sqrt(d_k)                                              │
│ Problem: Variance grows with sequence length; extreme entropy leads to │
│ logit explosions and softmax saturation.                               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2020: Hard Gradient & Activation Clamping (T5, OPT, PaLM)             │
│ S_clipped = torch.clamp(S, min=-C, max=C)                              │
│ Problem: d(clamp)/dx = 0 when |x| > C. Causes dead zones in attention  │
│ maps and stalls gradient descent for outlier tokens.                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2022: QK-Normalization (DeBERTa, Swin, ViT-22B)                        │
│ S = (RMSNorm(Q) * RMSNorm(K)^T) * scale                                │
│ Problem: Adds extra normalization overhead; normalizes vectors to unit │
│ sphere, which restricts the expressive dynamic range of dot-products.  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2024: Gemma 2 Double Logit Soft-Capping (Google DeepMind)              │
│ S = Cap * tanh(S / Cap)                                                │
│ Result: Smooth, infinitely differentiable, strictly bounded, preserves │
│ near-linear behavior around 0, fully compatible with FP8 tensors.      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 Mathematical Formulation of the Soft-Capping Operator

Let $x \in \mathbb{R}$ represent an unconstrained logit (either an attention dot-product score or an unnormalized vocabulary output). For a chosen cap parameter $C \in \mathbb{R}^+$, the soft-capping function $f_C(x)$ is defined as:

$$f_C(x) = C \cdot \tanh\left(\frac{x}{C}\right) = C \left( \frac{e^{x/C} - e^{-x/C}}{e^{x/C} + e^{-x/C}} \right)$$

#### Asymptotic Properties:
1. **Strict Bounds**:
   $$\lim_{x \to +\infty} f_C(x) = +C, \quad \lim_{x \to -\infty} f_C(x) = -C$$
   $$\forall x \in \mathbb{R}, \quad f_C(x) \in (-C, +C)$$

2. **First-Order Maclaurin Series (Linearity near zero)**:
   Recall that $\tanh(z) = z - \frac{z^3}{3} + \frac{2z^5}{15} - \mathcal{O}(z^7)$. Setting $z = \frac{x}{C}$:
   $$f_C(x) = C \left( \frac{x}{C} - \frac{x^3}{3 C^3} + \mathcal{O}\left(\frac{x^5}{C^5}\right) \right) = x - \frac{x^3}{3 C^2} + \mathcal{O}\left(\frac{x^5}{C^4}\right)$$
   For small logits $|x| \ll C$, the distortion is strictly cubic:
   $$\left| f_C(x) - x \right| \approx \frac{|x|^3}{3 C^2}$$
   When $x = 5.0$ and $C = 50.0$, the relative distortion is $\frac{125}{3 \times 2500} = \frac{125}{7500} \approx 1.66\%$.

### 4.2 Analytical Gradient and Backpropagation Dynamics

During the backward pass of backpropagation, the incoming adjoint $\frac{\partial \mathcal{L}}{\partial f_C}$ is multiplied by the local derivative $\frac{d f_C}{d x}$:

$$\frac{d}{dx} \left[ C \tanh\left(\frac{x}{C}\right) \right] = C \cdot \text{sech}^2\left(\frac{x}{C}\right) \cdot \frac{1}{C} = \text{sech}^2\left(\frac{x}{C}\right) = 1 - \tanh^2\left(\frac{x}{C}\right)$$

Expressing the derivative in terms of the forward output $y = f_C(x)$:

$$\frac{d f_C}{dx} = 1 - \left(\frac{y}{C}\right)^2$$

$$\frac{\partial \mathcal{L}}{\partial x} = \frac{\partial \mathcal{L}}{\partial y} \cdot \left[ 1 - \left(\frac{y}{C}\right)^2 \right]$$

#### Comparison of Gradient Transmission:
| Mechanism | Forward Function $y(x)$ | Derivative $dy/dx$ at $x = 0$ | Derivative $dy/dx$ at $x = 2C$ |
| :--- | :--- | :--- | :--- |
| **Uncapped** | $x$ | $1.0$ | $1.0$ (Risk of exploding gradients) |
| **Hard Clamped** | $\min(\max(x, -C), C)$ | $1.0$ | **$0.0$ (Dead gradient, no learning)** |
| **Soft-Capped** | $C \tanh(x/C)$ | $1.0$ | $1 - \tanh^2(2) \approx 0.0707$ **(Smooth non-zero gradient)** |

### 4.3 Gemma 2 Double Capping Parameters

Gemma 2 employs two specific cap thresholds:
1. **Attention Logits Cap ($C_{\text{attn}} = 50.0$)**:
   Applied to the scaled dot-product:
   $$S_{i,j} = 50.0 \times \tanh\left( \frac{Q_i K_j^T}{50.0 \sqrt{d_k}} \right)$$
   Why $50.0$? In IEEE 754 float32 and bfloat16, $e^{50.0} \approx 5.18 \times 10^{21}$, which fits comfortably within the dynamic range of bfloat16 ($3.39 \times 10^{38}$) and float32 ($3.40 \times 10^{38}$). Moreover, the maximum possible difference between two attention logits is bounded by $100.0$. In the softmax function:
   $$\text{softmax}(S)_{i,j} = \frac{e^{S_{i,j} - \max_k S_{i,k}}}{\sum_k e^{S_{i,k} - \max_k S_{i,k}}}$$
   The worst-case exponent is $e^{-100.0} \approx 3.72 \times 10^{-44}$, completely avoiding subnormal underflow traps in bfloat16.

2. **Final Projection Vocab Logits Cap ($C_{\text{vocab}} = 30.0$)**:
   Applied to the unembedding projection before cross-entropy:
   $$Z_v = 30.0 \times \tanh\left( \frac{h \cdot W_{\text{vocab}, v}}{30.0} \right)$$
   Why $30.0$? The vocabulary is $256,128$ tokens wide. When calculating the cross-entropy loss:
   $$\mathcal{L} = -\log \frac{e^{Z_{\text{target}}}}{\sum_{v=1}^{V} e^{Z_v}} = -Z_{\text{target}} + \log\left(\sum_{v=1}^V e^{Z_v}\right)$$
   If an outlier logit reaches $100.0$ while others are $0.0$, the model assigns $99.999999\%$ confidence to a single token, rendering temperature sampling ineffective and causing sharp cross-entropy gradient spikes. Bounding logits to $[-30.0, +30.0]$ guarantees that the maximum log-odds ratio between any two tokens cannot exceed $60.0$, preventing entropy collapse during SFT and RLHF.

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              ATTENTION STABILITY ARCHITECTURES COMPARISON                               │
├──────────────────────┬──────────────────────┬──────────────────────┬────────────────────────────────────┤
│ Strategy             │ Models Utilizing     │ Training Overhead    │ Stability Under Low-Precision FP8  │
├──────────────────────┼──────────────────────┼──────────────────────┼────────────────────────────────────┤
│ No Capping           │ LLaMA 3, Mistral 7B  │ 0% baseline          │ Vulnerable to loss spikes at 10T+  │
│ Hard Clamping        │ Older OPT, BLOOM     │ ~1% (cheap min/max)  │ Zero-gradient plateau bugs         │
│ QK LayerNorm         │ Command-R+, Swin     │ +4-6% FLOPs (norm)   │ Good, but alters vector geometry   │
│ Logit Soft-Capping   │ Gemma 2 (2B/9B/27B)  │ +1-2% FLOPs (tanh)   │ Exceptional; native FP8 stability  │
└──────────────────────┴──────────────────────┴──────────────────────┴────────────────────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Custom Autograd and Triton Soft-Capping Kernels

In this laboratory, we implement:
1. Pure PyTorch baseline with torch autograd.
2. Custom PyTorch autograd function using analytical $\text{sech}^2(x/C) = 1 - (y/C)^2$ for memory-efficient gradient backprop.
3. Verification script comparing gradient accuracy and numerical stability under extreme inputs ($> 500.0$).

Save this script as `gemma2_soft_capping_lab.py`:

```python
"""
Google Gemma 2 Logit Soft-Capping Implementation Lab
Validates Attention Capping (50.0) and Vocab Capping (30.0)
Tests analytical backward pass vs torch autograd and verifies NaN immunity.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import math

class SoftCappingAutograd(torch.autograd.Function):
    """
    Memory-efficient custom autograd function for Soft-Capping.
    Forward: y = C * tanh(x / C)
    Backward: dy/dx = 1 - (y / C)^2
    Saves 'y' instead of 'x', reusing output tensor memory.
    """
    @staticmethod
    def forward(ctx, x: torch.Tensor, cap: float):
        ctx.cap = cap
        scaled_x = x / cap
        y = cap * torch.tanh(scaled_x)
        ctx.save_for_backward(y)
        return y

    @staticmethod
    def backward(ctx, grad_output: torch.Tensor):
        (y,) = ctx.saved_tensors
        cap = ctx.cap
        # Analytical gradient: d/dx [cap * tanh(x/cap)] = 1 - tanh^2(x/cap) = 1 - (y/cap)^2
        normalized_y = y / cap
        grad_x = grad_output * (1.0 - normalized_y * normalized_y)
        return grad_x, None

class Gemma2AttentionHead(nn.Module):
    def __init__(self, hidden_dim: int, num_heads: int, head_dim: int, attn_cap: float = 50.0):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.head_dim = head_dim
        self.attn_cap = attn_cap
        self.scale = 1.0 / math.sqrt(head_dim)

        self.q_proj = nn.Linear(hidden_dim, num_heads * head_dim, bias=False)
        self.k_proj = nn.Linear(hidden_dim, num_heads * head_dim, bias=False)
        self.v_proj = nn.Linear(hidden_dim, num_heads * head_dim, bias=False)
        self.out_proj = nn.Linear(num_heads * head_dim, hidden_dim, bias=False)

    def forward(self, x: torch.Tensor, mask: torch.Tensor = None) -> torch.Tensor:
        b, s, _ = x.shape
        q = self.q_proj(x).view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(b, s, self.num_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(b, s, self.num_heads, self.head_dim).transpose(1, 2)

        # Raw scaled dot-product attention scores
        raw_scores = torch.matmul(q, k.transpose(-1, -2)) * self.scale

        # Gemma 2 Attention Soft-Capping (50.0)
        capped_scores = SoftCappingAutograd.apply(raw_scores, self.attn_cap)

        if mask is not None:
            capped_scores = capped_scores + mask

        attn_weights = F.softmax(capped_scores, dim=-1, dtype=torch.float32).to(x.dtype)
        attn_out = torch.matmul(attn_weights, v)
        attn_out = attn_out.transpose(1, 2).contiguous().view(b, s, self.num_heads * self.head_dim)
        return self.out_proj(attn_out)

class Gemma2OutputHead(nn.Module):
    def __init__(self, hidden_dim: int, vocab_size: int, vocab_cap: float = 30.0):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.vocab_size = vocab_size
        self.vocab_cap = vocab_cap
        self.weight = nn.Parameter(torch.randn(vocab_size, hidden_dim) * 0.02)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        # Raw projection
        raw_logits = F.linear(hidden_states, self.weight)
        # Gemma 2 Vocab Projection Soft-Capping (30.0)
        capped_logits = SoftCappingAutograd.apply(raw_logits, self.vocab_cap)
        return capped_logits

def run_stability_benchmark():
    print("=" * 80)
    print("RUNNING GEMMA 2 LOGIT SOFT-CAPPING VERIFICATION HARNESS")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Executing on Compute Device: {device}")

    # 1. Verification of Gradient Exactness
    x = torch.randn(4, 16, 64, device=device, dtype=torch.float64, requires_grad=True)
    cap = 50.0
    
    # Custom autograd
    y_custom = SoftCappingAutograd.apply(x, cap)
    loss_custom = y_custom.sum()
    loss_custom.backward()
    grad_custom = x.grad.clone()
    x.grad.zero_()

    # PyTorch native autograd
    y_native = cap * torch.tanh(x / cap)
    loss_native = y_native.sum()
    loss_native.backward()
    grad_native = x.grad.clone()

    max_diff = (grad_custom - grad_native).abs().max().item()
    print(f"Gradient Exactness Check: Max absolute diff = {max_diff:.2e} (Passed: {max_diff < 1e-12})")

    # 2. Extreme Activation Stress Test (Standard vs Hard-Clipped vs Soft-Capped)
    print("\n--- Extreme Activation Stress Test (Input values up to 1,000.0) ---")
    extreme_inputs = torch.tensor([-1000.0, -100.0, -25.0, 0.0, 25.0, 100.0, 1000.0], device=device, dtype=torch.float32)
    
    # Uncapped
    exp_uncapped = torch.exp(extreme_inputs)
    
    # Hard Clamped to [-50, 50]
    clamped = torch.clamp(extreme_inputs, -50.0, 50.0)
    exp_clamped = torch.exp(clamped)
    
    # Soft Capped to 50.0
    soft_capped = cap * torch.tanh(extreme_inputs / cap)
    exp_soft = torch.exp(soft_capped)

    print(f"{'Input x':<10} | {'Uncapped exp(x)':<18} | {'Clamped [-50,50]':<18} | {'Soft-Capped (50.0)':<18}")
    print("-" * 72)
    for i in range(len(extreme_inputs)):
        val_x = extreme_inputs[i].item()
        val_uncapped = f"{exp_uncapped[i].item():.2e}" if not math.isinf(exp_uncapped[i].item()) else "OVERFLOW (Inf)"
        val_clamped = f"{clamped[i].item():.2f}"
        val_soft = f"{soft_capped[i].item():.4f}"
        print(f"{val_x:<10.1f} | {val_uncapped:<18} | {val_clamped:<18} | {val_soft:<18}")

    # 3. Softmax Gradient Comparison
    print("\n--- Backward Gradient Comparison under Extreme Logits ---")
    x_clamped = extreme_inputs.clone().detach().requires_grad_(True)
    x_soft = extreme_inputs.clone().detach().requires_grad_(True)

    y_c = torch.clamp(x_clamped, -50.0, 50.0).sum()
    y_c.backward()

    y_s = (cap * torch.tanh(x_soft / cap)).sum()
    y_s.backward()

    print(f"{'Input x':<10} | {'Clamped Grad dy/dx':<20} | {'Soft-Capped Grad dy/dx':<25}")
    print("-" * 62)
    for i in range(len(extreme_inputs)):
        vx = extreme_inputs[i].item()
        gc = x_clamped.grad[i].item()
        gs = x_soft.grad[i].item()
        print(f"{vx:<10.1f} | {gc:<20.4f} | {gs:<25.6e}")

    print("\nConclusion: Hard clamp kills gradients (0.0000) for |x| > 50, preventing learning.")
    print("Soft-capping maintains non-zero, stable gradients even at x = 1000.0!")

if __name__ == "__main__":
    run_stability_benchmark()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (Grace Blackwell GB10 FP8 Execution)

### 7.1 The FP8 Underflow/Overflow Bottleneck
On the NVIDIA Blackwell GB10 Tensor Cores, FP8 (both E4M3 and E5M2 formats) provides $2\times$ the computational density and memory bandwidth of BF16. However, FP8 E4M3 has an extremely narrow dynamic range:
- Maximum representable finite value: $\text{MAX}_{\text{FP8}} = 448.0$.
- Smallest positive normalized value: $\text{MIN}_{\text{FP8}} = 2^{-6} \approx 0.015625$.

In standard architectures without logit soft-capping, attention logits in deep layers frequently experience intermediate spikes exceeding $500.0$, instantly overflowing to `NaN` in FP8 E4M3 or forcing aggressive scaling factors that push smaller attention weights into underflow (zero).

### 7.2 Why Soft-Capping Unlocks Zero-Spike FP8 on DGX Spark
Because Gemma 2 mathematically enforces:
$$\max |S_{i,j}| \le 50.0 \ll 448.0$$
$$\max |Z_v| \le 30.0 \ll 448.0$$

The Blackwell Tensor Cores can execute FP8 matrix multiplications on GEMM operations and attention projections with fixed scale factors ($\text{scale} = 1.0$), completely eliminating the latency of delayed-scaling history buffers (`amax` reduction across iterations).

### 7.3 FlashAttention / vLLM Soft-Capping Kernel Adaptation
Standard FlashAttention-2 assumes unconstrained logits:
$$P = \text{softmax}(Q K^T / \sqrt{d})$$
To run Gemma 2 in vLLM or Hugging Face without falling back to slow un-fused PyTorch attention, the attention CUDA kernel must fuse the $\tanh$ operation directly into the shared-memory SRAM tile:

```cuda
// NVIDIA Blackwell SRAM fused soft-capping attention snippet
__device__ inline float soft_cap_score(float raw_score, float inv_cap, float cap) {
    // raw_score = (Q * K^T) * scale
    // Perform tanh in hardware via fast CUDA intrinsic __nv_fast_tanh
    return cap * __nv_fast_tanh(raw_score * inv_cap);
}
```
vLLM supports this natively via `--dtype bfloat16` or `--dtype float8_e4m3fn` when loading `google/gemma-2-9b` or `google/gemma-2-27b`.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate the Maximum Attention Logit Difference**:
   Given $C_{\text{attn}} = 50.0$, what is the maximum theoretical ratio between the largest attention weight $w_{\max}$ and smallest attention weight $w_{\min}$ for a sequence of length $L$?
   - *Solution*: Max difference between two capped scores is $50.0 - (-50.0) = 100.0$. Therefore, $\frac{w_{\max}}{w_{\min}} = e^{100.0} \approx 2.688 \times 10^{43}$.

2. **Calculate the Logit Derivative at $x = 25.0$ with $C = 50.0$**:
   - *Solution*: $z = 25.0 / 50.0 = 0.5$. $\tanh(0.5) \approx 0.462117$. The derivative is $1 - \tanh^2(0.5) = 1 - (0.462117)^2 = 1 - 0.21355 = 0.78645$.

3. **Derive the Memory Savings of Storing $y$ vs $x$ in Custom Autograd**:
   - *Solution*: In PyTorch, during forward pass $y = f(x)$, $y$ is already allocated as the forward output. In standard autograd, PyTorch must save $x$ in context (`ctx.save_for_backward(x)`). By computing $dy/dx = 1 - (y/C)^2$, the backward pass reuses the already materialized tensor $y$, saving $1 \times \text{tensor\_size}$ of VRAM per attention layer. Across 46 layers in Gemma 2 27B, this saves $\approx 4.8\text{ GB}$ of activation memory during training.

### Troubleshooting FAQ

- **Q: Why does my Gemma 2 fine-tuning loss diverge when I disable soft-capping?**
  *A*: When users inadvertently replace Gemma 2 attention with vanilla Hugging Face LlamaAttention (e.g., during custom model surgery), the model loses its logit bounding. Gemma 2 was pre-trained with logit weights that naturally produce inputs in the range of $[-150, 150]$. Passing these through uncapped $\exp(x)$ creates immediate float16 overflow and gradient explosion.
- **Q: Does soft-capping slow down inference on the GB10?**
  *A*: When using unfused PyTorch, $\tanh$ introduces a memory round-trip to DRAM, reducing throughput by 12%. However, in optimized engines (vLLM, TensorRT-LLM), the soft-capping $\tanh$ is fused directly into the register tile of the FlashAttention kernel, resulting in zero memory bandwidth overhead and $< 1\%$ compute impact.
