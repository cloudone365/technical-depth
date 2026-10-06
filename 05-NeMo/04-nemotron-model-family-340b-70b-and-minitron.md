# Volume 04: Nemotron Model Family: 340B, 70B, and Minitron

```
==================================================================================================
TARGET AUDIENCE: Lead Model Researchers, Enterprise AI Architects, Alignment Engineers
PREREQUISITES   : Transformer architectural scaling, reward modeling, Bradley-Terry formulations
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master the Nemotron architectural lineage: Nemotron-4 340B (Base, Instruct, Reward),
                  Llama-3.1-Nemotron-70B, and compact Minitron distilled models.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

To empower enterprises to build, customize, and generate synthetic data without proprietary API lock-in, NVIDIA engineered the **Nemotron foundation model family**. Headlined by the colossal **Nemotron-4 340B** triad, the high-efficiency **Llama-3.1-Nemotron-70B**, and the edge-ready **Minitron** compressed family, Nemotron represents a complete enterprise ecosystem for pretraining, alignment, and synthetic flywheel generation.

Unlike closed frontier models, Nemotron-4 models are distributed under a permissive commercial license that explicitly permits using model outputs to train and align smaller enterprise models.

```
                           ┌─────────────────────────────────────────┐
                           │      Nemotron-4 340B Base Model         │
                           │   (9 Trillion Multilingual Tokens)      │
                           └────────────────────┬────────────────────┘
                                                │ Supervised Fine-Tuning
                                                ▼
       ┌────────────────────────────────────────────────────────────────────────┐
       │                 Nemotron Synthetic Data & Alignment Engine             │
       │                                                                        │
       │   ┌───────────────────────────┐        ┌───────────────────────────┐   │
       │   │  Nemotron-4-340B-Instruct │        │  Nemotron-4-340B-Reward   │   │
       │   │  High-Density Prompts &   │ ─────► │  Multi-Attribute Quality  │   │
       │   │  Synthetic Completions    │        │  Scoring (HelpSteer2)     │   │
       │   └───────────────────────────┘        └─────────────┬─────────────┘   │
       └──────────────────────────────────────────────────────┼─────────────────┘
                                                              │ Guided RLHF / DPO
                                                              ▼
       ┌───────────────────────────────┐        ┌───────────────────────────────┐
       │   Llama-3.1-Nemotron-70B      │        │  Minitron 8B -> 4B Distilled  │
       │  (Beats GPT-4o on Arena-Hard) │        │  (Edge & Local Laptop Deploy) │
       └───────────────────────────────┘        └───────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Master University Professor & The Apprentice
3. Evolutionary Lineage: From Megatron-Turing 530B to Nemotron-4 340B
4. First-Principles Mathematics & Algorithmic Formulations
   - Nemotron-4 340B Architectural Scaling Dimensions
   - Multi-Attribute Reward Modeling (HelpSteer2 Formulation)
   - Bradley-Terry Preference Probability Calculus
   - Synthetic Data Rejection Sampling Quality Filter
5. Comparative Trade-Off Matrix: Nemotron Model Family
6. Concrete Production Hands-On Lab: Nemotron Multi-Attribute Reward Scorer
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Master University Professor & The Apprentice

Imagine an enterprise needing to train 100 specialized junior apprentices (domain-specific 4B–8B models) to run factory operations:

- **The Proprietary Cloud API Trap**:
  You hire an expensive foreign consultant (proprietary commercial LLM API). The consultant gives great answers, but their contract states: *"You may not record my answers, and you may not use my explanations to train your apprentices."* You are trapped paying hourly consulting fees forever.

- **The Nemotron-4 340B Ecosystem (The Master University Professor)**:
  NVIDIA provides a world-class academic master professor (**Nemotron-4 340B**) who moves permanently into your corporate campus:
  1. **The Lecturer (`Nemotron-4-340B-Instruct`)**: Writes thousands of complex test questions and exemplary step-by-step textbook solutions.
  2. **The Grader (`Nemotron-4-340B-Reward`)**: Evaluates every generated answer across 5 distinct report card metrics: Helpfulness, Correctness, Coherence, Complexity, and Verbosity.
  3. **The Graduation Process**: Only the highest-scoring materials are fed to your local apprentices (**Minitron-4B** and **Nemotron-70B**). Your apprentices achieve master-level performance on your local DGX hardware with zero cloud data egress.

---

## 3. Evolutionary Lineage: From Megatron-Turing 530B to Nemotron-4 340B

```
Generation 1 (2021)           Generation 2 (2023)           Generation 3 (2024-2026)
Megatron-Turing MT-NLG 530B   Nemotron-3 8B / 22B           Nemotron-4 340B & Minitron
──────────────────────────    ──────────────────────────    ──────────────────────────
- Dense 530B parameter giant  - Smaller parameter sizes     - Triad: Base, Instruct, Reward
- Proof-of-concept Megatron   - Standard Chat tuning        - HelpSteer2 multi-attribute RLHF
- Inflexible research license - High compute cost           - Llama-3.1-Nemotron-70B SOTA
- High latency, no synthetic  - No multi-attribute reward   - Commercial synthetic data license
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Nemotron-4 340B Architectural Scaling Dimensions

Nemotron-4 340B is configured as a dense autoregressive Transformer engineered for optimal Megatron 3D parallel factorization:

| Hyperparameter Symbol | Architectural Specification | Value in Nemotron-4 340B |
| :--- | :--- | :--- |
| $d_{\text{model}}$ | Hidden Model Dimension | **18,432** |
| $N_{\text{layers}}$ | Transformer Layer Count | **96 Layers** |
| $N_{\text{heads}}$ | Query Attention Heads | **96 Heads** ($d_{\text{head}} = 192$) |
| $N_{\text{kv\_heads}}$| Grouped-Query Key/Value Heads | **8 Heads** (12:1 GQA Compression) |
| $d_{\text{ffn}}$ | SwiGLU Intermediate Dimension | **73,728** ($4 \times d_{\text{model}}$) |
| $|\mathcal{V}|$ | Tokenizer Vocabulary Size | **256,000 Tokens** |
| $L_{\text{ctx}}$ | Native Context Window | **4,096 Tokens** |

The parameter count calculation:

$$N_{\text{params}} \approx 96 \times \left( 4 d_{\text{model}}^2 + 3 d_{\text{model}} d_{\text{ffn}} \right) + 2 |\mathcal{V}| d_{\text{model}} \approx 340 \times 10^9$$

### Multi-Attribute Reward Modeling (HelpSteer2 Formulation)

Standard reward models collapse evaluation into a single scalar $r \in \mathbb{R}$. Nemotron-4-340B-Reward trains on the **HelpSteer2** dataset, predicting an explicit 5-dimensional vector of human preferences:

$$\mathbf{r}(x, y) = \begin{bmatrix} r_{\text{helpfulness}} \\ r_{\text{correctness}} \\ r_{\text{coherence}} \\ r_{\text{complexity}} \\ r_{\text{verbosity}} \end{bmatrix} \in [0, 4]^5$$

Given prompt $x$ and completion $y$, the overall scalar reward for RLHF is a linear combination parameterized by enterprise steering weights $\mathbf{w}$:

$$R(x, y) = \mathbf{w}^\top \mathbf{r}(x, y) = \sum_{k=1}^5 w_k \cdot r_k(x, y)$$

Where standard helpfulness optimization sets:
$\mathbf{w} = [0.65, \; 0.80, \; 0.45, \; 0.10, \; -0.15]^\top$, actively penalizing empty verbosity while rewarding factual correctness.

### Bradley-Terry Preference Probability Calculus

Given two candidate responses $y_1$ and $y_2$ for prompt $x$, the probability that $y_1$ is preferred over $y_2$ is governed by the Bradley-Terry logistic distribution:

$$P(y_1 \succ y_2 \mid x) = \sigma\left( R(x, y_1) - R(x, y_2) \right) = \frac{1}{1 + \exp\left( -(R(x, y_1) - R(x, y_2)) \right)}$$

---

## 5. Comparative Trade-Off Matrix: Nemotron Model Family

| Model Name | Parameter Count | VRAM (BF16) | VRAM (FP8) | Primary Production Use Case | Recommended Hardware |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Nemotron-4 340B (Base/Inst)**| 340 Billion | 680 GB | 340 GB | High-Quality Synthetic Data Generation | Multi-Node H100/Blackwell |
| **Nemotron-4 340B-Reward**| 340 Billion | 680 GB | 340 GB | Multi-Attribute Data Filtering & RLHF | Multi-Node H100/Blackwell |
| **Llama-3.1-Nemotron-70B** | 70 Billion | 140 GB | 70 GB | SOTA Production Reasoning & Coding | **Single DGX Spark (FP8 / AWQ)** |
| **Nemotron-Mini 4B / Minitron**| 4 Billion | 8 GB | 4 GB | Edge Inference, Low-Latency SRE Agents| Any Laptop / Single GPU |

---

## 6. Concrete Production Hands-On Lab: Nemotron Multi-Attribute Reward Scorer

This self-contained Python script implements the mathematical mechanics of the Nemotron-4-Reward / HelpSteer2 architecture, computing multi-attribute quality vectors and Bradley-Terry win probabilities.

```python
#!/usr/bin/env python3
"""
Nemotron-4 Multi-Attribute Reward Model & Preference Engine.
Demonstrates HelpSteer2 5-vector attribute scoring, weighted composite scalar rewards,
and Bradley-Terry win probability calculation.
"""

import math
from typing import Dict, List, Tuple

# =====================================================================
# 1. HELPSTEER2 MULTI-ATTRIBUTE REWARD SPECIFICATION
# =====================================================================

class HelpSteer2Scorer:
    """
    Simulates Nemotron-4-340B-Reward scoring over 5 continuous dimensions in [0, 4].
    """
    ATTRIBUTES = ["helpfulness", "correctness", "coherence", "complexity", "verbosity"]

    def __init__(self, custom_weights: Dict[str, float] = None):
        # Default enterprise preference weighting
        self.weights = custom_weights or {
            "helpfulness": 0.65,
            "correctness": 0.85,
            "coherence": 0.50,
            "complexity": 0.15,
            "verbosity": -0.20  # Negative weight penalizes unhelpful bloat
        }

    def compute_composite_reward(self, attribute_scores: Dict[str, float]) -> float:
        """Computes linear scalar reward: R = sum(w_k * r_k)."""
        scalar = 0.0
        for attr, weight in self.weights.items():
            score = attribute_scores.get(attr, 0.0)
            scalar += weight * score
        return scalar

    @staticmethod
    def bradley_terry_probability(reward_a: float, reward_b: float) -> float:
        """P(A > B) = 1 / (1 + exp(-(R_A - R_B)))"""
        margin = reward_a - reward_b
        return 1.0 / (1.0 + math.exp(-margin))

# =====================================================================
# 2. SYNTHETIC CANDIDATE EVALUATION PIPELINE
# =====================================================================

def run_reward_model_lab():
    print("=" * 80)
    print("NEMOTRON-4 MULTI-ATTRIBUTE REWARD & PREFERENCE LAB")
    print("=" * 80)

    scorer = HelpSteer2Scorer()

    # Candidate A: Concise, highly accurate, mathematically rigorous response
    candidate_a_scores = {
        "helpfulness": 3.8,
        "correctness": 3.9,
        "coherence": 3.9,
        "complexity": 3.2,
        "verbosity": 1.5  # Concise
    }

    # Candidate B: Verbose, hallucinated details, polite but repetitive filler
    candidate_b_scores = {
        "helpfulness": 2.1,
        "correctness": 1.2,
        "coherence": 3.5,
        "complexity": 1.8,
        "verbosity": 3.9  # Excessively verbose
    }

    reward_a = scorer.compute_composite_reward(candidate_a_scores)
    reward_b = scorer.compute_composite_reward(candidate_b_scores)

    prob_a_wins = scorer.bradley_terry_probability(reward_a, reward_b)

    print("Candidate A (Concise & Correct):")
    print(f"  -> Attributes: {candidate_a_scores}")
    print(f"  -> Composite Scalar Reward: {reward_a:.4f}\n")

    print("Candidate B (Verbose & Inaccurate):")
    print(f"  -> Attributes: {candidate_b_scores}")
    print(f"  -> Composite Scalar Reward: {reward_b:.4f}\n")

    print("--- Preference Evaluation ---")
    print(f"Bradley-Terry Probability P(Candidate A > Candidate B): {prob_a_wins * 100:.2f}%\n")

    assert prob_a_wins > 0.90, "Candidate A should overwhelmingly defeat Candidate B!"
    print("[SUCCESS] Nemotron multi-attribute reward calculation and preference ranking verified.")

if __name__ == "__main__":
    run_reward_model_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Model sizing on the NVIDIA DGX Spark (128 GB Unified Memory):
1. **Llama-3.1-Nemotron-70B Execution**:
   - In **FP8 Precision**: Model weights consume $\approx 70\text{ GB}$.
   - Leaves $\approx 55\text{ GB}$ of unified memory for KV-cache (supporting over 64 concurrent streams at 8k context).
   - This makes the single-node DGX Spark a premier on-premise execution engine for enterprise-grade 70B reasoning.

2. **Nemotron-4 340B Downstream Distillation**:
   - Running the 340B model directly requires a cluster of 8x H100/Blackwell nodes.
   - On the DGX Spark, enterprises consume synthetic datasets generated by Nemotron-4 340B to train and align local **Nemotron-Mini 4B** or **Llama-Nemotron-70B** models with zero cloud fees.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Enterprise Weight Tuning)**:
   Suppose an enterprise customer wants Nemotron to write comprehensive, pedagogical documentation for elementary school students. Adjust the `HelpSteer2Scorer` attribute weights $\mathbf{w}$ to prioritize high verbosity and simple vocabulary (negative complexity).

2. **Exercise 2 (Rejection Sampling Threshold)**:
   Author a Python filter that accepts synthetic generations only if $r_{\text{correctness}} \ge 3.5$ and composite reward $R \ge 4.0$.

### Solutions

**Solution for Exercise 1**:
```python
educational_weights = {
    "helpfulness": 0.80,
    "correctness": 0.90,
    "coherence": 0.60,
    "complexity": -0.40, # Heavily penalize academic jargon
    "verbosity": 0.50    # Reward detailed step-by-step walkthroughs
}
```

### Troubleshooting FAQ

- **Q: Llama-3.1-Nemotron-70B generates repetitive apologies.**
  - *Fix*: The model's chat template requires specific Nemotron system prompt conditioning. Ensure you use the official HuggingFace / NeMo chat template and do not omit `<|im_start|>system\n...<|im_end|>`.

- **Q: Model generation hangs on DGX Spark when running in FP8.**
  - *Fix*: Verify that your vLLM or TensorRT-LLM container is built with Blackwell architecture support (`sm_100` or `sm_120`). Older CUDA 12.2 containers without Blackwell FP8 PTX instructions will trigger illegal instruction traps.
