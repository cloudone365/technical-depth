#!/usr/bin/env bash
# Everything CI runs that doesn't need a Spark (Volume 20 §0).
#   tests/run-local-checks.sh           # static checks
#   API=1 tests/run-local-checks.sh     # + server-side dry-run against $KUBECONFIG (any k8s ≥1.30, CPU-only is fine)
set -euo pipefail
cd "$(dirname "$0")/.."
KUBECTL=${KUBECTL:-kubectl}
CRD_CATALOG='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

echo "== yamllint";   yamllint -s .
echo "== kustomize build + kubeconform"
for k in $(find manifests -name kustomization.yaml -printf '%h\n' | sort); do
  "$KUBECTL" kustomize "$k" | kubeconform -strict -summary -kubernetes-version 1.32.0 \
    -schema-location default -schema-location "$CRD_CATALOG" -ignore-missing-schemas -output text - \
    | sed "s|^|  $k: |" | grep -v ' 0 errors' || true
  "$KUBECTL" kustomize "$k" | kubeconform -strict -kubernetes-version 1.32.0 \
    -schema-location default -schema-location "$CRD_CATALOG" -ignore-missing-schemas - >/dev/null
done
for f in addons/*.yaml breakfix/*.yaml manifests/*/*.yaml tests/policy/*.yaml; do
  [[ $(basename "$f") == kustomization.yaml ]] && continue
  kubeconform -strict -kubernetes-version 1.32.0 -schema-location default -schema-location "$CRD_CATALOG" -ignore-missing-schemas "$f"
done
echo "== promtool";   promtool check rules <(python3 - <<'PY'
import yaml
for d in yaml.safe_load_all(open("manifests/95-observability/rules.yaml")):
    print(yaml.safe_dump({"groups": d["spec"]["groups"]}))
PY
)
echo "== dashboard up to date"
diff -q <(python3 manifests/95-observability/gen_dashboard.py) manifests/95-observability/spark-k8s-dashboard.json
echo "== python";     python3 -m py_compile manifests/40-ingress/mock_llm.py manifests/70-gpu/gemm_bench.py \
                        manifests/80-distributed/base/allreduce_bench.py manifests/90-serving/pd-disagg/pd_proxy.py \
                        manifests/90-serving/triton/model_repository/*/1/model.py manifests/95-observability/gen_dashboard.py
echo "== mock-llm functional"
python3 manifests/40-ingress/mock_llm.py & pid=$!; trap 'kill $pid 2>/dev/null' EXIT; sleep 1
curl -sf localhost:8000/health >/dev/null
n=$(curl -sN localhost:8000/v1/chat/completions -d '{"stream":true,"max_tokens":5}' | grep -c '^data:')
[[ $n -eq 6 ]] || { echo "expected 6 SSE events, got $n"; exit 1; }
kill $pid; trap - EXIT
echo "== shellcheck"; shellcheck -S warning scripts/*.sh tests/*.sh
if [[ "${API:-0}" == 1 ]]; then
  echo "== server-side dry-run (API=1)"
  scripts/apply-lab.sh
  scripts/verify.sh tenancy admission
fi
echo "ALL LOCAL CHECKS PASSED"
