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
for d in . k8s/models/* k8s/spec-decode/* k8s/long-context/* k8s/lora/* k8s/sglang/* k8s/apps observability breakfix/*; do "$KUBECTL" kustomize "$d" | kc -; done
for f in k8s/jobs/*.yaml k8s/ops/*.yaml k8s/trtllm/*.yaml k8s/llamacpp/*.yaml k8s/multinode/*.yaml k8s/autoscale/*.yaml k8s/kserve/*.yaml k8s/rl/*.yaml; do kc "$f"; done
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
expect "faster" python3 tools/mla_attention_demo.py --decode-bench 256 --batch 1
expect "WITH aux-loss-free" python3 tools/moe_router_demo.py --steps 40
expect "Block scales" python3 tools/fp8_blockscale.py --n 512
expect "identical" python3 tools/ring_attention_demo.py --world 2 --seq 512
expect "identical" torchrun --nproc-per-node 2 tools/ring_attention_demo.py --seq 512
expect "eplb-lite" python3 tools/eplb_sim.py --experts 64 --gpus 8 --redundant 8
python3 tools/spec_decode_calc.py --sweep >/dev/null
expect "padded" python3 tools/grouped_gemm_bench.py --iters 2
echo "== training tools (dry run)"
expect "rewards behave" python3 tools/grpo_tiny.py --dry-run
expect "<answer>" python3 tools/sft_lora.py --dry-run
expect "40.4 M" python3 tools/lora_calc.py deepseek-r1-distill-qwen-7b --rank 16

echo "== eval harness vs oracle mock (must score exactly 50 % per suite)"
python3 tests/mock_oracle.py 18765 & pids+=($!); sleep 1
out=$(mktemp -d)
python3 tools/eval_harness.py --url http://127.0.0.1:18765 --model mock --out "$out/r.json" >/dev/null
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); acc={k:v['accuracy'] for k,v in d['suites'].items()}; assert acc=={'math':0.5,'code':0.5,'json':0.5}, acc; print('eval harness OK', acc)" "$out/r.json"
expect "tok/ok" python3 tools/eval_harness.py --report "$out/r.json"
expect "hosted model: cost = provider price (\$8.00" python3 tools/eval_harness.py --url http://127.0.0.1:18765 --model api --suites json --hosted --api-price-out 8
echo "== agent tool loop vs scripted mock"
python3 tests/mock_tools_server.py 18767 & pids+=($!); sleep 1
expect "20.35" python3 tools/agent_tools.py "What is 17% of 119.7?" --url http://127.0.0.1:18767 --audit "$T.audit"
python3 -c "import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r['tool']=='calculator' and r['result']['result']==20.349, r; print('agent audit OK')" "$T.audit"
python3 -c "import sys; sys.path.insert(0,'tools'); import agent_tools as a
assert a.validate('calculator', {}) and a.validate('search_docs', {'query': 'x', 'rm': 1}) and not a.validate('gpu_slices', {})
print('agent argument validation OK')"
echo "== needle test vs mock"
python3 tests/mock_needle.py 18768 & pids+=($!); sleep 1
expect "4/4 needles found" python3 tools/needle_test.py --url http://127.0.0.1:18768 --model m --lengths 1000 4000 --depths 0.2 0.8
echo "== stream probe vs mocks (normal / buffered / truncated)"
python3 tests/mock_stream.py 18770 & pids+=($!); python3 tests/mock_stream.py 18771 buffered & pids+=($!)
python3 tests/mock_stream.py 18772 truncated & pids+=($!); sleep 1
expect "finish_reason=stop" python3 tools/stream_probe.py --url http://127.0.0.1:18770 --model m
! python3 tools/stream_probe.py --url http://127.0.0.1:18770 --model m | grep -q BUFFERED
expect "BUFFERED" python3 tools/stream_probe.py --url http://127.0.0.1:18771 --model m
expect "TRUNCATED" python3 tools/stream_probe.py --url http://127.0.0.1:18772 --model m
echo "== format check vs mock (format 100 %, correct 50 %)"
python3 tests/mock_format.py 18773 & pids+=($!); sleep 1
expect "format 40/40 (100%)  correct 20/40 (50%)" python3 tools/format_check.py --url http://127.0.0.1:18773 --model m -n 40 --concurrency 1
echo "== vault-sync vs mock Vault + Kubernetes API (create, unchanged, rotate → restart)"
python3 tests/mock_vault_k8s.py 18769 & pids+=($!); sleep 1
sa=$(mktemp -d); echo jwt > "$sa/token"; echo llm-serving > "$sa/namespace"
vs() { VAULT_ADDR=http://127.0.0.1:18769 K8S_API=http://127.0.0.1:18769 SA_DIR="$sa" \
  SYNC_MAP='[{"vault":"kv/data/spark-lab/deepseek/litellm","secret":"litellm-master-key","keys":{"key":"master_key"},"restart":["deployment/litellm"]}]' \
  python3 tools/vault_sync.py; }
expect "restart  deployment/litellm" vs
expect "unchanged litellm-master-key" vs
curl -s -X POST http://127.0.0.1:18769/_rotate >/dev/null
expect "restart  deployment/litellm" vs
echo "== catalog pinning + upstream drift vs mock Hub"
python3 tests/mock_hf.py 18774 & pids+=($!); sleep 1
cat_copy=$(mktemp); cp models.yaml "$cat_copy"
HF_ENDPOINT=http://127.0.0.1:18774 python3 tools/catalog_drift.py pin --catalog "$cat_copy" --only r1-7b >/dev/null
grep -q "revision: [0-9a-f]\{40\}" "$cat_copy"
expect "0 moved" env HF_ENDPOINT=http://127.0.0.1:18774 python3 tools/catalog_drift.py report --catalog "$cat_copy"
curl -s -X POST http://127.0.0.1:18774/ >/dev/null
if HF_ENDPOINT=http://127.0.0.1:18774 python3 tools/catalog_drift.py report --catalog "$cat_copy" >"$T"; then cat "$T"; echo "drift not detected"; exit 1; fi
grep -q "MOVED     r1-7b" "$T" && echo "catalog drift OK"
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
  expect "questions  hit@1" python3 tools/rag_demo.py eval --dirs "02 Kubernetes" --qdrant http://127.0.0.1:6333 --embed-url http://127.0.0.1:18766
  python3 tools/rag_demo.py ingest --repo ../.. --dirs "02 Kubernetes" --qdrant http://127.0.0.1:6333 --embed-url http://127.0.0.1:18766 >/dev/null
  expect "alias technical-depth: technical-depth-" python3 tools/rag_demo.py rollback --qdrant http://127.0.0.1:6333 --embed-url http://127.0.0.1:18766
  if python3 tools/rag_demo.py ingest --repo ../.. --dirs "02 Kubernetes" --gate 0.99 --qdrant http://127.0.0.1:6333 --embed-url http://127.0.0.1:18766 >"$T" 2>&1; then
    cat "$T"; echo "quality gate should have failed"; exit 1
  fi
  grep -q "GATE FAILED" "$T" || { cat "$T"; exit 1; }
  echo "rag OK (blue/green ingest, eval, rollback, quality gate)"
fi

if [[ "${API:-0}" == 1 ]]; then
  echo "== server-side dry-run (API=1)"
  "$KUBECTL" apply -k "$K8S_LAB/manifests/00-platform" >/dev/null
  "$KUBECTL" apply -f k8s/multinode/namespace.yaml >/dev/null
  "$KUBECTL" apply --dry-run=server -k . >/dev/null
  for d in k8s/models/* k8s/spec-decode/* k8s/long-context/* k8s/lora/* k8s/sglang/* k8s/apps observability; do "$KUBECTL" apply --dry-run=server -k "$d" >/dev/null; done
  for f in k8s/jobs/*.yaml k8s/ops/*.yaml k8s/trtllm/*.yaml k8s/llamacpp/*.yaml k8s/multinode/*.yaml k8s/autoscale/*.yaml k8s/rl/*.yaml; do "$KUBECTL" apply --dry-run=server -f "$f" >/dev/null; done
  echo "== pod templates vs PSA / CEL policies / quotas"
  python3 "$K8S_LAB/tests/pod_template_check.py" k8s/models/* k8s/lora/* k8s/sglang/* k8s/apps k8s/jobs/*.yaml k8s/ops/*.yaml \
    k8s/trtllm/*.yaml k8s/llamacpp/*.yaml k8s/multinode/lws-vllm-70b.yaml k8s/rl/*.yaml
fi
echo "ALL LOCAL CHECKS PASSED"
