#!/usr/bin/env bash
# Everything CI runs for the 03 DeepSeek lab without a GPU (Volume 40 §0).
#   tests/run-local-checks.sh            static + tool tests (needs: python3 + torch CPU, pyyaml, kubectl, kubeconform, promtool, shellcheck)
#   QDRANT=/path/to/qdrant tests/run-local-checks.sh   + RAG against a real Qdrant binary
#   API=1 tests/run-local-checks.sh      + server-side dry-run of every manifest and pod template
set -euo pipefail
cd "$(dirname "$0")/.."
KUBECTL=${KUBECTL:-kubectl}
K8S_LAB="../../02 Kubernetes/lab"
CRD_CATALOG='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
kc() { kubeconform -strict -kubernetes-version 1.32.0 -schema-location default -schema-location "$CRD_CATALOG" -ignore-missing-schemas "$@"; }
T=$(mktemp)
expect() {   # expect <pattern> <command…>: run it, fail loudly unless the output contains <pattern>
  local pat=$1; shift
  "$@" >"$T" 2>&1 || { cat "$T"; return 1; }
  grep -q -- "$pat" "$T" || { cat "$T"; echo "expected output to contain: $pat"; return 1; }
}
pids=(); cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }; trap cleanup EXIT

echo "== yamllint";            yamllint -s .
echo "== overlays in sync";    python3 scripts/gen_overlays.py --check
echo "== kustomize + kubeconform"
for d in . k8s/models/* k8s/sglang/* k8s/apps observability breakfix/*; do "$KUBECTL" kustomize "$d" | kc -; done
for f in k8s/jobs/*.yaml k8s/ops/*.yaml k8s/trtllm/*.yaml k8s/llamacpp/*.yaml k8s/multinode/*.yaml; do kc "$f"; done
echo "== promtool";            promtool check rules <(python3 -c "
import yaml
for d in yaml.safe_load_all(open('observability/rules.yaml')): print(yaml.safe_dump({'groups': d['spec']['groups']}))")
echo "== dashboard up to date"; diff -q <(python3 observability/gen_dashboard.py) observability/deepseek-serving-dashboard.json
echo "== python compile";      python3 -m py_compile tools/*.py tests/*.py scripts/*.py observability/*.py
echo "== shellcheck";          shellcheck -S warning -x scripts/*.sh tests/*.sh

echo "== model_math: published parameter counts"
python3 - <<'PY'
import sys; sys.path.insert(0, "tools")
from model_math import params, resolve, kv_bytes_per_token
t, a = params(resolve("deepseek-v3"));      assert 668e9 < t < 674e9 and 36e9 < a < 39e9, (t, a)   # 671B / 37B
t, a = params(resolve("deepseek-v2-lite")); assert 15.5e9 < t < 16e9 and 2.3e9 < a < 2.9e9, (t, a)  # 15.7B / 2.4B
t, _ = params(resolve("qwen2.5-32b"));      assert 32.5e9 < t < 33.0e9, t
assert kv_bytes_per_token(resolve("deepseek-v3")) == 61 * (512 + 64) * 2
assert kv_bytes_per_token(resolve("qwen2.5-7b")) == 2 * 28 * 4 * 128 * 2
print("model_math OK")
PY
echo "== architecture demos (CPU)"
expect "all three formulations produce the same output" python3 tools/mla_attention_demo.py
expect "WITH aux-loss-free" python3 tools/moe_router_demo.py --steps 40
expect "Block scales" python3 tools/fp8_blockscale.py --n 512
expect "identical" python3 tools/ring_attention_demo.py --world 2 --seq 512
expect "eplb-lite" python3 tools/eplb_sim.py --experts 64 --gpus 8 --redundant 8
python3 tools/spec_decode_calc.py --sweep >/dev/null
echo "== training tools (dry run)"
expect "rewards behave" python3 tools/grpo_tiny.py --dry-run
expect "<answer>" python3 tools/sft_lora.py --dry-run
expect "40.4 M" python3 tools/lora_calc.py deepseek-r1-distill-qwen-7b --rank 16

echo "== eval harness vs oracle mock (must score exactly 50 % per suite)"
python3 tests/mock_oracle.py 18765 & pids+=($!); sleep 1
out=$(mktemp -d)
python3 tools/eval_harness.py --url http://127.0.0.1:18765 --model mock --out "$out/r.json" >/dev/null
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); acc={k:v['accuracy'] for k,v in d['suites'].items()}; assert acc=={'math':0.5,'code':0.5,'json':0.5}, acc; print('eval harness OK', acc)" "$out/r.json"
echo "== agent tool loop vs scripted mock"
python3 tests/mock_tools_server.py 18767 & pids+=($!); sleep 1
expect "20.35" python3 tools/agent_tools.py "What is 17% of 119.7?" --url http://127.0.0.1:18767
echo "== weights manifest"
w=$(mktemp -d); echo a > "$w/x.safetensors"; python3 tools/weights_verify.py snapshot "$w" --out "$w.json" >/dev/null
python3 tools/weights_verify.py verify "$w" "$w.json" >/dev/null && echo b > "$w/x.safetensors" && ! python3 tools/weights_verify.py verify "$w" "$w.json" >/dev/null && echo "weights_verify OK"

if [[ -n "${QDRANT:-}" ]]; then
  echo "== RAG against a real Qdrant"
  (cd "$(mktemp -d)" && exec "$QDRANT") >/dev/null 2>&1 & pids+=($!)
  python3 tests/mock_embed_chat.py 18766 & pids+=($!); sleep 4
  python3 tools/rag_demo.py ingest --repo ../.. --dirs "02 Kubernetes" --qdrant http://127.0.0.1:6333 --embed-url http://127.0.0.1:18766 >/dev/null
  expect "21-vllm-high-throughput-llm-serving.md" python3 tools/rag_demo.py ask "preStop sleep grace period streams rollout" \
    --qdrant http://127.0.0.1:6333 --embed-url http://127.0.0.1:18766 --chat-url http://127.0.0.1:18766
  echo "rag OK"
fi

if [[ "${API:-0}" == 1 ]]; then
  echo "== server-side dry-run (API=1)"
  "$KUBECTL" apply -k "$K8S_LAB/manifests/00-platform" >/dev/null
  "$KUBECTL" apply -f k8s/multinode/namespace.yaml >/dev/null
  "$KUBECTL" apply --dry-run=server -k . >/dev/null
  for d in k8s/models/* k8s/sglang/* k8s/apps observability; do "$KUBECTL" apply --dry-run=server -k "$d" >/dev/null; done
  for f in k8s/jobs/*.yaml k8s/ops/*.yaml k8s/trtllm/*.yaml k8s/llamacpp/*.yaml k8s/multinode/*.yaml; do "$KUBECTL" apply --dry-run=server -f "$f" >/dev/null; done
  echo "== pod templates vs PSA / CEL policies / quotas"
  python3 "$K8S_LAB/tests/pod_template_check.py" k8s/models/* k8s/sglang/* k8s/apps k8s/jobs/*.yaml k8s/ops/*.yaml \
    k8s/trtllm/*.yaml k8s/llamacpp/*.yaml k8s/multinode/lws-vllm-70b.yaml
fi
echo "ALL LOCAL CHECKS PASSED"
