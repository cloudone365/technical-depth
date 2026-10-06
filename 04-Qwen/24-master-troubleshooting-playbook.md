# Volume 24: Master Troubleshooting Playbook for Qwen2.5

```
==================================================================================================
TARGET AUDIENCE: SREs, MLOps Engineers, Foundation Model Researchers, Infrastructure Engineers
PREREQUISITES   : PyTorch autograd, CUDA memory allocators, NCCL communication, RoPE rotary math
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Systematic triage and instant remediation playbook for critical production failures:
                  CUDA OOMs, NaN loss spikes, RoPE context drift, NCCL deadlocks, and token loops.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Operating large language models in enterprise production inevitably encounters failure modes that do not exist in conventional software engineering. Failures range from silent numerical degradation (where models emit repetitive gibberish without throwing an error) to catastrophic distributed deadlocks during fine-tuning or sudden CUDA Out-of-Memory crashes under unexpected prompt surges.

This volume serves as the **authoritative master triage playbook**, providing algorithmic diagnostics, root-cause derivations, and verified remediation recipes for every major failure mode in the Qwen2.5 lifecycle.

```
                         [Incident Triggered]
                                   │
       ┌───────────────────────────┴───────────────────────────┐
       ▼                                                       ▼
[Hard System Crash]                                 [Silent Quality Failure]
  │                                                   │
  ├─► CUDA OOM (Prefill vs Decode)                    ├─► NaN / Inf Loss Spikes (SFT/LoRA)
  ├─► NCCL / NVLink Distributed Deadlock              ├─► Repetitive Token Degeneration Loops
  ├─► SIGBUS (/dev/shm Exhaustion)                    ├─► RoPE High-Context Hallucinations
  └─► CUDA Graph Compilation Failure                  └─► Truncation / Missing <|im_end|>
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Aircraft Emergency Quick Reference Handbook (QRH)
3. Incident Triage Matrix: Fast-Path Failure Identification
4. Deep-Dive Failure Modes & First-Principles Remediations
   - Mode 1: CUDA Out-of-Memory (OOM) Allocation Triage
   - Mode 2: Numerical Instability & NaN Loss Explosion in SFT
   - Mode 3: RoPE Context Boundary Failure & Rotary Frequency Misalignment
   - Mode 4: Silent Truncation & Premature EOT Termination
   - Mode 5: Distributed NCCL Ring All-Reduce Deadlocks
5. Comparative Trade-Off Matrix: Remediation Strategies
6. Concrete Production Hands-On Lab: Automated Tensor Sanity & Gradient Health Verifier
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Aircraft Emergency Quick Reference Handbook (QRH)

When an engine fails on a Boeing 787 at 35,000 feet, the pilots do not open a 1,000-page physics textbook on gas turbine aerodynamics. They open the **Quick Reference Handbook (QRH)**:
- Step 1: Identify symptom (flameout alarm).
- Step 2: Immediate memory actions (throttle idle, fuel cutoff switch).
- Step 3: Verify parameters (hydraulic pressure, altitude hold).
- Step 4: Execute restart checklist.

This volume is your **AI Engineering QRH**. When Qwen2.5 crashes at 2 AM on your DGX Spark cluster, you use this guide to identify the signature, apply the immediate containment patch, and permanently eliminate the root cause.

---

## 3. Incident Triage Matrix: Fast-Path Failure Identification

| Observed Symptom | Primary Suspect Subsystem | Immediate Diagnostic Command | Root Cause Reference |
| :--- | :--- | :--- | :--- |
| `torch.cuda.OutOfMemoryError` | KV-cache allocation or prefill burst | `nvidia-smi` / inspect prompt token length | [Section 4.1](#mode-1-cuda-out-of-memory-oom-allocation-triage) |
| Loss drops to `NaN` during ms-swift SFT | Learning rate / Float16 overflow / SwiGLU | Check gradient norm in WandB/TensorBoard | [Section 4.2](#mode-2-numerical-instability--nan-loss-explosion-in-sft) |
| Model repeats identical phrase 100 times | Repetition penalty or RoPE context limit | Check if input length $> \text{max\_seq\_len}$ | [Section 4.3](#mode-3-rope-context-boundary-failure--rotary-frequency-misalignment) |
| Model cuts off mid-sentence without error | Stop token `<|im_end|>` missing or max_tokens hit | Inspect API response `finish_reason` | [Section 4.4](#mode-4-silent-truncation--premature-eot-termination) |
| Distributed training hangs indefinitely | NCCL socket timeout or NVLink link down | `dmesg \| grep -i nvlink` or check RoCE PFC | [Section 4.5](#mode-5-distributed-nccl-ring-all-reduce-deadlocks) |

---

## 4. Deep-Dive Failure Modes & First-Principles Remediations

### Mode 1: CUDA Out-of-Memory (OOM) Allocation Triage

#### Root-Cause Derivation
CUDA OOMs occur in two distinct runtime phases:
1. **Prefill OOM**: Occurs during initial prompt activation calculation. For sequence length $L$, the activation memory of self-attention scales as $\mathcal{O}(B \cdot H \cdot L \cdot d)$. If FlashAttention is not active, standard attention allocates a full tensor $\mathbf{A} \in \mathbb{R}^{B \times H \times L \times L}$, causing instant OOM on prompts $> 8\text{k}$ tokens.
2. **Decode OOM**: Occurs when dynamically growing PagedAttention KV-cache blocks exceed GPU VRAM allocation.

#### Remediation Checklist
1. **Force FlashAttention-2 or FlashAttention-3**:
   Ensure `attn_implementation="flash_attention_2"` is passed to the model loader.
2. **Enable Chunked Prefill in vLLM**:
   Split massive input prompts into discrete compute chunks (e.g. 512 tokens), preventing monolithic activation spikes:
   ```bash
   --enable-chunked-prefill --max-num-batched-tokens 2048
   ```
3. **Cap KV-Cache Utilization Ceiling**:
   Reduce `--gpu-memory-utilization` from `0.95` to `0.88` to preserve a dedicated 15 GB buffer for CUDA runtime scratchpads and memory fragmentation.

---

### Mode 2: Numerical Instability & NaN Loss Explosion in SFT

#### Root-Cause Derivation
Qwen2.5 employs the **SwiGLU** activation function:

$$\text{SwiGLU}(\mathbf{x}) = \left( \mathbf{x} \mathbf{W}_{\text{gate}} \cdot \sigma(\mathbf{x} \mathbf{W}_{\text{gate}}) \right) \odot (\mathbf{x} \mathbf{W}_{\text{up}})$$

In standard IEEE 754 half-precision `float16`, the maximum representable value is $65,504$. If an un-normalized activation exceeds $256$, multiplying intermediate gating tensors produces values $> 65,536$, immediately triggering arithmetic overflow to $+\infty$. In backpropagation:

$$\frac{\partial \mathcal{L}}{\partial \mathbf{W}} = \infty \implies \text{New Weights} = \mathbf{W} - \eta \cdot \infty = \text{NaN}$$

#### Remediation Checklist
1. **MANDATORY: Convert Training Precision to `bfloat16`**:
   `bfloat16` has the identical 8-bit dynamic exponent range as `float32` (values up to $\approx 3.4 \times 10^{38}$), mathematically eliminating overflow risks:
   ```bash
   swift sft --model Qwen/Qwen2.5-32B-Instruct --fp16 false --bf16 true
   ```
2. **Enforce Gradient Clipping**:
   Clip the global gradient $L_2$ norm to $1.0$:
   ```python
   torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
   ```
3. **Warmup Ratio**:
   Set `warmup_ratio=0.05` to prevent massive gradient shocks during the first 50 optimization steps.

---

### Mode 3: RoPE Context Boundary Failure & Rotary Frequency Misalignment

#### Root-Cause Derivation
Qwen2.5 natively scales up to 131,072 tokens by configuring the RoPE base frequency:

$$\theta = 1,000,000 \; (10^6)$$

If a deployment script or third-party inference runtime resets $\theta$ to the default LLaMA base frequency ($\theta = 10,000$):

$$\lambda_i = 2\pi \cdot \theta^{2i/d}$$

The rotary wavelengths for higher feature dimensions compress by a factor of 100x. When context exceeds 8,192 tokens, position embedding vectors $\mathbf{R}_m$ begin to overlap cyclically, causing catastrophic perplexity explosion and infinite phrase repetition.

#### Remediation Checklist
1. **Verify Tokenizer & Config JSON**:
   Inspect `config.json` and ensure:
   ```json
   "rope_theta": 1000000.0,
   "max_position_embeddings": 131072
   ```
2. **Enable Dynamic YaRN Scaling for $> 32\text{k}$ Context**:
   ```json
   "rope_scaling": {
     "type": "yarn",
     "factor": 4.0,
     "original_max_position_embeddings": 32768
   }
   ```

---

### Mode 4: Silent Truncation & Premature EOT Termination

#### Root-Cause Derivation
Qwen2.5 uses `<|im_end|>` (Token ID `151645`) and `<|endoftext|>` (Token ID `151643`) as end-of-turn indicators.
If an inference engine wrapper (e.g. naive HuggingFace pipeline) does not explicitly register `eos_token_id=[151645, 151643]`:
- The model may output `<|im_end|>` as plain text and continue generating hallucinated dialogues.
- Conversely, if `max_tokens` is set too low (e.g. default 256 in some gateways), structured JSON responses will be chopped in half without any error code.

#### Remediation Checklist
- Always inspect `finish_reason` in the JSON response:
  - If `finish_reason == "length"`: The output was truncated due to token ceilings. Increase `max_tokens`.
  - If `finish_reason == "stop"`: Normal clean termination.

---

### Mode 5: Distributed NCCL Ring All-Reduce Deadlocks

#### Root-Cause Derivation
In distributed setups across multiple GPUs or nodes, if a single GPU encounters a delayed kernel or silent ECC memory failure while other GPUs reach an `all_reduce` synchronization barrier, the entire cluster hangs indefinitely waiting for the lagging rank.

#### Remediation Checklist
1. Set NCCL timeout and debugging environment variables in all launch scripts:
   ```bash
   export NCCL_DEBUG=INFO
   export NCCL_DEBUG_SUBSYS=ALL
   export NCCL_ASYNC_ERROR_HANDLING=1
   export TORCH_DISTRIBUTED_DEBUG=DETAIL
   ```
2. On DGX Spark nodes with RoCEv2, verify Priority Flow Control (PFC) is lossless on traffic class 3:
   ```bash
   roce_sys admin show
   ```

---

## 5. Comparative Trade-Off Matrix: Remediation Strategies

| Issue Subsystem | Naive / Heuristic Fix | Production First-Principles Solution | Trade-Off Impact |
| :--- | :--- | :--- | :--- |
| **Prefill OOM** | Drop batch size to 1 | **Enable Chunked Prefill (vLLM)** | Preserves high batch throughput |
| **Loss NaN Spikes** | Reduce Learning Rate by 10x | **Switch from FP16 to BF16 + Clip 1.0**| Preserves fast convergence rate |
| **Context Degradation**| Hard truncate at 4k tokens | **Correct `rope_theta: 1e6` + YaRN** | Unlocks full 32k–128k context |
| **Silent Truncation** | Guess bigger `max_tokens` | **Bind `eos_token_id` to ChatML tokens**| 100% deterministic stops |
| **Distributed Hangs** | Kill pod manually with `kill -9`| **Configure `NCCL_ASYNC_ERROR_HANDLING`**| Automatic fault detection & failover|

---

## 6. Concrete Production Hands-On Lab: Automated Tensor Sanity & Gradient Health Verifier

This runnable diagnostic script scans model weights, activations, and gradients, detecting NaNs, Infs, extreme outlier magnitudes, and RoPE angle misalignments before training or deployment.

```python
#!/usr/bin/env python3
"""
Production Tensor Health & Gradient Sanity Verifier for Qwen2.5.
Automates detection of NaN/Inf spikes, float16 overflows, and RoPE corruption.
"""

import math
import torch
import torch.nn as nn

class ModelHealthChecker:
    @staticmethod
    def audit_tensor(name: str, tensor: torch.Tensor) -> bool:
        """Checks for NaNs, Infs, and dangerous dynamic range spikes."""
        if tensor is None:
            return True

        has_nan = torch.isnan(tensor).any().item()
        has_inf = torch.isinf(tensor).any().item()

        if has_nan or has_inf:
            print(f"[CRITICAL FAILURE] Tensor '{name}' contains NaN={has_nan}, Inf={has_inf}!")
            return False

        max_val = tensor.abs().max().item()
        if max_val > 65000.0:
            print(f"[WARNING] Tensor '{name}' max value is {max_val:.2f} (Near float16 overflow threshold!)")

        return True

    @staticmethod
    def verify_rope_theta(config_theta: float, expected_theta: float = 1000000.0) -> bool:
        """Validates that Qwen2.5 base RoPE frequency is configured correctly."""
        if not math.isclose(config_theta, expected_theta, rel_tol=1e-3):
            print(f"[CORRUPTED ROPE] config rope_theta is {config_theta}, expected {expected_theta}!")
            return False
        print(f"[HEALTHY ROPE] RoPE base frequency verified: {config_theta:.0f}")
        return True

    @staticmethod
    def audit_gradients(model: nn.Module, max_norm_threshold: float = 5.0) -> bool:
        """Calculates total L2 gradient norm across all trainable parameters."""
        total_norm = 0.0
        for p in model.parameters():
            if p.grad is not None:
                param_norm = p.grad.data.norm(2)
                total_norm += param_norm.item() ** 2
        total_norm = math.sqrt(total_norm)

        print(f"[GRADIENT AUDIT] Total L2 Gradient Norm: {total_norm:.4f}")
        if total_norm > max_norm_threshold:
            print(f"[EXPLODING GRADIENT WARNING] Norm {total_norm:.2f} exceeds threshold {max_norm_threshold}!")
            return False
        return True

def run_diagnostics():
    print("=" * 80)
    print("QWEN2.5 PRODUCTION HEALTH & SANITY DIAGNOSTIC LAB")
    print("=" * 80)

    checker = ModelHealthChecker()

    # Test 1: RoPE Frequency Check
    print("\nTest 1: Validating RoPE Base Frequency...")
    checker.verify_rope_theta(config_theta=1000000.0)
    checker.verify_rope_theta(config_theta=10000.0) # Bad LLaMA legacy setting

    # Test 2: Weight & Activation Sanity Check
    print("\nTest 2: Auditing Layer Weights...")
    healthy_weight = torch.randn(1024, 1024, dtype=torch.bfloat16)
    checker.audit_tensor("model.layers.0.mlp.gate_proj", healthy_weight)

    corrupted_weight = healthy_weight.clone()
    corrupted_weight[10, 10] = float("nan")
    checker.audit_tensor("model.layers.0.mlp.corrupted_proj", corrupted_weight)

    # Test 3: Gradient Norm Check on Toy Model
    print("\nTest 3: Auditing Gradient Dynamics...")
    toy_layer = nn.Linear(64, 64)
    x = torch.randn(8, 64)
    out = toy_layer(x).sum()
    out.backward()
    checker.audit_gradients(toy_layer)

    print("\n[SUCCESS] Production health diagnostics suite executed.")

if __name__ == "__main__":
    run_diagnostics()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

DGX Spark operational diagnostics:
1. **Verify Blackwell Unified Memory Health**:
   ```bash
   nvidia-smi --query-gpu=gpu_name,memory.total,memory.used,temperature.gpu,power.draw --format=csv
   ```
2. **Inspect Grace ARM to GB10 NVLink-C2C Link State**:
   ```bash
   nvidia-smi nvlink --status
   ```
   Ensure NVLink status reports `ACTIVE` with `900 GB/s` aggregate bandwidth. Any link in `FAULT` or `RECOVERY` indicates a hardware failure requiring firmware re-initialization.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Automated Stop Hook)**:
   Implement a PyTorch training hook that monitors the batch loss on every iteration and automatically aborts training if `loss > 15.0` or `torch.isnan(loss)`.

2. **Exercise 2 (Dynamic Attention Fallback)**:
   Author a function that attempts to allocate a FlashAttention-2 context, catches CUDA OOM exceptions, and automatically falls back to CPU offloaded attention or chunked prefill.

### Solutions

**Solution for Exercise 1**:
```python
def training_loss_guard_hook(step: int, loss_val: float):
    if math.isnan(loss_val) or math.isinf(loss_val) or loss_val > 15.0:
        raise ArithmeticError(f"Emergency training abort at step {step}: Loss diverged to {loss_val}!")
```

### Troubleshooting FAQ

- **Q: Model answers fine on short inputs, but on 16k inputs it starts outputting continuous spaces or commas.**
  - *Fix*: You have a RoPE base frequency mismatch. Ensure `rope_theta` is set to `1000000.0`. If using vLLM, add `--max-model-len 32768`.

- **Q: Training loss drops nicely, then suddenly at step 420 becomes NaN and stays NaN forever.**
  - *Fix*: An activation in SwiGLU overflowed float16 limits. Immediately restart training from checkpoint 400 using `bf16=True` and enable gradient clipping with `max_norm=1.0`.
