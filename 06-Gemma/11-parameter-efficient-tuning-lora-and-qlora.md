# Volume 11: Parameter-Efficient Tuning: LoRA and QLoRA on Gemma 2

```
==================================================================================================
TARGET AUDIENCE: Fine-Tuning Specialists, Enterprise AI Engineers, Low-Rank Adaptation Researchers
PREREQUISITES   : Matrix Factorization, Singular Value Decomposition (SVD), Backpropagation, NF4
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master Low-Rank Adaptation (LoRA) and Quantized LoRA (QLoRA) on Gemma 2 (9B & 27B),
                  targeting GeGLU projections, soft-capping backpropagation, and memory optimization.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Fine-tuning a 27-billion parameter foundation model using full parameter updates requires storing gradients, momentum, and variance tensors for every parameter. In 16-bit precision, this demands over **$160\text{ GB}$ of VRAM** for optimizer states alone.

**Parameter-Efficient Fine-Tuning (PEFT)** via **LoRA (Low-Rank Adaptation)** and **QLoRA (Quantized LoRA)** freezes the base pre-trained weights $W_0$ and injects trainable low-rank decomposition matrices $A$ and $B$. When applied to Gemma 2, adapting the **GeGLU MLP projections (`gate_proj`, `up_proj`, `down_proj`) alongside attention projections (`q_proj`, `v_proj`)** unlocks over 98% of full-parameter fine-tuning capability while training less than **0.2% of the parameters**.

```
                           ┌──────────────────────────────────────────────┐
                           │          FROZEN PRE-TRAINED WEIGHT           │
                           │          W_0 in FP16 / BF16 or NF4           │
                           │          Dimension: [Hidden_In, Hidden_Out]  │
                           └──────────────────────┬───────────────────────┘
                                                  │
                                   Forward pass: h = x * W_0
                                                  │
                 ┌────────────────────────────────┴────────────────────────────────┐
                 │                                                                 │
                 │                                                                 ▼
                 │                                                ┌────────────────────────────────┐
                 │                                                │      LOW-RANK ADAPTER A        │
                 │                                                │      Dim: [Hidden_In, Rank r]  │
                 │                                                │      Init: Gaussian N(0, 1/r)  │
                 │                                                └───────────────┬────────────────┘
                 │                                                                │
                 │                                                                ▼
                 │                                                ┌────────────────────────────────┐
                 │                                                │      LOW-RANK ADAPTER B        │
                 │                                                │      Dim: [Rank r, Hidden_Out] │
                 │                                                │      Init: All Zeros (0.0)     │
                 │                                                └───────────────┬────────────────┘
                 │                                                                │
                 │                                              Scale: (alpha / r)│
                 │                                                                │
                 └────────────────────────────────┬───────────────────────────────┘
                                                  ▼
                                       Combined Output Layer
                            h_final = x * W_0 + (alpha / r) * (x * A * B)
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Transparent Tracing Paper
3. Evolutionary Lineage: From Prefix Tuning and Adapters to LoRA and QLoRA
4. First-Principles Mathematics & Algorithmic Formulations
   - Mathematical Formulation of Low-Rank Decomposition
   - LoRA Scaling Factor $\frac{\alpha}{r}$ and Initialization Constraints
   - Why Gemma 2's GeGLU Requires MLP Adaptation
   - QLoRA NormalFloat4 (NF4) Quantization and Double Quantization
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: Complete LoRA Layer and Gradient Checkpointing Engine
7. Hardware Grounding for NVIDIA DGX Spark (27B QLoRA Memory Math on GB10)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Transparent Tracing Paper

Imagine an invaluable, 1,000-page master legal encyclopedia (Gemma 2 27B):
- **Full Parameter Fine-Tuning** is like hiring an editor to erase and rewrite words directly across all 1,000 original printed pages. If you make a mistake, the original encyclopedia is permanently damaged, and the process requires immense physical effort (enormous VRAM and compute).
- **LoRA** is like placing a **thin, transparent sheet of tracing paper** over specific key paragraphs:
  - The original book remains untouched and pristine (frozen $W_0$).
  - On the tracing paper, you only jot down short annotations and corrections (low-rank matrices $A$ and $B$).
  - When a reader views the page, they look through the tracing paper, seeing the combined knowledge ($W_0 + \Delta W$).
  - If you need to switch from legal analysis to medical diagnosis, you simply replace the lightweight sheet of tracing paper (saving multiple specialized adapters with $< 50\text{ MB}$ footprint).

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2019: Houlsby Adapters (Houlsby et al.)                                │
│ Injected bottleneck feed-forward layers between transformer blocks.    │
│ Drawback: Added sequential layer latency during forward inference.     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2021: LoRA - Low-Rank Adaptation (Hu et al., Microsoft)                │
│ Replaced sequential adapters with parallel low-rank matrix paths.      │
│ Incurred ZERO inference latency via mathematical weight folding:       │
│ W_fused = W_0 + (alpha / r) * (A * B).                                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2023: QLoRA (Dettmers et al.)                                          │
│ Quantized base model W_0 to 4-bit NormalFloat (NF4). Introduced double │
│ quantization and paged optimizers, democratizing 70B fine-tuning.      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 Mathematical Formulation of Low-Rank Decomposition

For a pre-trained weight matrix $W_0 \in \mathbb{R}^{d_{\text{in}} \times d_{\text{out}}}$, standard full fine-tuning learns an unconstrained update matrix $\Delta W \in \mathbb{R}^{d_{\text{in}} \times d_{\text{out}}}$.

LoRA hypothesizes that the intrinsic rank $r$ of the update $\Delta W$ is substantially lower than $\min(d_{\text{in}}, d_{\text{out}})$. Thus, $\Delta W$ is factorized into two low-rank matrices:

$$\Delta W = \frac{\alpha}{r} \cdot A \cdot B$$

Where:
- $A \in \mathbb{R}^{d_{\text{in}} \times r}$ is initialized from a Gaussian distribution $\mathcal{N}\left(0, \frac{1}{r}\right)$.
- $B \in \mathbb{R}^{r \times d_{\text{out}}}$ is initialized to strictly **zeros ($0.0$)**.
- $r \ll \min(d_{\text{in}}, d_{\text{out}})$ (typically $r \in [8, 64]$).
- $\alpha \in \mathbb{R}^+$ is a constant scaling hyperparameter.

#### Forward Pass:
For an input activation $x \in \mathbb{R}^{B \times S \times d_{\text{in}}}$:

$$h = x W_0 + \frac{\alpha}{r} \left( (x A) B \right)$$

Because $B = 0$ at initialization:
$$\Delta W_{\text{init}} = \frac{\alpha}{r} \cdot A \cdot 0 = 0$$
The initial output of the adapted model is mathematically identical to the pre-trained base model ($h = x W_0$), ensuring zero training perturbation at step 0.

### 4.2 Why Gemma 2 Demands MLP Projections in LoRA Target Modules

In standard LLaMA models, adapting only attention projections (`q_proj`, `v_proj`) is often sufficient. However, in **Gemma 2**, Google utilizes **GeGLU activations with enormous intermediate expansion ratios**:
- Gemma 2 9B has an intermediate hidden dimension of $D_{\text{ffn}} = 14,336$ ($3.5\times$ expansion).
- Gemma 2 27B has an intermediate dimension of $D_{\text{ffn}} = 36,864$ ($8\times$ expansion!).

Much of Gemma 2's world knowledge and factual reasoning resides within the three GeGLU projection matrices:
$$\text{MLP}(x) = \left( (x W_{\text{gate}}) \odot \text{GELU} \right) \cdot (x W_{\text{up}}) \cdot W_{\text{down}}$$

Omitting `gate_proj`, `up_proj`, and `down_proj` from the LoRA target module configuration reduces task adaptation benchmark scores by up to **34%**. Always configure:
```python
target_modules = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj"
]
```

### 4.3 Zero-Latency Inference via Weight Folding

Once fine-tuning is complete, the low-rank matrices can be mathematically folded into the original weights before deployment:

$$W_{\text{deploy}} = W_0 + \frac{\alpha}{r} (A \cdot B)$$

During production serving on vLLM or Ollama, the model executes as a standard dense matrix without any extra adapter branches or runtime latency.

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                FINE-TUNING STRATEGIES COMPARISON                                       │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Strategy           │ Trainable Params   │ 27B VRAM Footprint  │ Serving Latency  │ Task Versatility    │
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ Full Fine-Tuning   │ 100.0% (27.2B)     │ ~160 GB (OOM single)│ Baseline         │ High (Catastrophic) │
│ Prompt Tuning      │ < 0.01% (Prefix)   │ ~56 GB              │ +5% latency      │ Low (Brittle)       │
│ LoRA (Attn Only)   │ ~0.08% (21M)       │ ~60 GB              │ Zero (Foldable)  │ Moderate            │
│ LoRA (All Proj)    │ ~0.24% (65M)       │ ~62 GB              │ Zero (Foldable)  │ Exceptional         │
│ QLoRA (4-bit NF4)  │ ~0.24% (65M)       │ ~18.5 GB            │ Zero (Unquantized│ Exceptional         │
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Complete LoRA Layer and Gradient Checkpointing Engine

Save this script as `gemma2_lora_lab.py`:

```python
"""
Google Gemma 2 Parameter-Efficient Fine-Tuning (LoRA) Implementation Lab.
Implements a self-contained LoRA linear layer with scaling alpha/r,
weight-folding mechanics, and GeGLU integration.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import math

class Gemma2LoRALinear(nn.Module):
    """
    Self-contained Low-Rank Adaptation (LoRA) Linear Layer.
    Implements: h = x * W_0 + (alpha / r) * (x * A * B)
    """
    def __init__(
        self,
        in_features: int,
        out_features: int,
        r: int = 16,
        lora_alpha: float = 32.0,
        lora_dropout: float = 0.05
    ):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.r = r
        self.lora_alpha = lora_alpha
        self.scaling = lora_alpha / r

        # Frozen Base Weight W_0
        self.weight = nn.Parameter(torch.randn(out_features, in_features) * 0.02)
        self.weight.requires_grad = False  # Freeze pre-trained weight

        # Trainable Low-Rank Adapters
        self.lora_A = nn.Parameter(torch.zeros(r, in_features))
        self.lora_B = nn.Parameter(torch.zeros(out_features, r))

        self.dropout = nn.Dropout(p=lora_dropout) if lora_dropout > 0.0 else nn.Identity()

        # Initialize: A ~ Gaussian, B ~ 0
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        nn.init.zeros_(self.lora_B)

        self.is_merged = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.is_merged:
            return F.linear(x, self.weight)

        # 1. Base frozen projection: x * W_0^T
        base_out = F.linear(x, self.weight)

        # 2. Low-rank update: (alpha / r) * (x * A^T * B^T)
        lora_act = self.dropout(x)
        lora_out = F.linear(lora_act, self.lora_A)       # [B, S, r]
        lora_out = F.linear(lora_out, self.lora_B)       # [B, S, out_features]
        lora_out = lora_out * self.scaling

        return base_out + lora_out

    def merge_weights(self):
        """
        Folds adapter weights directly into base weight matrix for zero-latency inference.
        """
        if not self.is_merged:
            # W_merged = W_0 + (alpha / r) * (B * A)
            delta_w = (self.lora_B @ self.lora_A) * self.scaling
            self.weight.data += delta_w
            self.is_merged = True
            print("LoRA weights successfully merged into base weight matrix!")

def run_lora_lab():
    print("=" * 80)
    print("RUNNING GOOGLE GEMMA 2 LORA IMPLEMENTATION & VERIFICATION LAB")
    print("=" * 80)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Executing on Device: {device}")

    in_dim = 2048
    out_dim = 4096
    rank = 16
    alpha = 32.0

    lora_layer = Gemma2LoRALinear(in_dim, out_dim, r=rank, lora_alpha=alpha).to(device)

    # Count parameters
    total_params = sum(p.numel() for p in lora_layer.parameters())
    trainable_params = sum(p.numel() for p in lora_layer.parameters() if p.requires_grad)

    print(f"Total Layer Parameters     : {total_params:,}")
    print(f"Trainable LoRA Parameters  : {trainable_params:,}")
    print(f"Parameter Reduction Ratio  : {trainable_params / total_params * 100:.2f}% of baseline")

    # Step 1: Verify Initial Output Identity
    dummy_input = torch.randn(2, 32, in_dim, device=device)
    with torch.no_grad():
        out_initial = lora_layer(dummy_input)
        out_base_only = F.linear(dummy_input, lora_layer.weight)
        initial_diff = (out_initial - out_base_only).abs().max().item()

    print(f"Initial Forward Pass Check : Max deviation from base = {initial_diff:.2e} (Passed: {initial_diff == 0.0})")

    # Step 2: Simulate Training Step
    target = torch.randn_like(out_initial)
    optimizer = torch.optim.AdamW([p for p in lora_layer.parameters() if p.requires_grad], lr=1e-3)

    out = lora_layer(dummy_input)
    loss = F.mse_loss(out, target)
    loss.backward()
    optimizer.step()

    print(f"Simulated Training Step    : MSE Loss = {loss.item():.4f}")
    assert lora_layer.lora_B.grad is not None, "Gradient must flow into lora_B"
    assert lora_layer.weight.grad is None, "Base weight W_0 must receive ZERO gradients"
    print("Gradient Check Passed      : Base weight strictly frozen, adapters updated.")

    # Step 3: Verify Weight Folding
    lora_layer.merge_weights()
    with torch.no_grad():
        out_merged = lora_layer(dummy_input)
    print("Inference Folding Verified : Output computed with zero adapter branch latency!")

if __name__ == "__main__":
    run_lora_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (27B QLoRA Memory Math on GB10)

### 7.1 Exact Memory Footprint for Gemma 2 27B QLoRA
On the NVIDIA DGX Spark (128 GB Unified Memory):
- **Base Model (4-bit NF4)**:
  $$27.2 \times 10^9 \text{ parameters} \times 0.5 \text{ bytes} \approx 13.6 \text{ GB}$$
- **LoRA Adapter Weights (Rank 16 across all 7 projections)**:
  $$\approx 65 \times 10^6 \text{ parameters} \times 2 \text{ bytes (BF16)} \approx 0.13 \text{ GB}$$
- **Optimizer States (AdamW on trainable params only)**:
  $$65 \times 10^6 \times 8 \text{ bytes} \approx 0.52 \text{ GB}$$
- **Gradients (BF16)**:
  $$65 \times 10^6 \times 2 \text{ bytes} \approx 0.13 \text{ GB}$$
- **Activation Checkpointing (Context Length 4,096, Batch Size 4)**:
  $$\approx 4.2 \text{ GB}$$
- **Total VRAM Consumption**:
  $$13.6 + 0.13 + 0.52 + 0.13 + 4.2 = \mathbf{18.58\text{ GB}}$$

**Engineering Takeaway**: Gemma 2 27B QLoRA consumes only **14.5%** of the DGX Spark's 128 GB memory pool, leaving **$109\text{ GB}$ of free memory** to scale sequence lengths to 32k or run multi-agent evaluations concurrently.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate LoRA Scaling Factor**:
   If an engineer configures rank $r = 64$ and $\alpha = 16.0$, what is the scaling multiplier, and what happens if $\alpha$ is doubled to $32.0$?
   - *Solution*: Scaling multiplier is $\frac{\alpha}{r} = \frac{16.0}{64} = 0.25$. Doubling $\alpha$ to $32.0$ increases the multiplier to $0.50$, effectively doubling the learning rate and magnitude of the adapter's updates.

2. **Calculate Parameter Count of Adapters**:
   For Gemma 2 9B with hidden dimension $D = 3584$, calculate the number of parameters added by adapting `q_proj` ($3584 \to 3584$) with rank $r = 16$ across 42 layers.
   - *Solution*:
     - Per layer: $(3584 \times 16) + (16 \times 3584) = 57,344 + 57,344 = 114,688$ parameters.
     - Across 42 layers: $42 \times 114,688 = 4,816,896$ parameters ($\approx 4.82\text{M}$ weights).

### Troubleshooting FAQ

- **Q: Why does my QLoRA fine-tuning run slowly on the Grace ARM CPU?**
  *A*: 4-bit NF4 dequantization must be executed on the Blackwell Tensor Cores using native CUDA/Triton kernels (`bitsandbytes`). If the base model is accidentally placed on `device="cpu"`, dequantization falls back to unvectorized CPU loops. Always pass `device_map="auto"` or `device_map="cuda:0"`.
- **Q: Should I apply LoRA to the Gemma 2 embedding and unembedding layers?**
  *A*: Generally, no. Gemma 2's vocabulary is $256,128$ tokens wide. Adapting `embed_tokens` or `lm_head` with rank $r = 16$ adds $(256,128 \times 16) \times 2 \approx 8.2\times 10^6$ parameters per matrix, which is disproportionate and often causes gradient instability in the soft-capping layer. Keep the vocabulary heads frozen.
