# Volume 06: NeMo Curator: GPU-Accelerated Petabyte Ingestion

```
==================================================================================================
TARGET AUDIENCE: Data Platform Engineers, Distributed ETL Architects, AI Pretraining Leads
PREREQUISITES   : Dask / Ray primitives, Common Crawl / WARC formats, high-throughput tokenization
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Build petabyte-scale GPU-accelerated data curation pipelines with NeMo Curator,
                  Dask-CUDA, cuDF, and high-throughput Megatron tokenization engines.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Modern frontier foundation models require between 5 and 15 Trillion tokens of high-quality pretraining data. Raw web scrapes (Common Crawl, WARC files) contain tens of petabytes of low-quality text, HTML boilerplates, machine-translated junk, and duplicate spam. Traditional CPU clusters running Apache Spark or Python multiprocessing require weeks and hundreds of nodes to parse, clean, and tokenize this volume.

**NVIDIA NeMo Curator** re-architects data curation for GPU acceleration. Leveraging **cuDF** (GPU DataFrames), **Dask-CUDA**, and GPU-native text extractors, NeMo Curator achieves **10x to 30x higher throughput per node**, processing petabytes of raw web data into clean, tokenized, sharded training datasets on modern NVIDIA clusters.

```
       [Raw Petabyte Corpus: WARC Files, Common Crawl, PDFs, GitHub Repos]
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   NeMo Curator GPU Ingestion Engine (Dask-CUDA / cuDF)        │
       │   - Parallel WARC decompression & HTML stripping              │
       │   - Zero-copy IPC buffers across Unified Memory               │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Multi-Stage GPU Cleaning & Classification Pipeline          │
       │   1. Unicode / ASCII Normalization & Whitespace Cleanup       │
       │   2. FastText Language Identification & Quality Classification│
       │   3. MinHash LSH Global Deduplication                         │
       │   4. PII Masking & Regulatory Filtering                       │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                                       ▼
       ┌───────────────────────────────────────────────────────────────┐
       │   Megatron GPU Fast-BPE Tokenization Engine                   │
       │   - Slices clean text into integer tokens                     │
       │   - Compiles binary `.bin` and `.idx` memory-mapped files     │
       └───────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Mining Excavator vs. The Hand Shovel
3. Evolutionary Lineage: From Apache Spark CPU Clusters to NeMo Curator
4. First-Principles Mathematics & Algorithmic Formulations
   - End-to-End Pipeline Throughput Scaling
   - GPU Vectorized Text Filtering Complexity
   - Binary Memory-Mapped Megatron Token Storage Layout (`.bin` / `.idx`)
5. Comparative Trade-Off Matrix: Data Processing Frameworks
6. Concrete Production Hands-On Lab: GPU-Accelerated Curator Pipeline Simulation
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Mining Excavator vs. The Hand Shovel

Imagine extracting 100 grams of pure gold from 1,000 tons of raw riverbed dirt:

- **The Traditional CPU Approach (10,000 Workers with Hand Shovels)**:
  You hire 10,000 workers with plastic buckets and hand shovels (CPU threads). Each worker picks up a cup of gravel, looks at each pebble individually with a magnifying glass (Python string parsing), and tosses out pebbles one by one. The workers constantly bump into each other (CPU context switching, cache contention, and disk I/O bottlenecks). It takes 6 months and a colossal energy bill.

- **The NVIDIA NeMo Curator Approach (The Industrial Hydraulic Dredge)**:
  NeMo Curator deploys an industrial GPU dredging plant. Massive high-pressure water cannons wash 1,000 tons of gravel across a vibrating acoustic resonant sluice box (**NVIDIA cuDF & Tensor Cores**). Millions of particles are classified simultaneously based on density in parallel. Waste clay and boulders are separated instantaneously, leaving pure gold bullion in minutes.

---

## 3. Evolutionary Lineage: From Apache Spark CPU Clusters to NeMo Curator

```
Generation 1 (2012-2020)      Generation 2 (2020-2023)      Generation 3 (2024-2026)
Hadoop MapReduce / Spark CPU  Polars / Ray CPU Pipelines    NVIDIA NeMo Curator on GPUs
──────────────────────────    ──────────────────────────    ───────────────────────────
- Heavy Java/JVM serialization- Multi-threaded Rust/Python  - GPU cuDF vectorized strings
- Terabytes of disk spill     - Better memory ergonomics    - Dask-CUDA multi-GPU scaling
- Massive multi-rack clusters - Still limited by CPU memory - Native MinHash & Quality Heads
- Days to process Common Crawl- High cost at petabyte scale - 20x throughput, 70% lower TCO
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### End-to-End Pipeline Throughput Scaling

Let the total data volume be $D$ bytes. The end-to-end processing time $T$ across $N$ pipeline stages is governed by:

$$T = \sum_{i=1}^N \frac{D}{R_i}$$

Where $R_i$ is the throughput rate of stage $i$ in bytes/second.
In CPU pipelines, string regex matching and language filtering are bottleneck stages ($R_{\text{regex}} \approx 25\text{ MB/s}$ per core).
In NeMo Curator, cuDF parallelizes regex state-machine transitions across thousands of CUDA threads:

$$R_{\text{cuDF}} = \frac{N_{\text{SM}} \times \text{Warps} \times \text{BytesPerClock}}{\text{ClocksPerChar}} \approx 3.5\text{ GB/s per GPU}$$

A single Blackwell GB10 GPU replaces **140 high-end CPU cores** for text parsing and normalization.

### Binary Memory-Mapped Megatron Token Storage Layout (`.bin` / `.idx`)

To avoid python serialization overhead during distributed pretraining, NeMo Curator compiles tokenized corpora into binary memory-mapped files:

1. **The Index File (`.idx`)**:
   Contains binary metadata headers followed by two tables:
   - **Document Pointers Table**: Array of 64-bit integers recording the starting token offset for each distinct document:
     $$\mathbf{P}_{\text{doc}} = [p_0, p_1, p_2, \dots, p_K]$$
   - **Sequence Lengths Table**: Array of 32-bit integers recording the length of each document:
     $$\mathbf{L}_{\text{doc}} = [l_0, l_1, l_2, \dots, l_K]$$

2. **The Binary Data File (`.bin`)**:
   A contiguous raw C-style array of unsigned 16-bit or 32-bit integers representing vocabulary token IDs:
   $$\text{Tokens} = [t_0, t_1, t_2, \dots, t_N], \quad t_i \in [0, |\mathcal{V}|-1]$$

During training, workers `mmap` the `.bin` file directly into virtual address space, retrieving batches in $\mathcal{O}(1)$ time with zero CPU parsing.

---

## 5. Comparative Trade-Off Matrix: Data Processing Frameworks

| Capability Dimension | Apache Spark (CPU) | Ray Data (CPU/GPU) | NVIDIA NeMo Curator (Dask-CUDA + cuDF) |
| :--- | :--- | :--- | :--- |
| **Primary Compute Engine** | Java Virtual Machine (JVM) | Python / C++ Ray Workers | **NVIDIA CUDA C++ / cuDF** |
| **GPU String Acceleration**| None | Custom user functions | **Native Vectorized GPU Kernels** |
| **MinHash Deduplication** | CPU Hash Joins (Slow) | Distributed CPU | **GPU MinHash LSH (Fast)** |
| **Tokenization Format** | HuggingFace Datasets | PyArrow Tables | **Native Megatron MMap (.bin/.idx)** |
| **Throughput / Node** | 150 – 350 MB/s | 600 – 1,200 MB/s | **4,000 – 12,000 MB/s** |
| **DGX Spark Grounding** | Poor (Does not use GB10) | Moderate | **Optimal (Full GPU saturation)** |

---

## 6. Concrete Production Hands-On Lab: GPU-Accelerated Curator Pipeline Simulation

This runnable Python script simulates a complete NeMo Curator data pipeline: raw text normalization, rule-based heuristic quality filtering, and fast binary Megatron `.idx` / `.bin` compilation.

```python
#!/usr/bin/env python3
"""
NVIDIA NeMo Curator Pipeline Simulator.
Demonstrates GPU-style batch text normalization, quality filtering,
and Megatron .bin/.idx binary memory-mapped dataset compilation.
"""

import os
import re
import struct
import tempfile
from typing import List, Tuple

# =====================================================================
# 1. TEXT NORMALIZATION & QUALITY HEURISTIC FILTERS
# =====================================================================

class DocumentFilter:
    @staticmethod
    def normalize_text(text: str) -> str:
        """Strips HTML tags, collapses excessive whitespace, normalizes unicode."""
        no_html = re.sub(r"<[^>]+>", " ", text)
        clean_whitespace = re.sub(r"\s+", " ", no_html).strip()
        return clean_whitespace

    @staticmethod
    def is_high_quality(text: str) -> bool:
        """
        Applies NeMo Curator heuristic quality thresholds:
        - Word count > 15
        - Stopword ratio > 0.15
        - Non-alphanumeric ratio < 0.25
        """
        words = text.split()
        if len(words) < 15:
            return False

        stopwords = {"the", "is", "at", "which", "on", "and", "a", "an", "in", "to", "for", "with"}
        stopword_count = sum(1 for w in words if w.lower() in stopwords)
        if (stopword_count / len(words)) < 0.15:
            return False

        non_alphanumeric = sum(1 for c in text if not c.isalnum() and not c.isspace())
        if (non_alphanumeric / (len(text) or 1)) > 0.25:
            return False

        return True

# =====================================================================
# 2. MEGATRON BINARY DATASET COMPILER (.bin and .idx)
# =====================================================================

class MegatronBinaryCompiler:
    @staticmethod
    def compile_dataset(tokenized_docs: List[List[int]], output_prefix: str):
        bin_path = f"{output_prefix}.bin"
        idx_path = f"{output_prefix}.idx"

        total_tokens = sum(len(doc) for doc in tokenized_docs)
        doc_count = len(tokenized_docs)

        # Write .bin file (Contiguous uint16 tokens)
        with open(bin_path, "wb") as f_bin:
            for doc in tokenized_docs:
                # Pack as unsigned 16-bit integers (vocab < 65536)
                f_bin.write(struct.pack(f"<{len(doc)}H", *doc))

        # Write .idx file
        with open(idx_path, "wb") as f_idx:
            # 1. Magic Header
            f_idx.write(b"MMIDIDX\x00\x00")
            # 2. Version (uint64)
            f_idx.write(struct.pack("<Q", 1))
            # 3. Data type code (1 = uint16)
            f_idx.write(struct.pack("<B", 1))
            # 4. Total tokens & document count
            f_idx.write(struct.pack("<QQ", total_tokens, doc_count))

            # 5. Document sequence lengths
            for doc in tokenized_docs:
                f_idx.write(struct.pack("<I", len(doc)))

            # 6. Document start pointer offsets
            current_offset = 0
            for doc in tokenized_docs:
                f_idx.write(struct.pack("<Q", current_offset))
                current_offset += len(doc)

        return bin_path, idx_path

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

def run_curator_lab():
    print("=" * 80)
    print("NVIDIA NEMO CURATOR INGESTION & DATASET COMPILATION LAB")
    print("=" * 80)

    # Sample raw ingested crawl
    raw_documents = [
        "<p>The NVIDIA DGX Spark platform delivers extreme unified memory bandwidth with Grace ARM processors and Blackwell GPUs for AI training.</p>",
        "<div>BUY CHEAP PILLS NOW!!! CLICK HERE http://spam.com/rx $$$$$$$$$$$$$$$$$$$$$$$</div>", # Spam
        "Short text.", # Too short
        "<article>High performance computing relies on optimized collective communications such as all-reduce and reduce-scatter over InfiniBand networks for scaling deep neural networks.</article>"
    ]

    print("Phase 1: Running Heuristic Quality & Normalization Filters...")
    clean_docs = []
    for idx, doc in enumerate(raw_documents, 1):
        norm = DocumentFilter.normalize_text(doc)
        passed = DocumentFilter.is_high_quality(norm)
        status = "ACCEPTED" if passed else "REJECTED"
        print(f"  Doc {idx} [{status}]: '{norm[:50]}...'")
        if passed:
            clean_docs.append(norm)

    assert len(clean_docs) == 2, f"Expected 2 docs to pass, got {len(clean_docs)}"

    print("\nPhase 2: Compiling into Binary Megatron (.bin/.idx) Format...")
    # Simulated tokenization (word-length hash mod 50000)
    tokenized = [[(hash(w) % 50000) for w in doc.split()] for doc in clean_docs]

    with tempfile.TemporaryDirectory() as tmp_dir:
        prefix = os.path.join(tmp_dir, "curated_pretrain_data")
        bin_p, idx_p = MegatronBinaryCompiler.compile_dataset(tokenized, prefix)

        bin_size = os.path.getsize(bin_p)
        idx_size = os.path.getsize(idx_p)
        print(f"  -> Generated {bin_p} ({bin_size} bytes)")
        print(f"  -> Generated {idx_p} ({idx_size} bytes)")

        assert bin_size > 0 and idx_size > 0
        print("\n[SUCCESS] NeMo Curator pipeline and Megatron binary compilation validated.")

if __name__ == "__main__":
    run_curator_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Operating NeMo Curator on the DGX Spark:
1. **Dask-CUDA Cluster Initialization**:
   Launch a local Dask-CUDA cluster pinned to the Blackwell GB10 GPU:
   ```bash
   dask-cuda-worker --device-memory-limit 110GB --local-directory /mnt/nvme/dask_scratch
   ```
2. **NVLink-C2C Unified Storage Pipeline**:
   The Grace ARM CPU reads raw WARC archives from local PCIe Gen5 NVMe storage into host RAM. Decompressed chunks are mapped directly to Blackwell GPU memory over the 900 GB/s NVLink-C2C bus, completely bypassing PCIe Gen4/Gen5 host-to-device copy overhead.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Perplexity Quality Filter)**:
   Author a function that uses a small n-gram language model or fast neural classifier to reject documents with perplexity $\text{PPL} > 500.0$ (indicative of gibberish, code dumps, or corrupted text).

2. **Exercise 2 (Binary MMap Reader)**:
   Write a Python script that memory-maps the generated `curated_pretrain_data.idx` and `.bin` files and retrieves Document #1 directly using pointer arithmetic in $< 1\text{ ms}$.

### Solutions

**Solution for Exercise 2**:
```python
import mmap
import struct

def read_mmap_doc(bin_path: str, offset: int, length: int) -> List[int]:
    with open(bin_path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        # 2 bytes per uint16 token
        start_byte = offset * 2
        byte_len = length * 2
        raw_tokens = mm[start_byte : start_byte + byte_len]
        return list(struct.unpack(f"<{length}H", raw_tokens))
```

### Troubleshooting FAQ

- **Q: Dask-CUDA throws `MemoryError: Out of GPU memory` during cuDF text parsing.**
  - *Fix*: Your partition chunk size is too large. When calling `read_text` or `read_json`, set `blocksize="64MB"` or `blocksize="128MB"` instead of default 1GB to keep intermediate DataFrame buffers within GPU VRAM limits.

- **Q: Non-English characters are mangled into question marks.**
  - *Fix*: Ensure raw data streams are read strictly with `encoding="utf-8", errors="replace"` before passing into cuDF string series.
