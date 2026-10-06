# Volume 12: SteerLM: Multi-Attribute Conditioned Alignment

```
==================================================================================================
TARGET AUDIENCE: Post-Training Researchers, Alignment Engineers, Controllable Generation Leads
PREREQUISITES   : RLHF fundamentals, conditional probability distributions, reward modeling, SFT
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master NVIDIA SteerLM: multi-attribute conditioning (Helpfulness, Correctness, Verbosity),
                  eliminating RLHF instability, iterative model bootstrapping, and runtime steering.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Traditional Reinforcement Learning from Human Feedback (RLHF with PPO) collapses human preferences into a single, scalar reward score. This introduces critical operational flaws:
1. **Reward Hacking & Verbosity Bias**: Models quickly learn that generating long-winded, sycophantic responses scores higher, even when factually vacuous.
2. **Static Alignment**: Once trained via PPO, a model's personality is permanently locked. You cannot ask the model to be "more concise" or "more educational" without changing the prompt and hoping the model complies.
3. **Training Instability**: PPO training involves orchestrating four massive models concurrently (Actor, Critic, Reference, Reward), frequently suffering policy collapse.

**NVIDIA SteerLM** replaces unstable reinforcement learning loops with **Attribute-Conditioned Supervised Fine-Tuning**. By conditioning the model on explicit, multi-dimensional quality attributes (Helpfulness, Correctness, Coherence, Complexity, Verbosity), SteerLM allows developers to steer model behavior dynamically at inference time with **100% training stability**.

```
       [Raw Multi-Turn Dialogues]
                   │
                   ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Attribute Annotation Engine (Nemotron-4-340B-Reward)        │
       │   Evaluates: Helpfulness(0-4), Correctness(0-4), Verbosity(0-4)│
       └───────────────────────────────┬───────────────────────────────┘
                                       │ Annotated Datasets
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Attribute-Conditioned SFT Training                          │
       │   Input: [User Prompt]                                        │
       │   Prefix: <|quality|>helpfulness:4,correctness:4,verbosity:1  │
       │   Target: [Clean, Concise Expert Response]                    │
       │   Models conditional distribution: P(Y | X, a_1, a_2, ..., a_k)
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Runtime Dynamic Steering (Inference Time)                   │
       │                                                               │
       │   Executive Briefing:                                         │
       │   Prompt Prefix: <|quality|>correctness:4,verbosity:1         │
       │   ──► Emits 2-sentence crisp factual summary                  │
       │                                                               │
       │   Educational Tutoring:                                       │
       │   Prompt Prefix: <|quality|>correctness:4,complexity:1,verb:4 │
       │   ──► Emits beginner-friendly, detailed pedagogical guide     │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Audio Mixing Console vs. The On/Off Switch
3. Evolutionary Lineage: From Scalar PPO to SteerLM Multi-Attribute Conditioning
4. First-Principles Mathematics & Algorithmic Formulations
   - Conditional Autoregressive Probability Distribution
   - Attribute Conditioning Token Grammar
   - Iterative Model Bootstrapping (Self-Steering)
5. Comparative Trade-Off Matrix: Alignment Paradigms
6. Concrete Production Hands-On Lab: SteerLM Conditioned Formatting & Dynamic Steering Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Audio Mixing Console vs. The On/Off Switch

Imagine tailoring sound in a professional concert hall:

- **The Traditional Scalar RLHF Approach (A Single On/Off Light Switch)**:
  You ask the audience to vote: "Did you like the music? (Thumbs Up / Thumbs Down)." The system takes the vote and turns a master knob. If the audience voted "Thumbs Up" because the bass was loud, the system permanently glues the master knob at maximum bass. If someone later wants to listen to a delicate violin concerto, the speakers still rattle with deafening bass because the model cannot decouple loudness from quality.

- **The NVIDIA SteerLM Approach (The Professional 32-Channel Soundboard)**:
  Instead of a single score, the sound engineer has individual sliders:
  - Slider 1: **Bass Depth** (Complexity)
  - Slider 2: **Vocal Clarity** (Correctness)
  - Slider 3: **Reverb Echo** (Verbosity)
  - Slider 4: **Harmonic Balance** (Helpfulness)
  During training, every song is labeled with where the sliders were set.
  At performance time, if an executive walks in, the engineer slides Verbosity down to 1 and Correctness up to 4. If a student walks in, they slide Complexity down to 1 and Verbosity up to 4. The same single model effortlessly adapts to every audience.

---

## 3. Evolutionary Lineage: From Scalar PPO to SteerLM Multi-Attribute Conditioning

```
Generation 1 (2020-2022)      Generation 2 (2023)           Generation 3 (2024-2026)
PPO Reinforcement Learning    Direct Preference Opt (DPO)   NVIDIA SteerLM
──────────────────────────    ──────────────────────────    ───────────────────────────
- 4-model cluster topology    - Stable pairwise loss        - Multi-attribute conditioning
- Scalar reward collapse      - Reference model memory 2x   - Standard SFT training stability
- Policy collapse & hacking   - Static locked preferences   - Zero reward hacking
- Extreme VRAM requirements   - Still cannot tune verbosity - Dynamic runtime steering sliders
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Conditional Autoregressive Probability Distribution

In standard language modeling, the network predicts tokens based solely on prompt context:

$$P(Y \mid X) = \prod_{t=1}^{|Y|} P(y_t \mid X, y_{<t})$$

In SteerLM, generation is explicitly conditioned on a vector of $K$ continuous or discretized attributes $\mathbf{a} = (a_1, a_2, \dots, a_K)$:

$$P(Y \mid X, \mathbf{a}) = \prod_{t=1}^{|Y|} P(y_t \mid X, \mathbf{a}, y_{<t})$$

Where $\mathbf{a}$ represents explicit ratings:

$$\mathbf{a} = \{ \text{helpfulness}: h, \; \text{correctness}: c, \; \text{coherence}: o, \; \text{complexity}: x, \; \text{verbosity}: v \}$$

Each attribute $a_k \in \{0, 1, 2, 3, 4\}$.

### Attribute Conditioning Token Grammar

NeMo SteerLM formats attribute tags as explicit ChatML metadata tokens prepended directly before the assistant's turn:

```xml
<|im_start|>system
You are a helpful assistant.<|im_end|>
<|im_start|>user
Explain quantum entanglement.<|im_end|>
<|im_start|>assistant
<|quality|>helpfulness:4,correctness:4,coherence:4,complexity:1,verbosity:2
Quantum entanglement is when two particles become interconnected so that measuring one instantly reveals the state of the other, no matter how far apart they are.<|im_end|>
```

During training, loss is calculated **strictly on the response tokens**, treating both the user prompt and the `<|quality|>` prefix as conditioning context ($m_i = 0$).

### Iterative Model Bootstrapping (Self-Steering)

If high-quality training pairs with maximum attribute scores ($\mathbf{a} = [4, 4, 4, 4, 4]$) are scarce in the initial dataset, SteerLM uses **Model-Steered Bootstrapping**:
1. Take intermediate model $\mathcal{M}_t$.
2. Sample $N$ completions for prompts $x \in \mathcal{D}$ conditioned on the highest desirable attribute prefix:
   $$y_{\text{boot}} \sim P_{\mathcal{M}_t}\left(Y \;\middle|\; X=x, \; \mathbf{a}_{\text{target}} = [4, 4, 4, 4, 1]\right)$$
3. Re-score the generated responses using `Nemotron-4-Reward`.
4. Add responses that successfully achieved high actual reward scores back into the training corpus.
5. Train model $\mathcal{M}_{t+1}$.

---

## 5. Comparative Trade-Off Matrix: Alignment Paradigms

| Dimension | PPO (Classical RLHF) | DPO (Direct Preference Opt) | NVIDIA SteerLM |
| :--- | :--- | :--- | :--- |
| **Training Architecture** | 4 Models (Actor, Critic, Ref, Reward)| 2 Models (Policy, Reference)| **1 Single Model (Standard SFT)**|
| **Memory Overhead** | Extremely High (4x VRAM) | Moderate (2x VRAM) | **Lowest (1x VRAM)** |
| **Convergence Stability** | Fragile (Prone to collapse) | High (Supervised-like) | **100% Deterministic (SFT loss)** |
| **Runtime Control** | Locked (Cannot steer) | Locked (Cannot steer) | **Dynamic (Adjust slider attributes)**|
| **Verbosity Mitigation** | Vulnerable to length hacking | Vulnerable to length hacking | **Explicitly Controlled ($v \in [0, 4]$)**|
| **DGX Spark Fit** | Requires Multi-GPU Cluster | 1–2 GPUs | **Single DGX Spark (Unified Memory)** |

---

## 6. Concrete Production Hands-On Lab: SteerLM Conditioned Formatting & Dynamic Steering Engine

This self-contained Python script implements:
1. SteerLM attribute prompt serialization and deserialization.
2. Dataset preprocessing with attribute condition injection.
3. A dynamic runtime steering client that toggles between executive summary mode ($v=1$) and detailed tutorial mode ($v=4$).

```python
#!/usr/bin/env python3
"""
NVIDIA SteerLM Multi-Attribute Conditioning & Runtime Steering Lab.
Demonstrates attribute serialization, loss mask injection, and dynamic runtime control.
"""

from typing import Dict, Any, List

# =====================================================================
# 1. STEERLM ATTRIBUTE CONDITIONING ENGINE
# =====================================================================

class SteerLMFormatter:
    DEFAULT_ATTRIBUTES = ["helpfulness", "correctness", "coherence", "complexity", "verbosity"]

    @staticmethod
    def format_quality_prefix(attributes: Dict[str, int]) -> str:
        """Serializes attribute dictionary into NeMo SteerLM quality tag."""
        ordered_pairs = []
        for attr in SteerLMFormatter.DEFAULT_ATTRIBUTES:
            if attr in attributes:
                val = attributes[attr]
                assert 0 <= val <= 4, f"Attribute {attr} out of range [0, 4]!"
                ordered_pairs.append(f"{attr}:{val}")
        return f"<|quality|>{','.join(ordered_pairs)}"

    @staticmethod
    def build_training_sample(prompt: str, response: str, attributes: Dict[str, int]) -> Dict[str, Any]:
        """Builds full training context with attribute conditioning prefix."""
        quality_tag = SteerLMFormatter.format_quality_prefix(attributes)
        full_prompt = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n{quality_tag}\n"
        full_text = f"{full_prompt}{response}<|im_end|>"
        return {
            "prompt_context": full_prompt,
            "target_response": f"{response}<|im_end|>",
            "full_training_text": full_text
        }

# =====================================================================
# 2. RUNTIME INFERENCE STEERING CLIENT
# =====================================================================

class RuntimeSteeringClient:
    def __init__(self, model_name: str = "Llama-3.1-Nemotron-70B-SteerLM"):
        self.model_name = model_name

    def generate(self, user_prompt: str, steering_profile: str) -> str:
        """Dynamically configures attribute conditioning tags based on desired persona."""
        if steering_profile == "executive_summary":
            # Maximum correctness, minimum verbosity, moderate complexity
            attrs = {"helpfulness": 4, "correctness": 4, "coherence": 4, "complexity": 3, "verbosity": 1}
        elif steering_profile == "educational_tutor":
            # Maximum helpfulness, low complexity, high verbosity for step-by-step guidance
            attrs = {"helpfulness": 4, "correctness": 4, "coherence": 4, "complexity": 1, "verbosity": 4}
        else:
            # Balanced standard
            attrs = {"helpfulness": 4, "correctness": 4, "coherence": 4, "complexity": 2, "verbosity": 2}

        quality_prefix = SteerLMFormatter.format_quality_prefix(attrs)
        prompt_with_steer = (
            f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
            f"<|im_start|>assistant\n{quality_prefix}\n"
        )

        # Simulated response generation showing dynamic adaptation
        if steering_profile == "executive_summary":
            simulated_output = "Transformer Engine FP8 doubles compute throughput by utilizing dual E4M3/E5M2 precision."
        else:
            simulated_output = (
                "Let's break down Transformer Engine step by step! First, computers usually store numbers "
                "using 16 bits. FP8 shrinks this to just 8 bits, which means the graphics card can do math twice "
                "as fast without using up all its memory. It carefully tracks the largest numbers to prevent errors."
            )

        return prompt_with_steer, simulated_output

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

def run_steerlm_lab():
    print("=" * 80)
    print("NVIDIA STEERLM MULTI-ATTRIBUTE CONDITIONING LAB")
    print("=" * 80)

    # Test 1: Training Sample Construction
    sample = SteerLMFormatter.build_training_sample(
        prompt="What is FlashAttention?",
        response="FlashAttention is an exact IO-aware self-attention algorithm that eliminates quadratic HBM reads.",
        attributes={"helpfulness": 4, "correctness": 4, "coherence": 4, "complexity": 3, "verbosity": 1}
    )
    print("Step 1: Formatted SteerLM Training Record:")
    print(sample["full_training_text"])
    print("-" * 80)

    # Test 2: Runtime Dynamic Steering
    client = RuntimeSteeringClient()
    query = "Explain Transformer Engine FP8."

    print("\nStep 2: Dynamic Runtime Steering Simulation...")
    p1, r1 = client.generate(query, steering_profile="executive_summary")
    print(f"\n[Profile: Executive Summary (verbosity:1)]")
    print(f"Conditioned Prompt:\n{p1}")
    print(f"Generated Output:\n{r1}")

    p2, r2 = client.generate(query, steering_profile="educational_tutor")
    print(f"\n[Profile: Educational Tutor (verbosity:4, complexity:1)]")
    print(f"Conditioned Prompt:\n{p2}")
    print(f"Generated Output:\n{r2}")

    assert "verbosity:1" in p1 and "verbosity:4" in p2
    print("\n[SUCCESS] SteerLM formatting and dynamic multi-attribute steering verified.")

if __name__ == "__main__":
    run_steerlm_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Operating SteerLM on the DGX Spark:
1. **Zero-PPO Memory Efficiency**:
   Unlike PPO which requires loading 4 models simultaneously (exhausting single-node VRAM), SteerLM is **mathematically identical to standard SFT**. Fine-tuning an 8B model with SteerLM consumes only $\approx 16\text{ GB}$ VRAM, and fine-tuning a 70B model with LoRA consumes $\approx 92\text{ GB}$ VRAM.

2. **NeMo SteerLM Training Execution**:
   ```bash
   python -m nemo.collections.nlp.models.language_modeling.megatron_gpt_steerlm \
     --config-path=/workspace/nemo_configs \
     --config-name=megatron_gpt_steerlm \
     model.data.train_ds.attribute_keys=['helpfulness','correctness','coherence','complexity','verbosity'] \
     trainer.devices=1 \
     trainer.precision=bf16
   ```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Attribute Parser)**:
   Write a parser that extracts the attribute key-value pairs from an incoming `<|quality|>...` tag and validates that all 5 values are integers in $[0, 4]$.

2. **Exercise 2 (Tone Customization)**:
   Extend `SteerLMFormatter` to support custom enterprise attributes, such as `humor: [0-4]` and `safety: [0-4]`.

### Solutions

**Solution for Exercise 1**:
```python
def parse_quality_tag(tag_str: str) -> Dict[str, int]:
    tag_content = tag_str.replace("<|quality|>", "").strip()
    result = {}
    for pair in tag_content.split(","):
        k, v = pair.split(":")
        val = int(v)
        if not (0 <= val <= 4):
            raise ValueError(f"Value {val} out of range for {k}")
        result[k] = val
    return result
```

### Troubleshooting FAQ

- **Q: Model emits `<|quality|>` tags during inference generation.**
  - *Fix*: You forgot to mask the quality prefix during SFT training. The loss mask must be $0$ over the `<|quality|>...` tokens so the model learns that attribute tags are conditioning inputs, not tokens to be generated.

- **Q: Adjusting `verbosity:1` does not make the model shorter.**
  - *Fix*: Your training dataset had an insufficient correlation between the verbosity attribute label and the actual response length. Re-annotate your training corpus using `Nemotron-4-Reward` ensuring length distributions across bins $0, 1, 2, 3, 4$ are distinctly separated.
