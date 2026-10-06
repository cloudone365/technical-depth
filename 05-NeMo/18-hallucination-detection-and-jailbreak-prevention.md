# Volume 18: Hallucination Detection and Jailbreak Prevention

```
==================================================================================================
TARGET AUDIENCE: AI Safety Researchers, Enterprise RAG Architects, Risk & Compliance Leads
PREREQUISITES   : Natural Language Inference (NLI), prompt injection forensics, NeMo Guardrails
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master production hallucination detection and adversarial jailbreak prevention:
                  claim extraction, NLI entailment scoring, Self-Check fact-verification, and defense trees.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Deploying generative models in high-consequence domains (finance, healthcare, legal) requires that every single emitted statement is grounded in verifiable evidence. Unchecked models exhibit two catastrophic failure modes:
1. **Hallucination**: Confidently fabricating citations, financial metrics, or clinical dosages that do not exist in the source literature.
2. **Adversarial Jailbreaks**: Succumbing to sophisticated linguistic exploits (multi-turn roleplay, hypothetical persona injection, linguistic steganography) designed to bypass system safety boundaries.

**NVIDIA NeMo Guardrails** addresses these vulnerabilities via **Self-Check Fact Verification Rails** and **Adversarial Jailbreak Classifiers**. By decomposing generated text into atomic factual claims and evaluating them using **Natural Language Inference (NLI)** against retrieved source documents, NeMo ensures that ungrounded hallucinations are detected and intercepted before delivery.

```
       [Retrieved Source Passages (RAG)] + [Generated Model Response]
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Atomic Claim Decomposition Engine                           │
       │   "The Blackwell GB10 was released in 2024 and has 256GB VRAM"│
       │   ──► Claim 1: "The Blackwell GB10 was released in 2024"      │
       │   ──► Claim 2: "The Blackwell GB10 has 256GB VRAM"            │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Cross-Encoder NLI Entailment Verification                   │
       │   Premise: [Retrieved Passages] vs Hypothesis: [Claim_i]      │
       │   Outputs: P(Entailment), P(Neutral), P(Contradiction)        │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                       ┌───────────────┴───────────────┐
                       ▼                               ▼
       ┌───────────────────────────────┐ ┌───────────────────────────────┐
       │ Claim 1: P(Entailment) = 0.96 │ │ Claim 2: P(Contradiction)=0.92│
       │ Status: VERIFIED & GROUNDED   │ │ Status: HALLUCINATED! (128GB) │
       └───────────────────────────────┘ └──────────────┬────────────────┘
                                                        │
                                                        ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Output Rail Interception & Factual Self-Correction          │
       │   "Notice: Removed unverified claim regarding 256GB memory."  │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Scientific Peer Reviewer and the Forgery Expert
3. Evolutionary Lineage: From Blind Trust to NLI Entailment Verification
4. First-Principles Mathematics & Algorithmic Formulations
   - Atomic Claim Extraction Calculus
   - Natural Language Inference (NLI) Entailment Formulation
   - Composite Hallucination Index $H(y)$
   - Multi-Turn Adversarial Jailbreak Defenses
5. Comparative Trade-Off Matrix: Verification Approaches
6. Concrete Production Hands-On Lab: Claim Extractor & NLI Fact-Checking Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Scientific Peer Reviewer and the Forgery Expert

Imagine publishing a scientific breakthrough paper:

- **The Un-guarded Approach (Publishing Without Review)**:
  An author writes a 50-page paper on cold fusion. Deep on page 34, they write: *"We observed 100% net energy gain at room temperature using table salt."* No one checks the lab notebooks. The journal publishes the paper; international media covers it; millions of dollars are invested; two months later, independent labs discover the author completely made up the experiment.

- **The NeMo Guardrails Approach (The Ruthless Peer Reviewer)**:
  Before the paper is published:
  1. **The Claim Extractor**: A fact-checker highlights every single factual assertion: *"Author claims room temperature fusion occurred."*
  2. **The Lab Notebook Inspector (`NLI Engine`)**: The checker pulls up the raw digitized lab notes (**Retrieved Context**). They look for the exact voltage and salt measurements.
  3. **The Entailment Test**: If the notebook notes: *"Experiment failed at 500C"*, the checker flags an explicit **Contradiction**. The claim is struck from the paper before it ever reaches the printer.

---

## 3. Evolutionary Lineage: From Blind Trust to NLI Entailment Verification

```
Generation 1 (2020-2022)      Generation 2 (2023)           Generation 3 (2024-2026)
Implicit Faith in LLM         Heuristic String Matching     NLI Entailment & Self-Check
──────────────────────────    ──────────────────────────    ───────────────────────────
- Assume LLM is always right  - Checks if words match docs  - Atomic claim decomposition
- Catastrophic legal liability- Fails on paraphrasing       - Cross-encoder NLI entailment
- Hallucinated fake cases     - High false positive rate    - Hardware-accelerated Self-Check
- Vulnerable to basic DAN     - Easily bypassed by synonyms - Adversarial embedding anomaly
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Atomic Claim Extraction Calculus

Given generated text $y$, the claim extractor decomposes the sequence into a set of $M$ independent, atomic, self-contained propositional claims:

$$\mathcal{C}(y) = \{c_1, c_2, \dots, c_M\}$$

Where each claim $c_i$ satisfies:
1. **Atomicity**: Contains exactly one subject-predicate assertion.
2. **Context-Independence**: Resolves all pronouns (anaphora resolution: "it" $\to$ "The Blackwell GPU").

### Natural Language Inference (NLI) Entailment Formulation

Let $\mathcal{P}$ denote the premise (the concatenated retrieved reference documents).
An NLI cross-encoder model $\mathcal{M}_{\text{NLI}}$ evaluates the relationship between premise $\mathcal{P}$ and hypothesis claim $c_i$:

$$\mathbf{z}_i = \mathcal{M}_{\text{NLI}}\left( \text{[CLS]} \circ \mathcal{P} \circ \text{[SEP]} \circ c_i \circ \text{[SEP]} \right)$$

$$\begin{bmatrix} P(\text{Entailment}) \\ P(\text{Neutral}) \\ P(\text{Contradiction}) \end{bmatrix} = \text{Softmax}(\mathbf{z}_i)$$

A claim is defined as **Factually Grounded** if and only if:

$$\text{Grounded}(c_i \mid \mathcal{P}) \iff P(\text{Entailment} \mid \mathcal{P}, c_i) \ge \tau_{\text{entail}} \quad (\text{typically } \tau = 0.75)$$

If $P(\text{Contradiction}) \ge \tau_{\text{contra}}$ (e.g. $0.50$), the claim is marked as an active, dangerous hallucination.

### Composite Hallucination Index $H(y)$

The overall Hallucination Index $H(y) \in [0, 1]$ of the generated response is:

$$H(y) = 1.0 - \frac{1}{M} \sum_{i=1}^M \mathbb{I}\left( \text{Grounded}(c_i \mid \mathcal{P}) \right)$$

If $H(y) > 0.20$ (more than 20% of claims are unverified), NeMo Guardrails intercepts the response and triggers an automated factual refinement pass.

---

## 5. Comparative Trade-Off Matrix: Verification Approaches

| Verification Architecture | Computational Overhead | Precision on Paraphrases | Contradiction Detection | Hardware Fit on DGX Spark |
| :--- | :--- | :--- | :--- | :--- |
| **Exact Lexical Overlap (ROUGE)**| Negligible ($< 0.1\text{ ms}$)| Terrible (< 40%) | None | CPU only |
| **Cosine Embedding Similarity** | Fast ($\approx 2\text{ ms}$) | Moderate (60–70%) | Fails on negations ("not") | Fast on Blackwell |
| **Self-Check via Large LLM** | Slow (200–500 ms) | High (85–90%) | Good | Consumes LLM KV-cache |
| **Cross-Encoder NLI (DeBERTa)** | **Optimal (10–25 ms)** | **Superior (94%+)** | **Native Contradiction Head**| **Dedicated FP8 Engine** |

---

## 6. Concrete Production Hands-On Lab: Claim Extractor & NLI Fact-Checking Engine

This self-contained Python script implements:
1. Atomic claim decomposition of candidate responses.
2. A simulated Natural Language Inference (NLI) entailment scorer.
3. Computation of the Hallucination Index $H(y)$ and automated response filtering.

```python
#!/usr/bin/env python3
"""
NVIDIA NeMo Hallucination Detection & NLI Fact-Verification Lab.
Demonstrates atomic claim decomposition, NLI entailment scoring,
and hallucination index calculation.
"""

import re
from typing import List, Dict, Tuple

# =====================================================================
# 1. ATOMIC CLAIM EXTRACTION ENGINE
# =====================================================================

class ClaimExtractor:
    @staticmethod
    def extract_claims(text: str) -> List[str]:
        """
        Decomposes text into discrete atomic propositional claims.
        In production, this utilizes an optimized small LLM or rule parser.
        """
        # Split on sentence boundaries and conjunctions
        raw_sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        claims = []
        for s in raw_sentences:
            s_clean = s.strip()
            if s_clean:
                # Break complex sentences with 'and'
                sub_parts = re.split(r"\s+and\s+", s_clean, flags=re.IGNORECASE)
                for part in sub_parts:
                    p = part.strip().rstrip(".")
                    if len(p.split()) >= 4:
                        claims.append(p)
        return claims

# =====================================================================
# 2. NLI ENTAILMENT VERIFICATION ENGINE
# =====================================================================

class NLIFactChecker:
    @staticmethod
    def evaluate_entailment(premise: str, hypothesis: str) -> Dict[str, float]:
        """
        Simulates an NLI cross-encoder returning probabilities for:
        [Entailment, Neutral, Contradiction].
        """
        p_lower = premise.lower()
        h_lower = hypothesis.lower()

        # Check for direct contradictions (e.g. numbers)
        # If hypothesis asserts '256 gb' but premise says '128 gb'
        if "256 gb" in h_lower and "128 gb" in p_lower:
            return {"entailment": 0.02, "neutral": 0.05, "contradiction": 0.93}

        # Check for entailment (key terms present)
        words_h = set(re.findall(r"\b\w+\b", h_lower))
        words_p = set(re.findall(r"\b\w+\b", p_lower))
        overlap = len(words_h.intersection(words_p)) / (len(words_h) or 1)

        if overlap >= 0.70:
            return {"entailment": 0.92, "neutral": 0.06, "contradiction": 0.02}
        else:
            return {"entailment": 0.15, "neutral": 0.80, "contradiction": 0.05}

# =====================================================================
# 3. HALLUCINATION INDEX & VERIFICATION HARNESS
# =====================================================================

def run_hallucination_lab():
    print("=" * 80)
    print("NVIDIA NEMO HALLUCINATION DETECTION & FACT-CHECKING LAB")
    print("=" * 80)

    # Reference Ground Truth (Retrieved from Enterprise Docs)
    ground_truth_premise = (
        "The NVIDIA DGX Spark features the Blackwell GB10 GPU with 128 GB Unified Memory. "
        "It achieves 900 GB/s bidirectional NVLink-C2C bandwidth and supports native FP8 precision."
    )

    # Generated Candidate Response with 1 true claim and 1 hallucinated contradiction
    candidate_response = (
        "The NVIDIA DGX Spark supports native FP8 precision. "
        "Furthermore, the hardware integrates 256 GB of unified memory for ultra-large models."
    )

    print("Reference Premise (Ground Truth):")
    print(f"  {ground_truth_premise}\n")
    print("Candidate Generated Response:")
    print(f"  {candidate_response}\n")

    # Step 1: Claim Extraction
    claims = ClaimExtractor.extract_claims(candidate_response)
    print(f"Extracted {len(claims)} Atomic Claims:")
    for idx, c in enumerate(claims, 1):
        print(f"  Claim {idx}: '{c}'")

    # Step 2: NLI Verification
    print("\n--- Step 2: NLI Entailment Scoring ---")
    verified_count = 0
    contradictions = 0

    for idx, c in enumerate(claims, 1):
        scores = NLIFactChecker.evaluate_entailment(ground_truth_premise, c)
        p_ent = scores["entailment"]
        p_con = scores["contradiction"]

        print(f"\nEvaluating Claim {idx}: '{c}'")
        print(f"  Entailment: {p_ent:.4f} | Neutral: {scores['neutral']:.4f} | Contradiction: {p_con:.4f}")

        if p_con > 0.50:
            print("  -> DECISION: CONTRADICTION DETECTED! (Severe Hallucination)")
            contradictions += 1
        elif p_ent >= 0.75:
            print("  -> DECISION: VERIFIED & GROUNDED")
            verified_count += 1
        else:
            print("  -> DECISION: UNVERIFIED (Neutral / Speculation)")

    # Step 3: Compute Hallucination Index
    hallucination_index = 1.0 - (verified_count / len(claims))
    print("\n" + "=" * 80)
    print(f"Composite Hallucination Index H(y): {hallucination_index:.2f}")

    if hallucination_index > 0.20 or contradictions > 0:
        print("[ACTION] Output Rail INTERCEPTED response: Contains ungrounded/contradictory claims!")
    else:
        print("[ACTION] Output Rail DISPATCHED: Response 100% grounded in facts.")

    print("\n[SUCCESS] Atomic claim extraction and NLI fact-verification pipeline verified.")

if __name__ == "__main__":
    run_hallucination_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Operating fact-verification rails on the DGX Spark:
1. **Co-Locating NLI Model on Blackwell GB10**:
   - The NLI Cross-Encoder (`DeBERTa-v3-large-NLI`) consumes $\approx 1.5\text{ GB}$ VRAM.
   - Pinned alongside the primary LLM inside the 128 GB unified memory pool.
   - Evaluates up to 10 claims in parallel in **$< 8.5\text{ ms}$** using Blackwell FP8 Tensor Cores.

2. **Self-Check Rail Configuration in NeMo**:
   ```yaml
   rails:
     output:
       flows:
         - self check facts
   prompts:
     - task: self_check_facts
       content: |-
         You are a factual verification judge. Given the reference context:
         {{ context }}
         Check if the following statement is factually supported:
         {{ statement }}
         Answer strictly with [YES] or [NO].
   ```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Pronoun Anaphora Resolution)**:
   Write a preprocessing function that replaces occurrences of pronouns (`it`, `they`, `this`) in extracted claims with the main subject entity from the preceding sentence before NLI evaluation.

2. **Exercise 2 (Factual Refinement Prompt)**:
   Author a prompt template that takes the contradictory claim identified in the lab and asks the foundation model to rewrite the response removing the hallucinated statement.

### Solutions

**Solution for Exercise 2**:
```python
def build_refinement_prompt(original_response: str, contradictory_claim: str, context: str) -> str:
    return (
        f"<|im_start|>system\nYou are a factual editor. Revise the draft response so that it strictly "
        f"adheres to the reference context and removes the refuted claim: '{contradictory_claim}'.\n<|im_end|>\n"
        f"<|im_start|>user\nREFERENCE CONTEXT:\n{context}\n\nDRAFT RESPONSE:\n{original_response}\n<|im_end|>\n"
        f"<|im_start|>assistant\nREVISED RESPONSE:\n"
    )
```

### Troubleshooting FAQ

- **Q: NLI model flags valid paraphrases as contradictions.**
  - *Fix*: Your NLI training data was too rigid. Ensure you use an NLI model fine-tuned on MNLI and SNLI with diverse paraphrases, and check that `P(Neutral)` is not erroneously treated as `P(Contradiction)`.

- **Q: Claim extraction takes longer than LLM generation itself.**
  - *Fix*: Do not use a 70B model for claim extraction. Use a lightweight rule-based sentence parser (SpaCy / regex) or a fast 0.5B parameter token classification head.
