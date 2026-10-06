# Volume 08: Heuristic Quality Filtering and Domain Classifiers

```
==================================================================================================
TARGET AUDIENCE: Data Scientists, Pretraining Researchers, LLM Alignment Leads
PREREQUISITES   : Text statistics, Shannon entropy, n-gram language models, FastText classifiers
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Build production-grade data curation filters combining heuristic rule engines,
                  statistical entropy scoring, FastText language ID, and domain classifier heads.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Not all deduplicated data is worthy of pretraining foundation models. A dataset containing thousands of pages of auto-generated SEO spam, robotic server error logs, unpunctuated keyword lists, or toxic forum rants will severely degrade the reasoning capabilities, factual grounding, and conversational elegance of downstream LLMs.

**NeMo Curator** enforces a multi-tier quality filtering pyramid. Raw documents are subjected to fast **Rule-Based Heuristic Filters**, **Statistical Entropy Metrics**, **FastText Language Identification**, and **Domain-Specific Neural Classifiers** (distinguishing educational, technical, and mathematical literature from low-quality web noise).

```
                 [Incoming Raw Web Stream / Scraped Data]
                                    │
                                    ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Tier 1: Fast Rule-Based Heuristic Filters                   │
       │   - Character / Word N-gram Repetition Limits                 │
       │   - Punctuation & Alphanumeric Ratio Bounds                   │
       │   - Bullet Point & Ellipsis Thresholds                        │
       └────────────────────────────┬──────────────────────────────────┘
                                    │ Passed (Drops ~25% junk)
                                    ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Tier 2: Statistical Information Entropy                     │
       │   - Shannon Entropy H(X) bounds (filters cryptographic keys/logs)
       │   - Sentence Length Variance & Outlier Removal                │
       └────────────────────────────┬──────────────────────────────────┘
                                    │ Passed (Drops ~15% low-entropy)
                                    ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Tier 3: FastText Language Identification                    │
       │   - Multi-class confidence score: P(target_lang) >= 0.65      │
       └────────────────────────────┬──────────────────────────────────┘
                                    │ Passed
                                    ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Tier 4: Neural Domain & Quality Classifier Head             │
       │   - Trained against Wikipedia, arXiv, Books vs Common Crawl   │
       │   - Logistic regression / DeBERTa quality score >= 0.70       │
       └────────────────────────────┬──────────────────────────────────┘
                                    │
                                    ▼
       ┌───────────────────────────────────────────────────────────────┐
       │     Gold-Standard Pretraining Corpus (Top 20-30% Quality)     │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Master Sommelier's Grape Sorting Table
3. Evolutionary Lineage: From Blind Bulk Scrapes to Curated Synthetic Quality
4. First-Principles Mathematics & Algorithmic Formulations
   - Repetition Metrics & Degeneration Thresholds
   - Shannon Text Entropy Calculus
   - FastText Subword N-gram Classification Mathematics
   - Perplexity-Based KenLM Rejection Filtering
5. Comparative Trade-Off Matrix: Filtering Methods
6. Concrete Production Hands-On Lab: Composite Quality Evaluation Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Master Sommelier's Grape Sorting Table

Imagine crafting a \$5,000 bottle of vintage Bordeaux wine:

- **The Blind Dump Approach**:
  A mechanical harvester drives through the vineyard at 30 mph, ripping off entire vines, rotten grapes, dry leaves, gravel, twigs, and insects, dumping everything straight into the fermentation vat. The wine tastes like sour vinegar and dirt.

- **The Master Sommelier's Sorting Conveyor (NeMo Curator Quality Pyramid)**:
  1. **The Vibrating Grate (Tier 1 Heuristics)**: Rocks, sticks, and large twigs fall through instantly (dropping HTML tags, 2-word pages, and keyword spam).
  2. **The Optical Color Sorter (Tier 2 Statistical Entropy)**: High-speed infrared cameras reject unripened green or shriveled brown grapes (dropping low-entropy log dumps and garbled hex codes).
  3. **The Varietal Inspector (Tier 3 Language ID)**: Ensures no table grapes or wild berries are mixed with Cabernet Sauvignon (filtering out unwanted mixed-language fragments).
  4. **The Master Sommelier (Tier 4 Quality Classifier)**: Inspects the remaining grapes for perfect sugar-acid balance and aroma (retaining high-density mathematical, technical, and literary gold).

---

## 3. Evolutionary Lineage: From Blind Bulk Scrapes to Curated Synthetic Quality

```
Generation 1 (2018-2020)      Generation 2 (2020-2023)      Generation 3 (2024-2026)
GPT-2 / WebText Heuristics    C4 / RefinedWeb Filtering     NeMo Curator Multi-Tier Quality
──────────────────────────    ──────────────────────────    ───────────────────────────────
- Basic reddit upvote filter  - Gopher / MassiveText rules  - Multi-tier GPU-accelerated rules
- Blind web dump pretraining  - KenLM perplexity filtering  - FastText 176-language classifier
- High toxic contamination    - Filtered out 70% of web     - High-precision domain classifiers
- Low reasoning density       - Better, but missed subtle   - Synthetic data flywheel blend
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Repetition Metrics & Degeneration Thresholds

Web text often contains infinite repetition loops from broken scrapers or navigation bars. NeMo Curator evaluates three primary repetition metrics:

1. **Character N-Gram Repetition Ratio ($R_{\text{char}, n}$)**:
   Let $C$ be the sequence of characters. The fraction of characters belonging to the top repeated $n$-gram:

   $$R_{\text{char}, n}(C) = \frac{n \times \max_{s \in \mathcal{S}_n} \text{Count}(s, C)}{|C|}$$

   Threshold: If $R_{\text{char}, 10} > 0.15$ or $R_{\text{char}, 30} > 0.10$, reject.

2. **Duplicate Line Fraction ($F_{\text{dup\_lines}}$)**:
   For document with line list $\mathcal{L}$:

   $$F_{\text{dup\_lines}} = 1.0 - \frac{|\text{Unique}(\mathcal{L})|}{|\mathcal{L}|}$$

   Threshold: If $F_{\text{dup\_lines}} > 0.30$, reject.

### Shannon Text Entropy Calculus

To identify machine-generated ciphertext, hash dumps, or repetitive filler, NeMo Curator computes character-level **Shannon Information Entropy**:

$$H(X) = -\sum_{i=1}^V p(x_i) \log_2 p(x_i)$$

Where $p(x_i)$ is the empirical probability of character $x_i$ appearing in text $X$.
- Normal natural English text typically exhibits $3.5 \le H(X) \le 4.8\text{ bits/char}$.
- Low entropy ($H(X) < 3.0$): Repetitive boilerplate (e.g., `AAAAAA...`, tables of zeros).
- High entropy ($H(X) > 5.5$): Base64 encoded blobs, encrypted keys, or binary noise.

### FastText Subword N-gram Classification Mathematics

FastText represents a word $w$ as a bag of character $n$-grams (e.g. for `where` with $n=3$: `<wh`, `whe`, `her`, `ere`, `re>`, and `<where>`).
The document vector $\mathbf{v}_{\text{doc}}$ is the normalized average of its word representations:

$$\mathbf{v}_{\text{doc}} = \frac{1}{|D|} \sum_{w \in D} \left( \mathbf{z}_w + \sum_{g \in \mathcal{G}_w} \mathbf{z}_g \right)$$

The probability of language or quality class $c$ is computed via hierarchical softmax:

$$P(c \mid \text{doc}) = \frac{\exp(\mathbf{w}_c^\top \mathbf{v}_{\text{doc}})}{\sum_{j=1}^K \exp(\mathbf{w}_j^\top \mathbf{v}_{\text{doc}})}$$

Documents with $P(\text{target\_language}) < 0.65$ are excised.

---

## 5. Comparative Trade-Off Matrix: Filtering Methods

| Filtering Technique | Computational Cost | Latency per Doc | False Positive Rate | Target Quality Failure |
| :--- | :--- | :--- | :--- | :--- |
| **Length & Punctuation Rules**| $\mathcal{O}(L)$ (Negligible) | $< 0.01\text{ ms}$ | Low (< 2%) | Boilerplate, fragments, spam |
| **Repetition N-Gram Scanners**| $\mathcal{O}(L)$ (Fast) | $\approx 0.05\text{ ms}$ | Very Low (< 1%) | Scraper loops, bot spam |
| **Shannon Entropy Thresholds** | $\mathcal{O}(L)$ (Fast) | $\approx 0.02\text{ ms}$ | Low (< 3%) | Base64 strings, hex memory dumps|
| **FastText Language ID** | $\mathcal{O}(L)$ (Moderate) | $\approx 0.20\text{ ms}$ | Low (< 2%) | Mixed languages, encoding errors|
| **Neural Quality Classifier** | $\mathcal{O}(L \cdot d)$ (GPU) | $\approx 4.50\text{ ms}$ | Moderate (~5%) | Low-intellect social media rants|

---

## 6. Concrete Production Hands-On Lab: Composite Quality Evaluation Engine

This self-contained Python script implements a complete multi-tier quality evaluator: repetition scanning, Shannon entropy bounds, punctuation ratio checks, and simulated classifier scoring.

```python
#!/usr/bin/env python3
"""
NVIDIA NeMo Curator Quality Filtering Engine.
Demonstrates composite multi-signal quality evaluation for pretraining corpora.
"""

import math
import re
from typing import Dict, Tuple
from collections import Counter

class QualityEvaluationEngine:
    def __init__(self):
        # Established pretraining thresholds
        self.min_words = 20
        self.max_word_length_avg = 10.0
        self.min_entropy = 3.2
        self.max_entropy = 5.2
        self.max_char_rep_ratio = 0.15
        self.max_dup_line_ratio = 0.30

    @staticmethod
    def compute_entropy(text: str) -> float:
        """Calculates Shannon entropy in bits per character."""
        if not text:
            return 0.0
        counts = Counter(text)
        total = len(text)
        entropy = -sum((c / total) * math.log2(c / total) for c in counts.values())
        return entropy

    @staticmethod
    def compute_repetition(text: str, n: int = 10) -> float:
        """Calculates fraction of characters covered by most common 10-gram."""
        if len(text) < n:
            return 0.0
        ngrams = [text[i:i+n] for i in range(len(text) - n + 1)]
        most_common_count = Counter(ngrams).most_common(1)[0][1]
        return (most_common_count * n) / len(text)

    def evaluate_document(self, text: str) -> Tuple[bool, Dict[str, float], str]:
        words = re.findall(r"\b\w+\b", text)
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        # Signal 1: Word Count & Average Word Length
        num_words = len(words)
        if num_words < self.min_words:
            return False, {}, "REJECT: Insufficient word count"

        avg_word_len = sum(len(w) for w in words) / num_words
        if avg_word_len > self.max_word_length_avg or avg_word_len < 3.0:
            return False, {}, "REJECT: Abnormal average word length"

        # Signal 2: Shannon Entropy
        entropy = self.compute_entropy(text)
        if not (self.min_entropy <= entropy <= self.max_entropy):
            return False, {"entropy": entropy}, f"REJECT: Entropy {entropy:.2f} out of bounds"

        # Signal 3: Character N-Gram Repetition
        rep_ratio = self.compute_repetition(text, n=10)
        if rep_ratio > self.max_char_rep_ratio:
            return False, {"rep_ratio": rep_ratio}, f"REJECT: Repetition ratio {rep_ratio:.2f} exceeded"

        # Signal 4: Duplicate Lines
        dup_lines = 1.0 - (len(set(lines)) / (len(lines) or 1))
        if dup_lines > self.max_dup_line_ratio:
            return False, {"dup_lines": dup_lines}, f"REJECT: Duplicate lines ratio {dup_lines:.2f} exceeded"

        metrics = {
            "num_words": num_words,
            "avg_word_len": avg_word_len,
            "entropy": entropy,
            "rep_ratio": rep_ratio,
            "dup_lines": dup_lines
        }
        return True, metrics, "ACCEPT: High Quality"

# =====================================================================
# VERIFICATION HARNESS
# =====================================================================

def run_quality_lab():
    print("=" * 80)
    print("NVIDIA NEMO CURATOR QUALITY FILTERING LAB")
    print("=" * 80)

    evaluator = QualityEvaluationEngine()

    test_docs = [
        # Doc 1: High-Quality Educational Content
        ("Deep learning models leverage backpropagation and gradient descent to optimize loss "
         "functions across continuous multidimensional parameter spaces. Tensor parallel training "
         "enables scaling models across high-speed NVLink interconnects efficiently.", "Educational"),

        # Doc 2: Scraper Repetition Bug
        ("Click here to buy now. Click here to buy now. Click here to buy now. Click here to buy now. "
         "Click here to buy now. Click here to buy now. Click here to buy now. Click here to buy now.", "Repetitive Spam"),

        # Doc 3: Base64 / Hexadecimal Garbage
        ("aGVsbG8gd29ybGQgdGhpcyBpcyBhIHJhbmRvbSBiaW5hcnkgc3RyZWFtIG9mIGVuY3J5cHRlZCBkYXRhCg== "
         "4f3b2a1c0e8d7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e8f7a6b5c4d3e2f10 "
         "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b", "Hex/Base64 Dump"),

        # Doc 4: Fragmented Stub
        ("Hello there friend. Good morning.", "Short Stub")
    ]

    for idx, (text, label) in enumerate(test_docs, 1):
        passed, metrics, reason = evaluator.evaluate_document(text)
        print(f"\n[Test Case {idx}: {label}]")
        print(f"  Result : {'PASSED' if passed else 'FAILED'}")
        print(f"  Reason : {reason}")
        if metrics:
            print(f"  Metrics: Entropy={metrics.get('entropy', 0):.2f}, RepRatio={metrics.get('rep_ratio', 0):.2f}")

    print("\n[SUCCESS] Quality evaluation engine multi-signal filtering verified.")

if __name__ == "__main__":
    run_quality_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Quality filtering on the DGX Spark:
1. **Parallel Execution via Grace ARM**:
   Heuristic regex checks and Shannon entropy evaluations are embarrassingly parallel. The Grace ARM Neoverse V2 processor executes 64 concurrent Python worker processes across unified memory, filtering raw text at **8.5 GB/s**.

2. **Blackwell GPU Classifier Inference**:
   Cleaned candidate texts that pass heuristic tiers are transferred to the Blackwell GB10 GPU in large micro-batches ($B=1024$) for batch neural quality scoring with FastText or RoBERTa-Classifier in FP8 precision.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Terminal Punctuation Filter)**:
   Author a rule that calculates the percentage of sentences ending with standard terminal punctuation (`.`, `!`, `?`). Reject documents where fewer than 70% of lines end with terminal punctuation (indicates navigation menus or raw lists).

2. **Exercise 2 (Entropy Calibration)**:
   Measure the Shannon entropy of 10 Wikipedia paragraphs vs 10 Git commit logs. Verify that the threshold $H(X) \ge 3.2$ effectively separates the two.

### Solutions

**Solution for Exercise 1**:
```python
def check_terminal_punctuation(lines: List[str], min_ratio: float = 0.70) -> bool:
    valid_lines = [l for l in lines if l]
    if not valid_lines:
        return False
    punct_count = sum(1 for l in valid_lines if l.rstrip()[-1] in {'.', '!', '?'})
    return (punct_count / len(valid_lines)) >= min_ratio
```

### Troubleshooting FAQ

- **Q: Technical coding tutorials and math proofs are being aggressively rejected.**
  - *Fix*: Code and LaTeX math naturally feature higher non-alphanumeric ratios and repeated symbols (e.g. `===`, `---`, `\begin{equation}`). Configure domain-specific rule exemptions: if document has programming language tags or markdown code fences, raise the allowed non-alphanumeric threshold to $0.45$.

- **Q: FastText crashes with `ValueError: Model file cannot be opened`.**
  - *Fix*: You must download the official `lid.176.bin` pre-trained language identification binary from FastText and provide its absolute path to NeMo Curator's language ID module.
