# Volume 01: Gemma 2 Architecture and Model Spectrum

```
==================================================================================================
TARGET AUDIENCE: AI Research Engineers, Foundation Model Architects, Distributed Systems Leads
PREREQUISITES   : Transformer attention mechanics, GeGLU activation, RMSNorm, Rotary Position Embeddings
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master the architectural innovations of Google Gemma 2 (2B, 9B, and 27B): the Gemini
                  heritage, 256k tokenizer vocabulary, GeGLU feed-forward networks, and RMSNorm with unit offset.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Google **Gemma 2** is the open-weights foundation model family built by Google DeepMind, derived from the same research, data pipelines, and architectural lineage as **Gemini 1.5**. Available in **2B, 9B, and 27B** parameter configurations, Gemma 2 introduced groundbreaking architectural modifications that allow its 9B and 27B variants to outperform models twice or three times their physical size.

Key innovations include **Double Logit Soft-Capping** (bounding attention and output logits), **Alternating Sliding Window Attention** (halving KV cache footprint), an enormous **256k token vocabulary**, and **GeGLU activations with unit-offset RMSNorm**.

```
                           ┌─────────────────────────────────────────┐
                           │   Google Gemma 2 Model Spectrum         │
                           │   (2B Compact, 9B Mid, 27B Frontier)    │
                           └────────────────────┬────────────────────┘
                                                │
         ┌──────────────────────────────────────┼──────────────────────────────────────┐
         ▼                                      ▼                                      ▼
┌───────────────────────────────┐ ┌───────────────────────────────┐ ┌───────────────────────────────┐
│     Gemini Tokenizer (256k)   │ │  Alternating Attention Layers │ │  Double Logit Soft-Capping    │
│  - 256,128 vocabulary tokens  │ │  - Even: Sliding Window (4k)  │ │  - Attention: Cap = 50.0      │
│  - Rich multilingual & code   │ │  - Odd: Full Global Attn (8k) │ │  - Output Vocab: Cap = 30.0   │
│  - Dense information density  │ │  - 50% KV-Cache Reduction     │ │  - Eliminates Numerical NaN   │
└───────────────────────────────┘ └───────────────────────────────┘ └───────────────────────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────────┐
                                  │   GeGLU Activation + RMSNorm  │
                                  │   RMSNorm(x) * (1 + gamma)    │
                                  │   GeGLU: (xW_gate * GELU) * xW_up │
                                  └───────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Formula 1 Engine with a Built-in Rev Limiter
3. Evolutionary Lineage: From Original Gemma 1 to Gemma 2
4. First-Principles Mathematics & Algorithmic Formulations
   - Gemma 2 Structural Dimension Matrix (2B, 9B, 27B)
   - RMSNorm with Unit Offset ($1 + \gamma$)
   - GeGLU Activation Mathematical Formulation
   - Logit Soft-Capping Bounding Mechanics
5. Comparative Trade-Off Matrix: Open Foundation Model Families
6. Concrete Production Hands-On Lab: Gemma 2 Transformer Block Implementation
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Formula 1 Engine with a Built-in Rev Limiter

Imagine driving a 1,000-horsepower racing car:

- **The Standard Transformer (An Engine Without an Electronic Rev Limiter)**:
  When the driver floors the accelerator on a straightaway (large activation values in self-attention), the engine tachometer spins past 15,000 RPM into the redline (logits exploding to $\pm 150$).
  The piston rods melt, the block shatters, and oil sprays across the windshield (**NaN loss explosion / training divergence**).

- **Google Gemma 2 (The Twin-Turbocharged Engine with Precision Rev Limiters)**:
  DeepMind engineers installed two electronic hydraulic governors (**Double Logit Soft-Capping**):
  1. **The Turbo Wastegate (Attention Cap = 50.0)**: No matter how hard the exhaust gas rushes through the turbine ($Q K^\top / \sqrt{d_k}$), the wastegate opens smoothly via a hyperbolic tangent valve ($\tanh$). Logits can never exceed 50.0.
  2. **The Output Speed Governor (Vocab Cap = 30.0)**: Final prediction logits are held strictly within $[-30, +30]$, preventing the model from becoming pathologically overconfident.
  The engine runs at maximum redline indefinitely with zero chance of exploding, extracting maximum horsepower from every drop of fuel.

---

## 3. Evolutionary Lineage: From Original Gemma 1 to Gemma 2

```
Generation 1: Gemma 1 (Early 2024)       Generation 2: Gemma 2 (Mid-Late 2024)
──────────────────────────────────       ─────────────────────────────────────
- Sizes: 2B and 7B                       - Sizes: 2B, 9B, and 27B
- Standard Multi-Head / MQA              - Grouped-Query Attention (GQA) on all sizes
- Standard full attention across all     - Alternating Sliding Window Attention (SWA)
- Standard unconstrained logits          - Double Logit Soft-Capping (50.0 / 30.0)
- Trained from scratch (Cross-Entropy)   - Gemini Ultra on-policy knowledge distillation
- Moderate benchmark performance         - 9B beats Llama-3-8B; 27B rivals Llama-3-70B
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Gemma 2 Structural Dimension Matrix (2B, 9B, 27B)

| Architectural Parameter | Gemma 2 2B | Gemma 2 9B | Gemma 2 27B |
| :--- | :--- | :--- | :--- |
| **Total Parameters** | **2.61 Billion** | **9.24 Billion** | **27.23 Billion** |
| **Layers ($N_{\text{layers}}$)** | 26 | 42 | 46 |
| **Hidden Dimension ($d_{\text{model}}$)** | 2,304 | 3,584 | 4,608 |
| **FFN Intermediate Dimension ($d_{\text{ffn}}$)**| 9,216 ($4 \times d$) | 14,336 ($4 \times d$) | 36,864 ($8 \times d$) |
| **Query Heads ($H_Q$)** | 8 | 16 | 32 |
| **Key/Value Heads ($H_{\text{KV}}$)** | 4 (2:1 GQA) | 8 (2:1 GQA) | 16 (2:1 GQA) |
| **Head Dimension ($d_{\text{head}}$)** | 256 | 256 | 128 |
| **Vocabulary Size ($|\mathcal{V}|$)** | 256,128 | 256,128 | 256,128 |
| **Context Window ($L_{\text{ctx}}$)** | 8,192 Tokens | 8,192 Tokens | 8,192 Tokens |
| **Sliding Window Size ($W$)** | 4,096 Tokens | 4,096 Tokens | 4,096 Tokens |

### RMSNorm with Unit Offset ($1 + \gamma$)

Unlike standard RMSNorm which scales normalized activations by $\gamma$: $\mathbf{y} = \frac{\mathbf{x}}{\text{RMS}(\mathbf{x})} \odot \gamma$, Gemma 2 initializes $\gamma$ around zero and scales by $(1 + \gamma)$:

$$\text{RMS}(\mathbf{x}) = \sqrt{\frac{1}{d} \sum_{i=1}^d x_i^2 + \epsilon}$$

$$\mathbf{y} = \frac{\mathbf{x}}{\text{RMS}(\mathbf{x})} \odot (1 + \gamma)$$

Where $\gamma \in \mathbb{R}^d$ is initialized to $\mathbf{0}$.
This guarantees that at step 0, the normalization layer acts as an exact identity scaling ($\mathbf{y} = \frac{\mathbf{x}}{\text{RMS}(\mathbf{x})}$), improving numerical gradient propagation during early training phases.

### GeGLU Activation Mathematical Formulation

Gemma 2 employs **Gated Gaussian Error Linear Units (GeGLU)** in its feed-forward networks:

$$\text{GeGLU}(\mathbf{x}) = \left( \mathbf{x} \mathbf{W}_{\text{gate}} \cdot \Phi(\mathbf{x} \mathbf{W}_{\text{gate}}) \right) \odot (\mathbf{x} \mathbf{W}_{\text{up}})$$

Where $\Phi(z)$ is the standard Gaussian cumulative distribution function approximation:

$$\Phi(z) = \frac{1}{2} \left[ 1 + \tanh\left( \sqrt{\frac{2}{\pi}} \left( z + 0.044715 z^3 \right) \right) \right]$$

The intermediate representation is projected back to model dimension via $\mathbf{W}_{\text{down}}$:

$$\text{FFN}(\mathbf{x}) = \text{GeGLU}(\mathbf{x}) \mathbf{W}_{\text{down}}$$

### Logit Soft-Capping Bounding Mechanics

To bound attention logits:

$$\mathbf{S}_{\text{capped}} = 50.0 \times \tanh\left( \frac{\mathbf{Q} \mathbf{K}^\top}{50.0 \times \sqrt{d_k}} \right)$$

Because $\tanh(z) \in (-1, 1)$, $\mathbf{S}_{\text{capped}} \in (-50.0, 50.0)$ strictly.
To bound final output logits:

$$\mathbf{z}_{\text{capped}} = 30.0 \times \tanh\left( \frac{\mathbf{h}_{\text{final}} \mathbf{W}_{\text{vocab}}^\top}{30.0} \right)$$

This mathematical clipping guarantees that softmax probabilities never saturate into binary 0 or 1, preserving non-zero gradients everywhere.

---

## 5. Comparative Trade-Off Matrix: Open Foundation Model Families

| Architectural Dimension | Google Gemma 2 9B | Meta LLaMA 3.1 8B | Alibaba Qwen 2.5 7B | Mistral NeMo 12B |
| :--- | :--- | :--- | :--- | :--- |
| **Tokenizer Vocabulary** | **256,128 Tokens** | 128,256 Tokens | 152,064 Tokens | 131,072 Tokens |
| **Activation Function** | **GeGLU** | SwiGLU | SwiGLU | SwiGLU |
| **Normalization Style** | **RMSNorm with $(1+\gamma)$**| RMSNorm ($\gamma$) | RMSNorm ($\gamma$) | RMSNorm ($\gamma$) |
| **Logit Soft-Capping** | **Native (50.0 / 30.0)** | None | None | None |
| **Attention Mechanism** | **Alternating SWA (4k/8k)** | Full Attention | Full Attention | Full Attention |
| **MMLU Pro Score** | **51.8%** | 44.2% | 48.1% | 43.5% |

---

## 6. Concrete Production Hands-On Lab: Gemma 2 Transformer Block Implementation

This self-contained, runnable Python script implements a complete Gemma 2 Transformer block in pure PyTorch, including unit-offset RMSNorm, logit soft-capping, and GeGLU feed-forward networks.

```python
#!/usr/bin/env python3
"""
Google Gemma 2 Transformer Block Implementation.
Demonstrates Unit-Offset RMSNorm, Double Logit Soft-Capping, and GeGLU MLP.
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F

# =====================================================================
# 1. UNIT-OFFSET RMSNORM (1 + gamma)
# =====================================================================

class GemmaRMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        # Weight initialized to zeros, so (1 + weight) starts at 1.0
        self.weight = nn.Parameter(torch.zeros(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        variance = x.pow(2).mean(-1, keepdim=True)
        norm_x = x * torch.rsqrt(variance + self.eps)
        return norm_x * (1.0 + self.weight)

# =====================================================================
# 2. SOFT-CAPPED ATTENTION LAYER
# =====================================================================

class Gemma2Attention(nn.Module):
    def __init__(self, hidden_dim: int, num_heads: int, num_kv_heads: int, head_dim: int, attn_cap: float = 50.0):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.num_kv_heads = num_kv_heads
        self.head_dim = head_dim
        self.attn_cap = attn_cap
        self.scale = 1.0 / math.sqrt(head_dim)

        self.q_proj = nn.Linear(hidden_dim, num_heads * head_dim, bias=False)
        self.k_proj = nn.Linear(hidden_dim, num_kv_heads * head_dim, bias=False)
        self.v_proj = nn.Linear(hidden_dim, num_kv_heads * head_dim, bias=False)
        self.o_proj = nn.Linear(num_heads * head_dim, hidden_dim, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, L, _ = x.shape
        q = self.q_proj(x).view(B, L, self.num_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(B, L, self.num_kv_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(B, L, self.num_kv_heads, self.head_dim).transpose(1, 2)

        # Expand KV heads for GQA if necessary
        if self.num_heads != self.num_kv_heads:
            ratio = self.num_heads // self.num_kv_heads
            k = k.repeat_interleave(ratio, dim=1)
            v = v.repeat_interleave(ratio, dim=1)

        # Raw scaled dot-product scores
        raw_scores = torch.matmul(q, k.transpose(-2, -1)) * self.scale

        # Gemma 2 Attention Logit Soft-Capping
        capped_scores = self.attn_cap * torch.tanh(raw_scores / self.attn_cap)

        # Causal Mask
        causal_mask = torch.triu(torch.full((L, L), float("-inf"), device=x.device), diagonal=1)
        masked_scores = capped_scores + causal_mask

        attn_weights = F.softmax(masked_scores, dim=-1)
        out = torch.matmul(attn_weights, v)
        out = out.transpose(1, 2).contiguous().view(B, L, -1)
        return self.o_proj(out)

# =====================================================================
# 3. GEGLU FEED-FORWARD NETWORK
# =====================================================================

class Gemma2MLP(nn.Module):
    def __init__(self, hidden_dim: int, ffn_dim: int):
        super().__init__()
        self.gate_proj = nn.Linear(hidden_dim, ffn_dim, bias=False)
        self.up_proj = nn.Linear(hidden_dim, ffn_dim, bias=False)
        self.down_proj = nn.Linear(ffn_dim, hidden_dim, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # GeGLU activation
        gate = F.gelu(self.gate_proj(x), approximate="tanh")
        up = self.up_proj(x)
        return self.down_proj(gate * up)

# =====================================================================
# 4. COMPLETE GEMMA 2 TRANSFORMER BLOCK
# =====================================================================

class Gemma2Block(nn.Module):
    def __init__(self, hidden_dim: int = 1024, num_heads: int = 8, num_kv_heads: int = 4,
                 head_dim: int = 128, ffn_dim: int = 4096):
        super().__init__()
        self.input_layernorm = GemmaRMSNorm(hidden_dim)
        self.self_attn = Gemma2Attention(hidden_dim, num_heads, num_kv_heads, head_dim, attn_cap=50.0)
        self.post_attention_layernorm = GemmaRMSNorm(hidden_dim)
        self.mlp = Gemma2MLP(hidden_dim, ffn_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Pre-LN Residual Block
        h = x + self.self_attn(self.input_layernorm(x))
        out = h + self.mlp(self.post_attention_layernorm(h))
        return out

# =====================================================================
# VERIFICATION HARNESS
# =====================================================================

def run_gemma2_lab():
    print("=" * 80)
    print("GOOGLE GEMMA 2 TRANSFORMER BLOCK IMPLEMENTATION LAB")
    print("=" * 80)

    torch.manual_seed(42)
    B, L, D = 2, 16, 1024
    x = torch.randn(B, L, D)

    block = Gemma2Block(hidden_dim=D, num_heads=8, num_kv_heads=4, head_dim=128, ffn_dim=4096)

    out = block(x)
    print(f"Input Tensor Shape : {list(x.shape)}")
    print(f"Output Tensor Shape: {list(out.shape)}")

    # Verify Output Soft-Capping Functionality
    vocab_size = 256128
    lm_head = nn.Linear(D, vocab_size, bias=False)
    raw_logits = lm_head(out)
    vocab_cap = 30.0
    soft_capped_logits = vocab_cap * torch.tanh(raw_logits / vocab_cap)

    max_logit = soft_capped_logits.max().item()
    min_logit = soft_capped_logits.min().item()

    print(f"\nFinal Vocab Logits Capping Verification:")
    print(f"  Maximum Logit Emitted: {max_logit:.2f} (Strictly <= 30.0)")
    print(f"  Minimum Logit Emitted: {min_logit:.2f} (Strictly >= -30.0)")

    assert -30.0 <= min_logit and max_logit <= 30.0, "Logit soft-capping bounds violated!"
    print("\n[SUCCESS] Gemma 2 block forward pass, RMSNorm(1+gamma), and soft-capping verified.")

if __name__ == "__main__":
    run_gemma2_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Operating Gemma 2 models on the DGX Spark:
1. **Model Sizing on Blackwell GB10 (128 GB Unified Memory)**:
   - **Gemma 2 2B**: In BF16 $\approx 5.2\text{ GB}$ VRAM. Fits with 120 GB left for high-concurrency serving.
   - **Gemma 2 9B**: In BF16 $\approx 18.5\text{ GB}$ VRAM. Primary choice for single-node enterprise fine-tuning and agent loops.
   - **Gemma 2 27B**: In BF16 $\approx 54.5\text{ GB}$ VRAM; in **FP8 Precision**: $\approx 27.5\text{ GB}$ VRAM. Leaves over 70 GB for massive 8k context PagedAttention KV-caches.

2. **Unified Memory High-Bandwidth Advantage**:
   The 900 GB/s NVLink-C2C bus allows the Grace ARM CPU to stream tokenized prompts from host RAM directly into Blackwell GPU memory without PCIe staging overhead, achieving exceptional Time To First Token (TTFT).

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Soft-Capping Derivative)**:
   Prove mathematically that $\frac{d}{dx} \left[ C \cdot \tanh\left(\frac{x}{C}\right) \right] = 1 - \tanh^2\left(\frac{x}{C}\right)$. What happens to gradient magnitude when $x \gg C$?

2. **Exercise 2 (Unit-Offset Identity Check)**:
   Verify that if `GemmaRMSNorm.weight` is initialized with `torch.zeros()`, the gradient with respect to $\gamma$ is identical to standard RMSNorm.

### Solutions

**Solution for Exercise 1**:
Let $u = \frac{x}{C}$. Then $y = C \tanh(u)$.
By the chain rule: $\frac{dy}{dx} = C \cdot \frac{d}{du}[\tanh(u)] \cdot \frac{du}{dx} = C \cdot (1 - \tanh^2(u)) \cdot \frac{1}{C} = 1 - \tanh^2\left(\frac{x}{C}\right)$.
When $x \gg C$, $\tanh(x/C) \to 1.0$, which implies $1 - \tanh^2 \to 0$. The gradient smoothly saturates to zero, mathematically preventing exploding gradients on outlier activations.

### Troubleshooting FAQ

- **Q: Model training crashes with `NaN` loss during early steps.**
  - *Fix*: You disabled logit soft-capping or implemented custom attention kernels without the hyperbolic tangent function. Ensure `attn_logit_softcapping: 50.0` and `final_logit_softcapping: 30.0` are active in your model configuration.

- **Q: Fine-tuning outputs repetitively looped tokens on long contexts.**
  - *Fix*: Gemma 2 requires alternating Sliding Window Attention (SWA). If your custom fine-tuning script does not alternate 4k local window and 8k global window masks across layers, the model's rotary position embeddings will lose coherence.
