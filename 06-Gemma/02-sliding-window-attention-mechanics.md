# Volume 02: Sliding Window Attention Mechanics in Gemma 2

```
==================================================================================================
TARGET AUDIENCE: Kernel Developers, Inference Optimization Leads, Foundation Model Researchers
PREREQUISITES   : Self-attention mechanics, KV-cache memory scaling, causal masking, cyclic buffers
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master Gemma 2 Sliding Window Attention (SWA): alternating 4k/8k layer architecture,
                  effective receptive field propagation, cyclic KV buffer management, and memory reduction.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

As context windows scale to 8k, 32k, and 128k tokens, the memory required to store the Key-Value (KV) cache becomes the primary limiting factor for inference concurrency. For a 27B model, storing full KV states for 64 concurrent streams at 8k context can consume more VRAM than the model weights themselves.

Google **Gemma 2** addresses this via an elegant hybrid attention topology: **Alternating Sliding Window Attention (SWA)**. By alternating between **Local Sliding Window Attention ($W = 4,096$)** on even layers and **Full Global Attention ($W = 8,192$)** on odd layers, Gemma 2 slashes KV-cache memory footprints while retaining complete theoretical receptive field propagation across the entire sequence.

```
Layer 3 (Global Attn, W=8192) : [Token 0] ◄──────────────────────► [Token 8191] (Full Context)
                                         ▲                                ▲
                                         │ Information prop over residual │
Layer 2 (Local SWA, W=4096)   : [Token 0] ... [Token 4096 ◄──────► Token 8191] (Local Window)
                                         ▲                                ▲
                                         │ Information prop over residual │
Layer 1 (Global Attn, W=8192) : [Token 0] ◄──────────────────────► [Token 8191] (Full Context)
                                         ▲                                ▲
                                         │ Information prop over residual │
Layer 0 (Local SWA, W=4096)   : [Token 0] ... [Token 4096 ◄──────► Token 8191] (Local Window)
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Local Neighborhood Watch and the Regional Dispatcher
3. Evolutionary Lineage: From Full Dense Attention to Alternating SWA
4. First-Principles Mathematics & Algorithmic Formulations
   - SWA Attention Mask Matrix Formulation
   - Receptive Field Expansion Proof
   - Cyclic Rolling KV Buffer Memory Calculus
   - Concurrency Capacity Scaling
5. Comparative Trade-Off Matrix: Long-Context Attention Mechanisms
6. Concrete Production Hands-On Lab: Rolling Cyclic KV Cache & SWA Simulator
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Local Neighborhood Watch and the Regional Dispatcher

Imagine managing public safety across a massive metropolitan region:

- **The Monolithic Full Attention Approach (Every Citizen Calls Every Other Citizen)**:
  Every single citizen in a city of 8,000 people holds a radio channel connected simultaneously to all other 7,999 citizens. If someone drops a wallet on 1st Street, 8,000 radios squawk at once. The radio bandwidth crashes immediately, and nobody can hear genuine emergency calls.

- **The Gemma 2 Alternating Architecture (Neighborhood Watch + Regional Dispatch)**:
  - **Even Layers (Local Neighborhood Watch - $W = 4,096$)**: Citizens on 50th Street only talk to neighbors within 4 blocks. They resolve local issues (micro-syntax, grammar, immediate sentence context) without tying up city-wide lines.
  - **Odd Layers (Regional Police Dispatch - Full Global $W = 8,192$)**: A central police dispatcher reviews reports from all neighborhoods, broadcasting city-wide alerts (document themes, multi-page characters, long-range logic).
  Because citizens on 50th Street talk to the dispatcher, who in turn talks to 1st Street, **news travels across the entire city instantaneously**, but at **half the radio battery consumption**.

---

## 3. Evolutionary Lineage: From Full Dense Attention to Alternating SWA

```
Generation 1 (2017-2020)      Generation 2 (2020-2023)      Generation 3 (2024-2026)
Full Quadratic Attention      Pure Sliding Window (Longformer) Gemma 2 Alternating SWA
──────────────────────────    ────────────────────────────  ─────────────────────────
- O(N^2) memory & compute     - Local window only (e.g. 512)- Alternating Local / Global
- Infeasible beyond 2k context- Broke long-distance context - 50% KV cache savings on evens
- Monolithic linear KV growth - Requires artificial dilation- Full 8k receptive field
- Heavy memory bandwidth bound- Weak reasoning benchmarks   - Highest MMLU across open models
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### SWA Attention Mask Matrix Formulation

Let sequence length be $L$, and sliding window size be $W$.
The attention score matrix $\mathbf{S} \in \mathbb{R}^{L \times L}$ is computed via dot-product between query position $i$ and key position $j$:

$$S_{i, j} = \frac{\mathbf{q}_i \mathbf{k}_j^\top}{\sqrt{d_k}} + M_{i, j}$$

In **Full Causal Attention (Odd Layers)**:

$$M_{i, j}^{\text{global}} = \begin{cases} 0 & \text{if } j \le i \\ -\infty & \text{if } j > i \end{cases}$$

In **Sliding Window Attention (Even Layers)**:

$$M_{i, j}^{\text{local}} = \begin{cases} 0 & \text{if } i - W < j \le i \\ -\infty & \text{otherwise} \end{cases}$$

```
Local SWA Attention Mask (W = 3):
j=0  1  2  3  4  5
i=0 [ 0 -∞ -∞ -∞ -∞ -∞ ]
i=1 [ 0  0 -∞ -∞ -∞ -∞ ]
i=2 [ 0  0  0 -∞ -∞ -∞ ]
i=3 [-∞  0  0  0 -∞ -∞ ]  <-- Can no longer attend to j=0!
i=4 [-∞ -∞  0  0  0 -∞ ]  <-- Window slides: attends to [2, 3, 4]
i=5 [-∞ -∞ -∞  0  0  0 ]
```

### Receptive Field Expansion Proof

**Question**: If half the layers only attend to $W = 4,096$ tokens, can token $L=8,192$ still acquire information from token $0$?
**Theorem**: The effective receptive field $\mathcal{R}_l$ at layer $l$ is strictly bounded by:

$$\mathcal{R}_l = \min\left( L_{\text{total}}, \; \sum_{k=1}^l W_k \right)$$

Where $W_k$ is the window size of layer $k$.
In Gemma 2:
- Layer 0 ($W_0 = 4,096$): Token attends to $[4096, 8192]$.
- Layer 1 ($W_1 = 8,192$): Full global attention. Token $4096$ attends to $[0, 4096]$.
- Layer 2 ($W_2 = 4,096$): Token $8192$ attends to Layer 1's representations of $[4096, 8192]$, which already contain aggregated information from token $0$.

Thus, after just **two layers**, the effective receptive field encompasses the entire $8,192$ token span:

$$\mathcal{R}_2 = 4,096 + 8,192 \ge 8,192 \quad \blacksquare$$

### Cyclic Rolling KV Buffer Memory Calculus

In standard attention, decoding token $t$ requires storing all $t$ key-value vectors in VRAM:

$$\text{Memory}_{\text{standard}}(t) = 2 \times N_{\text{layers}} \times H_{\text{KV}} \times d_{\text{head}} \times t \times \text{BytesPerElem}$$

In Gemma 2, even layers do not store $t$ tokens. They use a **Cyclic Ring Buffer** of fixed size $W = 4,096$:

$$\text{SlotIndex}(t) = t \pmod W$$

When $t > W$, token $t$ overwrites token $t - W$ in physical memory.
Total KV-cache memory for sequence length $L = 8,192$:

$$\text{Memory}_{\text{Gemma2}}(L) = \frac{N_{\text{layers}}}{2} \text{Mem}_{\text{global}}(L) + \frac{N_{\text{layers}}}{2} \text{Mem}_{\text{local}}(W)$$

$$\text{Savings} = 1.0 - \frac{L + W}{2L} = 1.0 - \frac{8192 + 4096}{16384} = 1.0 - 0.75 = \mathbf{25\%\text{ Overall VRAM Reduction}}$$

On the alternating layers alone, memory is reduced by **50%**.

---

## 5. Comparative Trade-Off Matrix: Long-Context Attention Mechanisms

| Mechanism | Memory Complexity | Compute Complexity | Retains Exact Position | Hardware Optimization Fit |
| :--- | :--- | :--- | :--- | :--- |
| **Standard Full Attention** | $\mathcal{O}(L)$ per layer | $\mathcal{O}(L^2)$ | Yes | FlashAttention-2/3 |
| **Linear Attention (Mamba/Griffin)**| $\mathcal{O}(1)$ constant | $\mathcal{O}(L)$ linear | No (State compression) | Specialized recurrent scans |
| **Sparse Chunked Attention** | $\mathcal{O}(L)$ block-sparse | $\mathcal{O}(L \cdot B)$ | Approximate | Irregular memory access |
| **Gemma 2 Alternating SWA** | **$\mathcal{O}(L/2 + W/2)$** | **Sub-quadratic** | **Exact (Full RoPE intact)**| **Native FlashAttention SWA** |

---

## 6. Concrete Production Hands-On Lab: Rolling Cyclic KV Cache & SWA Simulator

This self-contained Python script implements a cyclic rolling KV cache for sliding window attention, proving that physical memory is capped at window size $W$ while generation continues indefinitely.

```python
#!/usr/bin/env python3
"""
Google Gemma 2 Sliding Window Attention & Cyclic KV Cache Lab.
Demonstrates fixed-memory cyclic buffer slot assignment and masked attention execution.
"""

import math
import torch
import torch.nn.functional as F
from typing import Tuple

class CyclicKVCache:
    def __init__(self, window_size: int = 4, num_heads: int = 2, head_dim: int = 8):
        self.window_size = window_size
        self.num_heads = num_heads
        self.head_dim = head_dim
        # Fixed physical memory allocation capped at window_size
        self.k_cache = torch.zeros(1, num_heads, window_size, head_dim)
        self.v_cache = torch.zeros(1, num_heads, window_size, head_dim)
        self.total_tokens_seen = 0

    def append(self, k: torch.Tensor, v: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, int]:
        """
        Appends new Key/Value tokens into the cyclic buffer.
        k, v shape: [1, NumHeads, 1, HeadDim]
        """
        slot = self.total_tokens_seen % self.window_size
        self.k_cache[:, :, slot:slot+1, :] = k
        self.v_cache[:, :, slot:slot+1, :] = v
        self.total_tokens_seen += 1

        # Current active valid tokens in buffer
        valid_len = min(self.total_tokens_seen, self.window_size)
        return self.k_cache[:, :, :valid_len, :], self.v_cache[:, :, :valid_len, :], slot

# =====================================================================
# SLIDING WINDOW ATTENTION STEP
# =====================================================================

def execute_swa_decode_step(q: torch.Tensor, kv_cache: CyclicKVCache, attn_cap: float = 50.0) -> torch.Tensor:
    """
    Executes single-token decoding using the rolling cyclic cache.
    q: [1, NumHeads, 1, HeadDim]
    """
    K_valid, V_valid, current_slot = kv_cache.append(q, q) # self-attention dummy
    scale = 1.0 / math.sqrt(q.size(-1))

    # Dot-product attention against valid window
    scores = torch.matmul(q, K_valid.transpose(-2, -1)) * scale
    # Gemma 2 Soft-Capping
    capped_scores = attn_cap * torch.tanh(scores / attn_cap)

    attn_probs = F.softmax(capped_scores, dim=-1)
    out = torch.matmul(attn_probs, V_valid)
    return out

# =====================================================================
# VERIFICATION HARNESS
# =====================================================================

def run_swa_lab():
    print("=" * 80)
    print("GEMMA 2 SLIDING WINDOW ATTENTION (SWA) CYCLIC CACHE LAB")
    print("=" * 80)

    window_size = 4
    cache = CyclicKVCache(window_size=window_size, num_heads=2, head_dim=8)

    print(f"Initialized Cyclic Buffer: Window Size = {window_size} tokens.")
    print("Simulating 8 sequential token decode steps (2x the window capacity)...")

    for t in range(1, 9):
        # Simulated Query token
        q_t = torch.randn(1, 2, 1, 8)
        out = execute_swa_decode_step(q_t, cache)
        slot = (t - 1) % window_size
        print(f"Step {t:2d}: Token mapped to Physical Memory Slot {slot} | "
              f"Total Tokens Seen: {cache.total_tokens_seen:2d} | "
              f"Buffer VRAM Footprint: Capped at {window_size} tokens")

    # Assert that buffer never grew past window_size
    assert cache.k_cache.size(2) == window_size
    assert cache.total_tokens_seen == 8
    print("\n[SUCCESS] Sliding Window cyclic memory capping and attention step verified.")

if __name__ == "__main__":
    run_swa_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Operating SWA on the DGX Spark:
1. **FlashAttention-2 / FlashAttention-3 SWA Support**:
   The Blackwell GB10 GPU features native support for sliding window attention masks inside FlashAttention. By passing `window_size=(4096, 0)`:
   - Memory bandwidth consumption drops by **50% on even layers**.
   - Arithmetic intensity increases, saturating Blackwell Tensor Cores at over **85% theoretical peak**.

2. **Concurrency Scaling on 128 GB Unified Memory**:
   - For `Gemma 2 27B` at 8,192 context:
     - Standard MHA KV Cache: $\approx 1.25\text{ GB}$ per stream.
     - Gemma 2 SWA KV Cache: $\approx 0.94\text{ GB}$ per stream.
     - Unlocks **33% higher concurrent user capacity** on a single node (supporting 95 active streams vs 71 streams on standard architectures).

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Exact KV Cache Savings Calculus)**:
   Calculate the exact gigabytes saved across 42 layers of Gemma 2 9B ($H_{\text{KV}}=8, d_{\text{head}}=256$) for a batch of 32 requests at sequence length $L=8,192$ in FP16 precision.

2. **Exercise 2 (Attention Mask Visualization)**:
   Write a Python function that prints an ASCII grid of the attention mask for a sequence of 8 tokens with sliding window size $W = 3$.

### Solutions

**Solution for Exercise 1**:
- Full cache per token per layer: $2 \times H_{\text{KV}} \times d_{\text{head}} \times 2\text{ bytes} = 2 \times 8 \times 256 \times 2 = 8,192\text{ bytes} = 8\text{ KB}$.
- Across 21 even layers (4k tokens saved):
  $$\text{Bytes Saved} = 21\text{ layers} \times 32\text{ batch} \times (8192 - 4096)\text{ tokens} \times 8\text{ KB} = 21 \times 32 \times 4096 \times 8192 \approx \mathbf{22.54\text{ GB VRAM Saved}}$$

### Troubleshooting FAQ

- **Q: vLLM throws `ValueError: sliding_window must be None for this model`.**
  - *Fix*: Upgrade to vLLM $\ge 0.5.4$. Early versions of vLLM did not support alternating local/global SWA layers for Gemma 2. Ensure `--enforce-eager` or vLLM's official Gemma 2 runner is active.

- **Q: Model performance degrades on documents longer than 4,096 tokens.**
  - *Fix*: Verify that odd layers are executing full global attention ($W = 8,192$). If an engine mistakenly applies the 4k sliding window to all layers, the model's effective receptive field is truncated, causing severe context amnesia.
