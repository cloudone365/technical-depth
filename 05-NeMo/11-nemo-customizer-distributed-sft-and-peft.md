# Volume 11: NeMo Customizer: Distributed SFT and PEFT

```
==================================================================================================
TARGET AUDIENCE: Post-Training Engineers, Distributed Systems Devs, Enterprise Customization Leads
PREREQUISITES   : Supervised Fine-Tuning (SFT), PyTorch autograd, FlashAttention-2 VarLen, LoRA
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master enterprise-scale model customization using NeMo Customizer: FlashAttention VarLen
                  sequence packing, loss masking, distributed gradient accumulation, and Megatron PEFT.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Supervised Fine-Tuning (SFT) transforms a raw, unpredictable base model into an instruction-following enterprise specialist. However, enterprise customization pipelines face massive compute inefficiencies: naive batching pads variable-length dialogues with useless `<pad>` tokens, wasting up to **60% of GPU compute and memory bandwidth** on non-informative zeros.

**NVIDIA NeMo Customizer** solves this through **FlashAttention Variable-Length (VarLen) Sequence Packing**, multi-GPU Megatron data loaders, strict prompt-loss masking, and integrated Parameter-Efficient Fine-Tuning (PEFT). By concatenating multiple dialogues into monolithic context buffers and resetting self-attention boundaries dynamically, NeMo Customizer accelerates SFT throughput by **2.5x to 4x**.

```
  Traditional Naive Padding Batch (60% Wasted Computation):
  Batch 0: [User Prompt 1] [Response 1] [<pad>] [<pad>] [<pad>] [<pad>]
  Batch 1: [User Prompt 2] [Long Response 2 ..........................]
  Batch 2: [Short Prompt]  [Short Resp] [<pad>] [<pad>] [<pad>] [<pad>]
  
  NeMo Customizer FlashAttention VarLen Sequence Packing (100% Compute Utilization):
  Packed Buffer: [Prompt 1][Resp 1][Prompt 2][Long Resp 2][Short Prompt][Short Resp]
  cu_seqlens   : [0,       L1,             L1+L2,                   L1+L2+L3]
  Loss Mask    : [0 0 ...  1 1 1][0 0 ...  1 1 1 1 1 1 1][0 0 ...   1 1 1 1]
                                       │
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │      NeMo Customizer SFT Engine         │
                  │   - Gradient Accumulation across Nodes  │
                  │   - Megatron Distributed LoRA / QLoRA   │
                  │   - Cosine Decay with Linear Warmup     │
                  └────────────────────┬────────────────────┘
                                       │
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │ Custom Aligned Enterprise Foundation    │
                  │ Checkpoint Ready for Triton Deploy (.nemo│
                  └─────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Packing Peanuts vs. Precision Cargo Packing
3. Evolutionary Lineage: From Padded Hugging Face SFT to NeMo VarLen Customizer
4. First-Principles Mathematics & Algorithmic Formulations
   - FlashAttention VarLen Cumulative Sequence Lengths (`cu_seqlens`)
   - Autoregressive SFT Loss Formulation with Prompt Masking
   - Megatron Distributed LoRA Gradient Dynamics
5. Comparative Trade-Off Matrix: Fine-Tuning Approaches
6. Concrete Production Hands-On Lab: FlashAttention Sequence Packing & SFT Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Packing Peanuts vs. Precision Cargo Packing

Imagine shipping 1,000 packages of varying sizes on a fleet of cargo ships:

- **The Naive Padding Approach (Boxes Filled with Styrofoam Peanuts)**:
  The shipping company mandates that every package must use a giant 10-foot wooden shipping crate. If a customer sends a small watch, the company puts the watch in the crate and fills the remaining 9.9 feet with useless Styrofoam packing peanuts (`<pad>` tokens). You need 100 ships to transport 1,000 items, and you burn 90% of your fuel transporting useless plastic foam.

- **The NeMo Customizer Approach (Tetris Precision Cargo Packing)**:
  NeMo throws away individual crates. Workers pack the watches, laptops, and bicycles tightly together into a single shipping container without a millimeter of wasted space (**Sequence Packing**).
  To ensure cargo doesn't get mixed up, workers paint color-coded divider stripes on the container floor (**Cumulative Sequence Offsets `cu_seqlens`**). The ship operates at **100% capacity**, burning zero wasted fuel.

---

## 3. Evolutionary Lineage: From Padded Hugging Face SFT to NeMo VarLen Customizer

```
Generation 1 (2019-2021)      Generation 2 (2022-2023)      Generation 3 (2024-2026)
Padded PyTorch SFT Loops      Hugging Face TRL DataCollator NeMo Customizer VarLen Megatron
──────────────────────────    ──────────────────────────    ───────────────────────────────
- Fixed max_seq_len padding   - Dynamic batch padding       - Zero padding sequence packing
- 60-70% wasted FLOPs         - Padded to batch max length  - FlashAttention VarLen native
- OOM on long outliers        - Still suffers ~30% waste    - Megatron 3D parallel LoRA
- Single-GPU or basic DDP     - Heavy CPU collation cost    - 3x throughput on Blackwell
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### FlashAttention VarLen Cumulative Sequence Lengths (`cu_seqlens`)

Standard Multi-Head Attention expects a 4D tensor $\mathbf{Q}, \mathbf{K}, \mathbf{V} \in \mathbb{R}^{B \times H \times L \times d}$.
In FlashAttention VarLen, all $B$ variable-length sequences are flattened into a single 1D sequence of total length:

$$L_{\text{total}} = \sum_{b=1}^B L_b$$

The input tensors are packed into 3D shape $[L_{\text{total}}, H, d]$.
To prevent tokens in Sequence $i$ from attending to tokens in Sequence $j$, the kernel receives an auxiliary 1D array of **Cumulative Sequence Lengths** $\mathbf{cu\_seqlens} \in \mathbb{N}^{B+1}$:

$$\mathbf{cu\_seqlens} = \left[ 0, \; L_1, \; L_1 + L_2, \; \dots, \; \sum_{b=1}^B L_b \right]$$

During the self-attention CUDA kernel execution, thread-blocks compute boundaries dynamically:

$$\text{Attn}(Q_t, K_\tau) = 0 \quad \text{if } \tau < \mathbf{cu\_seqlens}[b] \text{ or } \tau \ge \mathbf{cu\_seqlens}[b+1]$$

This achieves strict boundary isolation with **zero padding tokens**.

### Autoregressive SFT Loss Formulation with Prompt Masking

In Supervised Fine-Tuning, the model should only be penalized for predicting the assistant's response, not the user's prompt.
Let sequence $X = (x_1, x_2, \dots, x_N)$ consist of prompt tokens $1 \dots P$ and completion tokens $P+1 \dots N$.
The masked SFT loss is:

$$\mathcal{L}_{\text{SFT}}(\theta) = -\frac{1}{\sum_{i=1}^N m_i} \sum_{i=1}^{N-1} m_i \log P_\theta(x_{i+1} \mid x_1, \dots, x_i)$$

Where the binary loss mask $m_i$ is:

$$m_i = \begin{cases} 0 & \text{if } i < P \quad \text{(User Prompt / System Context)} \\ 1 & \text{if } i \ge P \quad \text{(Target Assistant Completion)} \end{cases}$$

### Megatron Distributed LoRA Gradient Dynamics

When applying LoRA inside Megatron Tensor Parallel layers, the base frozen weight $\mathbf{W}_0$ is sharded across $N$ GPUs.
For Column Parallel Linear:

$$\mathbf{W}_0 = [\mathbf{W}_{0, 1} \; \mathbf{W}_{0, 2}], \quad \mathbf{B} = [\mathbf{B}_1 \; \mathbf{B}_2], \quad \mathbf{A} \text{ is replicated}$$

$$\mathbf{Y}_i = \mathbf{X} \mathbf{W}_{0, i} + \frac{\alpha}{r} (\mathbf{X} \mathbf{A}) \mathbf{B}_i$$

Because matrix $\mathbf{A} \in \mathbb{R}^{d_{\text{in}} \times r}$ has rank $r \ll d_{\text{in}}$ (typically $r=16$), communicating its gradients requires negligible bandwidth compared to full-parameter SFT.

---

## 5. Comparative Trade-Off Matrix: Fine-Tuning Approaches

| Dimension | Full Parameter SFT | LoRA / QLoRA | Prefix Tuning / P-Tuning | NeMo VarLen Customizer |
| :--- | :--- | :--- | :--- | :--- |
| **Trainable Params** | 100% | 0.05% – 0.2% | 0.01% – 0.05% | Configurable (Full or PEFT) |
| **VRAM Consumption (32B)**| ~240 GB (ZeRO-3) | **~38 GB (FP8 / BF16)** | ~36 GB | **Optimized (~34 GB)** |
| **Padding Waste** | High (in standard loops)| High (in standard loops) | High | **0% (VarLen Packed)** |
| **Throughput Speedup** | Baseline (1.0x) | 1.8x | 1.9x | **3.2x – 4.0x (Packed + Flash)** |
| **DGX Spark Fit** | Requires Offloading | **Native in VRAM** | Native in VRAM | **Native Peak Performance** |

---

## 6. Concrete Production Hands-On Lab: FlashAttention Sequence Packing & SFT Engine

This self-contained Python script implements:
1. Sequence packing concatenating variable-length dialogues into a fixed-budget buffer.
2. Calculation of `cu_seqlens` cumulative length boundaries.
3. Generation of the exact prompt-loss mask.
4. Programmatic execution of the masked autoregressive cross-entropy loss.

```python
#!/usr/bin/env python3
"""
NeMo Customizer Sequence Packing & Masked SFT Simulator.
Demonstrates FlashAttention VarLen buffer packing, cu_seqlens generation,
and prompt-masked loss computation.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Dict, Tuple

# =====================================================================
# 1. SEQUENCE PACKING & CU_SEQLENS ENGINE
# =====================================================================

class SequencePacker:
    @staticmethod
    def pack_conversations(conversations: List[Dict[str, List[int]]], max_buffer_len: int) -> Tuple[torch.Tensor, torch.Tensor, List[int]]:
        """
        Packs multiple conversations into a single 1D buffer.
        Returns:
            packed_tokens: Tensor [TotalTokens]
            loss_mask: Tensor [TotalTokens]
            cu_seqlens: List of cumulative offsets [0, L1, L1+L2, ...]
        """
        packed_tokens = []
        packed_mask = []
        cu_seqlens = [0]

        for conv in conversations:
            prompt = conv["prompt"]
            response = conv["response"]
            seq_len = len(prompt) + len(response)

            if len(packed_tokens) + seq_len > max_buffer_len:
                break # Buffer full

            # Append tokens
            packed_tokens.extend(prompt)
            packed_tokens.extend(response)

            # Mask: 0 for prompt, 1 for response
            packed_mask.extend([0] * len(prompt))
            packed_mask.extend([1] * len(response))

            cu_seqlens.append(cu_seqlens[-1] + seq_len)

        return (
            torch.tensor(packed_tokens, dtype=torch.long),
            torch.tensor(packed_mask, dtype=torch.float32),
            cu_seqlens
        )

# =====================================================================
# 2. MASKED SFT LOSS COMPUTATION
# =====================================================================

def compute_packed_sft_loss(logits: torch.Tensor, targets: torch.Tensor, loss_mask: torch.Tensor) -> torch.Tensor:
    """
    Computes CrossEntropyLoss strictly on tokens where loss_mask == 1.
    """
    # Shift logits and targets for next-token prediction
    shift_logits = logits[:-1, :].contiguous()
    shift_targets = targets[1:].contiguous()
    shift_mask = loss_mask[1:].contiguous()

    # Calculate per-token cross entropy
    loss_raw = F.cross_entropy(shift_logits, shift_targets, reduction="none")

    # Apply binary mask
    masked_loss = loss_raw * shift_mask
    total_active_tokens = shift_mask.sum().clamp(min=1.0)
    final_loss = masked_loss.sum() / total_active_tokens

    return final_loss

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

def run_sft_packing_lab():
    print("=" * 80)
    print("NEMO CUSTOMIZER SEQUENCE PACKING & MASKED SFT LAB")
    print("=" * 80)

    # 3 Sample Dialogues of variable lengths
    dialogues = [
        {"prompt": [10, 20, 30], "response": [40, 50]},         # Len: 5 (Prompt: 3, Resp: 2)
        {"prompt": [100, 101], "response": [102, 103, 104, 105]}, # Len: 6 (Prompt: 2, Resp: 4)
        {"prompt": [5, 6, 7, 8], "response": [9]}                 # Len: 5 (Prompt: 4, Resp: 1)
    ]

    print("Phase 1: Packing Dialogues into VarLen Buffer...")
    packed_tokens, loss_mask, cu_seqlens = SequencePacker.pack_conversations(dialogues, max_buffer_len=32)

    print(f"  Packed Tokens Length: {len(packed_tokens)} tokens (Zero Padding!)")
    print(f"  Packed Tokens       : {packed_tokens.tolist()}")
    print(f"  Loss Mask           : {loss_mask.tolist()}")
    print(f"  cu_seqlens Offsets  : {cu_seqlens}")

    assert cu_seqlens == [0, 5, 11, 16], f"Unexpected cu_seqlens: {cu_seqlens}"
    assert loss_mask.sum().item() == (2 + 4 + 1), "Active target tokens mismatch!"

    print("\nPhase 2: Executing Masked SFT Loss Calculation...")
    vocab_size = 200
    toy_head = nn.Linear(32, vocab_size)
    dummy_hidden = torch.randn(len(packed_tokens), 32)
    logits = toy_head(dummy_hidden)

    loss = compute_packed_sft_loss(logits, packed_tokens, loss_mask)
    print(f"  -> Successfully computed SFT Loss: {loss.item():.4f}")

    # Backward pass validation
    loss.backward()
    print("  -> Autograd backward pass executed cleanly on active tokens.")

    print("\n[SUCCESS] FlashAttention VarLen sequence packing and masked SFT validated.")

if __name__ == "__main__":
    run_sft_packing_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Executing NeMo Customizer on the DGX Spark:
1. **Launching SFT with Sequence Packing**:
   ```bash
   python -m nemo.collections.nlp.models.language_modeling.megatron_gpt_sft \
     --config-path=/workspace/nemo_configs \
     --config-name=megatron_gpt_sft \
     model.data.train_ds.packed_sequence=true \
     model.data.train_ds.max_seq_length=4096 \
     model.megatron_amp_O2=true \
     trainer.precision=bf16
   ```

2. **PEFT Sizing on Blackwell GB10**:
   - Fine-tuning **Llama-3.1-Nemotron-70B** via LoRA ($r=16$):
     - Base weights in FP8: $\approx 70\text{ GB}$.
     - LoRA parameters + Optimizer states: $\approx 4\text{ GB}$.
     - Activations with sequence packing: $\approx 18\text{ GB}$.
     - **Total Memory**: $\approx 92\text{ GB}$ out of 128 GB available.
   - SFT runs **100% on a single DGX Spark node** without requiring multi-node clustering.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Packing Efficiency Metric)**:
   Write a utility that measures the packing efficiency: $\eta = \frac{\sum L_i}{N_{\text{batches}} \times L_{\text{max}}}$, comparing traditional padding against sequence packing on a dataset with log-normal length distribution.

2. **Exercise 2 (LoRA Dropout Implementation)**:
   Extend the LoRA forward formulation to include an input dropout layer: $\Delta \mathbf{W} = \frac{\alpha}{r} \left( \text{Dropout}(\mathbf{X}) \mathbf{A} \right) \mathbf{B}$.

### Solutions

**Solution for Exercise 2**:
```python
class LoRALayerWithDropout(nn.Module):
    def __init__(self, in_features, out_features, r=16, alpha=32.0, dropout_p=0.05):
        super().__init__()
        self.r = r
        self.scaling = alpha / r
        self.dropout = nn.Dropout(p=dropout_p)
        self.A = nn.Parameter(torch.randn(r, in_features) * (1.0 / r))
        self.B = nn.Parameter(torch.zeros(out_features, r))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: [..., in_features]
        dropped_x = self.dropout(x)
        return self.scaling * F.linear(F.linear(dropped_x, self.A), self.B)
```

### Troubleshooting FAQ

- **Q: Training loss does not decrease during packed SFT.**
  - *Fix*: You forgot to reset the attention mask between packed sequences. Without `cu_seqlens`, tokens in Conversation 2 attend to Conversation 1, causing attention confusion and loss stagnation. Ensure `packed_sequence=true` and `attn_implementation="flash_attention_2"` are both enabled.

- **Q: CUDA OOM occurs on sudden long document spikes.**
  - *Fix*: Enable `--enable-chunked-prefill` and configure your sequence packer to split outlier documents exceeding `max_buffer_len` into separate chunks.
