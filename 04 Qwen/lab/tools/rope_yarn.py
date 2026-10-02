#!/usr/bin/env python3
"""RoPE and YaRN for Qwen2.5, in numbers (Volume 02).

Qwen2.5 rotates each pair of query/key dimensions i by angle  pos · θ_i,
θ_i = base^(-2i/d)  (base 1,000,000, head_dim 128). Training saw positions < 32,768.
A dimension whose wavelength 2π/θ_i is LONGER than the training context never completed
a full turn in training — beyond 32K it produces angles the model has never seen.

YaRN ("NTK-by-parts", Peng et al. 2023) fixes exactly those dimensions:
  r_i = L_train / wavelength_i
  r_i < α (1)   → low-frequency: interpolate, θ_i / s     (s = scale factor, 4 for 32K→128K)
  r_i > β (32)  → high-frequency: keep θ_i                 (local word order stays sharp)
  in between    → linear ramp
and scales attention logits by mscale = 0.1·ln(s) + 1.

  python3 rope_yarn.py                       # Qwen2.5 defaults, s = 4
  python3 rope_yarn.py --factor 8 --target 262144
Stdlib only.
"""
import argparse
import math


def inv_freq(base, d):
    return [base ** (-2 * i / d) for i in range(d // 2)]


def yarn(theta, s, l_train, alpha=1.0, beta=32.0):
    out, regime = [], []
    for t in theta:
        r = l_train / (2 * math.pi / t)
        g = 0.0 if r < alpha else 1.0 if r > beta else (r - alpha) / (beta - alpha)
        out.append((1 - g) * t / s + g * t)
        regime.append("interp" if g == 0 else "keep" if g == 1 else "ramp")
    return out, regime


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", type=float, default=1_000_000.0)
    ap.add_argument("--head-dim", type=int, default=128)
    ap.add_argument("--train", type=int, default=32_768, help="original_max_position_embeddings")
    ap.add_argument("--factor", type=float, default=4.0, help="YaRN scale s")
    ap.add_argument("--target", type=int, default=131_072)
    a = ap.parse_args()

    th = inv_freq(a.base, a.head_dim)
    th_y, reg = yarn(th, a.factor, a.train)
    n = len(th)
    unseen = [i for i, t in enumerate(th) if a.train * t < 2 * math.pi]       # never completed a full turn
    print(f"RoPE base {a.base:,.0f}, head_dim {a.head_dim} → {n} rotary frequency pairs; trained to {a.train:,}, target {a.target:,}")
    print(f"\n{'pair':>4} {'wavelength (tokens)':>20} {'turns in training':>18}  {'regime':7} {'max angle @target (rad)':>24} {'with YaRN':>10}")
    for i in list(range(0, n, 8)) + [n - 1]:
        wl = 2 * math.pi / th[i]
        print(f"{i:4d} {wl:20,.0f} {a.train / wl:18.3f}  {reg[i]:7} {a.target * th[i]:24.2f} {a.target * th_y[i]:10.2f}")
    counts = {k: reg.count(k) for k in ("keep", "ramp", "interp")}
    print(f"\nYaRN s={a.factor:g}: keep {counts['keep']} high-frequency pairs, ramp {counts['ramp']}, interpolate {counts['interp']} low-frequency pairs")
    print(f"pairs that never completed a turn in training: {len(unseen)} "
          f"(their angles at {a.target:,} exceed anything seen in training without scaling)")
    print(f"lowest-frequency pair {n - 1}: trained max angle {a.train * th[n - 1]:.3f} rad; at target "
          f"{a.target * th[n - 1]:.3f} rad unscaled vs {a.target * th_y[n - 1]:.3f} rad with YaRN")
    ok = all(a.target * ty <= a.train * t * 1.0001 or a.train * t >= 2 * math.pi for t, ty in zip(th, th_y))
    print("✓ with YaRN every never-full-turn pair stays inside its trained angle range" if ok else
          "✗ some low-frequency pairs still leave their trained range — raise --factor")
    print(f"attention temperature (mscale) = 0.1·ln({a.factor:g}) + 1 = {0.1 * math.log(a.factor) + 1:.4f}")


if __name__ == "__main__":
    main()
