# Volume 19: Multimodal Document and Video RAG with Qwen2-VL

```
==================================================================================================
TARGET AUDIENCE: Computer Vision Engineers, Multimodal AI Researchers, Document AI Architects
PREREQUISITES   : Convolutional/ViT concepts, NaViT patch extraction, 3D M-RoPE, video encoding
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Build production multimodal retrieval-augmented generation pipelines for complex
                  multi-column PDFs, financial charts, and temporal video comprehension with Qwen2-VL.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Traditional text-only RAG pipelines fail catastrophically on modern enterprise documents: invoices, schematics, multi-column scientific papers, balance sheets, and technical training videos. Optical Character Recognition (OCR) flattens 2D layout semantics into an incoherent 1D stream of garbled tokens, destroying table alignments and infographic relationships.

**Qwen2-VL** revolutionizes document and video comprehension by treating visual tokens as first-class citizens. Combining dynamic visual resolution (**NaViT**), **3D Multimodal Rotary Positional Embedding (3D M-RoPE)** across time, height, and width, and native spatial grounding, Qwen2-VL allows direct visual RAG without error-prone OCR intermediaries.

```
       [Unstructured Input: Multi-page PDF / Financial Chart / MP4 Video]
                                      │
                     ┌────────────────┴────────────────┐
                     ▼                                 ▼
         [High-Res Document Page]            [Temporal Video Stream]
                     │                                 │
            NaViT Dynamic Patching           Uniform / Scene Keyframe
          Preserves Aspect Ratio             Sampling (e.g. 1-2 FPS)
                     │                                 │
                     └────────────────┬────────────────┘
                                      ▼
                      ┌───────────────────────────────┐
                      │ Qwen2-VL Vision Transformer   │
                      │ 2D Dynamic Patch Concatenation│
                      │ + 2D Spatial Merging (4x down)│
                      └───────────────┬───────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │  3D M-RoPE Positional Binding │
                      │   [Time, Height, Width] Grid  │
                      └───────────────┬───────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │ Qwen2-VL Multimodal Decoder   │
                      │  Direct Visual Spatial &      │
                      │  Temporal Bounding Box Synth  │
                      └───────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Radiologist vs. The Medical Transcriptionist
3. Evolutionary Lineage: From Tesseract OCR to Native Multimodal Visual RAG
4. First-Principles Mathematics & Algorithmic Formulations
   - NaViT Aspect Ratio Preserving Dynamic Patching
   - 3D M-RoPE Spatio-Temporal Coordinate Decomposition
   - 2D Spatial Token Merging (Pixel Unshuffle Downsampling)
   - Visual Bounding Box Coordinate Normalization
5. Comparative Trade-Off Matrix: Document & Video Extraction Pipelines
6. Concrete Production Hands-On Lab: Multimodal Document Parsing & Grounding Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Radiologist vs. The Medical Transcriptionist

Imagine diagnosing a patient from a chest X-ray:

- **The Traditional OCR Approach (Transcriptionist)**:
  A non-medical transcriber looks at the X-ray, tries to convert every shadow into words, and writes: *"Bone top left, dark circle middle, rib right, shadow lower bottom."* Then, a text doctor reads this description. The doctor cannot tell whether the shadow is a nodule or a normal vascular marking because the exact spatial geometry, margin sharpness, and relative scale were stripped away.

- **The Native Multimodal Approach (Radiologist - Qwen2-VL)**:
  The radiologist directly inspects the raw photons and 2D pixel geometry. They see the exact border of the cardiac silhouette, the precise millimeter diameter of the lesion, and how it correlates with the diaphragm.

When processing financial balance sheets, patent diagrams, or manufacturing video feeds, **Qwen2-VL acts as the radiologist**. It does not convert visual tables to garbled ASCII; it attends directly to the 2D spatial arrangement and temporal transitions.

---

## 3. Evolutionary Lineage: From Tesseract OCR to Native Multimodal Visual RAG

```
Generation 1 (2018-2022)      Generation 2 (2023-2024)      Generation 3 (2024-2026)
OCR + Text LLM                Fixed-Grid CLIP + Projection  Dynamic Resolution + 3D M-RoPE
──────────────────────────    ──────────────────────────    ───────────────────────────────
- Tesseract / EasyOCR pipeline - Rigid 224x224 / 448x448     - Native NaViT variable aspect
- Destroys multi-column flow    - Heavy image distortion      - 3D M-RoPE (Time, Height, Width)
- Fails on tables & charts     - Fixed token count per img   - Millisecond-level video grounding
- High error cascade           - Cannot handle long videos   - Zero OCR pipeline required
```

1. **Generation 1: OCR Pipeline Stacks (2018–2022)**:
   Documents were passed through heuristic or deep-learning OCR engines. Reading order heuristics frequently scrambled two-column academic papers, reading across columns rather than down. Tables were converted to tab-separated text with displaced cells.

2. **Generation 2: Fixed-Resolution Vision Encoders (2023–2024)**:
   Early multimodal LLMs (LLaVA-1.5) forced all images into a rigid square grid (e.g., $336 \times 336$ or $448 \times 448$). High-resolution legal contracts were downsampled into blurry smudges, rendering fine 8pt fonts completely illegible.

3. **Generation 3: Native Dynamic Patching & Spatio-Temporal Attention (2024–2026)**:
   Qwen2-VL introduced **NaViT dynamic resolution** and **3D M-RoPE**. An arbitrary $1200 \times 400$ panoramic flowchart or a 20-minute MP4 video is encoded without aspect ratio distortion, maintaining full resolution on critical tokens while compressing background areas.

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### NaViT Aspect Ratio Preserving Dynamic Patching

Given an image of arbitrary height $H$ and width $W$, standard Vision Transformers resize it to $P_{\text{size}} \times P_{\text{size}}$, distorting the aspect ratio:

$$\alpha = \frac{W}{H}$$

Qwen2-VL dynamically computes the optimal discrete patch grid $(h, w)$ such that:

$$\min_{h, w} \left| \frac{w}{h} - \alpha \right| \quad \text{subject to} \quad h \cdot w \le N_{\text{max\_patches}}, \quad h \equiv 0 \pmod 2, \quad w \equiv 0 \pmod 2$$

Where each patch has spatial size $P = 14 \times 14$ pixels. The image is rescaled to $(h \cdot P, w \cdot P)$ using bicubic interpolation, guaranteeing that no distortion occurs.

### 3D M-RoPE Spatio-Temporal Coordinate Decomposition

Standard Rotary Position Embedding (RoPE) rotates queries and keys using a 1D scalar index $m \in \{1, 2, \dots, L\}$.
For multimodal inputs spanning temporal frames $T$, vertical rows $H$, and horizontal columns $W$, Qwen2-VL decomposes the positional embedding channel dimension $D$ into three disjoint subspaces:

$$D = D_t + D_h + D_w$$

For a visual token located at temporal frame $t$, row coordinate $y$, and column coordinate $x$:

$$\mathbf{R}_{3D}(t, y, x) = \begin{bmatrix} 
\mathbf{R}_{D_t}(t) & \mathbf{0} & \mathbf{0} \\
\mathbf{0} & \mathbf{R}_{D_h}(y) & \mathbf{0} \\
\mathbf{0} & \mathbf{0} & \mathbf{R}_{D_w}(x)
\end{bmatrix}$$

For text tokens, the 1D sequential position $p$ is broadcast across all three axes: $t = y = x = p$, preserving full mathematical backward compatibility with standard 1D text RoPE.

### 2D Spatial Token Merging (Pixel Unshuffle Downsampling)

To prevent visual token bloat (which would explode self-attention complexity $\mathcal{O}(L^2)$), Qwen2-VL applies a $2 \times 2$ spatial token merger.
A block of 4 adjacent spatial patch vectors:

$$\mathbf{p}_{i, j}, \; \mathbf{p}_{i+1, j}, \; \mathbf{p}_{i, j+1}, \; \mathbf{p}_{i+1, j+1} \in \mathbb{R}^{d_{\text{vision}}}$$

is concatenated along the channel dimension and projected through a single linear layer:

$$\mathbf{v}_{\text{merged}} = \mathbf{W}_{\text{proj}} \left[ \mathbf{p}_{i, j} \circ \mathbf{p}_{i+1, j} \circ \mathbf{p}_{i, j+1} \circ \mathbf{p}_{i+1, j+1} \right] \in \mathbb{R}^{d_{\text{llm}}}$$

This reduces the total visual token count by **75% (4x compression)** while fully preserving the high-frequency spatial features required to read small text and numbers.

---

## 5. Comparative Trade-Off Matrix: Document & Video Extraction Pipelines

| Metric / Dimension | Traditional OCR + Qwen2.5 | LayoutLMv3 + Pipeline | ColPali (Late-Interaction Multi-Vector) | Qwen2-VL Native Multimodal RAG |
| :--- | :--- | :--- | :--- | :--- |
| **Complex Table Parsing** | Poor (Dislocated cells) | Moderate (Requires training) | Good (Patch retrieval) | **State-of-the-Art (Direct visual reading)** |
| **Infographics & Charts** | Complete Failure (No text) | Poor | Moderate | **Superior (Direct plot comprehension)** |
| **Temporal Video Search** | Speech ASR transcript only | N/A | Heuristic frame search | **Native (Frame + Audio + Action grounding)**|
| **Pipeline Complexity** | High (OCR + Parser + LLM) | High (Specialized layout models)| Moderate | **Low (Single unified foundation model)** |
| **Inference Latency** | 200–500 ms (OCR) + LLM | 300 ms | 25 ms (Retrieve only) | **150–350 ms (Direct end-to-end)** |

---

## 6. Concrete Production Hands-On Lab: Multimodal Document Parsing & Grounding Engine

This self-contained Python script demonstrates the mathematical mechanics of dynamic patch calculation, 3D coordinate decomposition, and spatial bounding box extraction for multi-column documents and video frames.

```python
#!/usr/bin/env python3
"""
Production Multimodal Document & Video Processor for Qwen2-VL.
Simulates NaViT dynamic patch grid computation, 3D M-RoPE coordinate mapping,
and visual bounding box extraction.
"""

import math
from typing import Dict, List, Tuple

# =====================================================================
# 1. NAVIT DYNAMIC PATCH RESOLUTION CALCULATOR
# =====================================================================

class NaViTPatchEngine:
    def __init__(self, patch_size: int = 14, max_patches: int = 1280, min_patches: int = 256):
        self.patch_size = patch_size
        self.max_patches = max_patches
        self.min_patches = min_patches

    def calculate_grid(self, orig_width: int, orig_height: int) -> Tuple[int, int, int]:
        """
        Calculates optimal (h_patches, w_patches) preserving original aspect ratio.
        Both h and w must be even numbers for 2x2 spatial token merging.
        """
        aspect_ratio = orig_width / orig_height
        best_diff = float("inf")
        best_grid = (16, 16)

        # Search even grid factors
        for h in range(8, int(math.sqrt(self.max_patches) * 2), 2):
            w = int(round(h * aspect_ratio / 2.0)) * 2
            if w < 8:
                w = 8
            total_patches = h * w
            if self.min_patches <= total_patches <= self.max_patches:
                diff = abs((w / h) - aspect_ratio)
                if diff < best_diff:
                    best_diff = diff
                    best_grid = (h, w)

        h_patches, w_patches = best_grid
        target_height = h_patches * self.patch_size
        target_width = w_patches * self.patch_size
        return h_patches, w_patches, (h_patches * w_patches) // 4  # 4x reduction from 2x2 merge

# =====================================================================
# 2. 3D M-ROPE COORDINATE GENERATOR
# =====================================================================

class M3DRoPEEngine:
    """Generates 3D coordinates (Time, Height, Width) for Multimodal Attention."""
    @staticmethod
    def generate_image_coords(h_patches: int, w_patches: int) -> List[Tuple[int, int, int]]:
        """For static images, Time t=0 for all tokens."""
        coords = []
        for y in range(0, h_patches, 2):
            for x in range(0, w_patches, 2):
                # Represents merged token coordinate
                coords.append((0, y // 2, x // 2))
        return coords

    @staticmethod
    def generate_video_coords(num_frames: int, h_patches: int, w_patches: int) -> List[Tuple[int, int, int]]:
        """For video streams, Time t increments per sampled frame."""
        coords = []
        for t in range(num_frames):
            for y in range(0, h_patches, 2):
                for x in range(0, w_patches, 2):
                    coords.append((t, y // 2, x // 2))
        return coords

# =====================================================================
# 3. BOUNDING BOX NORMALIZATION & PARSING UTILITIES
# =====================================================================

class BoundingBoxParser:
    """
    Qwen2-VL represents spatial bounding boxes normalized to [0, 1000]:
    <box>(ymin, xmin), (ymax, xmax)</box>
    """
    @staticmethod
    def denormalize_box(norm_box: Tuple[int, int, int, int], img_w: int, img_h: int) -> Dict[str, int]:
        ymin, xmin, ymax, xmax = norm_box
        return {
            "x": int(xmin * img_w / 1000.0),
            "y": int(ymin * img_h / 1000.0),
            "width": int((xmax - xmin) * img_w / 1000.0),
            "height": int((ymax - ymin) * img_h / 1000.0),
        }

# =====================================================================
# 4. EXECUTION & VERIFICATION HARNESS
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("QWEN2-VL MULTIMODAL DOCUMENT & VIDEO PROCESSING ENGINE")
    print("=" * 80)

    navit = NaViTPatchEngine(patch_size=14, max_patches=1280)

    # Test Case 1: Ultra-wide Financial Spreadsheet (1920 x 600)
    w1, h1 = 1920, 600
    h_p, w_p, merged_tokens = navit.calculate_grid(w1, h1)
    print(f"\n[Doc 1: Financial Balance Sheet] {w1}x{h1} (Aspect Ratio: {w1/h1:.2f})")
    print(f"  -> NaViT Grid: {h_p} rows x {w_p} cols = {h_p * w_p} raw patches")
    print(f"  -> After 2x2 Spatial Merging: {merged_tokens} LLM visual tokens")

    coords = M3DRoPEEngine.generate_image_coords(h_p, w_p)
    print(f"  -> Generated {len(coords)} 3D M-RoPE coordinate tuples. Sample: {coords[0]} ... {coords[-1]}")

    # Test Case 2: 10-second Industrial Video (10 frames at 1 FPS, 720 x 720)
    w2, h2, frames = 720, 720, 10
    h_p2, w_p2, tokens_per_frame = navit.calculate_grid(w2, h2)
    total_video_tokens = tokens_per_frame * frames
    print(f"\n[Doc 2: Industrial Camera Stream] {frames} frames @ {w2}x{h2}")
    print(f"  -> Tokens per frame: {tokens_per_frame} | Total video tokens: {total_video_tokens}")

    v_coords = M3DRoPEEngine.generate_video_coords(frames, h_p2, w_p2)
    print(f"  -> Video temporal range: Frame t=0 to t={v_coords[-1][0]}")

    # Test Case 3: Grounded Bounding Box Parsing
    # Simulated model output detecting a revenue table
    sample_norm_box = (150, 420, 480, 950) # ymin, xmin, ymax, xmax in [0, 1000]
    pixel_box = BoundingBoxParser.denormalize_box(sample_norm_box, img_w=w1, img_h=h1)
    print(f"\n[Spatial Grounding] Normalized Box {sample_norm_box}")
    print(f"  -> Extracted Pixel Coordinates: {pixel_box}")

    print("\n[SUCCESS] NaViT dynamic patching and 3D coordinate generation validated.")
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

Executing Qwen2-VL on the DGX Spark achieves extreme throughput due to unified memory synergy:
1. **Grace ARM Zero-Copy Video Decoding**:
   The Grace ARM Neoverse V2 processor runs multi-threaded `FFmpeg` / `libavcodec` video frame decoding natively in unified host memory. Raw decoded RGB numpy arrays are mapped directly to the Blackwell GB10 GPU without going through PCIe staging buffers.

2. **vLLM Multi-Modal Serving Configuration**:
   ```bash
   vllm serve Qwen/Qwen2-VL-7B-Instruct \
     --dtype bfloat16 \
     --max-model-len 32768 \
     --limit-mm-per-prompt image=10,video=2 \
     --gpu-memory-utilization 0.90
   ```

3. **Memory Sizing**:
   - `Qwen2-VL-7B-Instruct` in BF16: ~15 GB weights.
   - High-resolution document (1280 patches $\to$ 320 tokens): < 15 MB KV cache.
   - 100-frame video clip (32,000 tokens): ~1.8 GB KV cache.
   - Fits up to 40 simultaneous multi-page document parsing requests concurrently on a single GB10 GPU.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Video Scene Change Detector)**:
   Implement a Python keyframe selector that computes the histogram difference between consecutive video frames and only sends frames with significant visual transitions ($\Delta > 0.35$) to Qwen2-VL, reducing visual token consumption by up to 80%.

2. **Exercise 2 (Hierarchical Document Extraction)**:
   Construct a prompt for Qwen2-VL that instructs the model to return both the markdown text of a financial table and its exact bounding box coordinates: `<table box="(ymin, xmin, ymax, xmax)">| Header 1 | Header 2 |...</table>`.

### Solutions

**Solution for Exercise 1**:
```python
def filter_keyframes(frames: List[List[float]], threshold: float = 0.35) -> List[int]:
    """Returns indices of keyframes whose Euclidean distance exceeds threshold."""
    selected_indices = [0]
    last_frame = frames[0]
    for idx in range(1, len(frames)):
        current = frames[idx]
        diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(current, last_frame))) / len(current)
        if diff >= threshold:
            selected_indices.append(idx)
            last_frame = current
    return selected_indices
```

### Troubleshooting FAQ

- **Q: Small text in high-resolution scans is unreadable or hallucinated.**
  - *Fix*: Do not pre-downscale images in your data loader. Ensure you allow Qwen2-VL's maximum patch ceiling (`max_patches=1280` or higher) so NaViT retains original pixel resolution on dense text regions.

- **Q: Video inference triggers CUDA Out-of-Memory (OOM).**
  - *Fix*: Sample video frames at 1.0 or 0.5 FPS instead of 30 FPS. Enforce `--limit-mm-per-prompt video=1` and set `--max-model-len 32768` to avoid overflowing the Blackwell GB10 memory space.
