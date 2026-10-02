#!/usr/bin/env bash
# Everything CI runs for the 04 Qwen lab without a GPU (Volume 25 §0).
#   tests/run-local-checks.sh                      static + tool tests against mocks
#   QDRANT=/path/to/qdrant tests/run-local-checks.sh   + multimodal RAG against a real Qdrant
#   API=1 tests/run-local-checks.sh                + server-side dry-run of every manifest and pod template
# Needs: python3 + pillow + pyyaml (+ qwen-agent==0.0.29 for the Qwen-Agent test), kubectl, kubeconform, shellcheck, yamllint.
set -euo pipefail
cd "$(dirname "$0")/.."
KUBECTL=${KUBECTL:-kubectl}
K8S_LAB="../../02 Kubernetes/lab"
DS_LAB="../../03 DeepSeek/lab"
CRD_CATALOG='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
kc() { kubeconform -strict -kubernetes-version 1.32.0 -schema-location default -schema-location "$CRD_CATALOG" -ignore-missing-schemas "$@"; }
T=$(mktemp)
expect() {
  local pat=$1; shift
  "$@" >"$T" 2>&1 || { cat "$T"; return 1; }
  grep -q -- "$pat" "$T" || { cat "$T"; echo "expected output to contain: $pat"; return 1; }
}
pids=(); cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }; trap cleanup EXIT

echo "== yamllint";            yamllint -s .
echo "== overlays in sync";    scripts/gen-overlays.sh --check
echo "== kustomize + kubeconform"
for d in . k8s/models/* k8s/multi/* breakfix/*; do "$KUBECTL" kustomize "$d" | kc -; done
for f in k8s/jobs/*.yaml; do kc "$f"; done
echo "== python compile";      python3 -m py_compile tools/*.py tests/*.py
echo "== shellcheck";          shellcheck -S warning -x scripts/*.sh tests/*.sh
echo "== catalog: every entry fits its util (03 model_math)"
python3 - <<'PY'
import subprocess, sys, yaml
presets = {"qwen2.5-0.5b": "qwen2.5-0.5b", "qwen2.5-7b": "qwen2.5-7b", "qwen2.5-7b-128k": "qwen2.5-7b",
           "qwen2.5-14b": "qwen2.5-14b", "qwen2.5-32b-awq": "qwen2.5-32b", "qwen2.5-coder-7b": "qwen2.5-coder-7b",
           "qwen2.5-coder-7b-base": "qwen2.5-coder-7b", "qwen2.5-coder-32b-awq": "qwen2.5-coder-32b",
           "qwen2.5-math-7b": "qwen2.5-math-7b", "qwen2.5-vl-7b": "qwen2.5-vl-7b", "qwq-32b-awq": "qwq-32b"}
for m in yaml.safe_load(open("models.yaml"))["models"]:
    dtype = "awq" if "awq" in m["name"] else "bf16"
    out = subprocess.run([sys.executable, "../../03 DeepSeek/lab/tools/model_math.py", presets[m["name"]], "--dtype", dtype,
                          "--util", str(m["util"]), "--ctx", str(m["max_len"])], capture_output=True, text=True).stdout
    assert "✓ fits" in out, (m["name"], out)
    print(f"  {m['name']:24} util {m['util']:<5} ✓ fits")
PY
echo "== RoPE / YaRN"
expect "every never-full-turn pair stays inside" python3 tools/rope_yarn.py
expect "raise --factor" python3 tools/rope_yarn.py --factor 2
echo "== FIM eval vs mock (fim 50 %, chat 100 %)"
python3 tests/mock_fim.py 18780 & pids+=($!); sleep 1
expect "pass@1 5/10" python3 tools/fim_eval.py --url http://127.0.0.1:18780 --model m
expect "pass@1 10/10" python3 tools/fim_eval.py --url http://127.0.0.1:18780 --model m --mode chat
echo "== TIR vs CoT vs mock (TIR 100 %, CoT 50 %)"
python3 tests/mock_tir.py 18781 & pids+=($!); sleep 1
expect "40/40 correct" python3 tools/tir_math.py --url http://127.0.0.1:18781 --model m --mode tir
expect "20/40 correct" python3 tools/tir_math.py --url http://127.0.0.1:18781 --model m --mode cot
echo "== vision eval vs mock (50 %) + Qwen2.5-VL token math"
python3 tests/mock_vl.py 18782 & pids+=($!); sleep 1
expect "total 8/16" python3 tools/vl_eval.py --url http://127.0.0.1:18782 --model m --n 4
python3 -c "import sys; sys.path.insert(0,'tools'); from vl_eval import vision_tokens as v
assert v(512,512)[0] == 324 and v(1024,768)[0] == 999 and v(20,20)[0] == 4, (v(512,512), v(1024,768)); print('vision token math OK')"
echo "== synthetic data: verify, dedup, hard-case mining"
python3 tests/mock_synth.py 18783 & pids+=($!); sleep 1
s=$(mktemp -d)
expect "kept=8 student_wrong=4" python3 tools/synth_data.py --url http://127.0.0.1:18783 --teacher t --student s -n 20 --out "$s"
python3 -c "import json,sys; r=[json.loads(l) for l in open(sys.argv[1])]; assert len(r)==4 and all('rejected_response' in x for x in r); print('dpo.jsonl OK')" "$s/dpo.jsonl"
if python3 -c "import qwen_agent" 2>/dev/null; then
  echo "== Qwen-Agent loop vs mock (nous function calling)"
  python3 tests/mock_qwen_agent.py 18784 & pids+=($!); sleep 1
  expect "\[answer\] 3 of 4 slices is 75.0%" env OPENAI_BASE=http://127.0.0.1:18784/v1 MODEL=m python3 tools/qwen_agent_demo.py "What percent of 4 is 3?"
else echo "== Qwen-Agent test skipped (pip install qwen-agent==0.0.29)"; fi
if [[ -n "${QDRANT:-}" ]]; then
  echo "== multimodal RAG: VL transcription → bge-m3 → real Qdrant → VL answer"
  (cd "$(mktemp -d)" && exec "$QDRANT") >/dev/null 2>&1 & pids+=($!)
  python3 "$DS_LAB/tests/mock_embed_chat.py" 18766 & pids+=($!); sleep 4
  pg=$(mktemp -d); E=(--dir "$pg" --vl-url http://127.0.0.1:18782 --embed-url http://127.0.0.1:18766 --qdrant http://127.0.0.1:6333)
  python3 tools/mm_rag.py make-docs --dir "$pg" -n 6 >/dev/null
  expect "collection qwen-mm: 6 pages" python3 tools/mm_rag.py ingest "${E[@]}"
  expect "6 questions  retrieval hit@1" python3 tools/mm_rag.py eval "${E[@]}"
fi
if [[ "${API:-0}" == 1 ]]; then
  echo "== server-side dry-run (API=1)"
  "$KUBECTL" apply --dry-run=server -k . >/dev/null
  for d in k8s/models/* k8s/multi/* breakfix/*; do "$KUBECTL" apply --dry-run=server -k "$d" >/dev/null; done
  for f in k8s/jobs/*.yaml; do "$KUBECTL" apply --dry-run=server -f "$f" >/dev/null; done
  echo "== pod templates vs PSA / CEL policies / quotas"
  python3 "$K8S_LAB/tests/pod_template_check.py" k8s/models/* k8s/multi/* k8s/jobs/*.yaml
fi
echo "ALL LOCAL CHECKS PASSED"
