#!/usr/bin/env python3
"""Multi-head Latent Attention, from scratch, verified (Volume 01).

Builds one attention layer three ways and checks they agree:
  1. MHA  — every head caches its own K and V
  2. MLA naive  — K/V are *reconstructed* from a small latent c_kv (DeepSeek-V2/V3)
  3. MLA absorbed — the decode-time trick: W_UK is folded into the query and W_UV
     into the output, so attention runs directly on the cached latent and K/V
     are never materialised.
Then prints what each variant has to keep in the KV cache per token.

Runs on CPU (laptop/CI) or CUDA (Spark):  python3 mla_attention_demo.py [--device cuda]
Dimensions default to a scaled-down DeepSeek-V3 layer; pass --v3 for the real sizes.
"""
import argparse
import math

import torch


def rope(x, pos, base=10000.0):
    """Rotary embedding on the last dim of x (…, T, d_r); pos: (T,)."""
    d = x.shape[-1]
    inv = 1.0 / (base ** (torch.arange(0, d, 2, device=x.device, dtype=torch.float64) / d))
    ang = pos[:, None].double() * inv[None, :]
    cos, sin = ang.cos().to(x.dtype), ang.sin().to(x.dtype)
    x1, x2 = x[..., 0::2], x[..., 1::2]
    out = torch.empty_like(x)
    out[..., 0::2] = x1 * cos - x2 * sin
    out[..., 1::2] = x1 * sin + x2 * cos
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--v3", action="store_true", help="use DeepSeek-V3 layer sizes (needs a GPU)")
    ap.add_argument("--tokens", type=int, default=64)
    a = ap.parse_args()
    torch.manual_seed(0)
    dev, dt = a.device, torch.float64 if a.device == "cpu" else torch.float32
    if a.v3:
        d, h, dn, dr, dv, r = 7168, 128, 128, 64, 128, 512
    else:
        d, h, dn, dr, dv, r = 512, 8, 32, 16, 32, 64
    T = a.tokens
    s = 1 / math.sqrt(d)
    W_Q = torch.randn(d, h * (dn + dr), device=dev, dtype=dt) * s
    W_DKV = torch.randn(d, r, device=dev, dtype=dt) * s          # down-projection to the latent
    W_UK = torch.randn(r, h * dn, device=dev, dtype=dt) / math.sqrt(r)
    W_UV = torch.randn(r, h * dv, device=dev, dtype=dt) / math.sqrt(r)
    W_KR = torch.randn(d, dr, device=dev, dtype=dt) * s          # decoupled RoPE key, shared by all heads
    W_O = torch.randn(h * dv, d, device=dev, dtype=dt) / math.sqrt(h * dv)
    x = torch.randn(T, d, device=dev, dtype=dt)
    pos = torch.arange(T, device=dev)
    scale = 1 / math.sqrt(dn + dr)
    mask = torch.full((T, T), float("-inf"), device=dev, dtype=dt).triu(1)

    q = (x @ W_Q).view(T, h, dn + dr)
    q_nope, q_pe = q[..., :dn], rope(q[..., dn:].transpose(0, 1), pos).transpose(0, 1)
    c_kv = x @ W_DKV                                             # (T, r)   ← cached
    k_pe = rope(x @ W_KR, pos)                                   # (T, dr)  ← cached

    # --- 2. MLA naive: reconstruct per-head K, V from the latent -------------------------
    k_nope = (c_kv @ W_UK).view(T, h, dn)
    v = (c_kv @ W_UV).view(T, h, dv)
    scores = (torch.einsum("thd,shd->hts", q_nope, k_nope) + torch.einsum("thd,sd->hts", q_pe, k_pe)) * scale
    out_naive = torch.einsum("hts,shd->thd", torch.softmax(scores + mask, -1), v).reshape(T, h * dv) @ W_O

    # --- 3. MLA absorbed: attend over the latent directly ------------------------------
    W_UK_h = W_UK.view(r, h, dn)                                 # per-head slices
    W_UV_h = W_UV.view(r, h, dv)
    q_lat = torch.einsum("thd,rhd->thr", q_nope, W_UK_h)         # fold W_UK into the query
    scores_abs = (torch.einsum("thr,sr->hts", q_lat, c_kv) + torch.einsum("thd,sd->hts", q_pe, k_pe)) * scale
    o_lat = torch.einsum("hts,sr->thr", torch.softmax(scores_abs + mask, -1), c_kv)   # (T, h, r)
    out_abs = torch.einsum("thr,rhd->thd", o_lat, W_UV_h).reshape(T, h * dv) @ W_O    # fold W_UV into output

    # --- 1. plain MHA with the same K/V (as if they had been cached per head) ----------------
    k_full = torch.cat([k_nope, k_pe[:, None, :].expand(T, h, dr)], -1)
    q_full = torch.cat([q_nope, q_pe], -1)
    out_mha = torch.nn.functional.scaled_dot_product_attention(
        q_full.transpose(0, 1), k_full.transpose(0, 1), v.transpose(0, 1), is_causal=True
    ).transpose(0, 1).reshape(T, h * dv) @ W_O

    ref = out_naive.abs().max().item()
    err_abs = (out_naive - out_abs).abs().max().item() / ref        # relative to the output scale
    err_mha = (out_naive - out_mha).abs().max().item() / ref
    print(f"device={dev} dtype={dt} d={d} heads={h} nope={dn} rope={dr} v={dv} latent r={r} tokens={T}")
    print(f"max rel. |naive - absorbed| = {err_abs:.2e}   max rel. |naive - MHA| = {err_mha:.2e}")
    tol = 1e-10 if dt == torch.float64 else 1e-3
    assert err_abs < tol and err_mha < tol, "variants disagree"
    print("✓ all three formulations produce the same output\n")

    elems_mha = 2 * h * (dn + dr)            # K (nope+rope) and V per head (V uses dv; dn == dv here)
    elems_gqa8 = 2 * 8 * (dn + dr)           # a GQA model with 8 KV heads
    elems_mla = r + dr
    print("KV cache per token per layer (elements):")
    print(f"  MHA ({h} heads)       : {elems_mha:6d}")
    print(f"  GQA (8 KV heads)      : {elems_gqa8:6d}")
    print(f"  MLA (latent + rope)   : {elems_mla:6d}   → {elems_mha / elems_mla:.1f}× smaller than MHA")


if __name__ == "__main__":
    main()
