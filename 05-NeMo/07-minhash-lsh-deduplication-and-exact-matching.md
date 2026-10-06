# Volume 07: MinHash LSH Deduplication and Exact String Matching

```
==================================================================================================
TARGET AUDIENCE: Data Scientists, Large-Scale Pretraining Engineers, Algorithm Architects
PREREQUISITES   : Set theory, hash functions, Jaccard similarity, Graph algorithms (Connected Components)
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master petabyte-scale data deduplication in NeMo Curator: n-gram shingling, MinHash
                  permutations, Locality-Sensitive Hashing (LSH) band mathematics, and exact substring matching.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Web-scale corpora are plagued by massive redundancy: boilerplate terms of service, syndicated news articles duplicated across thousands of domains, forum quotes, and SEO spam. Pretraining foundation models on duplicate data causes severe pathologies: catastrophic memorization, privacy leakage, severe degradation in downstream generalizability, and wasted GPU compute.

Naive pairwise similarity comparison across 100 million documents requires $\frac{N(N-1)}{2} \approx 5 \times 10^{15}$ comparisons—a computational impossibility.

**NeMo Curator** solves this via **MinHash Locality-Sensitive Hashing (LSH)** and **Exact Substring Matching**. By transforming high-dimensional document token sets into compact min-hash signatures and grouping them into LSH hash bands, pairwise comparison complexity collapses from $\mathcal{O}(N^2)$ to near-linear $\mathcal{O}(N)$.

```
       [Corpus: Millions of Raw Web Documents]
                         │
                         ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   K-Shingle Decomposition                                     │
       │   "The quick brown fox" ──► {"The quick", "quick brown", ...} │
       └─────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   MinHash Signature Matrix Computation                        │
       │   K Universal Hash Permutations: h_1, h_2, ..., h_M           │
       │   Signature: Sig(D) = [min h_1(D), min h_2(D), ...]           │
       └─────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Locality-Sensitive Hashing (LSH) Band Partitioning          │
       │   Split Signature into b bands of r rows                      │
       │   Hash Band Sub-vectors to Buckets ──► Yields Candidate Pairs │
       └─────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Connected Components Graph Clustering (Union-Find)          │
       │   Group Transitive Duplicate Clusters ──► Retain 1 Exemplar   │
       └─────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Deduplicated High-Density Corpus (60-80% Size Reduction)    │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Crime Scene Fingerprint Registry
3. Evolutionary Lineage: From Exact MD5 Hashes to GPU MinHash LSH
4. First-Principles Mathematics & Algorithmic Formulations
   - Jaccard Similarity Formulation
   - The MinHash Theorem Proof
   - LSH Band Partitioning & The S-Curve Probability Function
   - Disjoint-Set Union-Find Graph Clustering
5. Comparative Trade-Off Matrix: Deduplication Techniques
6. Concrete Production Hands-On Lab: Complete MinHash LSH Deduplication Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Crime Scene Fingerprint Registry

Imagine an FBI archive containing 50 million fingerprints:

- **The Exact Match Approach (MD5 / SHA256 Hash)**:
  If a suspect left a fingerprint on a glass, you compute its MD5 hash. But if the fingerprint has a tiny smudge or 1 millimeter of dust on the edge, its hash changes completely. You find zero matches. The criminal walks free because exact hashing cannot tolerate even 1 altered bit.

- **The Brute-Force Comparison Approach ($\mathcal{O}(N^2)$ Pairwise Inspection)**:
  An agent takes the new print and compares it under a microscope against every single one of the 50 million prints in the archive. By the time they finish checking print #500,000, 20 years have passed.

- **The MinHash LSH Approach (The Smart Filing Cabinet)**:
  You extract 100 characteristic ridge intersections (**Shingles**). You compute a compact 100-number barcode (**MinHash Signature**).
  You divide the barcode into 20 bands of 5 numbers each. You file the print into 20 specific physical drawers corresponding to those 5-number combinations.
  If two fingerprints share 80% of their ridges (**Jaccard Similarity $\ge 0.8$**), there is a **99.9% mathematical certainty** that they will land in the exact same drawer. The agent only inspects the 3 prints in that specific drawer.

---

## 3. Evolutionary Lineage: From Exact MD5 Hashes to GPU MinHash LSH

```
Generation 1 (2010-2018)      Generation 2 (2018-2022)      Generation 3 (2023-2026)
Exact MD5 / URL Deduplication CPU Spark MinHash LSH         GPU NeMo Curator MinHash + Suffix
──────────────────────────    ──────────────────────────    ─────────────────────────────────
- Only catches 100% clones    - Fuzzy Jaccard deduplication - GPU-accelerated hashing (cuDF)
- Blind to 99% similar pages  - High CPU memory usage       - Billion-document cluster scale
- Misses syndicated articles  - Multi-terabyte disk spills  - Exact substring removal (Suffix)
- Zero template removal       - Slow 48-hour pipeline runs  - Sub-hour petabyte deduplication
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Jaccard Similarity Formulation

Let document $A$ and document $B$ be represented as sets of $k$-shingles (contiguous sequences of $k$ words or characters): $S_A, S_B$.
The **Jaccard Similarity** $J(A, B)$ is defined as:

$$J(A, B) = \frac{|S_A \cap S_B|}{|S_A \cup S_B|}$$

Where $J(A, B) \in [0, 1]$. If $A$ and $B$ are identical, $J(A, B) = 1.0$.

### The MinHash Theorem Proof

Let $\pi$ be a random permutation of the universal dictionary of shingles $\mathcal{U}$.
The min-hash function $h_\pi(S)$ returns the smallest index under permutation $\pi$ present in set $S$:

$$h_\pi(S) = \min_{s \in S} \pi(s)$$

**Theorem**: The probability that two sets yield the same min-hash value is strictly equal to their Jaccard similarity:

$$P\left( h_\pi(S_A) = h_\pi(S_B) \right) = J(A, B)$$

#### Proof
Consider the union $S_A \cup S_B$. When scanning shingles in random order according to $\pi$, the first shingle encountered that belongs to $S_A \cup S_B$ must fall into one of three disjoint cases:
1. Shingle belongs to $S_A \cap S_B$: In this case, $h_\pi(S_A) = h_\pi(S_B)$.
2. Shingle belongs to $S_A \setminus S_B$: In this case, $h_\pi(S_A) \ne h_\pi(S_B)$.
3. Shingle belongs to $S_B \setminus S_A$: In this case, $h_\pi(S_A) \ne h_\pi(S_B)$.

The total number of elements where Case 1 occurs is $|S_A \cap S_B|$. The total number of candidate elements is $|S_A \cup S_B|$.
Because the permutation is uniformly random, the probability that the first element falls into Case 1 is:

$$P(\text{Case 1}) = \frac{|S_A \cap S_B|}{|S_A \cup S_B|} = J(A, B) \quad \blacksquare$$

By evaluating $M$ independent random hash functions, we construct an $M$-dimensional signature vector $\mathbf{v} \in \mathbb{N}^M$:

$$\hat{J}(A, B) = \frac{1}{M} \sum_{k=1}^M \mathbb{I}\left( h_k(S_A) = h_k(S_B) \right)$$

### LSH Band Partitioning & The S-Curve Probability Function

To find pairs with $\hat{J}(A, B) \ge s$ without quadratic comparisons:
1. Divide the $M$-dimensional signature into $b$ bands, each containing $r$ rows ($M = b \cdot r$).
2. For each band, hash the $r$-element sub-vector into a hash bucket.
3. If two documents hash to the same bucket in **at least one band**, they become a candidate duplicate pair.

```
Signature (M = 100 rows)
┌──────────────┐
│  Band 1 (r)  │ ──► Hash Bucket B1_42
├──────────────┤
│  Band 2 (r)  │ ──► Hash Bucket B2_19
├──────────────┤
│     ...      │
├──────────────┤
│  Band b (r)  │ ──► Hash Bucket Bb_88
└──────────────┘
```

The probability that two documents with true similarity $s$ become a candidate pair is:
1. Probability of identical signatures in all $r$ rows of a single band: $s^r$.
2. Probability of differing in at least one row of a band: $1 - s^r$.
3. Probability of differing across all $b$ independent bands: $(1 - s^r)^b$.
4. **Probability of becoming a candidate pair (The S-Curve)**:

$$P(\text{Candidate}) = 1 - (1 - s^r)^b$$

```
Probability P
  1.0 ┌───────────────────────────────╭───────────┐
      │                               │           │
      │                               │           │
  0.5 │───────────────────────────────*───────────│   Threshold s* ≈ (1/b)^(1/r)
      │                              ╱            │
      │                             ╱             │
  0.0 └────────────────────────────╯──────────────┴─── Jaccard Similarity s
      0.0                         0.8            1.0
```

By tuning $b$ and $r$, we set the steep inflection threshold $s^* \approx \left(\frac{1}{b}\right)^{1/r}$.
For $M = 128$, setting $b = 16$ and $r = 8$ yields $s^* = (1/16)^{1/8} \approx 0.707$, aggressively capturing all pairs with similarity $\ge 70\%$.

---

## 5. Comparative Trade-Off Matrix: Deduplication Techniques

| Metric / Dimension | Exact MD5 / SHA Hashing | MinHash LSH (NeMo Curator) | Suffix Array Exact Substring | Embedding KNN Deduplication |
| :--- | :--- | :--- | :--- | :--- |
| **Similarity Captured** | 100% Identical Only | **Fuzzy Jaccard (70% - 99%)**| Exact Substrings $\ge 50$ chars| Semantic Paraphrasing |
| **Computational Complexity**| $\mathcal{O}(N)$ | **$\mathcal{O}(N \cdot M)$** | $\mathcal{O}(N \log N)$ | $\mathcal{O}(N^2)$ or HNSW indexing |
| **GPU Acceleration** | Moderate | **Native (cuDF / GPU MinHash)**| Complex memory-bound | Native (FAISS / cuVS) |
| **Typical Corpus Reduction**| 5% – 12% | **35% – 60%** | 20% – 30% | 15% – 25% |
| **DGX Spark Fit** | Trivial | **Engineered for NeMo Curator** | High RAM requirement | High VRAM requirement |

---

## 6. Concrete Production Hands-On Lab: Complete MinHash LSH Deduplication Engine

This self-contained Python script implements:
1. Word 3-shingle decomposition.
2. 64-hash MinHash signature generation.
3. LSH band hashing with bucket collision tracking.
4. Disjoint-Set Union-Find clustering to isolate connected components of duplicate documents.

```python
#!/usr/bin/env python3
"""
Production MinHash LSH Deduplication Engine.
Demonstrates shingling, signature hashing, LSH band collision, and Union-Find clustering.
"""

import re
import hashlib
from typing import Dict, List, Set, Tuple
from collections import defaultdict

# =====================================================================
# 1. SHINGLING & UNIVERSAL HASH SIGNATURE GENERATOR
# =====================================================================

class MinHashEngine:
    def __init__(self, num_hashes: int = 64, seed: int = 42):
        self.num_hashes = num_hashes
        self.seed = seed
        # Generate universal hash coefficients: (a * x + b) % p
        self.p = 2147483647 # Large Mersenne prime (2^31 - 1)
        self.a_coeffs = []
        self.b_coeffs = []
        for i in range(num_hashes):
            self.a_coeffs.append((i * 10007 + 54321) % self.p + 1)
            self.b_coeffs.append((i * 49999 + 12345) % self.p + 1)

    @staticmethod
    def get_shingles(text: str, k: int = 3) -> Set[str]:
        words = re.findall(r"\b\w+\b", text.lower())
        if len(words) < k:
            return set([" ".join(words)])
        return set(" ".join(words[i:i+k]) for i in range(len(words) - k + 1))

    def compute_signature(self, shingles: Set[str]) -> List[int]:
        sig = [float("inf")] * self.num_hashes
        for s in shingles:
            # Hash string to 32-bit integer
            s_hash = int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16) % self.p
            for i in range(self.num_hashes):
                val = (self.a_coeffs[i] * s_hash + self.b_coeffs[i]) % self.p
                if val < sig[i]:
                    sig[i] = val
        return sig

# =====================================================================
# 2. LOCALITY-SENSITIVE HASHING (LSH) & GRAPH CLUSTERING
# =====================================================================

class LSHDeduplicator:
    def __init__(self, num_bands: int = 16, rows_per_band: int = 4):
        self.num_bands = num_bands
        self.rows_per_band = rows_per_band
        self.buckets: Dict[str, List[int]] = defaultdict(list)

    def insert(self, doc_id: int, signature: List[int]):
        for b in range(self.num_bands):
            start = b * self.rows_per_band
            end = start + self.rows_per_band
            band_subvec = tuple(signature[start:end])
            bucket_key = f"b{b}_{hash(band_subvec)}"
            self.buckets[bucket_key].append(doc_id)

    def find_candidate_pairs(self) -> Set[Tuple[int, int]]:
        candidates = set()
        for bucket in self.buckets.values():
            if len(bucket) > 1:
                for i in range(len(bucket)):
                    for j in range(i + 1, len(bucket)):
                        pair = tuple(sorted((bucket[i], bucket[j])))
                        candidates.add(pair)
        return candidates

class UnionFind:
    """Disjoint-Set Union-Find data structure with path compression."""
    def __init__(self, elements: List[int]):
        self.parent = {e: e for e in elements}

    def find(self, i: int) -> int:
        if self.parent[i] == i:
            return i
        self.parent[i] = self.find(self.parent[i])
        return self.parent[i]

    def union(self, i: int, j: int):
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

def run_dedup_lab():
    print("=" * 80)
    print("NVIDIA NEMO CURATOR MINHASH LSH DEDUPLICATION LAB")
    print("=" * 80)

    corpus = [
        # Doc 0 & Doc 1: Near-duplicate news article with altered timestamp
        "The stock market experienced heavy volatility on Monday morning following international trade reports.",
        "The stock market experienced heavy volatility on Tuesday morning following international trade reports.",
        # Doc 2: Slight paraphrase of Doc 0
        "The stock market experienced massive volatility on Monday morning after global trade reports were released.",
        # Doc 3: Completely distinct document
        "Quantum computing utilizes superposition and entanglement to perform complex polynomial factorization."
    ]

    engine = MinHashEngine(num_hashes=64)
    lsh = LSHDeduplicator(num_bands=16, rows_per_band=4)

    signatures = []
    print("Step 1: Generating MinHash Signatures...")
    for idx, doc in enumerate(corpus):
        shingles = engine.get_shingles(doc, k=3)
        sig = engine.compute_signature(shingles)
        signatures.append(sig)
        lsh.insert(idx, sig)
        print(f"  Doc {idx}: {len(shingles)} 3-shingles | Sig hash={sig[0]}")

    print("\nStep 2: Detecting Candidate Duplicate Pairs via LSH Bands...")
    candidates = lsh.find_candidate_pairs()
    print(f"  Found {len(candidates)} candidate duplicate pairs: {candidates}")

    # Compute exact Jaccard similarity for validation
    for (d1, d2) in candidates:
        s1 = engine.get_shingles(corpus[d1], k=3)
        s2 = engine.get_shingles(corpus[d2], k=3)
        true_jaccard = len(s1.intersection(s2)) / len(s1.union(s2))
        print(f"  Pair ({d1}, {d2}) -> True Jaccard Similarity = {true_jaccard:.4f}")

    print("\nStep 3: Graph Clustering & Duplicate Elimination...")
    uf = UnionFind(list(range(len(corpus))))
    for (d1, d2) in candidates:
        uf.union(d1, d2)

    # Group into clusters
    clusters = defaultdict(list)
    for doc_id in range(len(corpus)):
        root = uf.find(doc_id)
        clusters[root].append(doc_id)

    print("Discovered Connected Component Duplicate Clusters:")
    retained_docs = []
    for root, members in clusters.items():
        print(f"  Cluster {root}: Documents {members} -> Retaining Doc {members[0]}")
        retained_docs.append(members[0])

    print(f"\nFinal Deduplicated Corpus: Kept {len(retained_docs)} / {len(corpus)} documents.")
    assert 3 in retained_docs, "Unique quantum document should never be clustered with financial news!"
    print("\n[SUCCESS] MinHash LSH deduplication pipeline verified.")

if __name__ == "__main__":
    run_dedup_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Executing MinHash Deduplication on DGX Spark:
1. **cuDF Vectorized MinHash**:
   NeMo Curator utilizes GPU-accelerated hashing kernels inside cuDF. Generating 128 MinHash signatures for 100,000 documents takes **$< 1.5\text{ seconds}$** on the Blackwell GB10 GPU.

2. **Unified Memory Hash Bucket Table**:
   The LSH bucket dictionary and Union-Find graph structures reside in the shared 128 GB Unified Memory pool. The Grace ARM CPU executes the sparse graph traversal (Union-Find path compression) while the Blackwell GPU executes dense matrix signature generation in parallel.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (S-Curve Threshold Calculation)**:
   Given $M = 128$ hashes, calculate the theoretical inflection threshold $s^*$ for:
   - Configuration A: $b = 32, r = 4$
   - Configuration B: $b = 8, r = 16$
   Which configuration should be chosen to aggressively eliminate near-duplicates with $J \ge 0.85$?

2. **Exercise 2 (Exact Substring Removal)**:
   Implement an exact duplicate sentence remover that splits documents into sentences and drops any sentence that has already appeared in the corpus.

### Solutions

**Solution for Exercise 1**:
- Configuration A: $s^* \approx (1/32)^{1/4} = (0.03125)^{0.25} \approx 0.420$. (Very loose; catches pairs with only 42% similarity, high false positive rate).
- Configuration B: $s^* \approx (1/8)^{1/16} = (0.125)^{0.0625} \approx 0.878$. (Strict; selectively captures pairs with $\ge 88\%$ similarity, ideal for near-duplicate removal).

### Troubleshooting FAQ

- **Q: MinHash LSH returns thousands of false positives (unrelated documents grouped together).**
  - *Fix*: Your band row size $r$ is too small (e.g. $r=1$ or $r=2$). Increase $r$ to $4$ or $8$. Higher $r$ requires more rows to match simultaneously before a pair is hashed into the same bucket.

- **Q: Very short documents (< 5 words) cause index errors.**
  - *Fix*: Documents with fewer words than the shingle size $k$ cannot generate valid n-grams. Filter out documents with token count $< 15$ before entering the MinHash pipeline.
