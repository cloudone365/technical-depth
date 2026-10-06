# Volume 13: Direct Preference Optimization (DPO) at Scale

```
==================================================================================================
TARGET AUDIENCE: Post-Training Researchers, Distributed Alignment Engineers, MLOps Leads
PREREQUISITES   : RLHF mathematics, Bradley-Terry model, KL divergence, Megatron-Core distributed
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master Direct Preference Optimization (DPO) at scale using NeMo and Megatron-Core:
                  mathematical derivation, reference policy management, and pairwise distributed execution.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

While Reinforcement Learning from Human Feedback (RLHF with PPO) requires training a separate reward model, value network, and policy network, **Direct Preference Optimization (DPO)** analytically re-parameterizes the reward function directly in terms of the language model's policy. This eliminates the need for reinforcement learning loops entirely, formulating preference alignment as a simple binary cross-entropy loss over pairwise preferences $(x, y_w, y_l)$.

However, executing DPO on multi-billion parameter models in enterprise settings introduces distributed systems challenges: hosting both the trainable policy $\pi_\theta$ and the frozen reference model $\pi_{\text{ref}}$ in GPU memory simultaneously, and coordinating pairwise sequence forwarding across Megatron-Core Tensor and Pipeline parallel groups.

```
       [Preference Pair: Prompt x, Winner y_w, Loser y_l]
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
┌───────────────────────────────┐     ┌───────────────────────────────┐
│ Active Policy Model π_θ       │     │ Frozen Reference Model π_ref  │
│ (Trainable, Megatron 3D)      │     │ (Frozen, Quantized FP8/BF16)  │
└──────────────┬────────────────┘     └──────────────┬────────────────┘
               │                                     │
               ▼                                     ▼
       Compute Log-Odds                      Compute Log-Odds
       log π_θ(y_w | x)                      log π_ref(y_w | x)
       log π_θ(y_l | x)                      log π_ref(y_l | x)
               │                                     │
               └──────────────────┬──────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   DPO Loss Computation Engine                       │
│                                                                     │
│   Implicit Reward: r_θ(x, y) = β * log( π_θ(y|x) / π_ref(y|x) )     │
│   Loss = -log σ( r_θ(x, y_w) - r_θ(x, y_l) )                        │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ Backpropagate Gradients
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Updates Active Policy π_θ (Zero Reinforcement Learning Instability) │
└─────────────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Direct Courtroom Jury vs. The Middleman
3. Evolutionary Lineage: From PPO Actor-Critic to Closed-Form DPO
4. First-Principles Mathematics & Algorithmic Formulations
   - Mathematical Derivation of DPO from Constrained RL
   - Implicit Reward Substitution
   - Gradient Dynamics & The Adaptive Weighting Factor
   - Reference Model Memory Optimization (FP8 Freezing)
5. Comparative Trade-Off Matrix: Alignment Algorithms
6. Concrete Production Hands-On Lab: Scalable DPO Loss & Implicit Reward Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Direct Courtroom Jury vs. The Middleman

Imagine training a trial lawyer to win cases before a judge:

- **The Traditional PPO Approach (Hiring an Unreliable Judicial Consultant)**:
  Instead of having the lawyer speak directly to the judge, you hire a third-party consultant (**The Reward Model**). The consultant guesses how much the judge will like each sentence and awards points. Then you hire a second consultant (**The Critic / Value Network**) to predict how many points the first consultant will give.
  The lawyer spends all their time trying to please the consultants rather than winning the case (**Reward Hacking**), and the cost of keeping four people in the room bankrupts the law firm (**Memory Exhaustion**).

- **The Direct Preference Optimization Approach (The Direct Verdict Transcript)**:
  DPO fires both consultants.
  You simply hand the lawyer a stack of historical transcripts: *"In Case #402, Lawyer A said this and won; Lawyer B said that and lost."*
  The algorithm mathematically derives the exact update that increases the probability of the winning phrase while decreasing the probability of the losing phrase, using the original lawyer's instincts (**The Reference Model**) as a guardrail against wild delusions.

---

## 3. Evolutionary Lineage: From PPO Actor-Critic to Closed-Form DPO

```
Generation 1 (2020-2022)      Generation 2 (2023)           Generation 3 (2024-2026)
PPO (Actor, Critic, Ref, Rew) Standard DPO (HuggingFace)    Megatron-Core Distributed DPO
──────────────────────────    ──────────────────────────    ─────────────────────────────
- 4 models in GPU memory      - Closed-form pairwise loss   - Megatron 3D parallel sharding
- PPO policy collapse         - 2 models (Policy + Ref)     - FP8 reference model freezing
- Unstable reward hacking     - Suffers from length hacking - Sequence packing integration
- Complex hyperparameter soup - OOM on large 70B models     - Length-normalized loss margins
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Mathematical Derivation of DPO from Constrained RL

The classical RLHF objective seeks an optimal policy $\pi_\theta$ that maximizes human reward $r(x, y)$ subject to a KL-divergence penalty against a reference policy $\pi_{\text{ref}}$:

$$\max_{\pi} \mathbb{E}_{x \sim \mathcal{D}, y \sim \pi(\cdot \mid x)} \left[ r(x, y) \right] - \beta D_{\text{KL}}\left( \pi(\cdot \mid x) \;\|\; \pi_{\text{ref}}(\cdot \mid x) \right)$$

This constrained optimization problem has a known closed-form analytical solution:

$$\pi^*(y \mid x) = \frac{1}{Z(x)} \pi_{\text{ref}}(y \mid x) \exp\left( \frac{1}{\beta} r(x, y) \right)$$

Where $Z(x) = \sum_y \pi_{\text{ref}}(y \mid x) \exp\left( \frac{1}{\beta} r(x, y) \right)$ is the partition function.
Taking the natural logarithm of both sides:

$$\log \pi^*(y \mid x) = \log \pi_{\text{ref}}(y \mid x) + \frac{1}{\beta} r(x, y) - \log Z(x)$$

Rearranging to solve for the ground-truth reward $r(x, y)$:

$$r(x, y) = \beta \log \frac{\pi^*(y \mid x)}{\pi_{\text{ref}}(y \mid x)} + \beta \log Z(x)$$

### Implicit Reward Substitution

Under the Bradley-Terry preference model, the probability that response $y_w$ is preferred over $y_l$ given prompt $x$ is:

$$P(y_w \succ y_l \mid x) = \sigma\left( r(x, y_w) - r(x, y_l) \right)$$

Substituting the expression for $r(x, y)$, the partition function $\beta \log Z(x)$ cancels out cleanly:

$$r(x, y_w) - r(x, y_l) = \beta \log \frac{\pi_\theta(y_w \mid x)}{\pi_{\text{ref}}(y_w \mid x)} - \beta \log \frac{\pi_\theta(y_l \mid x)}{\pi_{\text{ref}}(y_l \mid x)}$$

The final **DPO Loss Objective** minimized by gradient descent is:

$$\mathcal{L}_{\text{DPO}}(\theta; \pi_{\text{ref}}) = -\mathbb{E}_{(x, y_w, y_l) \sim \mathcal{D}} \left[ \log \sigma \left( \beta \log \frac{\pi_\theta(y_w \mid x)}{\pi_{\text{ref}}(y_w \mid x)} - \beta \log \frac{\pi_\theta(y_l \mid x)}{\pi_{\text{ref}}(y_l \mid x)} \right) \right]$$

### Gradient Dynamics & The Adaptive Weighting Factor

Differentiating $\mathcal{L}_{\text{DPO}}$ with respect to policy parameters $\theta$:

$$\nabla_\theta \mathcal{L}_{\text{DPO}}(\theta) = -\beta \cdot \sigma\left( \hat{r}_\theta(x, y_l) - \hat{r}_\theta(x, y_w) \right) \left[ \nabla_\theta \log \pi_\theta(y_w \mid x) - \nabla_\theta \log \pi_\theta(y_l \mid x) \right]$$

Notice the scalar weight:

$$\omega(x, y_w, y_l) = \sigma\left( \hat{r}_\theta(x, y_l) - \hat{r}_\theta(x, y_w) \right)$$

- When the model currently assigns a higher implicit reward to the loser than the winner ($\hat{r}(y_l) > \hat{r}(y_w)$), $\omega \to 1.0$, producing maximum gradient updates.
- When the model already strongly prefers the winner ($\hat{r}(y_w) \gg \hat{r}(y_l)$), $\omega \to 0.0$, vanishing the gradient and preventing over-optimization.

---

## 5. Comparative Trade-Off Matrix: Alignment Algorithms

| Dimension | PPO (Online RL) | KTO (Kahneman-Tversky) | Standard DPO | Megatron NeMo DPO |
| :--- | :--- | :--- | :--- | :--- |
| **Data Format Required** | Prompts only (needs reward model)| Unpaired (Binary Good/Bad) | Pairwise $(x, y_w, y_l)$ | **Pairwise $(x, y_w, y_l)$** |
| **Model Footprint** | 4 Models | 2 Models | 2 Models | **2 Models (Ref in FP8)** |
| **Training Stability** | Unstable (Policy drift) | Stable | Stable | **Highest (Megatron parallel)**|
| **Length Bias Susceptibility**| High | Moderate | High (without norm) | **Low (Length-Normalized)** |
| **DGX Spark Deployment** | Complex (Cluster required) | Single Node | Single Node | **Single Node (128 GB Unified)**|

---

## 6. Concrete Production Hands-On Lab: Scalable DPO Loss & Implicit Reward Engine

This self-contained Python script implements the mathematical mechanics of DPO: computing per-token log-probabilities for winner and loser completions, calculating implicit rewards, and evaluating the Bradley-Terry DPO loss.

```python
#!/usr/bin/env python3
"""
NVIDIA NeMo DPO Loss & Implicit Reward Engine.
Demonstrates per-token logp extraction, reference model ratio computation,
implicit reward calculation, and DPO loss backward pass.
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple

# =====================================================================
# 1. DPO LOSS COMPUTATION ENGINE
# =====================================================================

class DPOTrainer:
    def __init__(self, beta: float = 0.1, label_smoothing: float = 0.0):
        self.beta = beta
        self.label_smoothing = label_smoothing

    @staticmethod
    def get_batch_logps(logits: torch.Tensor, labels: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """
        Extracts sum of log-probabilities for active target tokens.
        logits: [Batch, SeqLen, Vocab]
        labels: [Batch, SeqLen]
        mask  : [Batch, SeqLen] (1 for response tokens, 0 for prompt)
        """
        # Shift for next-token prediction
        shift_logits = logits[:, :-1, :].contiguous()
        shift_labels = labels[:, 1:].contiguous()
        shift_mask = mask[:, 1:].contiguous()

        log_probs = F.log_softmax(shift_logits, dim=-1)
        per_token_logps = torch.gather(log_probs, dim=-1, index=shift_labels.unsqueeze(-1)).squeeze(-1)

        # Mask prompt tokens and sum across sequence
        masked_logps = per_token_logps * shift_mask
        return masked_logps.sum(dim=-1)

    def compute_loss(self,
                     pi_w_logps: torch.Tensor,
                     pi_l_logps: torch.Tensor,
                     ref_w_logps: torch.Tensor,
                     ref_l_logps: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Computes the standard DPO pairwise loss:
        L_DPO = -log sigma( beta * log(pi_w/ref_w) - beta * log(pi_l/ref_l) )
        """
        # Implicit rewards
        reward_w = self.beta * (pi_w_logps - ref_w_logps)
        reward_l = self.beta * (pi_l_logps - ref_l_logps)

        logits_diff = reward_w - reward_l

        if self.label_smoothing > 0.0:
            # Conservative label smoothing
            loss = (-F.logsigmoid(logits_diff) * (1.0 - self.label_smoothing) -
                    F.logsigmoid(-logits_diff) * self.label_smoothing).mean()
        else:
            loss = -F.logsigmoid(logits_diff).mean()

        return loss, reward_w.detach(), reward_l.detach()

# =====================================================================
# 2. VERIFICATION HARNESS
# =====================================================================

def run_dpo_lab():
    print("=" * 80)
    print("NVIDIA NEMO DIRECT PREFERENCE OPTIMIZATION (DPO) LAB")
    print("=" * 80)

    torch.manual_seed(42)
    batch_size, seq_len, vocab_size = 2, 16, 100

    # 1. Simulate Policy and Reference Models
    policy_head = nn.Linear(32, vocab_size)
    ref_head = nn.Linear(32, vocab_size)
    ref_head.load_state_dict(policy_head.state_dict()) # Identical at step 0
    ref_head.eval()

    # 2. Simulated Input Tensors (Batch: [Winner, Loser])
    dummy_hidden = torch.randn(batch_size, seq_len, 32)
    labels = torch.randint(0, vocab_size, (batch_size, seq_len))
    # Loss mask: 0 for first 6 tokens (prompt), 1 for remaining 10 tokens (response)
    mask = torch.cat([torch.zeros(batch_size, 6), torch.ones(batch_size, 10)], dim=1)

    # 3. Compute LogPs under Policy
    policy_logits = policy_head(dummy_hidden)
    pi_logps = DPOTrainer.get_batch_logps(policy_logits, labels, mask)
    pi_w, pi_l = pi_logps[0:1], pi_logps[1:2]

    # 4. Compute LogPs under Frozen Reference Model
    with torch.no_grad():
        ref_logits = ref_head(dummy_hidden)
        ref_logps = DPOTrainer.get_batch_logps(ref_logits, labels, mask)
        ref_w, ref_l = ref_logps[0:1], ref_logps[1:2]

    # 5. Compute DPO Loss
    dpo_trainer = DPOTrainer(beta=0.1)
    loss, rew_w, rew_l = dpo_trainer.compute_loss(pi_w, pi_l, ref_w, ref_l)

    print(f"Initial DPO Loss (Identical Policy & Ref) : {loss.item():.4f}")
    # At step 0 when pi == ref, reward difference is 0.0 -> logsigmoid(0) = -log(0.5) = 0.6931
    assert math.isclose(loss.item(), 0.6931, rel_tol=1e-2), f"Expected ln(2) ≈ 0.6931, got {loss.item():.4f}"

    # 6. Backward Pass
    loss.backward()
    print("Autograd backward pass completed cleanly. Policy gradients computed.")
    print(f"Implicit Winner Reward: {rew_w.item():.4f} | Implicit Loser Reward: {rew_l.item():.4f}")

    print("\n[SUCCESS] Direct Preference Optimization mathematical formulation verified.")

if __name__ == "__main__":
    run_dpo_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Executing DPO on the DGX Spark:
1. **Reference Model Memory Footprint**:
   Hosting two 70B models simultaneously would require $140\text{ GB} \times 2 = 280\text{ GB}$, exceeding single-node memory.
   **NeMo Solution on DGX Spark**:
   - The Active Policy $\pi_\theta$ is loaded in **BF16 with LoRA adapters** ($\approx 70\text{ GB} + 4\text{ GB} = 74\text{ GB}$).
   - The Reference Model $\pi_{\text{ref}}$ is quantized to **FP8** ($\approx 35\text{ GB}$) and frozen.
   - **Total Footprint**: $74 + 35 = 109\text{ GB}$ VRAM, fitting comfortably within the 128 GB Unified Memory space.

2. **NeMo CLI Execution**:
   ```bash
   python -m nemo.collections.nlp.models.language_modeling.megatron_gpt_dpo \
     --config-path=/workspace/nemo_configs \
     --config-name=megatron_gpt_dpo \
     model.dpo.beta=0.1 \
     model.dpo.reference_model_precision=fp8 \
     trainer.devices=1 \
     trainer.precision=bf16
   ```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Length-Normalized DPO)**:
   Modify the DPO loss formulation to normalize implicit rewards by response token length: $r_{\text{norm}}(x, y) = \frac{\beta}{|y|} \log \frac{\pi_\theta(y \mid x)}{\pi_{\text{ref}}(y \mid x)}$ (SimPO style). Prove that this eliminates verbosity hacking.

2. **Exercise 2 (Margin Penalty)**:
   Add a minimum target margin $\gamma > 0$ to the loss: $\mathcal{L} = -\log \sigma(\hat{r}_w - \hat{r}_l - \gamma)$. Verify that loss increases as $\gamma$ increases.

### Solutions

**Solution for Exercise 2**:
```python
def compute_margin_dpo(reward_w: float, reward_l: float, gamma: float = 0.5) -> float:
    diff = reward_w - reward_l - gamma
    # -log(sigma(diff))
    return -math.log(1.0 / (1.0 + math.exp(-diff)))
```

### Troubleshooting FAQ

- **Q: Model becomes completely unhinged and generates repetitive gibberish during DPO.**
  - *Fix*: Your learning rate or $\beta$ parameter is too large. Reduce learning rate to $5 \times 10^{-7}$ (DPO requires roughly 10x smaller learning rate than standard SFT) and ensure $\beta$ is in the range $[0.05, 0.15]$.

- **Q: DPO loss drops to zero within 10 steps.**
  - *Fix*: The policy has memorized the pairwise dataset, causing probabilities to degenerate. Increase the reference model KL weight $\beta$ or add label smoothing (`label_smoothing: 0.1`).
