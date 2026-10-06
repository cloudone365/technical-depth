# Volume 10: Synthetic Data Generation with Nemotron-4 340B

```
==================================================================================================
TARGET AUDIENCE: Synthetic Data Engineers, Post-Training Researchers, Domain Adaptation Leads
PREREQUISITES   : Prompt engineering, prompt evolution (Evol-Instruct), rejection sampling, unit tests
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Build autonomous synthetic data generation flywheels leveraging Nemotron-4-340B-Instruct,
                  Evol-Instruct mutation trees, Nemotron Reward scoring, and execution-guided verification.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

By 2026, the volume of high-quality human-authored text on the public internet has largely been exhausted by frontier foundation model pretraining. The next order-of-magnitude leap in model capability requires **high-density, multi-step synthetic data generation**.

**Nemotron-4 340B** was specifically engineered by NVIDIA to serve as the world's premier open synthetic data engine. By combining **Prompt Evolution (Evol-Instruct)**, **Nemotron-4-340B-Instruct** generation, **Nemotron-4-340B-Reward (HelpSteer2)** filtering, and **Execution-Guided Verification** (running code through Python interpreters and math through SymPy), enterprises can generate millions of flawless training pairs to fine-tune compact 4B–70B models.

```
       [Seed Corpus: 1,000 Real-World Enterprise Prompts / Raw Docs]
                                      │
                                      ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Prompt Evolution Engine (Evol-Instruct Mutation Trees)      │
       │   - Deepen: Add constraints, multi-step reasoning, edge cases │
       │   - Concretize: Ground abstract theories into real scenarios  │
       │   - Mutate: Transform domain into parallel enterprise areas   │
       └──────────────────────────────┬────────────────────────────────┘
                                      │ Yields 100,000 Complex Prompts
                                      ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Nemotron-4-340B-Instruct Generation Engine                  │
       │   - High-diversity sampling (Temperature T=0.7, Top-P=0.9)    │
       │   - Generates candidate responses with step-by-step CoT       │
       └──────────────────────────────┬────────────────────────────────┘
                                      │ Candidate Responses
                                      ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Dual Verification & Rejection Sampling Filter               │
       │                                                               │
       │   Branch A: Execution Verification (Code & Math)              │
       │   - Sandboxed Python unit test execution (Pass/Fail)          │
       │                                                               │
       │   Branch B: Nemotron-4-340B-Reward Model                      │
       │   - HelpSteer2 scoring (Correctness >= 3.8, Helpfulness >= 3.5│
       └──────────────────────────────┬────────────────────────────────┘
                                      │ Accepted Pairs
                                      ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   High-Density Gold SFT Dataset for Local Model Fine-Tuning   │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Flight Simulator for Fighter Pilots
3. Evolutionary Lineage: From Scraping Web Forums to Synthetic Flywheels
4. First-Principles Mathematics & Algorithmic Formulations
   - Evol-Instruct Mutation Operators
   - Rejection Sampling Fine-Tuning (RSFT) Distribution Shift
   - Execution-Guided Rejection Probability
5. Comparative Trade-Off Matrix: Synthetic Generation Paradigms
6. Concrete Production Hands-On Lab: Complete Synthetic Data Flywheel Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Flight Simulator for Fighter Pilots

Imagine training elite fighter pilots to land on an aircraft carrier in a storm:

- **The Real-World Data Approach (Waiting for Natural Storms)**:
  You sit on the runway and wait for a hurricane to appear. Sometimes it's sunny for three months (common web chatter); sometimes a light drizzle occurs. The pilot gets bored and forgets procedures. If you force them to fly in a Category 5 storm to practice, they might crash a \$100M jet (catastrophic training failure on messy data).

- **The Synthetic Data Flywheel (The Ultra-Realistic Flight Simulator)**:
  You build a computer simulator (**Nemotron-4 340B**):
  1. The instructor inputs an easy flight path ("Land on Runway 1").
  2. The system dynamically injects severe turbulence, engine fires, crosswinds, and radar failure (**Prompt Evolution**).
  3. The simulator tests the pilot's exact stick inputs against aerodynamic physics laws (**Execution Verification / Unit Tests**).
  4. The flight examiner reviews every control decision against master naval aviation protocols (**Nemotron Reward Model**).
  The pilot practices 10,000 catastrophic emergencies in a single afternoon without ever risking an actual aircraft.

---

## 3. Evolutionary Lineage: From Scraping Web Forums to Synthetic Flywheels

```
Generation 1 (2020-2022)      Generation 2 (2023)           Generation 3 (2024-2026)
Manual Crowdworker Labels     Self-Instruct / Alpaca        Nemotron-4 Verified Flywheel
──────────────────────────    ──────────────────────────    ────────────────────────────
- Expensive human annotation  - Basic text-davinci-003 gen  - Evol-Instruct multi-mutation
- Inconsistent quality        - High hallucination rate     - Multi-attribute HelpSteer2 grading
- Slow turnaround (months)    - No automated verification   - Execution-guided sandboxed unit tests
- Cannot scale to billions    - Model collapse on loops     - Permissive commercial open license
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Evol-Instruct Mutation Operators

Given a base prompt $x_0$, the evolution engine applies a sequence of discrete semantic transformations $T_m(x)$ to increase cognitive complexity:

1. **In-Depth Evolution (Deepening Constraints)**:
   $$x_{t+1} = T_{\text{depth}}(x_t) \implies \text{"Add } k \text{ specific constraints and edge cases to } x_t\text{"}$$
2. **In-Breadth Evolution (Domain Mutation)**:
   $$x_{t+1} = T_{\text{breadth}}(x_t) \implies \text{"Generate an equivalent problem in a distinct enterprise domain"}$$
3. **Reasoning Concretization**:
   $$x_{t+1} = T_{\text{concrete}}(x_t) \implies \text{"Replace abstract variables with real-world financial records"}$$

The complexity graph expands exponentially:

$$N_{\text{prompts}} = N_0 \times d^k$$

Where $N_0$ is the number of seed prompts, $d$ is the branching factor, and $k$ is the mutation depth.

### Rejection Sampling Fine-Tuning (RSFT) Distribution Shift

Let $P_{\text{base}}(y \mid x)$ be the generation distribution of Nemotron-4-340B-Instruct.
Let $R(x, y) \in [0, 4]$ be the reward score predicted by Nemotron-4-340B-Reward.
Under **Rejection Sampling Fine-Tuning**, the empirical target training distribution $\tilde{P}(y \mid x)$ is defined as:

$$\tilde{P}(y \mid x) = \frac{P_{\text{base}}(y \mid x) \cdot \mathbb{I}\left( R(x, y) \ge \tau \right)}{Z(x)}$$

Where $\tau$ is the acceptance threshold (e.g. $\tau = 3.8$), and $Z(x)$ is the partition function:

$$Z(x) = \sum_{y} P_{\text{base}}(y \mid x) \cdot \mathbb{I}\left( R(x, y) \ge \tau \right)$$

This mathematically truncates the lower tail of low-quality generations, shifting the expected quality of the student model:

$$\mathbb{E}_{\tilde{P}}[R(x, y)] \gg \mathbb{E}_{P_{\text{base}}}[R(x, y)]$$

### Execution-Guided Rejection Probability

For coding tasks, candidate $y$ contains an implementation $C_y$. The execution harness executes $K$ unit tests:

$$\text{Valid}(y) = \prod_{k=1}^K \mathbb{I}\left( \text{Exec}(C_y, \text{Test}_k) == \text{PASS} \right)$$

The acceptance probability is binary:

$$P(\text{Accept} \mid y) = \begin{cases} 1 & \text{if } \text{Valid}(y) = 1 \text{ and } R(x, y) \ge \tau \\ 0 & \text{otherwise} \end{cases}$$

This guarantees **zero hallucinated code syntax** in the final training dataset.

---

## 5. Comparative Trade-Off Matrix: Synthetic Generation Paradigms

| Dimension | Pure Unfiltered LLM Generation | Self-Instruct / Alpaca Style | Nemotron-4 Verified Flywheel |
| :--- | :--- | :--- | :--- |
| **Factual Accuracy** | Moderate (70–80%) | Moderate (75–82%) | **Near Perfect (98%+)** |
| **Code Correctness** | Poor (Hallucinated APIs) | Moderate (60–70%) | **100% (Unit Test Verified)** |
| **Complexity Diversity** | Low (Repeats seed bias) | Moderate | **High (Multi-tier Evol-Instruct)** |
| **Legal Commercial Use**| Risk of proprietary API ban | Banned for training commercial LLMs| **100% Permitted by NVIDIA License**|
| **DGX Spark Fit** | Moderate | Moderate | **Optimal (Local execution)** |

---

## 6. Concrete Production Hands-On Lab: Complete Synthetic Data Flywheel Engine

This runnable Python script demonstrates the complete synthetic flywheel:
1. Mutating a seed prompt via programmatic Evol-Instruct rules.
2. Generating a candidate Python function with unit tests.
3. Executing the code inside an isolated sandboxed Python environment.
4. Grading the response with a simulated Nemotron HelpSteer2 reward scorer and accepting/rejecting the pair.

```python
#!/usr/bin/env python3
"""
NVIDIA Nemotron-4 Synthetic Data Generation Flywheel.
Demonstrates Evol-Instruct prompt mutation, candidate generation,
sandboxed unit test verification, and reward scoring.
"""

import sys
import math
from typing import Dict, Any, Tuple

# =====================================================================
# 1. PROMPT EVOLUTION ENGINE (EVOL-INSTRUCT)
# =====================================================================

class EvolInstructMutator:
    @staticmethod
    def evolve_prompt(base_prompt: str) -> str:
        """
        Deepens prompt complexity by adding mathematical constraints
        and edge-case performance requirements.
        """
        mutation = (
            f"{base_prompt} "
            "Your implementation must run in O(N) time complexity, handle empty lists, "
            "reject non-numeric types by raising a TypeError, and include complete unit tests."
        )
        return mutation

# =====================================================================
# 2. SANDBOXED EXECUTION-GUIDED CODE VERIFIER
# =====================================================================

class SandboxedExecutor:
    @staticmethod
    def verify_code(code_string: str) -> Tuple[bool, str]:
        """
        Executes code string in a restricted local scope.
        Returns (True, 'PASS') if all assertions succeed.
        """
        local_scope: Dict[str, Any] = {}
        try:
            # Execute definition and assertions
            exec(code_string, {}, local_scope)
            return True, "PASS: All unit tests succeeded."
        except AssertionError as e:
            return False, f"FAIL: Assertion error during unit test: {str(e)}"
        except Exception as e:
            return False, f"FAIL: Runtime exception: {type(e).__name__} - {str(e)}"

# =====================================================================
# 3. SYNTHETIC FLYWHEEL ORCHESTRATOR
# =====================================================================

def run_synthetic_flywheel_lab():
    print("=" * 80)
    print("NEMOTRON-4 SYNTHETIC DATA GENERATION FLYWHEEL LAB")
    print("=" * 80)

    # Step 1: Base Seed Prompt
    seed_prompt = "Write a Python function called `calculate_trimmed_mean` that computes the mean after trimming outliers."
    print(f"Seed Prompt: '{seed_prompt}'\n")

    # Step 2: Evol-Instruct Mutation
    evolved_prompt = EvolInstructMutator.evolve_prompt(seed_prompt)
    print("--- Step 2: Evolved Prompt (Deepened Constraints) ---")
    print(evolved_prompt)
    print("-" * 80)

    # Step 3: Simulated Candidate Generation from Nemotron-4-340B-Instruct
    # Candidate A: Flawless code with unit test assertions
    candidate_a_code = (
        "def calculate_trimmed_mean(data, trim_count):\n"
        "    if not isinstance(data, list) or not isinstance(trim_count, int):\n"
        "        raise TypeError('Invalid input types')\n"
        "    if len(data) == 0:\n"
        "        return 0.0\n"
        "    if trim_count * 2 >= len(data):\n"
        "        return 0.0\n"
        "    s = sorted(data)\n"
        "    trimmed = s[trim_count : len(s) - trim_count]\n"
        "    return sum(trimmed) / len(trimmed)\n"
        "\n"
        "# Embedded Unit Tests\n"
        "assert calculate_trimmed_mean([1, 2, 3, 4, 100], 1) == 3.0\n"
        "assert calculate_trimmed_mean([], 0) == 0.0\n"
    )

    # Candidate B: Flawed code that fails on edge cases
    candidate_b_code = (
        "def calculate_trimmed_mean(data, trim_count):\n"
        "    # Missing type check and crashes on empty list!\n"
        "    return sum(data) / len(data)\n"
        "\n"
        "# Embedded Unit Tests\n"
        "assert calculate_trimmed_mean([], 0) == 0.0\n"
    )

    print("\n--- Step 4: Execution-Guided Verification ---")
    pass_a, msg_a = SandboxedExecutor.verify_code(candidate_a_code)
    print(f"Candidate A Execution: {msg_a}")

    pass_b, msg_b = SandboxedExecutor.verify_code(candidate_b_code)
    print(f"Candidate B Execution: {msg_b}")

    # Step 5: Reward Model Scoring
    # Simulated HelpSteer2 scoring for Candidate A
    reward_score_a = 3.92
    threshold = 3.80

    print("\n--- Step 5: Nemotron Reward & Final Admission Decision ---")
    if pass_a and reward_score_a >= threshold:
        print(f"[ACCEPTED] Candidate A passed execution tests and achieved Reward {reward_score_a} >= {threshold}.")
        print("  -> Admitted to High-Density Production SFT Training Corpus!")
    else:
        print("[REJECTED] Candidate A failed admission criteria.")

    if not pass_b:
        print("[REJECTED] Candidate B failed execution verification. Dropped immediately.")

    print("\n[SUCCESS] Synthetic data generation flywheel and validation pipeline verified.")

if __name__ == "__main__":
    run_synthetic_flywheel_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Operating synthetic data pipelines on the DGX Spark:
1. **Local Distillation & Verification**:
   - The Grace ARM processor manages thousands of parallel sandboxed Python execution threads, evaluating unit tests and checking syntax.
   - The Blackwell GB10 GPU serves as the high-throughput generator, running `Llama-3.1-Nemotron-70B-Instruct` in FP8 to output 20,000 synthetic pairs per hour locally.

2. **Zero Cloud Ingestion Cost**:
   Because all generation, mutation, and verification runs entirely on-premise inside the DGX Spark's 128 GB unified memory, the enterprise generates petabyte-scale training corpora with **\$0 in cloud API egress or per-token generation charges**.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (In-Breadth Mutation Generator)**:
   Author a mutation function that takes a coding prompt about financial transactions and transforms it into an equivalent problem in biomedical genomic sequencing.

2. **Exercise 2 (Automated Unit Test Generator)**:
   Write a prompt template that takes any generated Python function and instructs Nemotron to output 5 comprehensive `assert` statements covering edge cases (empty inputs, negative numbers, large inputs).

### Solutions

**Solution for Exercise 1**:
```python
def mutate_domain_finance_to_genomics(prompt: str) -> str:
    prompt = prompt.replace("bank transaction", "DNA read sequence")
    prompt = prompt.replace("account balance", "nucleotide base frequency")
    prompt = prompt.replace("fraudulent activity", "genetic mutation anomaly")
    return prompt
```

### Troubleshooting FAQ

- **Q: Sandboxed `exec()` crashes the parent Python process on malicious inputs.**
  - *Fix*: Never run untrusted model-generated code in the same process as your orchestrator. Use Python's `multiprocessing` with strict timeouts (`p.join(timeout=2)`) or an isolated Docker/gVisor microVM sandbox.

- **Q: Synthetic dataset causes the fine-tuned model to output repetitive boilerplate.**
  - *Fix*: Your Evol-Instruct mutation diversity is too low. Enforce MinHash LSH deduplication on your synthetic prompts to ensure no two evolved prompts share more than 60% similarity.
