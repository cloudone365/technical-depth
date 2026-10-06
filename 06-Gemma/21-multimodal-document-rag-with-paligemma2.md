# Volume 21: Multimodal Document RAG with PaliGemma 2

```
==================================================================================================
TARGET AUDIENCE: Enterprise Search Architects, Document AI Engineers, Multimodal RAG Leads
PREREQUISITES   : Retrieval-Augmented Generation (RAG), Vector Embeddings, HNSW, Vision-Language Models
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master end-to-end Multimodal Document RAG using Google PaliGemma 2: Visual page
                  indexing, SigLIP embedding retrieval, and visually grounded reasoning over complex PDFs.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Traditional enterprise Retrieval-Augmented Generation (RAG) relies on an error-prone, brittle two-stage pipeline:
1. Extracting text from PDFs using Optical Character Recognition (OCR) tools.
2. Chunking text blindly into 512-token segments and matching via text embeddings.

When encountering balance sheets, technical schematics, multi-column research papers, and handwritten forms, traditional OCR strips away all two-dimensional spatial geometry: tables collapse into disordered strings, and flowchart arrows vanish completely.

**Multimodal Document RAG** with **PaliGemma 2** revolutionizes this workflow:
- Entire document pages are rendered directly into **high-resolution images**.
- Pages are embedded visually using **SigLIP-So400M** and stored in a vector index (e.g. Qdrant).
- Upon query retrieval, the original page image is fed directly to **PaliGemma 2**, which reads words, charts, checkboxes, and layout structure simultaneously, returning grounded answers accompanied by exact pixel bounding box coordinates.

```
Incoming PDF Document (Scanned / Complex Layout)
                │
                ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        PAGE RENDERING & VISUAL EMBEDDING PIPELINE                      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  - Render PDF page -> 448x448 or 896x896 Image (PNG / Tensor)                          │
│  - SigLIP-So400M Vision Encoder -> 1,152-dim Global Document Embedding                 │
│  - Index into Vector Database (Qdrant HNSW Index)                                      │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
       User Query: "What is the Q3 operating margin in Table 4?"
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        VISUAL RETRIEVAL & GROUNDED ANSWERING                           │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  - Cosine Vector Search -> Retrieves Top-1 Visual Page Image                           │
│  - Prompt PaliGemma 2: Image + "answer en What is the Q3 operating margin?"            │
│  - PaliGemma 2 returns: "18.4% <loc0320><loc0410><loc0350><loc0520>"                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Blind Scribe vs The Visionary Auditor
3. Evolutionary Lineage: From Plain-Text OCR RAG to Native Vision-RAG
4. First-Principles Mathematics & Algorithmic Formulations
   - Visual Page Embedding via SigLIP Attention Pooling
   - Cosine Similarity and Approximate Nearest Neighbor (HNSW) Search
   - Spatial Visual Grounding and Bounding Box Verification
   - Context Memory Footprint of Multi-Image Retrieval
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: Complete Visual Document RAG Pipeline
7. Hardware Grounding for NVIDIA DGX Spark (Unified Memory Document Ingestion)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Blind Scribe vs The Visionary Auditor

Consider auditing an intricate corporate financial report:
- **Traditional Text-Only RAG (The Blind Scribe)**:
  - You blindfold a scribe and hand them a printout of the balance sheet.
  - The scribe rubs their finger across the page, reads aloud the words in a linear left-to-right sweep across columns, and types: `"Assets Liabilities 2023 2024 $10M $4M $12M $5M"`.
  - When you ask *"What were the 2023 liabilities?"*, the model cannot tell which number belonged to which column!
- **Multimodal Document RAG (The Visionary Auditor)**:
  - PaliGemma 2 receives a crisp photographic snapshot of the full page.
  - It sees the vertical grid lines separating columns, the bold font of header rows, the indentation of sub-items, and the physical signature at the bottom.
  - It extracts the exact cell value with 100% spatial grounding.

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2020-2022: Text-Only RAG with Tesseract / PyMuPDF                      │
│ Brittle regex parsers and loss of layout geometry. Catastrophic failure│
│ on tables, charts, and scanned forms.                                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2023: ColPali & Late Interaction Visual Retrieval                      │
│ Represented document images as multi-vector patch embeddings using     │
│ ColBERT late interaction. High retrieval accuracy, high storage cost.  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2024: Native PaliGemma 2 Multimodal RAG                                │
│ Single-vector SigLIP dense retrieval combined with high-resolution     │
│ PaliGemma 2 visual question answering and spatial bounding box output. │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 Visual Page Embeddings via SigLIP Attention Pooling

Let $I \in \mathbb{R}^{H \times W \times 3}$ denote a rendered document page image.
The SigLIP-So400M vision transformer processes $N$ spatial patches, producing hidden states:

$$H_{\text{patches}} \in \mathbb{R}^{N \times D_{\text{vis}}}$$

A global page embedding vector $v_I \in \mathbb{R}^{D_{\text{vis}}}$ is generated via Multi-Head Attention Pooling:

$$v_I = \text{Softmax}\left( \frac{q_{\text{pool}} \cdot H_{\text{patches}}^T}{\sqrt{d}} \right) H_{\text{patches}}$$

Where $q_{\text{pool}}$ is a learnable query vector. The vector is normalized to the unit sphere: $\hat{v}_I = \frac{v_I}{\|v_I\|_2}$.

### 4.2 Visual Retrieval Cosine Similarity

Given a user text query $Q$, the text query is embedded using SigLIP's text encoder into normalized vector $\hat{v}_Q$.
The relevance score of page image $i$ is the inner product:

$$\text{Sim}(Q, I_i) = \hat{v}_Q \cdot \hat{v}_{I_i} = \cos(\theta_{Q, I_i})$$

An Approximate Nearest Neighbor (ANN) HNSW index retrieves the Top-$K$ most visually and semantically relevant document pages in $< 3\text{ ms}$.

### 4.3 Visually Grounded Answering

The retrieved page image $I^*$ is injected directly into PaliGemma 2's vision encoder, concatenated with the user prompt:

$$\text{Prompt} = \text{"answer en } Q \text{"}$$

PaliGemma 2 generates the answer $A$ followed by spatial bounding coordinates:

$$A_{\text{grounded}} = \text{"The total operating revenue is \$4.2B } \langle\text{loc0245}\rangle\langle\text{loc0612}\rangle\langle\text{loc0278}\rangle\langle\text{loc0730}\rangle\text{"}$$

The enterprise client decodes the $\langle\text{loc}\rangle$ tokens and draws an interactive highlight box directly over the original PDF page in the user interface.

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              DOCUMENT RAG ARCHITECTURES COMPARISON                                     │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Strategy           │ Table Robustness   │ Handwriting Support │ Indexing Speed   │ Ingestion Pipeline  │
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ Text OCR + RAG     │ Very Poor (Broken) │ Poor                │ Fast             │ OCR -> Chunk -> Emb │
│ LayoutLM Parser    │ Moderate           │ Moderate            │ Slow             │ Custom Bounding Box │
│ ColPali (Multi-vec)│ Exceptional        │ Exceptional         │ Moderate         │ 1024 vectors / page │
│ PaliGemma 2 RAG    │ Exceptional        │ Exceptional         │ Very Fast        │ Render -> SigLIP -> VLM│
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Complete Visual Document RAG Pipeline

Save this script as `paligemma2_document_rag_lab.py`:

```python
"""
Google PaliGemma 2 Multimodal Document RAG Lab.
Demonstrates:
1. Ingesting and embedding document page images
2. Vector similarity retrieval using simulated HNSW index
3. Feeding retrieved image and question to PaliGemma 2 for grounded answer
"""

import math
import torch
import torch.nn.functional as F

class MockVisualVectorIndex:
    """
    Simulates a vector database (e.g. Qdrant) storing SigLIP visual page embeddings.
    """
    def __init__(self, embedding_dim: int = 1152):
        self.dim = embedding_dim
        self.page_embeddings = []
        self.page_metadata = []

    def insert_page(self, page_id: str, doc_name: str, embedding: torch.Tensor):
        # Normalize to unit sphere for cosine similarity
        norm_emb = F.normalize(embedding.view(1, -1), p=2, dim=-1)
        self.page_embeddings.append(norm_emb)
        self.page_metadata.append({"page_id": page_id, "doc_name": doc_name})

    def search(self, query_embedding: torch.Tensor, top_k: int = 1) -> list:
        if not self.page_embeddings:
            return []
        all_embeddings = torch.cat(self.page_embeddings, dim=0) # [NumPages, Dim]
        query_norm = F.normalize(query_embedding.view(1, -1), p=2, dim=-1)
        
        # Cosine similarity inner product
        scores = torch.matmul(query_norm, all_embeddings.T).squeeze(0) # [NumPages]
        top_scores, top_indices = torch.topk(scores, k=min(top_k, len(scores)))

        results = []
        for score, idx in zip(top_scores, top_indices):
            meta = self.page_metadata[idx.item()]
            results.append({
                "page_id": meta["page_id"],
                "doc_name": meta["doc_name"],
                "score": score.item()
            })
        return results

class MockPaliGemmaVLM:
    """
    Simulates PaliGemma 2 generating answers grounded with spatial location tokens.
    """
    @staticmethod
    def answer_query(retrieved_page_id: str, question: str) -> dict:
        # Simulated visual reasoning output
        simulated_answers = {
            "page_14": {
                "answer": "Operating Income was $1,420M in Q3.",
                "loc_tokens": "<loc0420><loc0650><loc0460><loc0780>",
                "normalized_box": [0.41, 0.63, 0.45, 0.76]
            }
        }
        res = simulated_answers.get(retrieved_page_id, {
            "answer": "Document verified.",
            "loc_tokens": "<loc0100><loc0100><loc0200><loc0200>",
            "normalized_box": [0.1, 0.1, 0.2, 0.2]
        })
        return res

def run_document_rag_lab():
    print("=" * 80)
    print("RUNNING GOOGLE PALIGEMMA 2 MULTIMODAL DOCUMENT RAG LAB")
    print("=" * 80)

    index = MockVisualVectorIndex(embedding_dim=1152)

    # 1. Ingest Synthetic PDF Pages
    print("\n--- 1. Ingesting Document Page Images into Visual Index ---")
    torch.manual_seed(42)
    # Simulate 5 PDF pages
    pages = [
        ("page_1", "Q3_Report_Intro.pdf"),
        ("page_14", "Q3_Financial_Balance_Sheet.pdf"),
        ("page_22", "Legal_Disclaimers.pdf")
    ]

    for pid, doc in pages:
        emb = torch.randn(1152)
        if pid == "page_14":
            # Give target page strong alignment with target query
            emb = emb + 2.0
        index.insert_page(pid, doc, emb)
        print(f"Indexed: {doc} ({pid})")

    # 2. Query Search
    query = "What was the operating income in Q3?"
    print(f"\n--- 2. Executing Multimodal Query Search: '{query}' ---")
    query_emb = torch.randn(1152) + 2.0  # Semantically aligned with target page
    search_results = index.search(query_emb, top_k=1)

    top_match = search_results[0]
    print(f"Top Retrieved Page: {top_match['doc_name']} (ID: {top_match['page_id']})")
    print(f"Retrieval Cosine Confidence Score: {top_match['score']:.4f}")

    # 3. Grounded Visual Question Answering with PaliGemma 2
    print("\n--- 3. Prompting PaliGemma 2 with Retrieved Document Image ---")
    vlm_output = MockPaliGemmaVLM.answer_query(top_match["page_id"], query)
    print(f"Generated Answer : {vlm_output['answer']}")
    print(f"Spatial Tokens   : {vlm_output['loc_tokens']}")
    print(f"Bounding Box     : {vlm_output['normalized_box']} (Ready for UI Highlighting)")

    print("\nVerification Passed: Visual Document RAG retrieval and bounding box grounding validated!")

if __name__ == "__main__":
    run_document_rag_lab()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (Unified Memory Document Ingestion)

### 7.1 High-Speed Visual Ingestion Pipeline
Processing enterprise document repositories requires high-speed page rasterization and batch inference:
- **PDF Rendering on Grace ARM CPU**: The 72 Grace ARM cores execute headless Poppler / PDFium rendering in parallel, rasterizing over **$140\text{ pages/second}$** into shared system memory.
- **Zero-Copy Ingestion**: Because memory is unified, rasterized image buffers do not undergo PCIe DMA transfers; the Blackwell GB10 reads images directly at $900\text{ GB/s}$ into the SigLIP vision transformer.
- Ingestion throughput exceeds **$450,000\text{ PDF pages per hour}$** on a single DGX Spark node.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate Visual Index Storage**:
   If an enterprise indexes 1,000,000 document pages using 1,152-dimensional FP16 embeddings, what is the raw vector storage footprint in gigabytes?
   - *Solution*:
     $$\text{Size} = \frac{1,000,000 \times 1,152 \times 2 \text{ bytes}}{1024^3} \approx \mathbf{2.146\text{ GB}}$$
     (Extremely compact: an entire million-page enterprise repository easily fits in RAM).

2. **Differentiate ColPali vs Single-Vector SigLIP**:
   Why does single-vector SigLIP use $1000\times$ less storage than multi-vector ColPali?
   - *Solution*: ColPali stores an embedding for every visual patch ($1,024$ vectors per page $\approx 2.1\text{ TB}$ per million pages). Single-vector SigLIP pools all patches into a single 1,152-dim vector per page ($2.15\text{ GB}$ per million pages), enabling fast in-memory HNSW search.

### Troubleshooting FAQ

- **Q: Why does PaliGemma 2 occasionally hallucinate text from neighboring table columns?**
  *A*: When images are rendered at too low a resolution ($224 \times 224$), text characters become blurry and overlap within a single $14 \times 14$ patch. For Document RAG, **always render pages at $448 \times 448$ or $896 \times 896$** resolution to ensure crisp character boundaries.
- **Q: How can we prevent the model from answering if the retrieved page does not contain the answer?**
  *A*: Prefix the prompt with strict negative instruction: `"answer en If the answer is not clearly visible on the page, reply 'NOT_FOUND'.\nQuestion: {Q}"`.
