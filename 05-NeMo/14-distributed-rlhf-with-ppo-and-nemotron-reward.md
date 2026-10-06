# Volume 14: Distributed RLHF with PPO and Nemotron Reward

```
==================================================================================================
TARGET AUDIENCE: Reinforcement Learning Researchers, Distributed MLOps Leads, Alignment Architects
PREREQUISITES   : Markov Decision Processes (MDPs), Policy Gradients, Generalized Advantage Estimation (GAE)
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master enterprise distributed RLHF with PPO: 4-model cluster topologies (Actor, Critic,
                  Reference, Reward), PPO clipped surrogate losses, GAE calculus, and Megatron scaling.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

While offline preference algorithms like DPO operate on static pre-collected datasets, **Online Reinforcement Learning from Human Feedback (RLHF with Proximal Policy Optimization - PPO)** allows the foundation model to explore, generate novel rollouts dynamically, and continuously adapt its reasoning trajectory against an active reward function.

Operating PPO at scale requires orchestrating **four distinct deep neural network models** concurrently across distributed clusters: the **Actor** (generates completions), the **Critic** (evaluates expected state values), the **Reference Model** (prevents policy drift), and the **Reward Model** (grades quality).

```
                              ┌────────────────────────────────────────┐
                              │           Prompt Buffer D_prompt       │
                              └───────────────────┬────────────────────┘
                                                  │
                                                  ▼
                              ┌────────────────────────────────────────┐
                              │  Actor Model π_θ (Rollout Generation)  │
                              └───────┬────────────────────────┬───────┘
                                      │ Emits Tokens y         │
                         ┌────────────┴────────────┐           │
                         ▼                         ▼           ▼
          ┌─────────────────────────┐ ┌─────────────────────────┐
          │ Frozen Reference π_ref  │ │ Nemotron-4-Reward R_ψ   │
          │ KL Divergence Boundary  │ │ HelpSteer2 Quality Score│
          └────────────┬────────────┘ └────────────┬────────────┘
                       │                           │
                       └─────────────┬─────────────┘
                                     │ Computes Token Penalized Reward
                                     ▼
                      ┌─────────────────────────────┐
                      │ Critic / Value Network V_ϕ  │
                      │ Computes State Values V(s)  │
                      └──────────────┬──────────────┘
                                     │
                                     ▼
                      ┌─────────────────────────────┐
                      │ Generalized Advantage (GAE) │
                      │ Advantage: A_t = GAE(γ, λ)  │
                      └──────────────┬──────────────┘
                                     │
            ┌────────────────────────┴────────────────────────┐
            ▼                                                 ▼
┌───────────────────────────────┐             ┌───────────────────────────────┐
│ PPO Clipped Policy Loss       │             │ Critic Value MSE Loss         │
│ Updates Actor Model π_θ       │             │ Updates Value Network V_ϕ     │
└───────────────────────────────┘             └───────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The High-Diving Olympic Athlete and the Judging Panel
3. Evolutionary Lineage: From REINFORCE to Distributed Megatron PPO
4. First-Principles Mathematics & Algorithmic Formulations
   - PPO Clipped Surrogate Objective Formulation
   - Generalized Advantage Estimation (GAE) Derivation
   - Per-Token KL Penalty Reward Shaping
   - Distributed 4-Model Memory Mapping
5. Comparative Trade-Off Matrix: Alignment Methodologies
6. Concrete Production Hands-On Lab: Complete PPO Step & GAE Calculator
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The High-Diving Olympic Athlete and the Judging Panel

Imagine training an Olympic platform diver:

- **The Static Offline Approach (DPO)**:
  The diver sits in a chair and watches video recordings of previous divers: *"Diver A scored an 8; Diver B scored a 6."* The diver never enters the swimming pool during training.

- **The Dynamic Online Approach (Distributed PPO)**:
  The diver stands at the top of the 10-meter platform in a live training arena:
  1. **The Diver (`Actor Model`)**: Performs a complex 3-flip dive into the water (**Rollout Generation**).
  2. **The Technique Coach (`Reference Model`)**: Observes from the side, blowing a whistle if the diver flails wildly or strays from foundational mechanics (**KL Divergence Guardrail**).
  3. **The Olympic Judges (`Reward Model - Nemotron-4`)**: Hold up scorecards based on splash size, body alignment, and entry angle (**HelpSteer2 Scoring**).
  4. **The Analytical Sports Biomechanist (`Critic Network`)**: Breaks down each millisecond of the dive, computing whether the diver was ahead of or behind expected score trajectory (**Generalized Advantage Estimation**).
  The diver climbs back up the ladder and refines their rotational momentum for the next dive.

---

## 3. Evolutionary Lineage: From REINFORCE to Distributed Megatron PPO

```
Generation 1 (1992-2016)      Generation 2 (2017-2022)      Generation 3 (2023-2026)
Classic REINFORCE / Actor-Crit Early PPO (OpenAI InstructGPT) Megatron-Core Distributed PPO
──────────────────────────    ──────────────────────────    ─────────────────────────────
- High gradient variance      - Single-GPU / Basic DDP      - 3D parallel Actor/Critic/Reward
- Policy easily collapsed     - Unstable reward scaling     - Asynchronous rollout generation
- Cannot handle long sequences- Severe memory bottlenecks   - FP8 reference model compression
- Slow convergence            - Frequent OOMs on 13B models - Scalable up to 70B+ parameters
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### PPO Clipped Surrogate Objective Formulation

Let the probability ratio between the updated policy $\pi_\theta$ and the rollout policy $\pi_{\theta_{\text{old}}}$ at token step $t$ be:

$$r_t(\theta) = \frac{\pi_\theta(y_t \mid x, y_{<t})}{\pi_{\theta_{\text{old}}}(y_t \mid x, y_{<t})}$$

To prevent destructively large policy updates that trigger catastrophic forgetting, PPO optimizes the clipped surrogate objective:

$$\mathcal{L}^{\text{CLIP}}(\theta) = \hat{\mathbb{E}}_t \left[ \min\left( r_t(\theta) \hat{A}_t, \; \text{clip}(r_t(\theta), \; 1 - \epsilon, \; 1 + \epsilon) \hat{A}_t \right) \right]$$

Where:
- $\hat{A}_t$ is the estimated advantage.
- $\epsilon$ is the clipping hyperparameter (typically $\epsilon = 0.2$).

```
Surrogate Loss L^CLIP
       ▲
       │                / (Unclipped if r_t * A_t is worse)
       │               /
       │  ┌───────────┐  <-- Clipped at (1 + ε) * A_t
       │  │           │
───────┼──┴───────────┴────────────────────────► Ratio r_t
       │ 1 - ε       1 + ε
```

### Generalized Advantage Estimation (GAE) Derivation

Let $V_\phi(s_t)$ be the scalar state value predicted by the Critic model at token $t$.
The temporal difference (TD) residual $\delta_t^V$ is:

$$\delta_t^V = R_t + \gamma V_\phi(s_{t+1}) - V_\phi(s_t)$$

Where $\gamma \in [0, 1]$ is the discount factor (for language modeling, typically $\gamma = 1.0$).
The Generalized Advantage Estimator $\hat{A}_t^{\text{GAE}(\gamma, \lambda)}$ is computed as an exponentially decaying sum of future TD residuals:

$$\hat{A}_t^{\text{GAE}} = \sum_{l=0}^\infty (\gamma \lambda)^l \delta_{t+l}^V = \delta_t^V + (\gamma \lambda) \hat{A}_{t+1}^{\text{GAE}}$$

Where $\lambda \in [0, 1]$ balances bias versus variance (typically $\lambda = 0.95$).

### Per-Token KL Penalty Reward Shaping

To prevent the Actor from exploiting flaws in the Reward Model, the final reward received at the terminal token $T$ is shaped by per-token KL penalties:

$$R_t = \begin{cases} -\beta \log \left( \frac{\pi_\theta(y_t \mid x, y_{<t})}{\pi_{\text{ref}}(y_t \mid x, y_{<t})} \right) & \text{for } t < T \\ R_{\text{Nemotron}}(x, y) - \beta \log \left( \frac{\pi_\theta(y_T \mid x, y_{<T})}{\pi_{\text{ref}}(y_T \mid x, y_{<T})} \right) & \text{for } t = T \end{cases}$$

---

## 5. Comparative Trade-Off Matrix: Alignment Methodologies

| Dimension | Supervised Fine-Tuning (SFT) | Direct Preference Opt (DPO) | Distributed PPO (RLHF) |
| :--- | :--- | :--- | :--- |
| **Exploration Mode** | Passive (Fixed target tokens) | Passive (Pre-computed pairs)| **Active (Generates novel rollouts)**|
| **Multi-Step Reasoning**| Weak (Cannot reward reasoning path)| Moderate | **Strong (Rewards terminal verification)**|
| **Computational Overhead**| Low (1x compute) | Moderate (2x forward passes)| **High (4-model orchestration)**|
| **VRAM Footprint** | Smallest | Moderate | **Largest (Actor, Critic, Ref, Reward)**|
| **DGX Spark Deployment** | Standard SFT | Native | **Requires FP8 Quantization for Ref/Rew**|

---

## 6. Concrete Production Hands-On Lab: Complete PPO Step & GAE Calculator

This self-contained Python script implements:
1. Per-token KL reward shaping combining terminal reward and reference log-odds.
2. Generalized Advantage Estimation (GAE) recursive calculation.
3. PPO clipped policy gradient loss computation.
4. Critic value MSE loss.

```python
#!/usr/bin/env python3
"""
NVIDIA NeMo PPO & Generalized Advantage Estimation (GAE) Simulator.
Demonstrates per-token reward shaping, GAE recursion, and clipped surrogate loss.
"""

import math
import torch
import torch.nn.functional as F
from typing import List, Tuple

# =====================================================================
# 1. GAE (GENERALIZED ADVANTAGE ESTIMATION) CALCULATOR
# =====================================================================

class GAECalculator:
    @staticmethod
    def compute_gae(rewards: List[float], values: List[float], gamma: float = 1.0, lam: float = 0.95) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Computes Advantages and Returns using recursive GAE:
        delta_t = r_t + gamma * V(t+1) - V(t)
        A_t = delta_t + gamma * lambda * A(t+1)
        """
        T = len(rewards)
        advantages = [0.0] * T
        last_gae = 0.0

        # Append terminal bootstrap value 0.0
        v_next = 0.0

        for t in reversed(range(T)):
            delta = rewards[t] + gamma * v_next - values[t]
            advantages[t] = delta + gamma * lam * last_gae
            last_gae = advantages[t]
            v_next = values[t]

        adv_tensor = torch.tensor(advantages, dtype=torch.float32)
        returns = adv_tensor + torch.tensor(values, dtype=torch.float32)

        # Normalize advantages across batch
        adv_normalized = (adv_tensor - adv_tensor.mean()) / (adv_tensor.std() + 1e-8)
        return adv_normalized, returns

# =====================================================================
# 2. PPO CLIPPED SURROGATE LOSS ENGINE
# =====================================================================

class PPOLossEngine:
    def __init__(self, clip_eps: float = 0.2, c_val: float = 0.5):
        self.clip_eps = clip_eps
        self.c_val = c_val

    def compute_policy_loss(self, logp_new: torch.Tensor, logp_old: torch.Tensor, advantages: torch.Tensor) -> torch.Tensor:
        """
        L^CLIP = -min( r * A, clip(r, 1-eps, 1+eps) * A )
        """
        ratio = torch.exp(logp_new - logp_old)
        surr1 = ratio * advantages
        surr2 = torch.clamp(ratio, 1.0 - self.clip_eps, 1.0 + self.clip_eps) * advantages
        policy_loss = -torch.min(surr1, surr2).mean()
        return policy_loss

    def compute_critic_loss(self, values: torch.Tensor, returns: torch.Tensor) -> torch.Tensor:
        """Value function MSE loss: L^VF = (V - Returns)^2"""
        return 0.5 * F.mse_loss(values, returns)

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

def run_ppo_lab():
    print("=" * 80)
    print("NVIDIA NEMO PPO & GAE ALIGNMENT SIMULATION LAB")
    print("=" * 80)

    # Simulated 5-token rollout
    seq_len = 5
    terminal_nemotron_reward = 3.8 # High-quality HelpSteer2 rating
    beta_kl = 0.05

    # Simulated logp ratios between policy and reference
    # Suppose policy drifted slightly on tokens 1, 2, 3
    pi_logps = [-1.2, -0.8, -1.5, -0.9, -0.4]
    ref_logps = [-1.1, -0.8, -1.2, -0.9, -0.4]

    # Step 1: Shape per-token rewards
    shaped_rewards = []
    for t in range(seq_len):
        kl = pi_logps[t] - ref_logps[t]
        r_t = -beta_kl * kl
        if t == seq_len - 1:
            r_t += terminal_nemotron_reward # Add terminal score at end
        shaped_rewards.append(r_t)

    print(f"Terminal Nemotron Reward Score: {terminal_nemotron_reward}")
    print(f"Per-Token Shaped Rewards (with KL penalty): {[round(x, 4) for x in shaped_rewards]}")

    # Step 2: Compute GAE with Critic Baseline Values
    critic_values = [2.0, 2.5, 2.8, 3.2, 3.5]
    advantages, returns = GAECalculator.compute_gae(shaped_rewards, critic_values)
    print(f"Computed Normalized Advantages (GAE): {[round(x, 4) for x in advantages.tolist()]}")
    print(f"Computed Target Returns: {[round(x, 4) for x in returns.tolist()]}")

    # Step 3: Compute PPO Loss Step
    ppo_engine = PPOLossEngine(clip_eps=0.2)
    # Simulate slightly updated policy logits
    pi_new_logps = torch.tensor([-1.18, -0.79, -1.45, -0.88, -0.38], requires_grad=True)
    pi_old_logps = torch.tensor(pi_logps)

    policy_loss = ppo_engine.compute_policy_loss(pi_new_logps, pi_old_logps, advantages)
    critic_loss = ppo_engine.compute_critic_loss(torch.tensor(critic_values, requires_grad=True), returns)

    print(f"\nPPO Clipped Policy Loss: {policy_loss.item():.4f}")
    print(f"Critic Value MSE Loss  : {critic_loss.item():.4f}")

    # Backward pass verification
    policy_loss.backward()
    print("Policy autograd backward pass verified successfully.")

    print("\n[SUCCESS] PPO rollout reward shaping, GAE recursion, and clipping verified.")

if __name__ == "__main__":
    run_ppo_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Deploying a 4-Model PPO topology on a single DGX Spark node:
1. **Memory Budget Allocation for 8B Model**:
   - **Actor ($\pi_\theta$)**: Trainable BF16 $\to \approx 16\text{ GB}$.
   - **Critic ($V_\phi$)**: Trainable BF16 $\to \approx 16\text{ GB}$.
   - **Reference ($\pi_{\text{ref}}$)**: Frozen FP8 $\to \approx 8\text{ GB}$.
   - **Reward ($R_\psi$)**: Frozen FP8 $\to \approx 8\text{ GB}$.
   - **Optimizer States (AdamW on Actor + Critic)**: $\approx 64\text{ GB}$.
   - **Total VRAM Footprint**: $16 + 16 + 8 + 8 + 64 = 112\text{ GB}$.
   - **Fits entirely on a single DGX Spark node** (128 GB Unified Memory) without multi-node clustering.

2. **NeMo Ray PPO Orchestrator**:
   ```bash
   python -m nemo.collections.nlp.models.language_modeling.megatron_gpt_ppo \
     --config-path=/workspace/nemo_configs \
     --config-name=megatron_gpt_ppo \
     model.ppo.clip_eps=0.2 \
     model.ppo.gamma=1.0 \
     model.ppo.lam=0.95 \
     trainer.devices=1
   ```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (GAE Degeneration Boundary)**:
   Prove that when $\lambda = 0$, GAE reduces to the pure 1-step TD advantage: $\hat{A}_t = \delta_t^V$. Prove that when $\lambda = 1$, GAE reduces to the empirical Monte Carlo return minus baseline: $\hat{A}_t = \sum_{l=0}^\infty \gamma^l R_{t+l} - V(s_t)$.

2. **Exercise 2 (Critic Clipping)**:
   Implement PPO value function clipping: $\mathcal{L}_{\text{val}} = \max\left( (V - \text{Returns})^2, \; (\text{clip}(V, V_{\text{old}} - \epsilon, V_{\text{old}} + \epsilon) - \text{Returns})^2 \right)$.

### Solutions

**Solution for Exercise 1**:
From the recursive definition: $\hat{A}_t = \sum_{l=0}^\infty (\gamma \lambda)^l \delta_{t+l}^V$.
- If $\lambda = 0$: $0^0 = 1$ for $l=0$, and $0^l = 0$ for $l \ge 1$. Hence $\hat{A}_t = \delta_t^V$ (Pure 1-step TD).
- If $\lambda = 1$: $\hat{A}_t = \sum_{l=0}^\infty \gamma^l \left( R_{t+l} + \gamma V(s_{t+l+1}) - V(s_{t+l}) \right)$. Expanding the telescoping sum cancels all intermediate $V(s)$ terms, leaving: $\hat{A}_t = \left( \sum_{l=0}^\infty \gamma^l R_{t+l} \right) - V(s_t)$ (Monte Carlo advantage).

### Troubleshooting FAQ

- **Q: Actor policy collapses into repeating spaces or periods during PPO.**
  - *Fix*: The KL penalty weight $\beta$ was set too low, allowing the policy to exploit an unconstrained reward vulnerability. Increase $\beta$ from $0.01$ to $0.05$ or $0.10$ to keep the Actor tethered to the Reference Model.

- **Q: Critic loss explodes to $10^5$ during training.**
  - *Fix*: Value returns are un-normalized. Ensure rewards are standardized or normalized, and initialize the Critic output projection head weights to near-zero ($10^{-3}$) so initial value predictions start at zero.
