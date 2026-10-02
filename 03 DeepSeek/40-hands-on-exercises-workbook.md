# Volume 40 — Hands-On Workbook: 30 Graded Exercises, a Release Gate and a Capstone on One DGX Spark

> **Module 03 · Part X — Operate and practise** · Prev: [39 Troubleshooting](39-master-troubleshooting-playbook.md) · Next: [41 Multi-ecosystem deployment](41-multi-ecosystem-qwen-llama-nemo-deployment.md)

| | |
|---|---|
| **You will build** | Proof that you can do what Module 03 teaches: 30 exercises with objective pass criteria (a command and its expected result), the accuracy release gate used for every model change, and a capstone that rebuilds the stack, upgrades a model through the gate and survives an unannounced fault |
| **Hardware** | spark-01 (exercises marked **2×** need spark-02) |
| **Time** | 3–5 days part-time. The capstone takes half a day |
| **Risk** | Low. Every exercise is reversible |
| **Lab files** | the whole [`lab/`](lab/), [`tests/run-local-checks.sh`](lab/tests/run-local-checks.sh), [`tools/eval_harness.py`](lab/tools/eval_harness.py) (`--gate`), [`scripts/verify.sh`](lab/scripts/verify.sh), [`scripts/llm-triage.sh`](lab/scripts/llm-triage.sh) |

---

## 0. Toolchain and CI: what "green" means

Everything in this module is checked twice: locally without a GPU, and in GitHub Actions on every pull request (`.github/workflows/deepseek-lab-ci.yml`).

```bash
cd "03 DeepSeek/lab"
pip install yamllint shellcheck-py pyyaml torch "trl==0.23.0" "peft==0.17.1" "transformers==4.56.2" "accelerate==1.10.1" "datasets==4.1.1"
# kubectl, kubeconform, promtool, mikefarah yq: versions in the CI workflow
tests/run-local-checks.sh                         # static + tools + mocks
QDRANT=/path/to/qdrant API=1 tests/run-local-checks.sh   # + real Qdrant + server-side dry-run + pod templates
```

| Stage | What it proves |
|---|---|
| yamllint, kubeconform, server dry-run, pod-template admission | every manifest is valid and admissible (Vol 19) |
| overlays in sync, dashboard in sync | generated files match their sources (Vols 15, 38) |
| promtool check + `promtool test rules` | alerts parse, and fire or stay quiet as intended (Vol 38) |
| architecture demos on CPU | MLA, MoE routing, FP8 scaling, ring attention, EPLB, grouped GEMM (Vols 01–09) |
| tool tests against mocks | eval harness and gate, stream probe, agent, RAG with blue/green and gate, vault-sync rotation, catalog drift, format check |
| Ansible job (CI) | `ansible-lint`, check-mode run on kind, budget guard refuses over-budget (Vol 31) |

**Pass:** `ALL LOCAL CHECKS PASSED`, and the PR's checks are green.

---

## 1. How to use this workbook

Each exercise has a **goal**, the **doing** (volume and section) and a **pass** criterion you can check by command. Keep a lab notebook (`results/notebook.md`) with every "record yours" number. Later exercises compare against it.

---

## 2. Architecture — HLD (learning path)

```mermaid
flowchart LR
  P0["§0 CI green"] --> P1["I · Architecture<br/>E1–E5"] --> P2["II · Kernels & context<br/>E6–E8"] --> P3["III · Models<br/>E9–E10"] --> P4["IV · Engines<br/>E11–E12"]
  P4 --> P5["V · Platform<br/>E13–E16"] --> P6["VI · Training & RL<br/>E17–E20"] --> P7["VII · Apps<br/>E21–E24"] --> P8["VIII · Automation & ops<br/>E25–E27"]
  P8 --> P9["IX · Comparisons<br/>E28"] --> P10["X · Operate<br/>E29–E30"] --> CAP(["Capstone"])
  classDef ctrl fill:#1f6feb,stroke:#0b3d91,color:#fff
  classDef gpu fill:#76b900,stroke:#3d6000,color:#000
  classDef store fill:#bf8700,stroke:#7a5600,color:#fff
  classDef sec fill:#cf222e,stroke:#82071e,color:#fff
  classDef obs fill:#fb8500,stroke:#9a5000,color:#000
  class P0,P5,P8 ctrl
  class P1,P2,P3,P4,P6 gpu
  class P7 store
  class P9,P10 obs
  class CAP sec
```

---

## 3. LLD — grading and the release gate

### 3.1 Rubric

| Grade | Requirement |
|---|---|
| Pass | every exercise's pass criterion met, numbers recorded |
| Merit | Pass + 8 drills in < 10 min each (E30) + one comparison memo (E28) |
| Distinction | Merit + capstone completed within the time box, with an operations report a colleague could act on |

### 3.2 The release gate

Every model change (new revision, new quantisation, new engine) must not reduce accuracy on your suites by more than a tolerance:

```bash
# once, on the accepted version
python3 tools/eval_harness.py --url http://localhost:8000 --model r1-7b --suites math json --out results/baseline-r1-7b.json
# on every candidate
python3 tools/eval_harness.py --url http://localhost:8000 --model r1-7b --suites math json \
  --gate results/baseline-r1-7b.json --tolerance 0.02 --out results/candidate-r1-7b.json
```

```text
gate vs results/baseline-r1-7b.json (tolerance 0.02):
  json  baseline 0.88  now 0.88  PASS
  math  baseline 0.80  now 0.78  PASS
GATE PASSED
```

The exit code is non-zero on `GATE FAILED`, so it drops into CI, Ansible or Argo CD post-sync hooks. With only 40 math items, one question is 2.5 points. Pick the tolerance with that granularity in mind, or run several seeds.

---

## 4. Integrations

The exercises reuse every lab file. Nothing here is new infrastructure. The gate (`--gate`) is used by the Vol 33 upgrade runbook and the capstone.

---

## 5. Lab — the exercises

### Part I — Architecture, proved in code (Vols 01–05)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E1 | Show the absorbed MLA decode is faster | `mla_attention_demo.py --decode-bench 8192 --batch 8 --device cuda` (Vol 01 §5) | outputs agree (relative error within tolerance) and absorbed is faster. Ratio recorded |
| E2 | Balance experts without an aux loss | `moe_router_demo.py --steps 200` (Vol 02) | max/mean expert load *with* balancing < without |
| E3 | Predict, then measure, speculative speed-up | `spec_decode_calc.py --alpha <measured α>` vs the `r1-7b-draft` overlay (Vol 03) | measured speed-up within ±25 % of the prediction |
| E4 | Show why block scaling exists | `fp8_blockscale.py` (Vol 04) | block-scaled error ≪ per-tensor error with outliers |
| E5 | Train GRPO and see the reward rise | `k8s/jobs/grpo.yaml` (Vol 05) | `reward` at step 200 > step 10. Format % up in `format_check.py` |

### Part II — Kernels and long context (Vols 06–10)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E6 | Explain grouped GEMM | `grouped_gemm_bench.py` on GPU (Vol 07) | grouped ≥ padded ≥ loop in speed. Explanation written |
| E7 | Prove 128K retrieval | `k8s/long-context/r1-7b-128k` + `needle_test.py` (Vol 08) | needles found at all depths up to 128K, or a written analysis of misses |
| E8 | Size redundant experts | `eplb_sim.py --experts 256 --gpus 32 --redundant 32` (Vol 09) | max/mean GPU load improvement recorded |

### Part III — Models on one GB10 (Vols 11–14)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E9 | Predict fit before deploying | `model_math.py` for 3 catalog models, then serve them (Vols 12, 15) | each prediction (fits, sequences, decode ceiling) matches reality within ~20 % |
| E10 | Measure FP8's cost | `compare-models.sh r1-32b r1-32b-fp8` (Vols 11, 35) | accuracy delta and tok/s delta recorded |

### Part IV — Serving engines (Vols 15–18)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E11 | Add a model to the catalog | new entry (e.g. R1-Distill-Qwen-14B) + `gen_overlays.py` + PR (Vol 15) | CI green. `serve-model.sh` smoke test prints 144 |
| E12 | Engine bake-off | same FP8 32B on vLLM, SGLang, TRT-LLM (Vols 16, 18) | three-engine table filled. Engine choice justified |

### Part V — Platform (Vols 19–22)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E13 | Catch an admission failure in CI | add `hostNetwork: true` to `k8s/jobs/eval.yaml`, run `API=1 tests/run-local-checks.sh` (Vol 19) | the pod-template check rejects it. Reverted |
| E14 | Cold vs warm load | Vol 20 §5.3 | table of three load times. You can explain what dominates each |
| E15 | Timeouts end to end | `stream_probe.py` at 3 hops + D04 (Vol 21) | hop table filled. D04 solved |
| E16 | Serve by day, train by night | office-hours ScaledObject + urgent preemption (Vol 22) | vLLM goes 1→0→1 on schedule. `Preempted` event observed |

### Part VI — Fine-tuning and RL (Vols 23–26)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E17 | Adapter that changes behaviour | SFT + publish + `format_check.py` base vs adapter (Vol 23) | format % with adapter ≥ base + 30 points |
| E18 | Same fine-tune, three tools | Vol 24 | table: wall time, peak memory, quality for TRL, Unsloth, LLaMA-Factory |
| E19 | Faster rollouts | `grpo.yaml` vs `grpo-vllm.yaml` (Vol 25) | s/step lower with vLLM. Ratio recorded |
| E20 | Full 7B fine-tune | ZeRO-3 NVMe (1×) **or** FSDP (2×) (Vol 26) | 30 steps complete. Per-rank or GB10 peak memory recorded |

### Part VII — Applications (Vols 27–30)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E21 | Restore chats | WebUI backup + restore drill (Vol 27) | a deleted chat reappears. Restore time recorded |
| E22 | Tenant isolation | team + key with model list, RPM, budget (Vol 28) | 401 for a disallowed model, 429 above the RPM, spend > 0 |
| E23 | Safe re-index | RAG ingest with gate, rollback, failing gate (Vol 29) | hit@5 ≥ 0.6. Rollback moves the alias. Gate failure leaves it unchanged |
| E24 | Least-privilege agent | in-cluster agent + `kubectl auth can-i` (Vol 30) | correct answer. `delete pods` and `get secrets` → no. Audit lines present |

### Part VIII — Automation and operations (Vols 31–33)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E25 | One-click with rollback | playbook `--check`, real run, `--tags serve` rollback (Vol 31) | `verify.sh` 0 failed. The previous model restored by one command |
| E26 | Rotate a secret | LiteLLM key rotation via Vault (Vol 32) | old key 401, new key 200, consumers restarted automatically |
| E27 | Gated upgrade | pin → bump → stage → `--gate` → promote (Vol 33 §3.4) | `GATE PASSED` before promotion. New manifest recorded |

### Part IX — Comparisons (Vols 34–37)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E28 | A model recommendation | any one of Vols 34–37 end to end | a one-page memo: table, cost per correct answer, latency, governance, decision |

### Part X — Operate (Vols 38–39)

| # | Goal | Do | Pass when |
|---|---|---|---|
| E29 | Alerts that work | `promtool test rules` + make `ReasoningTruncated` fire (Vol 38) | SUCCESS + alert Firing then Resolved |
| E30 | Eight drills | Vol 39 §5.2 | all eight diagnosed, each < 10 min, signal recorded |

### Capstone (half a day, time-boxed)

1. **Rebuild**: from the 02 platform, deploy the full stack with the Ansible playbook (Vol 31) for `r1-7b`. `verify.sh` shows 0 failed.
2. **Baseline**: record `results/baseline-r1-7b.json` with `eval_harness.py` (math + json, sandboxed code via the eval Job).
3. **Upgrade**: switch the reasoning alias to `r1-32b-fp8` through the gate against the 7B baseline (it should pass on math). Promote with `--tags serve`. Update the LiteLLM alias.
4. **Operate**: a partner injects one drill (D01–D08) without telling you which. Diagnose and fix it with `llm-triage.sh` and the playbook, against the clock.
5. **Report**: one page covering what's serving, its measured performance (TTFT p95, ITL p50, tokens/J), cost per correct answer, open risks, and what you'd buy or change next (spark-02, a bigger model, hosted fallback).

**Pass:** all five steps within four hours. The report has numbers from your dashboard and results files, not estimates.

---

## 6. Verify

```bash
tests/run-local-checks.sh && scripts/verify.sh && scripts/llm-triage.sh
ls results/*.json && wc -l results/notebook.md
```

| Check | Expected |
|---|---|
| CI | green on your fork's PR |
| notebook | every "record yours" filled in |
| gate | at least one PASS and one deliberate FAIL demonstrated |
| capstone | report saved next to the results |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `run-local-checks.sh` fails at kubeconform with "could not find schema" | offline, CRD catalog unreachable | run online. CI always has network |
| gate always passes | baseline taken from the same run | take the baseline from the accepted version only, and keep it under `results/` |
| gate flaps between runs | sampling noise on 40 items | `--temperature 0.6` stays, but average 3 runs, or widen the tolerance knowingly |
| exercise blocked by memory | another engine or Job still running | `kubectl get pods -A -o wide | grep -E 'vllm|sglang|trtllm|batch'`. Scale down |

---

## 8. Scale-out path

| Here | Team setting |
|---|---|
| your notebook | a shared results repo. Every model change is a PR with harness output attached |
| gate by hand | gate in CI (on a GPU runner) and as an Argo CD post-sync hook |
| one capstone | quarterly game days, rotating who injects and who responds |

---

## 9. Checklist

- [ ] CI is green and I know what each stage proves.
- [ ] All 30 exercises pass with recorded numbers.
- [ ] Every model change in my lab goes through the gate.
- [ ] I completed the capstone and wrote the report.
