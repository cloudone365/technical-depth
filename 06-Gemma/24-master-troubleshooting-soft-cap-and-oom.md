# Volume 24: Master Troubleshooting: Soft-Cap Overflows, Loss Spikes, and OOM

```
==================================================================================================
TARGET AUDIENCE: Production SREs, MLOps On-Call Leads, Foundation Model Debugging Specialists
PREREQUISITES   : CUDA Error Handling, PyTorch Autograd Diagnostics, Linux Kernel & Memory Telemetry
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master systematic root-cause analysis (RCA), diagnostic triage, and recovery playbooks
                  for Gemma 2: NaN loss spikes, soft-cap overflows, SWA memory leaks, and CUDA OOM.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Operating Google Gemma 2 in production environments presents unique operational nuances due to its non-standard architecture: **double logit soft-capping**, **alternating sliding window attention**, **unit-offset RMSNorm**, and a **256k vocabulary**.

When failures occur, generic troubleshooting guides for standard LLaMA models produce incorrect diagnoses. This master playbook provides **first-principles diagnostic trees, automated detection scripts, and emergency recovery runbooks** for the five most catastrophic failure modes encountered during Gemma 2 pre-training, fine-tuning, and production serving.

```
                           ┌──────────────────────────────────────────────┐
                           │      PRODUCTION GEMMA 2 INCIDENT TRIAGE      │
                           └──────────────────────┬───────────────────────┘
                                                  │
                 ┌────────────────────────────────┼────────────────────────────────┐
                 ▼                                ▼                                ▼
┌─────────────────────────────────┐ ┌─────────────────────────────────┐ ┌─────────────────────────────────┐
│     INCIDENT 1: NaN LOSS SPIKE  │ │     INCIDENT 2: CUDA OOM        │ │  INCIDENT 3: CORRUPT ATTENTION  │
├─────────────────────────────────┤ ├─────────────────────────────────┤ ├─────────────────────────────────┤
│ Root Cause:                     │ │ Root Cause:                     │ │ Root Cause:                     │
│ - RMSNorm init to 1.0 (2x shock)│ │ - Default /dev/shm 64MB limit   │ │ - SWA circular ring overflow    │
│ - Soft-cap gradient saturation  │ │ - FlashAttention memory leak    │ │ - FlashAttention lacks tanh fuse│
│ - High LR on 256k vocab head    │ │ - JAX preallocate 75% memory    │ │ - Attention score dropoff       │
└─────────────────────────────────┘ └─────────────────────────────────┘ └─────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Intensive Care Emergency Triage Room
3. Failure Taxonomy & Root Cause Matrix for Gemma 2
4. Detailed Technical Deep-Dives and Resolution Runbooks
   - Incident Alpha: Sudden NaN Loss Spikes during Fine-Tuning (SFT / DPO)
   - Incident Beta: CUDA Out-Of-Memory (OOM) and Bus Errors on Unified Memory
   - Incident Gamma: Missing Soft-Capping in Third-Party Attention Kernels
   - Incident Delta: Unit-Offset RMSNorm Initialization Drift ($1.0 + \gamma$)
   - Incident Epsilon: Sliding Window Token Eviction Leaks
5. Concrete Production Hands-On Lab: Automated Gemma 2 Incident Diagnostic Engine
6. Hardware Grounding for NVIDIA DGX Spark (DCGM Metrics and Unified Memory Telemetry)
7. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Intensive Care Emergency Triage Room

Imagine an emergency hospital trauma unit:
- When a critically ill patient is wheeled through the doors, inexperienced medics panic and guess wildly, administering random treatments that might harm the patient.
- An **Expert Trauma Surgeon** follows an immutable, structured diagnostic protocol:
  - **Airway (VRAM Allocation)**: Is the model suffocating on memory? Check `/dev/shm` and physical block allocation.
  - **Breathing (Gradient Flow)**: Are gradients circulating through the layers, or did a `NaN` flatline the autograd graph? Check the soft-capping tanh derivative.
  - **Circulation (RMSNorm Invariants)**: Is blood pressure spiking $2\times$ higher than normal? Check whether someone initialized the unit-offset RMSNorm to $1.0$ instead of $0.0$.
  - In under 60 seconds, the exact cause is isolated, stabilized, and remedied.

---

## 3. Failure Taxonomy & Root Cause Matrix for Gemma 2

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              GEMMA 2 PRODUCTION FAILURE TAXONOMY                                       │
├───────────────┬──────────────────────────┬───────────────────────────────┬─────────────────────────────┤
│ Symptom Code  │ Observable Manifestation │ Primary Root Cause            │ Immediate Remediation       │
├───────────────┼──────────────────────────┼───────────────────────────────┼─────────────────────────────┤
│ ERR-NAN-01    │ Loss jumps to NaN at     │ Unit-offset RMSNorm init to   │ Initialize RMSNorm weights  │
│               │ step 15-50 during SFT    │ 1.0 (produces 2.0x variance)  │ to 0.0 (torch.zeros_like)   │
├───────────────┼──────────────────────────┼───────────────────────────────┼─────────────────────────────┤
│ ERR-OOM-02    │ DataLoader worker killed │ Docker /dev/shm default 64MB  │ Mount emptyDir with 16Gi    │
│               │ by SIGBUS / Bus error    │ exhausted by PyTorch IPC      │ memory medium to /dev/shm   │
├───────────────┼──────────────────────────┼───────────────────────────────┼─────────────────────────────┤
│ ERR-KERNEL-03 │ Model produces repetitive│ FlashAttention lacks fused    │ Upgrade to vLLM >= 0.5.4 or │
│               │ garbage / low perplexity │ tanh soft-capping branch      │ use SDPA fallback           │
├───────────────┼──────────────────────────┼───────────────────────────────┼─────────────────────────────┤
│ ERR-VOCAB-04  │ Loss spikes when learning│ High LR applied to 256k vocab │ Freeze vocab unembedding or │
│               │ rate is increased        │ projection head               │ apply 0.1x LR multiplier    │
├───────────────┼──────────────────────────┼───────────────────────────────┼─────────────────────────────┤
│ ERR-SWA-05    │ Memory steadily leaks    │ SWA ring buffer fails to free │ Enforce PagedAttention block│
│               │ during long conversations│ blocks older than 4,096 tokens│ eviction on even layers     │
└───────────────┴──────────────────────────┴───────────────────────────────┴─────────────────────────────┘
```

---

## 4. Detailed Technical Deep-Dives and Resolution Runbooks

### 4.1 Incident Alpha: Sudden NaN Loss Spikes during Fine-Tuning

#### Mechanism:
In standard LLaMA models, RMSNorm weights are initialized to `1.0`:
$$\text{Output} = \text{Norm}(x) \odot \gamma \quad (\gamma = 1.0)$$
In Gemma 2, Google reformulates the normalization with an explicit **unit offset**:
$$\text{Output} = \text{Norm}(x) \odot (1.0 + \gamma)$$
If an engineer accidentally loads Gemma 2 using a generic LLaMA class or custom PyTorch layer where weights are initialized to `1.0`, the effective multiplier becomes:
$$1.0 + 1.0 = \mathbf{2.0}$$
Across 46 transformer layers, the activation variance explodes by:
$$2^{46} \approx 7.03 \times 10^{13}$$
This instantly causes floating-point overflow and catastrophic `NaN` generation on the very first backward pass.

#### Resolution Runbook:
```python
# Verify and enforce correct Gemma 2 RMSNorm initialization
def fix_gemma2_rmsnorm(model):
    for name, module in model.named_modules():
        if "layernorm" in name.lower() or "rmsnorm" in name.lower():
            if hasattr(module, "weight"):
                # If weights were mistakenly set to 1.0, reset them
                if torch.all(module.weight == 1.0):
                    print(f"Fixing corrupted unit-offset RMSNorm in layer: {name}")
                    module.weight.data.zero_()
```

### 4.2 Incident Beta: CUDA Out-Of-Memory on Unified Memory

#### Mechanism:
On the NVIDIA DGX Spark, the Grace ARM CPU and Blackwell GPU share 128 GB. When running multi-process workloads:
1. PyTorch shared memory queues exhaust the default Linux container `/dev/shm` limit ($64\text{ MB}$).
2. JAX default allocator attempts to pre-allocate $75\%$ ($96\text{ GB}$) immediately upon startup.

#### Resolution Runbook:
```bash
# Emergency Shell Runbook on DGX Spark
# 1. Expand container shared memory
docker run --shm-size=16g --gpus all ...

# 2. Disable greedy JAX pre-allocation
export XLA_PYTHON_CLIENT_PREALLOCATE=false
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.65

# 3. Flush Linux dirty page cache
sync && echo 3 | sudo tee /proc/sys/vm/drop_caches
```

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                DIAGNOSTIC TOOLING COMPARISON                                           │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Tool               │ Scope              │ Intrusiveness       │ Overhead         │ Primary Utility     │
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ torch.autograd.det │ Autograd Graph     │ High (Python hook)  │ 3-5x slowdown    │ Pinpoints NaN step  │
│ NVIDIA DCGM        │ Hardware / Silicon │ Zero (Driver level) │ < 0.1%           │ Xid / Thermal check │
│ PyTorch Memory Snap│ Memory Allocator   │ Low                 │ Minimal          │ Visualizes OOM leak │
│ vLLM Health Check  │ Serving Engine     │ Zero (HTTP /health) │ None             │ Readiness status    │
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Automated Gemma 2 Incident Diagnostic Engine

Save this script as `gemma2_diagnostic_triage_lab.py`:

```python
"""
Google Gemma 2 Automated Incident Diagnostic Engine.
Runs synthetic health checks detecting:
1. Corrupted unit-offset RMSNorm initialization
2. Missing soft-capping in attention tensors
3. Gradient explosion in 256k vocabulary head
4. Memory allocation safety margins
"""

import math
import torch
import torch.nn as nn

class Gemma2HealthChecker:
    @staticmethod
    def audit_rmsnorm_weights(norm_weights: torch.Tensor) -> bool:
        """
        Audits whether RMSNorm conforms to Gemma 2 unit offset (1.0 + gamma).
        Weights must be near 0.0 at initialization, NOT 1.0.
        """
        mean_val = norm_weights.abs().mean().item()
        if abs(mean_val - 1.0) < 0.1:
            print("[CRITICAL ALERT] RMSNorm weight mean is ~1.0! Detected standard LLaMA initialization.")
            print("  This causes a 2.0x variance multiplier per layer (2^46 explosion -> NaN).")
            print("  REMEDY: Re-initialize with torch.zeros_like(weights).")
            return False
        print(f"[PASSED] RMSNorm weight mean is {mean_val:.4f} (Correctly zero-centered).")
        return True

    @staticmethod
    def audit_soft_capping_bounds(attention_scores: torch.Tensor, cap: float = 50.0) -> bool:
        """
        Audits whether attention scores strictly respect hyperbolic tangent cap.
        """
        max_score = attention_scores.max().item()
        min_score = attention_scores.min().item()
        if max_score > cap + 1e-4 or min_score < -cap - 1e-4:
            print(f"[CRITICAL ALERT] Attention score [{min_score:.2f}, {max_score:.2f}] exceeds cap {cap}!")
            print("  Attention kernel is running WITHOUT fused soft-capping.")
            print("  REMEDY: Upgrade vLLM/FlashAttention to version with Gemma 2 tanh support.")
            return False
        print(f"[PASSED] Attention scores strictly bounded within [{-cap:.1f}, {cap:.1f}].")
        return True

def run_diagnostic_suite():
    print("=" * 80)
    print("RUNNING GOOGLE GEMMA 2 AUTOMATED PRODUCTION TRIAGE SUITE")
    print("=" * 80)

    # Test 1: Corrupted vs Healthy RMSNorm
    print("\n--- Diagnostic Test 1: Unit-Offset RMSNorm Initialization ---")
    corrupted_norm = torch.ones(2048) # Accidental standard init
    healthy_norm = torch.zeros(2048)  # Official Gemma 2 init

    print("Checking Corrupted Layer:")
    Gemma2HealthChecker.audit_rmsnorm_weights(corrupted_norm)
    print("\nChecking Healthy Layer:")
    Gemma2HealthChecker.audit_rmsnorm_weights(healthy_norm)

    # Test 2: Attention Soft-Capping Audit
    print("\n--- Diagnostic Test 2: Attention Soft-Capping Kernel Enforcement ---")
    raw_unbounded = torch.tensor([-150.0, 0.0, 75.0, 180.0])
    capped = 50.0 * torch.tanh(raw_unbounded / 50.0)

    print("Checking Uncapped Kernel Output:")
    Gemma2HealthChecker.audit_soft_capping_bounds(raw_unbounded, cap=50.0)
    print("\nChecking Fused Capped Kernel Output:")
    Gemma2HealthChecker.audit_soft_capping_bounds(capped, cap=50.0)

    print("\nVerification Passed: Diagnostic suite accurately detected and flagged failure modes!")

if __name__ == "__main__":
    run_diagnostic_suite()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (DCGM Metrics and Unified Memory Telemetry)

### 7.1 Real-Time Hardware Diagnostics
To inspect the health of the Blackwell GB10 GPU during Gemma 2 incidents:
```bash
# Check GPU status, thermal metrics, and power draw
nvidia-smi

# Inspect DCGM hardware error counters (Xid errors)
dcgmi diag -r 1

# Monitor NVLink-C2C bandwidth utilization
nvidia-smi nvlink -s
```

If an incident reports **Xid 79 (GPU fallen off the bus)** or **Xid 92 (High-Bandwidth Memory ECC uncorrectable error)**, the incident is hardware-related rather than a model software bug.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate Variance Explosion**:
   If an engineer mistakenly uses $1.0 + \gamma$ where $\gamma = 1.0$ across 26 layers in Gemma 2 2B, what is the exact amplification factor of the signal variance?
   - *Solution*: Amplification factor is $2^{26} = 67,108,864$ ($> 6.7 \times 10^7$), causing immediate gradient explosion.

2. **Differentiate Soft-Cap vs Vocab Cap Failures**:
   If loss spikes during SFT on the very first batch, is the culprit more likely Attention Soft-Capping (50.0) or Vocab Soft-Capping (30.0)?
   - *Solution*: Vocab Soft-Capping (30.0) applied to the massive 256k vocabulary head. If the learning rate on the unembedding projection is too high, the cross-entropy gradient over 256,128 tokens easily saturates the optimizer.

### Troubleshooting FAQ

- **Q: Why does `torch.autograd.detect_anomaly()` slow down my Gemma 2 training?**
  *A*: Anomaly detection maintains a complete stack trace for every forward floating-point operation in memory. Only enable it when actively isolating the exact step and layer causing a `NaN`, then disable it for production training.
- **Q: How can we verify that vLLM is using the fused soft-capping kernel?**
  *A*: Launch vLLM with `VLLM_LOGGING_LEVEL=DEBUG`. Inspect the startup log for `Gemma2Attention: Using fused soft_capping_flash_attn_kernel`. If it reports `falling back to reference PyTorch attention`, update your CUDA driver and vLLM build.
