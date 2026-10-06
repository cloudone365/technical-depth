# Volume 05: Minitron: Structured Pruning and Distillation

```
==================================================================================================
TARGET AUDIENCE: Model Compression Researchers, Edge AI Engineers, Efficiency Architects
PREREQUISITES   : Transformer layer architectures, Taylor expansion importance estimation, KL divergence
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master NVIDIA Minitron methodology: structured depth/width pruning, importance score
                  ranking, and post-pruning knowledge distillation to compress 8B models into 4B edge powerhouses.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Training state-of-the-art foundation models from scratch costs millions of dollars in compute. However, deploying an 8B–70B model to edge devices, latency-sensitive real-time robotics, or cost-constrained enterprise microservices is often impractical. Unstructured pruning (zeroing out random individual weights) creates irregular memory access patterns that fail to accelerate execution on GPU Tensor Cores.

**NVIDIA Minitron** revolutionizes model compression through **Structured Pruning** (slicing entire Transformer layers, attention heads, and hidden channels) combined with **Knowledge Distillation (KD)**. By pruning a pre-trained teacher (e.g. LLaMA-3.1-8B) down to a compact student (Minitron-4B) and retraining on just 40–100 Billion tokens (a tiny fraction of original pretraining), Minitron retains over **97% of original capabilities** at half the memory footprint and double the inference speed.

```
       ┌───────────────────────────────────────────────────────────────┐
       │   Pre-Trained Foundation Teacher Model (e.g., Llama-3.1-8B)   │
       │   32 Layers, 4096 Hidden Dim, 32 Attention Heads              │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   NeMo Importance Scoring & Structured Pruning                │
       │   - Depth: Identify & remove redundant layers (Cosine Sim)    │
       │   - Width: Truncate FFN hidden dim via 1st-Order Taylor       │
       │   - Attention: Prune low-importance Query/KV heads            │
       └───────────────────────────────┬───────────────────────────────┘
                                       │ Produces Compact Un-Tuned Student
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │             Knowledge Distillation (KD) Engine                │
       │                                                               │
       │  Teacher (8B Frozen)              Student (Minitron-4B Train) │
       │         │                                      │              │
       │         ▼                                      ▼              │
       │   Teacher Logits                         Student Logits       │
       │         └──────────────────┬───────────────────┘              │
       │                            ▼                                  │
       │            KL Divergence Loss: D_KL(P_T || P_S)               │
       │            + Intermediate Layer MSE Distillation              │
       └────────────────────────────┬──────────────────────────────────┘
                                    │ Retrained on 40B - 100B Tokens
                                    ▼
       ┌───────────────────────────────────────────────────────────────┐
       │     Minitron-4B Production Deployable Foundation Model        │
       │     (2x Inference Speedup, 50% VRAM, 97%+ Accuracy Kept)      │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: Pruning a Bonsai Tree
3. Evolutionary Lineage: From Unstructured Weight Sparsity to Minitron Structured KD
4. First-Principles Mathematics & Algorithmic Formulations
   - Depth Pruning via Layer Representation Cosine Similarity
   - Width Pruning via First-Order Taylor Expansion Importance
   - Temperature-Scaled Kullback-Leibler Divergence Loss
   - Hidden State MSE Distillation Loss
5. Comparative Trade-Off Matrix: Model Compression Techniques
6. Concrete Production Hands-On Lab: Layer Pruning & Knowledge Distillation Simulator
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: Pruning a Bonsai Tree

Imagine training a master artisan vs. shaping a bonsai tree:

- **Unstructured Pruning (Snipping Random Pine Needles)**:
  A landscaper takes microscopic scissors and snips off 50% of the individual pine needles scattered randomly across every branch. From a distance, the tree weighs less, but the branches are still just as wide, the trunk is still just as thick, and the wind still meets the same surface area resistance. On modern GPUs, Tensor Cores process entire matrices at once; random missing needles do not speed up computation.

- **Minitron Structured Pruning & Distillation (Precision Branch Surgery)**:
  The master botanist identifies entire branches that cast duplicate shadows (redundant Transformer layers) and cleanly saws them off (**Depth Pruning**). Then, they trim the outer perimeter branches by a clean 25% across all levels (**Width Pruning**).
  The tree is now structurally smaller, occupies half the physical volume, and allows wind (inference batches) to pass through twice as fast. Finally, the master artisan gives the compact tree focused nutrients (**Knowledge Distillation**) to regrow maximum foliage on its remaining compact branches.

---

## 3. Evolutionary Lineage: From Unstructured Weight Sparsity to Minitron Structured KD

```
Generation 1 (2015-2018)      Generation 2 (2019-2022)      Generation 3 (2024-2026)
Magnitude Weight Pruning      2:4 Semi-Structured Sparsity  NVIDIA Minitron Structured KD
──────────────────────────    ──────────────────────────    ─────────────────────────────
- Zero out weights < eps      - 2 non-zero per 4 elements   - Slices entire layers & dims
- Irregular memory access     - Hardware Ampere support     - Clean architectural shrinkage
- 0% speedup on GPUs          - Hard to fine-tune stably    - Retains 97%+ accuracy
- Requires specialized sparse - Fixed 2x compression ceiling- 40B token rapid recovery
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Depth Pruning via Layer Representation Cosine Similarity

To determine which Transformer layers can be excised with minimal disruption, NeMo computes the cosine similarity between the input hidden state $\mathbf{h}_{l-1}$ and the output hidden state $\mathbf{h}_l$ of layer $l$:

$$\text{Sim}(l) = \frac{\mathbf{h}_{l-1}^\top \mathbf{h}_l}{\|\mathbf{h}_{l-1}\|_2 \|\mathbf{h}_l\|_2}$$

If $\text{Sim}(l) \approx 1.0$, layer $l$ is an approximate identity function ($\mathbf{h}_l \approx \mathbf{h}_{l-1}$), contributing minimal transformation to the residual stream.
Layers are sorted by $\text{Sim}(l)$ in descending order, and the top-$K$ most redundant layers are excised from the network.

### Width Pruning via First-Order Taylor Expansion Importance

For intermediate MLP weights $\mathbf{W} \in \mathbb{R}^{d_{\text{ffn}} \times d_{\text{model}}}$, we estimate the change in loss $\Delta \mathcal{L}$ if channel $j$ is removed using a first-order Taylor expansion:

$$\Delta \mathcal{L}_j \approx \left| \frac{\partial \mathcal{L}}{\partial \mathbf{W}_j} \cdot \mathbf{W}_j \right|$$

NeMo computes the average importance score $\mathcal{I}_j$ over a calibration dataset $\mathcal{D}_{\text{calib}}$ of $M$ tokens:

$$\mathcal{I}_j = \frac{1}{M} \sum_{t=1}^M \left| \mathbf{g}_{j, t} \odot \mathbf{W}_j \right|$$

Where $\mathbf{g}_{j, t}$ is the gradient of the loss with respect to channel $j$. Channels with the lowest cumulative $\mathcal{I}_j$ are pruned, shrinking $d_{\text{ffn}}$ from e.g. 14,336 down to 9,216.

### Temperature-Scaled Kullback-Leibler Divergence Loss

During distillation, the compact student model $\mathcal{M}_S$ is trained to match the soften probability distribution of the frozen teacher model $\mathcal{M}_T$.
The temperature-scaled probability distribution over vocabulary $\mathcal{V}$ is:

$$P(v \mid x; T) = \frac{\exp\left( z_v / T \right)}{\sum_{j \in \mathcal{V}} \exp\left( z_j / T \right)}$$

Where $T > 1$ softens the distribution, revealing the "dark knowledge" (relative probabilities of non-target tokens).
The KD objective is the KL divergence:

$$\mathcal{L}_{\text{KD}} = T^2 \cdot D_{\text{KL}}\left( P_T(\cdot \mid x; T) \;\|\; P_S(\cdot \mid x; T) \right) = T^2 \sum_{v \in \mathcal{V}} P_T(v \mid x; T) \log \left( \frac{P_T(v \mid x; T)}{P_S(v \mid x; T)} \right)$$

The total loss combines distillation with standard cross-entropy on ground-truth tokens:

$$\mathcal{L}_{\text{total}} = \alpha \mathcal{L}_{\text{KD}} + (1 - \alpha) \mathcal{L}_{\text{CE}}$$

---

## 5. Comparative Trade-Off Matrix: Model Compression Techniques

| Compression Technique | Speedup on GPU | Memory Reduction | Accuracy Retention | Retraining Cost |
| :--- | :--- | :--- | :--- | :--- |
| **Unstructured Pruning (90%)**| 1.0x (No speedup!) | High (if compressed) | Terrible (< 40%) | Low |
| **2:4 Sparse (NVIDIA Ampere)** | 1.3x – 1.6x | 1.0x (dense weights)| Moderate (90–95%) | Moderate |
| **4-bit Quantization (AWQ/GPTQ)**| 1.8x – 2.5x | 3.5x – 4.0x | Excellent (98%+) | **Zero (Minutes)** |
| **Minitron Structured Pruning + KD**| **2.0x – 2.4x Native** | **2.0x Physical** | **Superior (97%+)** | **Moderate (~40B tokens)** |

---

## 6. Concrete Production Hands-On Lab: Layer Pruning & Knowledge Distillation Simulator

This self-contained Python script simulates the Minitron compression pipeline: computing layer representation cosine similarities, pruning the most redundant layer, and executing a knowledge distillation training step.

```python
#!/usr/bin/env python3
"""
NVIDIA Minitron Pruning & Knowledge Distillation Lab.
Demonstrates layer redundancy analysis, depth pruning, and temperature-scaled KD loss.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

# =====================================================================
# 1. TOY MULTI-LAYER TEACHER MODEL
# =====================================================================

class TransformerBlock(nn.Module):
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.linear1 = nn.Linear(hidden_dim, hidden_dim)
        self.linear2 = nn.Linear(hidden_dim, hidden_dim)
        self.norm = nn.LayerNorm(hidden_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Residual block
        residual = x
        h = F.gelu(self.linear1(self.norm(x)))
        out = residual + self.linear2(h)
        return out

class TeacherModel(nn.Module):
    def __init__(self, num_layers: int = 4, hidden_dim: int = 64, vocab_size: int = 200):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, hidden_dim)
        self.layers = nn.ModuleList([TransformerBlock(hidden_dim) for _ in range(num_layers)])
        self.head = nn.Linear(hidden_dim, vocab_size, bias=False)

    def forward_with_hidden_states(self, input_ids: torch.Tensor):
        x = self.embedding(input_ids)
        hidden_states = [x]
        for layer in self.layers:
            x = layer(x)
            hidden_states.append(x)
        logits = self.head(x)
        return logits, hidden_states

# =====================================================================
# 2. DEPTH PRUNING: LAYER IMPORTANCE ESTIMATION
# =====================================================================

class MinitronPruner:
    @staticmethod
    def identify_redundant_layer(hidden_states) -> int:
        """
        Computes cosine similarity between layer inputs and outputs.
        Returns the index of the layer with highest similarity (least transformation).
        """
        num_layers = len(hidden_states) - 1
        best_layer = -1
        max_similarity = -1.0

        for l in range(num_layers):
            h_in = hidden_states[l]
            h_out = hidden_states[l + 1]
            cos_sim = F.cosine_similarity(h_in.view(-1, h_in.size(-1)),
                                          h_out.view(-1, h_out.size(-1)), dim=-1).mean().item()
            print(f"Layer {l}: Cosine Similarity = {cos_sim:.5f}")
            if cos_sim > max_similarity:
                max_similarity = cos_sim
                best_layer = l

        return best_layer

# =====================================================================
# 3. KNOWLEDGE DISTILLATION LOSS ENGINE
# =====================================================================

def compute_kd_loss(student_logits: torch.Tensor,
                    teacher_logits: torch.Tensor,
                    labels: torch.Tensor,
                    temperature: float = 2.0,
                    alpha: float = 0.7) -> torch.Tensor:
    """
    Computes alpha * KD_Loss + (1 - alpha) * CE_Loss.
    """
    # 1. Softened KL Divergence
    p_s = F.log_softmax(student_logits / temperature, dim=-1)
    p_t = F.softmax(teacher_logits / temperature, dim=-1)
    loss_kd = F.kl_div(p_s, p_t, reduction="batchmean") * (temperature ** 2)

    # 2. Hard Ground-Truth Cross-Entropy
    loss_ce = F.cross_entropy(student_logits.view(-1, student_logits.size(-1)), labels.view(-1))

    return alpha * loss_kd + (1.0 - alpha) * loss_ce

# =====================================================================
# 4. VERIFICATION HARNESS
# =====================================================================

def run_minitron_lab():
    print("=" * 80)
    print("NVIDIA MINITRON PRUNING & KNOWLEDGE DISTILLATION LAB")
    print("=" * 80)

    torch.manual_seed(42)
    teacher = TeacherModel(num_layers=4, hidden_dim=64, vocab_size=200)

    # Seed Layer 2 to be near-identity to test detection
    with torch.no_grad():
        teacher.layers[2].linear1.weight.fill_(0.0)
        teacher.layers[2].linear2.weight.fill_(0.0)

    sample_ids = torch.randint(0, 200, (2, 16))
    teacher_logits, hidden_states = teacher.forward_with_hidden_states(sample_ids)

    print("\nPhase 1: Estimating Layer Redundancy via Cosine Similarity...")
    redundant_idx = MinitronPruner.identify_redundant_layer(hidden_states)
    print(f"\n[PRUNING DECISION] Identified Layer {redundant_idx} as most redundant (Selected for pruning).")
    assert redundant_idx == 2, "Failed to identify synthetic zeroed layer!"

    print("\nPhase 2: Distilling into 3-Layer Student Model...")
    student = TeacherModel(num_layers=3, hidden_dim=64, vocab_size=200)
    student_logits, _ = student.forward_with_hidden_states(sample_ids)

    kd_loss = compute_kd_loss(student_logits, teacher_logits.detach(), sample_ids, temperature=2.0)
    print(f"Initial Distillation Combined Loss: {kd_loss.item():.4f}")

    # Backward pass verification
    kd_loss.backward()
    print("Student gradients computed cleanly via teacher supervision.")

    print("\n[SUCCESS] Minitron structured pruning identification and distillation validated.")

if __name__ == "__main__":
    run_minitron_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Minitron development on DGX Spark:
1. **Teacher-Student Co-Location**:
   Distilling LLaMA-3.1-8B (Teacher) into Minitron-4B (Student):
   - Teacher weights (Frozen, FP8): $\approx 8\text{ GB}$.
   - Student weights (Trainable, BF16): $\approx 8\text{ GB}$.
   - Student Optimizer states (AdamW): $\approx 16\text{ GB}$.
   - **Total Footprint**: $\approx 32\text{ GB}$ VRAM.
   - Easily fits into the 128 GB Unified Memory space, allowing high distillation batch sizes ($B=32$, $L=4096$) without multi-GPU sharding.

2. **NeMo Pruning Command**:
   ```bash
   python -m nemo.collections.nlp.models.language_modeling.megatron_gpt_pruning \
     --model_path /workspace/models/llama3_8b.nemo \
     --output_path /workspace/models/minitron_4b_pruned.nemo \
     --target_num_layers 16 \
     --target_hidden_size 3072
   ```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Temperature Scaling Effect)**:
   Evaluate the KL divergence loss between identical student and teacher logits at $T=1.0$ vs $T=5.0$. Explain why multiplying by $T^2$ is necessary to keep gradient magnitudes consistent.

2. **Exercise 2 (Intermediate Hidden State Loss)**:
   Implement an auxiliary MSE loss term: $\mathcal{L}_{\text{hidden}} = \|\mathbf{h}_{\text{student}} - \mathbf{W}_{\text{proj}} \mathbf{h}_{\text{teacher}}\|_2^2$ to align intermediate representations.

### Solutions

**Solution for Exercise 1**:
As $T$ increases, $\frac{1}{T}$ shrinks logits toward uniform distribution, causing gradients of $\log \frac{P_T}{P_S}$ to scale down as $\frac{1}{T^2}$. Multiplying the loss by $T^2$ acts as a normalization factor ensuring that the magnitude of gradients flowing into student parameters remains independent of the chosen temperature.

### Troubleshooting FAQ

- **Q: Student model suffers catastrophic perplexity collapse after pruning.**
  - *Fix*: You skipped the distillation retraining phase. Slicing layers causes an immediate 30–50% capability drop. You must run at least 1,000–5,000 warm-up distillation steps on high-quality text to allow the remaining layers to adapt to the altered residual stream.

- **Q: Memory runs out when running teacher and student simultaneously.**
  - *Fix*: Freeze the teacher model with `teacher.eval()` and wrap its forward pass in `torch.no_grad()`. Furthermore, quantize the teacher to FP8 or 4-bit AWQ during distillation.
