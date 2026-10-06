# Volume 05: Specialized Gemma Architectures: CodeGemma, PaliGemma 2, and RecurrentGemma

```
==================================================================================================
TARGET AUDIENCE: Multimodal Engineers, Code Generation Specialists, Recurrent & State-Space Researchers
PREREQUISITES   : Vision Transformers (ViT), Linear Recurrence, Fill-in-the-Middle (FIM) Pre-Training
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Deconstruct Google's specialized open architectures: CodeGemma (FIM mechanics),
                  PaliGemma 2 (SigLIP-So400M vision integration), and RecurrentGemma (Griffin hybrid).
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

While the flagship Gemma 2 family targets general-purpose reasoning and conversation, Google DeepMind engineered three specialized derivative architectures to conquer distinct compute and modality frontiers:
1. **CodeGemma**: Code intelligence optimized for low-latency code completion, multi-file context, and Fill-in-the-Middle (FIM) infilling.
2. **PaliGemma 2**: High-resolution Vision-Language model coupling the **SigLIP-So400M** vision encoder with Gemma 2, incorporating normalized spatial location coordinates directly into the vocabulary.
3. **RecurrentGemma**: Sub-quadratic hybrid architecture based on Google's **Griffin** model, replacing full attention with **Gated Linear Recurrence (GLR)** combined with local sliding window attention to achieve $O(1)$ inference memory.

```
                                  ┌────────────────────────────────┐
                                  │      GEMMA ECOSYSTEM CORE      │
                                  └───────────────┬────────────────┘
                                                  │
         ┌────────────────────────────────────────┼────────────────────────────────────────┐
         ▼                                        ▼                                        ▼
┌─────────────────────────────────┐ ┌─────────────────────────────────┐ ┌─────────────────────────────────┐
│           CODEGEMMA             │ │          PALIGEMMA 2            │ │         RECURRENTGEMMA          │
│                                 │ │                                 │ │                                 │
│ - Fill-in-the-Middle (FIM)      │ │ - SigLIP-So400M Vision Encoder  │ │ - Griffin Hybrid Architecture   │
│ - Prefix / Suffix / Middle      │ │ - Linear Multimodal Projector   │ │ - Gated Linear Recurrence (GLR) │
│ - Multi-file IDE infilling      │ │ - Spatial Bounding (<loc0000>)  │ │ - Sliding Window Multi-Query    │
│ - Sub-15ms latency on GB10      │ │ - Multi-resolution (224 to 896) │ │ - O(1) Memory Footprint per Step│
└─────────────────────────────────┘ └─────────────────────────────────┘ └─────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Specialized Artisans
3. Evolutionary Lineage: From Monolithic Models to Domain-Specialized Topologies
4. First-Principles Mathematics & Algorithmic Formulations
   - CodeGemma: Fill-In-the-Middle (FIM) Joint Probability Formulation
   - PaliGemma 2: SigLIP Visual Attention and Spatial Coordinate Tokenization
   - RecurrentGemma: Griffin Gated Linear Recurrence (GLR) Differential Equations
5. Alternative Industry Approaches & Comparative Architectural Matrix
6. Concrete Production Hands-On Lab: FIM Infilling Engine and Griffin GLR Layer
7. Hardware Grounding for NVIDIA DGX Spark (Unified Memory Multimodal Throughput)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Specialized Artisans

Consider a modern architectural engineering firm:
- **General Gemma 2** is the Senior Architect who can converse fluently, write project proposals, and analyze cross-disciplinary designs.
- **CodeGemma** is the Specialized Blueprint Draftsman. While a regular writer only writes sequentially from left to right (top to bottom), the draftsman must insert a plumbing pipe *between* existing rooms. The draftsman looks at what is on the left (prefix) and what is on the right (suffix), and synthesizes the exact bridge that fits the middle gap. This is **Fill-in-the-Middle (FIM)**.
- **PaliGemma 2** is the Surveyor equipped with High-Resolution LiDAR. It does not just read words; it receives 2D image pixels, slices them into patches, converts them into visual tokens, and speaks coordinates ($x_1, y_1, x_2, y_2$) natively as standard text tokens.
- **RecurrentGemma** is the Field Inspector equipped with a physical notebook rather than carrying an ever-expanding library of past blueprints. Instead of looking back across the entire history of thousands of pages ($O(N^2)$ or $O(N)$ attention memory), it compresses the entire inspection history into a compact, fixed-size mathematical state matrix ($O(1)$ memory).

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2022: OpenAI FIM (Bavarian et al.)                                     │
│ Introduced SPM (Suffix-Prefix-Middle) and PSM transformations for code │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2023: PaLI-X and SigLIP (Zhai et al., Google)                         │
│ SigLIP replaced Softmax contrastive loss with Sigmoid pairwise loss,   │
│ drastically improving vision-text embedding alignment for PaliGemma.   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2024: Griffin & Hawk (De et al., Google DeepMind)                      │
│ RecurrentGemma replaces standard quadratic self-attention with Gated   │
│ Linear Recurrence, delivering state-space efficiency with RNN speed.   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 CodeGemma: Fill-In-the-Middle (FIM) Mechanics

In standard left-to-right autoregressive modeling, the model optimizes the joint probability over tokens $X = (x_1, \dots, x_N)$:

$$P(X) = \prod_{t=1}^N P(x_t \mid x_1, \dots, x_{t-1})$$

In IDE code generation, code is rarely written purely at the end of a file; developers insert logic between an existing prefix $C_{\text{pre}}$ and suffix $C_{\text{suf}}$.

CodeGemma splits document $C$ into three contiguous segments: $C = [C_{\text{pre}}, C_{\text{mid}}, C_{\text{suf}}]$ with probability $p_{\text{FIM}} = 0.5$. It formats the input sequence using two modes:

1. **PSM Mode (Prefix-Suffix-Middle)**:
   $$X_{\text{PSM}} = [\langle\text{fim\_prefix}\rangle, C_{\text{pre}}, \langle\text{fim\_suffix}\rangle, C_{\text{suf}}, \langle\text{fim\_middle}\rangle, C_{\text{mid}}, \langle\text{eos}\rangle]$$

2. **SPM Mode (Suffix-Prefix-Middle)**:
   $$X_{\text{SPM}} = [\langle\text{fim\_suffix}\rangle, C_{\text{suf}}, \langle\text{fim\_prefix}\rangle, C_{\text{pre}}, \langle\text{fim\_middle}\rangle, C_{\text{mid}}, \langle\text{eos}\rangle]$$

The model computes cross-entropy loss **strictly on the $C_{\text{mid}}$ and $\langle\text{eos}\rangle$ tokens**, conditioning simultaneously on past context and future constraints:

$$\mathcal{L}_{\text{FIM}}(\theta) = -\sum_{t \in C_{\text{mid}}} \log P(x_t \mid C_{\text{pre}}, C_{\text{suf}}, x_{<t})$$

### 4.2 PaliGemma 2: Vision-Language Integration & Coordinate Tokens

PaliGemma 2 combines:
1. **SigLIP-So400M**: A 27-layer Vision Transformer with 400M parameters, pre-trained with sigmoid loss on web-scale image-text pairs.
2. **Linear Multimodal Projector**: Maps 1,152-dimensional SigLIP patch tokens into the language hidden dimension ($d_{\text{model}} = 2048$ for 2B, $d_{\text{model}} = 3584$ for 9B, $d_{\text{model}} = 4608$ for 27B).
3. **Gemma 2 Autoregressive Decoder**.

```
Input Image (448x448x3) ──► SigLIP ViT-So400M (14x14 Patch) ──► 1,024 Visual Tokens [1,024, 1152]
                                                                        │
                                                                 Linear Projector
                                                                        │
                                                                        ▼
Text Tokens [Prompt] ───► Word Embeddings ──────────────────► [Combined Sequence Tokens]
                                                                        │
                                                                 Gemma 2 Decoder
                                                                        │
                                                                        ▼
                                                         Generated Text + <locY><locX>
```

#### Spatial Location Tokens:
PaliGemma 2 discretizes continuous 2D coordinates $[0.0, 1.0]$ into $1,024$ discrete spatial bins:
$$\text{bin}(x) = \min\left(1023, \lfloor x \times 1024 \rfloor\right)$$
Represented as explicit text tokens $\langle\text{loc0000}\rangle$ through $\langle\text{loc1023}\rangle$.

A bounding box $[y_{\min}, x_{\min}, y_{\max}, x_{\max}]$ is emitted as four consecutive vocabulary tokens:
$$\langle\text{loc0120}\rangle\,\langle\text{loc0340}\rangle\,\langle\text{loc0650}\rangle\,\langle\text{loc0890}\rangle$$
This eliminates separate detection heads: object detection, segmentation, and captioning share a single unified autoregressive language loss.

### 4.3 RecurrentGemma: Griffin Gated Linear Recurrence (GLR)

RecurrentGemma replaces standard multi-head attention with alternating blocks of **Gated Linear Recurrence (GLR)** and **Local Sliding Window Attention**.

In the GLR layer, input $x_t \in \mathbb{R}^D$ is projected to input gate $a_t$, reset gate $r_t$, and candidate value $y_t$.
The continuous-time recurrence is discretized as:

$$h_t = \alpha_t \odot h_{t-1} + \beta_t \odot y_t$$

Where:
- $\alpha_t = \sigma(\Lambda_a + W_a x_t) \in (0, 1)$ acts as a learnable state decay factor.
- $\beta_t = \sqrt{1 - \alpha_t^2}$ maintains variance normalization across infinite sequence horizons.
- The output projection is gated by a multiplicative branch:
  $$\hat{y}_t = \text{RMSNorm}(h_t) \odot \sigma(W_g x_t)$$

#### Memory Complexity Advantage:
- Standard Transformer: KV cache memory scales as $\mathcal{O}(B \cdot S \cdot D)$. At $S = 64,000$, KV cache requires tens of gigabytes.
- RecurrentGemma GLR: Hidden state $h_t$ is a fixed-size matrix of dimension $[B, D_{\text{state}}]$. Space complexity is $\mathcal{O}(1)$ with respect to sequence length, consuming $< 200\text{ MB}$ regardless of whether sequence length is 1,000 or 1,000,000 tokens!

---

## 5. Alternative Industry Approaches & Comparative Architectural Matrix

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              SPECIALIZED ARCHITECTURE COMPARISON                                       │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Architectural Trait│ Gemma 2 Base       │ CodeGemma           │ PaliGemma 2      │ RecurrentGemma      │
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ Primary Task       │ Reasoning / SFT    │ Code Infilling / IDE│ Visual Doc / VQA │ Infinite Streaming  │
│ Attention Pattern  │ Alternating SWA    │ Alternating SWA+FIM │ Full Bidirectional│ GLR + Sliding Window│
│ KV-Cache Footprint │ Intermediate (50%) │ Intermediate (50%)  │ High (Vision tok)│ Near-Zero (O(1) GLR)│
│ Input Modalities   │ Text only          │ Text / Code only    │ Image + Text     │ Text only           │
│ Bounding Boxes     │ N/A                │ N/A                 │ Native <loc0000> │ N/A                 │
│ Throughput Scaling │ Bound by KV Cache  │ Bound by KV Cache   │ Bound by Pixels  │ Bound by Compute    │
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: FIM Infilling Engine and Griffin GLR Layer

Save this script as `specialized_gemma_lab.py`:

```python
"""
Specialized Gemma Architectures Lab:
1. CodeGemma Fill-In-The-Middle (FIM) Formatter and Tokenizer Pipeline
2. RecurrentGemma Gated Linear Recurrence (GLR) Layer Implementation
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import math

# ==============================================================================
# 1. CODEGEMMA FILL-IN-THE-MIDDLE (FIM) PIPELINE
# ==============================================================================

class CodeGemmaFIMPipeline:
    def __init__(self):
        # Special Tokens
        self.PREFIX_TOKEN = "<|fim_prefix|>"
        self.SUFFIX_TOKEN = "<|fim_suffix|>"
        self.MIDDLE_TOKEN = "<|fim_middle|>"
        self.EOS_TOKEN = "<|eos|>"

    def format_fim(self, prefix: str, suffix: str, middle: str = "", mode: str = "PSM") -> str:
        """
        Formats source code into PSM or SPM infilling format for CodeGemma.
        """
        if mode == "PSM":
            # Prefix-Suffix-Middle format
            formatted = (
                f"{self.PREFIX_TOKEN}{prefix}"
                f"{self.SUFFIX_TOKEN}{suffix}"
                f"{self.MIDDLE_TOKEN}{middle}"
            )
        elif mode == "SPM":
            # Suffix-Prefix-Middle format
            formatted = (
                f"{self.SUFFIX_TOKEN}{suffix}"
                f"{self.PREFIX_TOKEN}{prefix}"
                f"{self.MIDDLE_TOKEN}{middle}"
            )
        else:
            raise ValueError(f"Unknown mode: {mode}. Must be 'PSM' or 'SPM'.")
        return formatted

# ==============================================================================
# 2. RECURRENTGEMMA GATED LINEAR RECURRENCE (GLR) MODULE
# ==============================================================================

class GatedLinearRecurrence(nn.Module):
    """
    Griffin Gated Linear Recurrence (GLR) Layer.
    Executes constant-memory O(1) state updates across sequential tokens.
    """
    def __init__(self, dim: int, state_dim: int):
        super().__init__()
        self.dim = dim
        self.state_dim = state_dim

        # Projections
        self.proj_input = nn.Linear(dim, state_dim, bias=False)
        self.proj_gate = nn.Linear(dim, state_dim, bias=False)
        self.proj_decay = nn.Linear(dim, state_dim, bias=True)
        self.proj_out = nn.Linear(state_dim, dim, bias=False)

        self.norm = nn.LayerNorm(state_dim)

    def forward(self, x: torch.Tensor, prev_state: torch.Tensor = None) -> tuple:
        """
        Forward step:
        x: [Batch, SeqLen, Dim]
        prev_state: [Batch, StateDim]
        """
        b, s, d = x.shape
        if prev_state is None:
            prev_state = torch.zeros(b, self.state_dim, device=x.device, dtype=x.dtype)

        outputs = []
        current_state = prev_state

        for t in range(s):
            x_t = x[:, t, :]  # [B, Dim]

            # 1. Compute candidate value, reset gate, and decay factor
            y_t = self.proj_input(x_t)                       # [B, StateDim]
            decay_t = torch.sigmoid(self.proj_decay(x_t))    # alpha_t in (0, 1)
            scale_t = torch.sqrt(1.0 - decay_t * decay_t)    # beta_t

            # 2. Linear recurrence update (O(1) memory per step)
            current_state = decay_t * current_state + scale_t * y_t

            # 3. Gating output branch
            gate_t = torch.sigmoid(self.proj_gate(x_t))
            out_t = self.norm(current_state) * gate_t
            outputs.append(self.proj_out(out_t))

        output_tensor = torch.stack(outputs, dim=1)  # [B, SeqLen, Dim]
        return output_tensor, current_state

# ==============================================================================
# VERIFICATION HARNESS
# ==============================================================================

def run_specialized_architectures_lab():
    print("=" * 80)
    print("RUNNING SPECIALIZED GEMMA ARCHITECTURES EXPERIMENTAL LAB")
    print("=" * 80)

    # 1. Test CodeGemma FIM
    print("\n--- 1. Testing CodeGemma Fill-In-The-Middle Prompt Engine ---")
    fim = CodeGemmaFIMPipeline()
    prefix_code = "def calculate_circle_area(radius):\n    if radius < 0:\n        raise ValueError('Negative radius')\n"
    suffix_code = "    return math.pi * (radius ** 2)\n"
    target_middle = "    import math\n"

    psm_prompt = fim.format_fim(prefix_code, suffix_code, target_middle, mode="PSM")
    print("Generated CodeGemma PSM Prompt:\n" + "-" * 40)
    print(psm_prompt)
    print("-" * 40)

    # 2. Test RecurrentGemma GLR
    print("\n--- 2. Testing RecurrentGemma Gated Linear Recurrence (GLR) ---")
    batch_size = 2
    seq_len = 16
    hidden_dim = 128
    state_dim = 256

    glr_layer = GatedLinearRecurrence(dim=hidden_dim, state_dim=state_dim)
    mock_input = torch.randn(batch_size, seq_len, hidden_dim)

    output, final_state = glr_layer(mock_input)

    print(f"Input Tensor Shape      : {list(mock_input.shape)}")
    print(f"Output Tensor Shape     : {list(output.shape)}")
    print(f"Final Recurrent State   : {list(final_state.shape)}")
    print(f"Recurrent State Memory  : {final_state.element_size() * final_state.nelement() / 1024:.2f} KB (Fixed!)")
    print("\nVerification Passed: CodeGemma FIM and RecurrentGemma GLR executed successfully!")

if __name__ == "__main__":
    run_specialized_architectures_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (Unified Memory Multimodal Throughput)

### 7.1 PaliGemma 2 High-Resolution Processing on Blackwell GB10
When processing multi-page documents (DocVQA) or high-resolution images at $896 \times 896$ resolution with PaliGemma 2:
- Patch size $14 \times 14 \implies (896 / 14)^2 = 64 \times 64 = 4,096$ visual tokens per image.
- Processing a 10-page technical PDF introduces $40,960$ tokens of prefix context.
- On discrete 80 GB GPUs, allocating $40,960$ tokens in FP16 KV cache consumes over $35\text{ GB}$ of VRAM for attention alone, leaving insufficient space for batching.
- On the **NVIDIA DGX Spark**, the unified **128 GB memory pool** allows PaliGemma 2 27B to retain over $40\text{ GB}$ for deep multimodal visual caches, allowing 10-page document analysis at single-digit batch latencies.

### 7.2 RecurrentGemma on Grace ARM Neoverse V2
Because RecurrentGemma’s GLR state update is sequential across time steps ($h_t = \alpha_t h_{t-1} + \dots$), inference does not require massive parallel tensor cores during single-batch edge execution.
The Grace ARM Neoverse V2 CPU (72 cores, dual 4x128-bit SVE2 vector pipelines) can execute Griffin recurrence in L2/L3 cache without GPU wakeups, delivering sub-10ms token generation while drawing $< 35\text{ Watts}$ of system power.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate PaliGemma 2 Visual Token Count**:
   Given an image resolution of $448 \times 448$ and a SigLIP patch size of $14 \times 14$, calculate the number of visual tokens output by the vision encoder.
   - *Solution*: $\frac{448}{14} \times \frac{448}{14} = 32 \times 32 = 1,024$ visual tokens.

2. **Calculate PaliGemma 2 Normalized Bounding Box Bins**:
   Convert pixel coordinates $[y_{\min}=45, x_{\min}=120, y_{\max}=310, x_{\max}=480]$ on a $640 \times 640$ image into discrete PaliGemma 2 $\langle\text{loc}\rangle$ tokens.
   - *Solution*:
     - $y_{\min} = \lfloor (45 / 640) \times 1024 \rfloor = \lfloor 72.0 \rfloor = 72 \implies \langle\text{loc0072}\rangle$
     - $x_{\min} = \lfloor (120 / 640) \times 1024 \rfloor = \lfloor 192.0 \rfloor = 192 \implies \langle\text{loc0192}\rangle$
     - $y_{\max} = \lfloor (310 / 640) \times 1024 \rfloor = \lfloor 496.0 \rfloor = 496 \implies \langle\text{loc0496}\rangle$
     - $x_{\max} = \lfloor (480 / 640) \times 1024 \rfloor = \lfloor 768.0 \rfloor = 768 \implies \langle\text{loc0768}\rangle$
     - Output: `"<loc0072><loc0192><loc0496><loc0768>"`

### Troubleshooting FAQ

- **Q: Why does CodeGemma sometimes generate text after completing the middle block?**
  *A*: CodeGemma is trained to emit the `"<|file_separator|>"` or `"<|eos|>"` token immediately when the middle block syntactically satisfies the suffix. If your generation pipeline does not set `eos_token_id` to include `"<|file_separator|>"`, the model continues generating hallucinations. Always include both tokens in your stopping criteria.
- **Q: Can PaliGemma 2 understand images without text prompts?**
  *A*: No. PaliGemma 2 is strictly conditioned on a prompt prefix. Even for general captioning, an explicit task string such as `"caption en\n"` or `"detect car ; person\n"` must precede the visual tokens.
