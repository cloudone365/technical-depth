# Volume 15: Parameter-Efficient Fine-Tuning (PEFT) in NVIDIA NeMo

```
==================================================================================================
TARGET AUDIENCE: ML Infrastructure Engineers, Multi-Tenant Platform Devs, Resource Optimization Leads
PREREQUISITES   : Linear algebra (SVD, matrix ranks), LoRA fundamentals, Megatron Transformer layers
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master enterprise PEFT in NVIDIA NeMo: LoRA, QLoRA, P-Tuning v2, bottleneck adapters,
                  selective layer freezing, and zero-overhead production weight merging.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

In enterprise environments serving dozens of internal departments (Finance, Legal, HR, Engineering), duplicating a 70B foundation model for every task requires terabytes of redundant GPU memory and storage. Full-parameter fine-tuning is economically and operationally unsustainable.

**NVIDIA NeMo PEFT** enables true multi-tenant foundation model architecture: a single, frozen base model (e.g. `Nemotron-70B`) serves as the immutable backbone in GPU memory, while lightweight task-specific adapters (consuming $< 50\text{ MB}$ of weights) are dynamically swapped or merged in real time.

```
       ┌───────────────────────────────────────────────────────────────┐
       │   Master Shared Frozen Foundation Model (e.g., Nemotron-70B)  │
       │   Loaded once into Blackwell GB10 VRAM (70 GB in FP8)         │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
┌─────────────────┐           ┌─────────────────┐           ┌─────────────────┐
│ Adapter A: SRE  │           │ Adapter B: Med  │           │ Adapter C: Code │
│ LoRA Rank 16    │           │ LoRA Rank 32    │           │ P-Tuning v2     │
│ VRAM: 35 MB     │           │ VRAM: 70 MB     │           │ VRAM: 12 MB     │
└─────────────────┘           └─────────────────┘           └─────────────────┘
         │                             │                             │
         └─────────────────────────────┼─────────────────────────────┘
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Zero-Latency Production Merge (Triton Inference Server)     │
       │   W_prod = W_0 + (alpha / r) * (B * A)                        │
       │   Executes at full unquantized native hardware speed          │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Master Wardrobe and the Magnetic Lapel Pins
3. Evolutionary Lineage: From Full Model Replication to Dynamic Adapter Switching
4. First-Principles Mathematics & Algorithmic Formulations
   - LoRA Low-Rank Factorization & Intrinsic Dimensionality
   - P-Tuning v2 Deep Prompt Key/Value Prefix Mathematics
   - Bottleneck Residual Adapters (Houlsby vs Pfeiffer)
   - Zero-Latency Inference Weight Folding Formulation
5. Comparative Trade-Off Matrix: PEFT Techniques in NeMo
6. Concrete Production Hands-On Lab: Complete LoRA & P-Tuning v2 Architecture
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Master Wardrobe and the Magnetic Lapel Pins

Imagine an executive who represents 20 different international charities:

- **The Full Fine-Tuning Approach (20 Identical Mansions)**:
  For every charity the executive represents, you build an entirely new \$10M mansion, buy a new Rolls-Royce, and buy a full wardrobe of 500 suits. The suits in all 20 mansions are identical, except for a tiny embroidered charity logo on the left collar. The real estate cost bankrupts the company.

- **The NeMo PEFT Approach (The Single Mansion with Magnetic Lapel Pins)**:
  The executive lives in one central mansion (**The Single Frozen 70B Base Model**).
  When representing Charity A (Finance), they snap a 1-gram magnetic gold pin onto their lapel (**LoRA Adapter A**).
  When representing Charity B (Legal), they swap the pin for a silver pin in 1 second (**LoRA Adapter B**).
  The mansion and wardrobe are 100% shared; each pin weighs almost nothing and costs \$5.

---

## 3. Evolutionary Lineage: From Full Model Replication to Dynamic Adapter Switching

```
Generation 1 (2018-2020)      Generation 2 (2021-2022)      Generation 3 (2023-2026)
Full Parameter Fine-Tuning    Initial LoRA & Prefix Tuning  NeMo Megatron PEFT & S-LoRA
──────────────────────────    ──────────────────────────    ───────────────────────────
- 100% weights updated        - 0.1% parameters updated     - 3D parallel distributed LoRA
- 1 checkpoint per task       - Single-GPU scripts only     - QLoRA NF4 base quantization
- Multi-terabyte disk bloat   - Inference latency penalty   - Zero-overhead weight folding
- Impossible multi-tenancy    - Fragmented adapter formats  - Dynamic multi-tenant serving
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### LoRA Low-Rank Factorization & Intrinsic Dimensionality

Aghajanyan et al. proved that foundation models possess an extremely low **intrinsic dimensionality**: the essential gradient updates during domain adaptation lie in a subspace of rank $r \ll d$.

Given a pre-trained weight $\mathbf{W}_0 \in \mathbb{R}^{d_{\text{out}} \times d_{\text{in}}}$, LoRA constrains the update $\Delta \mathbf{W}$ by decomposing it into two low-rank matrices:

$$\Delta \mathbf{W} = \frac{\alpha}{r} (\mathbf{B} \mathbf{A})$$

Where $\mathbf{A} \in \mathbb{R}^{r \times d_{\text{in}}}$ is initialized from a Gaussian distribution $\mathcal{N}\left(0, \frac{1}{r}\right)$, and $\mathbf{B} \in \mathbb{R}^{d_{\text{out}} \times r}$ is initialized to strictly zero.
At step 0:

$$\Delta \mathbf{W} = \frac{\alpha}{r} (\mathbf{0} \cdot \mathbf{A}) = \mathbf{0}$$

The forward pass computes:

$$\mathbf{h} = \mathbf{W}_0 \mathbf{x} + \frac{\alpha}{r} \mathbf{B} (\mathbf{A} \mathbf{x})$$

The parameter reduction ratio is:

$$\text{Compression} = \frac{r(d_{\text{in}} + d_{\text{out}})}{d_{\text{in}} \cdot d_{\text{out}}}$$

For $d_{\text{in}} = d_{\text{out}} = 8192$ and $r = 16$:

$$\text{Compression} = \frac{16 \times 16384}{67,108,864} = \frac{262,144}{67,108,864} \approx 0.39\% \quad (\mathbf{256\times \text{ fewer parameters}})$$

### P-Tuning v2 Deep Prompt Key/Value Prefix Mathematics

Unlike prompt tuning (which only prepends virtual tokens to the initial word embeddings), **P-Tuning v2** prepends learnable prefix vectors to the Key and Value matrices at **every single Transformer layer $l$**:

$$\mathbf{K}^{(l)} = \left[ \mathbf{P}_K^{(l)} \;;\; \mathbf{K}_{\text{text}}^{(l)} \right], \quad \mathbf{V}^{(l)} = \left[ \mathbf{P}_V^{(l)} \;;\; \mathbf{V}_{\text{text}}^{(l)} \right]$$

Where $\mathbf{P}_K^{(l)}, \mathbf{P}_V^{(l)} \in \mathbb{R}^{L_{\text{prefix}} \times d_{\text{head}}}$.
The Query tokens attend to the virtual prefix parameters, allowing direct steering of the attention distribution across all depths of the network.

### Zero-Latency Inference Weight Folding Formulation

During production serving, evaluating the two matrix multiplications $\mathbf{W}_0 \mathbf{x} + \frac{\alpha}{r} \mathbf{B} \mathbf{A} \mathbf{x}$ separately introduces extra kernel launches and memory reads.
Before deploying to Triton Inference Server, NeMo **folds** the low-rank delta directly into the frozen weight:

$$\mathbf{W}_{\text{deployed}} = \mathbf{W}_0 + \frac{\alpha}{r} (\mathbf{B} \mathbf{A})$$

The deployed model executes as a standard monolithic GEMM, achieving **identical inference speed to the base model with zero FLOPs overhead**.

---

## 5. Comparative Trade-Off Matrix: PEFT Techniques in NeMo

| PEFT Architecture | Trainable Params | VRAM Savings during SFT | Inference Latency Impact | Zero-Latency Merging |
| :--- | :--- | :--- | :--- | :--- |
| **LoRA** | 0.05% – 0.2% | **75% – 85%** | None (When folded) | **Yes (Direct Add)** |
| **QLoRA (NF4)** | 0.05% – 0.2% | **85% – 90%** | Slight (Dequant kernel) | Requires dequantization |
| **P-Tuning v2** | 0.01% – 0.05% | **85% – 90%** | Slight (Increases $L$) | No (Prefix must stay) |
| **Pfeiffer Adapter** | 0.5% – 1.0% | **70% – 80%** | Moderate (Extra MLP layers)| No (Extra layers) |
| **Selective Layer Freeze**| 10% – 20% | **40% – 50%** | None | Native |

---

## 6. Concrete Production Hands-On Lab: Complete LoRA & P-Tuning v2 Architecture

This self-contained Python script implements:
1. A modular PyTorch LoRA linear layer with scaling factor $\alpha/r$.
2. P-Tuning v2 virtual Key/Value prefix concatenation.
3. Mathematical proof of zero-latency weight folding.

```python
#!/usr/bin/env python3
"""
NVIDIA NeMo PEFT Architecture Simulator.
Demonstrates LoRA forward pass, P-Tuning v2 deep prefixes, and weight folding.
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F

# =====================================================================
# 1. MODULAR LORA LINEAR LAYER
# =====================================================================

class LoRALinear(nn.Module):
    def __init__(self, in_features: int, out_features: int, r: int = 16, alpha: float = 32.0):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.r = r
        self.scaling = alpha / r

        # Frozen base weight
        self.base_weight = nn.Parameter(torch.randn(out_features, in_features), requires_grad=False)

        # Trainable low-rank adapters
        self.lora_A = nn.Parameter(torch.randn(r, in_features) * (1.0 / math.sqrt(r)))
        self.lora_B = nn.Parameter(torch.zeros(out_features, r))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Base forward: x * W0^T
        base_out = F.linear(x, self.base_weight)
        # LoRA forward: x * A^T * B^T * scaling
        lora_out = F.linear(F.linear(x, self.lora_A), self.lora_B) * self.scaling
        return base_out + lora_out

    def merge_weights(self) -> torch.Tensor:
        """Computes folded production weight: W_merged = W0 + (alpha/r) * (B * A)."""
        delta_W = torch.matmul(self.lora_B, self.lora_A) * self.scaling
        return self.base_weight + delta_W

# =====================================================================
# 2. P-TUNING V2 DEEP PREFIX ENGINE
# =====================================================================

class PTuningV2Prefix(nn.Module):
    def __init__(self, prefix_len: int = 8, hidden_dim: int = 64):
        super().__init__()
        self.prefix_len = prefix_len
        # Learnable virtual Key and Value prefixes
        self.prefix_K = nn.Parameter(torch.randn(prefix_len, hidden_dim))
        self.prefix_V = nn.Parameter(torch.randn(prefix_len, hidden_dim))

    def prepend_prefixes(self, K: torch.Tensor, V: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        K, V shape: [Batch, SeqLen, HiddenDim]
        Returns: [Batch, PrefixLen + SeqLen, HiddenDim]
        """
        B = K.size(0)
        p_k = self.prefix_K.unsqueeze(0).expand(B, -1, -1)
        p_v = self.prefix_V.unsqueeze(0).expand(B, -1, -1)
        return torch.cat([p_k, K], dim=1), torch.cat([p_v, V], dim=1)

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

def run_peft_lab():
    print("=" * 80)
    print("NVIDIA NEMO PARAMETER-EFFICIENT FINE-TUNING (PEFT) LAB")
    print("=" * 80)

    torch.manual_seed(42)
    B, L, Din, Dout = 2, 8, 64, 64

    # 1. Test LoRA Forward & Weight Folding
    print("Step 1: Testing LoRA Forward Pass & Folding...")
    lora_layer = LoRALinear(Din, Dout, r=8, alpha=16.0)
    # Give lora_B non-zero weights to simulate trained state
    with torch.no_grad():
        lora_layer.lora_B.copy_(torch.randn_like(lora_layer.lora_B) * 0.1)

    x = torch.randn(B, L, Din)
    y_adapter = lora_layer(x)

    # Compute folded monolithic GEMM
    W_merged = lora_layer.merge_weights()
    y_folded = F.linear(x, W_merged)

    max_diff = torch.max(torch.abs(y_adapter - y_folded)).item()
    print(f"  Max Absolute Error (LoRA Dual GEMM vs Folded Monolithic): {max_diff:.8e}")
    assert torch.allclose(y_adapter, y_folded, atol=1e-5), "LoRA weight folding mismatch!"
    print("  -> Zero-latency production weight folding mathematically verified.")

    # 2. Test P-Tuning v2 Key/Value Prefix Injection
    print("\nStep 2: Testing P-Tuning v2 Prefix Injection...")
    prefix_engine = PTuningV2Prefix(prefix_len=4, hidden_dim=Dout)
    K_orig = torch.randn(B, L, Dout)
    V_orig = torch.randn(B, L, Dout)

    K_prepended, V_prepended = prefix_engine.prepend_prefixes(K_orig, V_orig)
    print(f"  Original Key Tensor Shape : {list(K_orig.shape)}")
    print(f"  Prepended Key Tensor Shape: {list(K_prepended.shape)} (Prefix Len: 4)")
    assert K_prepended.size(1) == L + 4

    print("\n[SUCCESS] LoRA factorization, weight folding, and P-Tuning v2 validated.")

if __name__ == "__main__":
    run_peft_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

PEFT deployment on the DGX Spark:
1. **Multi-Tenant Memory Layout**:
   - Master Base Model: `Llama-3.1-Nemotron-70B` pinned to GB10 GPU in FP8 ($\approx 70\text{ GB}$).
   - 20 Enterprise Adapters cached in Grace ARM RAM ($\approx 20 \times 35\text{ MB} = 700\text{ MB}$).
   - As incoming requests arrive, the appropriate adapter weights are mapped across the 900 GB/s NVLink-C2C bus in **$< 0.04\text{ ms}$**.

2. **NeMo Configuration Command**:
   ```bash
   python -m nemo.collections.nlp.models.language_modeling.megatron_gpt_peft \
     --config-path=/workspace/nemo_configs \
     --config-name=megatron_gpt_peft \
     model.peft.peft_scheme=lora \
     model.peft.lora_tuning.adapter_dim=16 \
     model.peft.lora_tuning.alpha=32 \
     model.peft.lora_tuning.target_modules=['attention.dense_h_to_4h','attention.dense_4h_to_h']
   ```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Adapter Memory Calculus)**:
   Calculate the exact file size in megabytes of a LoRA adapter for a model with 80 layers, $d_{\text{model}} = 8192$, targeting all $Q, K, V$ and Output projections with rank $r = 16$ in FP16 precision.

2. **Exercise 2 (Selective Layer Freezing)**:
   Author a PyTorch loop that iterates over a model's `named_parameters()` and freezes all layers except for LayerNorm parameters and the top 4 Transformer blocks.

### Solutions

**Solution for Exercise 1**:
- For each layer: 4 projections ($Q, K, V, O$).
- For each projection: $\mathbf{A} \in \mathbb{R}^{16 \times 8192}$ and $\mathbf{B} \in \mathbb{R}^{8192 \times 16}$.
- Params per projection: $2 \times (16 \times 8192) = 262,144$ parameters.
- Params per layer: $4 \times 262,144 = 1,048,576$.
- Across 80 layers: $80 \times 1,048,576 = 83,886,080$ parameters.
- In FP16 (2 bytes/param): $83,886,080 \times 2 \approx 167.77\text{ MB}$.

### Troubleshooting FAQ

- **Q: Model fine-tuned with LoRA produces identical output to base model.**
  - *Fix*: Your `lora_B` matrices were never updated (gradients were zero), or the scaling factor $\frac{\alpha}{r}$ was set to zero. Check that `adapter.requires_grad=True` and verify that the learning rate is sufficiently high (LoRA typically requires $1 \times 10^{-4}$ to $5 \times 10^{-4}$, significantly higher than full SFT).

- **Q: Triton Inference Server crashes when loading multiple LoRA adapters.**
  - *Fix*: In Triton's `config.pbtxt`, ensure dynamic LoRA caching is enabled with `--lora-cache-size 1024` and that all adapters share the exact base model architecture.
