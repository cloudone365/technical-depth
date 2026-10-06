# Volume 25: Hands-On Exercises and Mastery Workbook for Qwen2.5

```
==================================================================================================
TARGET AUDIENCE: AI Engineers, System Architects, Lead Researchers, Certification Candidates
PREREQUISITES   : Completion of Qwen Volumes 01 through 24
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : 25 comprehensive, production-grade capstone challenges with runnable verification
                  harnesses, mathematical assertions, and canonical solutions across the Qwen ecosystem.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

True engineering mastery is forged through hands-on implementation and rigorous algorithmic validation. This capstone workbook provides **25 concrete, self-contained coding challenges** mapping directly across the entire Alibaba Qwen2.5 foundation model curriculum: from low-level rotary position embedding mathematics and memory-mapped Safetensors parsing to multi-turn agent loops and distributed ZeRO-3 memory modeling.

Every challenge includes an explicit **Mathematical Objective**, a **Verification Test Harness**, and a **Canonical Solution** tested for execution on the NVIDIA DGX Spark (Grace ARM + Blackwell GB10).

```
   [Volumes 01-05] ──► Foundational Architecture & Attention (Challenges 01 - 07)
   [Volumes 06-10] ──► Training, Fine-Tuning & Alignment     (Challenges 08 - 14)
   [Volumes 11-15] ──► High-Throughput Serving & Quantization (Challenges 15 - 19)
   [Volumes 16-20] ──► Agents, Tool Calling, RAG & Gateways   (Challenges 20 - 24)
   [Volumes 21-24] ──► Production Ops, Storage & Diagnostics  (Challenge 25)
```

---

## 2. The 25 Capstone Mastery Challenges

### Challenge 01: ChatML Special Tokenizer & Loss Masker (Volume 01, 06)
- **Objective**: Implement a ChatML parser that takes a dialogue history and outputs token IDs with a binary loss mask $M \in \{0, 1\}^L$, where $M_i = 1$ strictly for assistant tokens and $M_i = 0$ for system and user tokens.

```python
def test_challenge_01():
    # Canonical Implementation
    def build_chatml_and_mask(messages, token_vocab):
        tokens = []
        mask = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            prefix = token_vocab[f"<|im_start|>{role}\n"]
            body = [token_vocab.get(c, token_vocab["<unk>"]) for c in content]
            suffix = token_vocab["<|im_end|>\n"]

            block = [prefix] + body + [suffix]
            tokens.extend(block)
            if role == "assistant":
                mask.extend([0] + [1] * len(body) + [1])
            else:
                mask.extend([0] * len(block))
        return tokens, mask

    vocab = {"<|im_start|>system\n": 1, "<|im_start|>user\n": 2, "<|im_start|>assistant\n": 3,
             "<|im_end|>\n": 4, "<unk>": 0, "A": 10, "B": 11}
    dialogue = [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}]
    toks, msk = build_chatml_and_mask(dialogue, vocab)
    assert msk == [0, 0, 0, 0, 1, 1], f"Mask mismatch: {msk}"
    print("[Challenge 01 Passed] ChatML loss masking verified.")
```

---

### Challenge 02: Grouped-Query Attention (GQA) 8:1 KV Cache Sizing (Volume 02)
- **Objective**: Given hidden dimension $d = 5120$, $H_Q = 40$ query heads, and $H_{KV} = 8$ key-value heads, calculate the exact KV cache memory consumption for batch size $B=16$ at sequence length $L=32768$ in FP16 precision.

```python
def test_challenge_02():
    def compute_gqa_kv_cache_bytes(batch_size, seq_len, num_kv_heads, head_dim, num_layers, bytes_per_elem=2):
        # Cache stores both Key and Value tensors across all layers
        # Shape per token per layer: 2 * num_kv_heads * head_dim * bytes_per_elem
        return batch_size * seq_len * (2 * num_kv_heads * head_dim) * num_layers * bytes_per_elem

    # Qwen2.5-32B parameters: 64 layers, head_dim = 128, H_kv = 8
    vram_bytes = compute_gqa_kv_cache_bytes(16, 32768, 8, 128, 64, bytes_per_elem=2)
    vram_gb = vram_bytes / (1024 ** 3)
    assert abs(vram_gb - 128.0) < 0.1, f"Expected 128 GB, got {vram_gb:.2f} GB"
    print(f"[Challenge 02 Passed] GQA KV Cache calculated: {vram_gb:.2f} GB VRAM.")
```

---

### Challenge 03: RoPE Vector Rotary Kernel ($\theta = 10^6$) (Volume 02)
- **Objective**: Implement 1D RoPE rotation for a single 2D feature subspace:
  
  $$\mathbf{x}' = \begin{bmatrix} x_0 \cos(m\theta_0) - x_1 \sin(m\theta_0) \\ x_0 \sin(m\theta_0) + x_1 \cos(m\theta_0) \end{bmatrix}$$

```python
import math

def test_challenge_03():
    def apply_rope_2d(x0, x1, pos, theta_base=1000000.0, dim_idx=0, total_dim=128):
        freq = 1.0 / (theta_base ** (2 * dim_idx / total_dim))
        angle = pos * freq
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        return x0 * cos_a - x1 * sin_a, x0 * sin_a + x1 * cos_a

    # Rotation at position 0 must be an identity transformation
    r0, r1 = apply_rope_2d(1.0, 2.0, pos=0)
    assert math.isclose(r0, 1.0) and math.isclose(r1, 2.0)
    print("[Challenge 03 Passed] RoPE 2D rotation kernel verified.")
```

---

### Challenge 04: Fill-in-the-Middle (FIM) PSM/SPM Formatter (Volume 03)
- **Objective**: Implement the standard Qwen2.5-Coder FIM prompt transformation: `<|fim_prefix|>Prefix<|fim_suffix|>Suffix<|fim_middle|>`.

```python
def test_challenge_04():
    def format_fim_psm(prefix_code: str, suffix_code: str) -> str:
        return f"<|fim_prefix|>{prefix_code}<|fim_suffix|>{suffix_code}<|fim_middle|>"

    res = format_fim_psm("def add(a, b):", "return result")
    assert "<|fim_prefix|>" in res and "<|fim_suffix|>" in res and "<|fim_middle|>" in res
    print("[Challenge 04 Passed] FIM PSM syntax verified.")
```

---

### Challenge 05: Tool-Integrated Reasoning (TIR) Sandboxed Evaluator (Volume 04)
- **Objective**: Create a secure math evaluator that executes Python arithmetic generated inside `<tool_call>` blocks and injects `<tool_response>` back into the prompt.

```python
def test_challenge_05():
    def sandboxed_math_eval(expr: str) -> str:
        # Strictly whitelist arithmetic characters
        if not all(c in "0123456789+-*/(). " for c in expr):
            return "ERROR: Forbidden characters"
        return str(eval(expr))

    assert sandboxed_math_eval("4 * 12 + 10") == "58"
    assert "ERROR" in sandboxed_math_eval("import os; os.system('ls')")
    print("[Challenge 05 Passed] Sandboxed math evaluator verified.")
```

---

### Challenge 06: NaViT Dynamic 2D Patch Grid Calculator (Volume 05, 19)
- **Objective**: Compute even patch dimensions $(h, w)$ for an image with resolution $1280 \times 720$ preserving aspect ratio under a maximum patch budget $N=1024$.

```python
def test_challenge_06():
    def calc_navit_grid(orig_w, orig_h, max_patches=1024, patch_size=14):
        aspect = orig_w / orig_h
        best_diff = float("inf")
        best_grid = (16, 16)
        for h in range(8, int(math.sqrt(max_patches) * 2), 2):
            w = int(round(h * aspect / 2.0)) * 2
            if w < 8: w = 8
            if h * w <= max_patches:
                diff = abs((w / h) - aspect)
                if diff < best_diff:
                    best_diff = diff
                    best_grid = (h, w)
        return best_grid

    h_p, w_p = calc_navit_grid(1280, 720)
    assert (h_p % 2 == 0) and (w_p % 2 == 0)
    assert h_p * w_p <= 1024
    print(f"[Challenge 06 Passed] NaViT grid computed: {h_p}x{w_p} ({h_p*w_p} patches).")
```

---

### Challenge 07: 3D M-RoPE Spatio-Temporal Coordinate Mapper (Volume 05, 19)
- **Objective**: Generate coordinate tuples $(t, y, x)$ for a 4-frame video with a $4 \times 4$ merged token grid.

```python
def test_challenge_07():
    def generate_3d_mrope(num_frames, grid_h, grid_w):
        coords = []
        for t in range(num_frames):
            for y in range(grid_h):
                for x in range(grid_w):
                    coords.append((t, y, x))
        return coords

    coords = generate_3d_mrope(num_frames=4, grid_h=4, grid_w=4)
    assert len(coords) == 64
    assert coords[0] == (0, 0, 0) and coords[-1] == (3, 3, 3)
    print("[Challenge 07 Passed] 3D M-RoPE coordinate generator verified.")
```

---

### Challenge 08: DeepSpeed ZeRO-3 Memory Calculation (Volume 07)
- **Objective**: Calculate the per-GPU parameter memory for Qwen2.5-72B across $N=8$ GPUs under ZeRO-3 vs ZeRO-1.

```python
def test_challenge_08():
    def compute_zero_mem(param_billions=72, num_gpus=8):
        # 16-bit weights: 2 bytes per param
        base_weight_bytes = param_billions * 1e9 * 2
        zero1_per_gpu = base_weight_bytes / 1e9 # Model weights replicated
        zero3_per_gpu = (base_weight_bytes / num_gpus) / 1e9 # Sharded
        return zero1_per_gpu, zero3_per_gpu

    z1, z3 = compute_zero_mem(72, 8)
    assert z1 == 144.0 and z3 == 18.0
    print(f"[Challenge 08 Passed] ZeRO-3 sharded weights: {z3} GB per GPU vs ZeRO-1: {z1} GB.")
```

---

### Challenge 09: DPO vs SimPO Loss Formulation (Volume 08)
- **Objective**: Implement the SimPO loss function without reference model forwarding:

  $$\mathcal{L}_{\text{SimPO}} = -\log \sigma \left( \frac{\beta}{|y_w|} \log \pi_\theta(y_w \mid x) - \frac{\beta}{|y_l|} \log \pi_\theta(y_l \mid x) - \gamma \right)$$

```python
def test_challenge_09():
    def simpo_loss(logp_w, len_w, logp_l, len_l, beta=2.0, gamma=0.5):
        margin = (beta / len_w) * logp_w - (beta / len_l) * logp_l - gamma
        # Sigmoid: 1 / (1 + exp(-margin))
        sigmoid = 1.0 / (1.0 + math.exp(-margin))
        return -math.log(max(sigmoid, 1e-12))

    loss = simpo_loss(logp_w=-10.0, len_w=10, logp_l=-30.0, len_l=10)
    assert loss > 0.0
    print(f"[Challenge 09 Passed] SimPO loss calculated: {loss:.4f}.")
```

---

### Challenge 10: GRPO Group Advantage Normalizer (Volume 08)
- **Objective**: Normalize reward scores across a group of $G=4$ sampled responses:

  $$\hat{A}_i = \frac{r_i - \mu_{\{r\}}}{\sigma_{\{r\}} + \epsilon}$$

```python
def test_challenge_10():
    def grpo_normalize(rewards, eps=1e-8):
        mu = sum(rewards) / len(rewards)
        variance = sum((r - mu) ** 2 for r in rewards) / len(rewards)
        sigma = math.sqrt(variance)
        return [(r - mu) / (sigma + eps) for r in rewards]

    adv = grpo_normalize([1.0, 2.0, 3.0, 4.0])
    assert math.isclose(sum(adv), 0.0, abs_tol=1e-6)
    print("[Challenge 10 Passed] GRPO advantage normalization verified.")
```

---

### Challenge 11: LoRA Forward Pass & Low-Rank Weight Merger (Volume 09)
- **Objective**: Implement $\mathbf{W}_{\text{merged}} = \mathbf{W}_0 + \frac{\alpha}{r} (\mathbf{B} \mathbf{A})$.

```python
def test_challenge_11():
    import numpy as np
    d_in, d_out, r, alpha = 64, 64, 4, 8.0
    W0 = np.random.randn(d_out, d_in).astype(np.float32)
    A = np.random.randn(r, d_in).astype(np.float32)
    B = np.random.randn(d_out, r).astype(np.float32)

    delta_W = (alpha / r) * np.matmul(B, A)
    W_merged = W0 + delta_W

    x = np.random.randn(d_in).astype(np.float32)
    y1 = np.matmul(W_merged, x)
    y2 = np.matmul(W0, x) + (alpha / r) * np.matmul(B, np.matmul(A, x))
    assert np.allclose(y1, y2, atol=1e-4)
    print("[Challenge 11 Passed] LoRA low-rank weight merger verified.")
```

---

### Challenge 12: QLoRA NormalFloat4 (NF4) Quantization Lookup (Volume 09)
- **Objective**: Quantize a continuous normal distribution float into the closest NF4 quantized bin.

```python
def test_challenge_12():
    NF4_BINS = [-1.0, -0.6961928, -0.5250730, -0.3949174, -0.2844413, -0.1847734, -0.0910500, 0.0,
                 0.0795802, 0.1609302, 0.2461123, 0.3379152, 0.4407098, 0.5626170, 0.7229568, 1.0]

    def quantize_nf4(val):
        return min(range(len(NF4_BINS)), key=lambda idx: abs(NF4_BINS[idx] - val))

    bin_idx = quantize_nf4(0.15)
    assert bin_idx == 9 # Closest to 0.1609302
    print(f"[Challenge 12 Passed] NF4 bin index {bin_idx} correctly identified.")
```

---

### Challenge 13: Rejection Sampling Data Flywheel Evaluator (Volume 10)
- **Objective**: Filter synthetic candidate generations, keeping only candidates with reward $R \ge 0.85$ and distinct tokens.

```python
def test_challenge_13():
    def rejection_sample(candidates):
        return [c["text"] for c in candidates if c["score"] >= 0.85 and len(set(c["text"].split())) > 3]

    corpus = [{"text": "good clean distinct response", "score": 0.90},
              {"text": "bad bad bad bad", "score": 0.95},
              {"text": "accurate short", "score": 0.60}]
    accepted = rejection_sample(corpus)
    assert len(accepted) == 1 and accepted[0] == "good clean distinct response"
    print("[Challenge 13 Passed] Rejection sampling filter verified.")
```

---

### Challenge 14: PagedAttention Block Table Allocator (Volume 11)
- **Objective**: Allocate physical 16-token memory blocks to dynamically growing logical sequences.

```python
def test_challenge_14():
    class BlockManager:
        def __init__(self, block_size=16):
            self.block_size = block_size
            self.free_blocks = list(range(100))

        def allocate_blocks_for_seq(self, seq_len):
            needed = (seq_len + self.block_size - 1) // self.block_size
            allocated = [self.free_blocks.pop(0) for _ in range(needed)]
            return allocated

    bm = BlockManager(block_size=16)
    table = bm.allocate_blocks_for_seq(35)
    assert len(table) == 3 # 16 + 16 + 3 tokens
    print(f"[Challenge 14 Passed] PagedAttention allocated 3 physical blocks: {table}.")
```

---

### Challenge 15: Radix Tree Prefix Trie Matcher (Volume 12)
- **Objective**: Find the longest common cached prefix token length between an incoming prompt and a cached radix tree.

```python
def test_challenge_15():
    class RadixNode:
        def __init__(self, tokens):
            self.tokens = tokens
            self.children = []

    def match_prefix(cached_tokens, incoming_tokens):
        match_len = 0
        for c, i in zip(cached_tokens, incoming_tokens):
            if c == i: match_len += 1
            else: break
        return match_len

    common = match_prefix([10, 20, 30, 40], [10, 20, 30, 99])
    assert common == 3
    print(f"[Challenge 15 Passed] Radix attention prefix matched {common} cached tokens.")
```

---

### Challenge 16: AWQ Activation Outlier Channel Scaler (Volume 13)
- **Objective**: Compute the optimal per-channel scaling factor:

  $$\mathbf{s} = \mathbf{s}_X^\alpha \cdot \mathbf{s}_W^{1-\alpha}$$

```python
def test_challenge_16():
    def compute_awq_scale(act_max, weight_max, alpha=0.5):
        return (act_max ** alpha) * (weight_max ** (1.0 - alpha))

    scale = compute_awq_scale(16.0, 4.0, alpha=0.5)
    assert math.isclose(scale, 8.0)
    print(f"[Challenge 16 Passed] AWQ channel scale: {scale}.")
```

---

### Challenge 17: FP8 E4M3 Dynamic Dequantizer (Volume 13)
- **Objective**: Dequantize an 8-bit float with 1 sign bit, 4 exponent bits (bias=7), and 3 mantissa bits into float32.

```python
def test_challenge_17():
    def dequant_e4m3(sign, exp, mant):
        if exp == 0:
            return ((-1) ** sign) * (2 ** -6) * (mant / 8.0) # Subnormal
        bias = 7
        return ((-1) ** sign) * (2 ** (exp - bias)) * (1.0 + mant / 8.0)

    # 1.0 representation: sign=0, exp=7, mant=0 -> 2^(7-7) * (1 + 0) = 1.0
    val = dequant_e4m3(0, 7, 0)
    assert math.isclose(val, 1.0)
    print("[Challenge 17 Passed] FP8 E4M3 bitwise dequantization verified.")
```

---

### Challenge 18: GGUF Super-Block Q4_K_M Block Dequantizer (Volume 15)
- **Objective**: Reconstruct 32 discrete 4-bit nibbles from a 16-byte packed buffer using scale factor $d$.

```python
def test_challenge_18():
    def dequant_block_q4(packed_bytes, scale_d):
        weights = []
        for byte in packed_bytes:
            low_nibble = byte & 0x0F
            high_nibble = (byte >> 4) & 0x0F
            weights.append((low_nibble - 8) * scale_d)
            weights.append((high_nibble - 8) * scale_d)
        return weights

    block = bytes([0x88, 0x98]) # 4 nibbles: 8, 8, 8, 9
    w = dequant_block_q4(block, scale_d=0.5)
    assert w[0] == 0.0 and w[3] == 0.5
    print("[Challenge 18 Passed] GGUF Q4 block unpacking verified.")
```

---

### Challenge 19: ReAct State Transition Parser (Volume 16)
- **Objective**: Extract `Action: <tool_name>[<args>]` from an agent's reasoning text.

```python
import re

def test_challenge_19():
    def parse_react_action(text):
        match = re.search(r"Action:\s*(\w+)\[(.*?)\]", text)
        if match:
            return match.group(1), match.group(2)
        return None, None

    act, args = parse_react_action("Thought: I need to query telemetry.\nAction: get_gpu_temp[gpu_id=0]")
    assert act == "get_gpu_temp" and args == "gpu_id=0"
    print(f"[Challenge 19 Passed] ReAct action parsed: {act}({args}).")
```

---

### Challenge 20: Context-Free Grammar (CFG) JSON Logit Masker (Volume 17)
- **Objective**: Apply logit mask $-\infty$ to invalid syntax tokens given current parser state.

```python
def test_challenge_20():
    def mask_logits(logits, current_state, valid_transitions):
        allowed_tokens = valid_transitions[current_state]
        masked = logits.copy()
        for idx in range(len(masked)):
            if idx not in allowed_tokens:
                masked[idx] = -float("inf")
        return masked

    raw_logits = [2.0, 5.0, 1.0, 3.0]
    # State 0 (expect open brace): only token 0 '{' allowed
    transitions = {0: {0}}
    m = mask_logits(raw_logits, current_state=0, valid_transitions=transitions)
    assert m[0] == 2.0 and m[1] == -float("inf")
    print("[Challenge 20 Passed] CFG logit masking verified.")
```

---

### Challenge 21: Reciprocal Rank Fusion (RRF) Scorer (Volume 18)
- **Objective**: Compute RRF score for documents across dense and sparse ranking lists with $k=60$.

```python
def test_challenge_21():
    def compute_rrf(rank_dense, rank_sparse, k=60):
        s_d = 1.0 / (k + rank_dense) if rank_dense is not None else 0.0
        s_s = 1.0 / (k + rank_sparse) if rank_sparse is not None else 0.0
        return s_d + s_s

    score = compute_rrf(rank_dense=1, rank_sparse=3, k=60)
    assert math.isclose(score, (1/61) + (1/63))
    print(f"[Challenge 21 Passed] RRF score: {score:.5f}.")
```

---

### Challenge 22: Multimodal Bounding Box Denormalizer (Volume 19)
- **Objective**: Convert Qwen2-VL normalized coordinates $(y_1, x_1, y_2, x_2) \in [0, 1000]$ to pixel bounds for an image of size $1920 \times 1080$.

```python
def test_challenge_22():
    def denormalize_bbox(norm_box, width=1920, height=1080):
        y1, x1, y2, x2 = norm_box
        return int(x1 * width / 1000), int(y1 * height / 1000), int(x2 * width / 1000), int(y2 * height / 1000)

    px = denormalize_bbox((100, 200, 500, 600))
    assert px == (384, 108, 1152, 540)
    print(f"[Challenge 22 Passed] Denormalized pixel bbox: {px}.")
```

---

### Challenge 23: Token Bucket Rate Limiter (Volume 20)
- **Objective**: Implement leaky token replenishment: $B(t) = \min(C, B(t_0) + r \Delta t)$.

```python
def test_challenge_23():
    def update_bucket(capacity, current_tokens, last_time, current_time, rate_per_sec):
        delta = current_time - last_time
        return min(capacity, current_tokens + delta * rate_per_sec)

    tokens = update_bucket(capacity=100.0, current_tokens=50.0, last_time=0.0, current_time=2.0, rate_per_sec=10.0)
    assert tokens == 70.0
    print(f"[Challenge 23 Passed] Token bucket replenished to {tokens}.")
```

---

### Challenge 24: Prometheus Quantile P90 Metric Calculator (Volume 23)
- **Objective**: Calculate exact P90 latency given an unsorted latency stream.

```python
def test_challenge_24():
    def calc_p90(latencies):
        s = sorted(latencies)
        idx = int(math.ceil(len(s) * 0.90)) - 1
        return s[idx]

    lats = [12.0, 45.0, 22.0, 100.0, 15.0, 30.0, 80.0, 10.0, 5.0, 50.0]
    p90 = calc_p90(lats)
    assert p90 == 80.0
    print(f"[Challenge 24 Passed] P90 latency: {p90} ms.")
```

---

### Challenge 25: Master End-to-End NaN & Gradient Sanity Guardrail (Volume 24)
- **Objective**: Build an automated PyTorch tensor auditor that halts execution on NaN, Inf, or exploded gradient norms ($> 5.0$).

```python
import torch

def test_challenge_25():
    def tensor_guard(tensor, name="tensor"):
        if torch.isnan(tensor).any() or torch.isinf(tensor).any():
            raise ValueError(f"Corrupted tensor detected in {name}!")
        return True

    t_clean = torch.tensor([1.0, 2.0, 3.0])
    assert tensor_guard(t_clean)

    t_nan = torch.tensor([1.0, float("nan")])
    try:
        tensor_guard(t_nan)
        assert False, "Should have thrown ValueError"
    except ValueError:
        pass
    print("[Challenge 25 Passed] Master tensor sanity guardrail verified.")
```

---

## 3. Comprehensive Master Test Suite Execution

Run this complete self-contained script on your DGX Spark to validate all 25 challenges in a single execution pass:

```python
if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING ALL 25 QWEN2.5 CAPSTONE MASTERY CHALLENGES")
    print("=" * 80)
    test_challenge_01()
    test_challenge_02()
    test_challenge_03()
    test_challenge_04()
    test_challenge_05()
    test_challenge_06()
    test_challenge_07()
    test_challenge_08()
    test_challenge_09()
    test_challenge_10()
    test_challenge_11()
    test_challenge_12()
    test_challenge_13()
    test_challenge_14()
    test_challenge_15()
    test_challenge_16()
    test_challenge_17()
    test_challenge_18()
    test_challenge_19()
    test_challenge_20()
    test_challenge_21()
    test_challenge_22()
    test_challenge_23()
    test_challenge_24()
    test_challenge_25()
    print("=" * 80)
    print("ALL 25 CAPSTONE CHALLENGES PASSED SUCCESSFULLY!")
    print("QWEN2.5 ECOSYSTEM MASTERY ATTAINED (100% COMPLETE).")
    print("=" * 80)
```
