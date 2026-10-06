#!/usr/bin/env bash
# Everything CI runs that doesn't need a Spark (Step 27 §0).
#   tests/run-local-checks.sh           # static checks
#   API=1 tests/run-local-checks.sh     # + server-side dry-run against the three contexts in $KUBECONFIG
#                                         (spark-root, dev-lab, llms — CI builds them on kind)
set -euo pipefail
cd "$(dirname "$0")/.."
KUBECTL=${KUBECTL:-kubectl}
KVER=${KVER:-1.33.0}                 # schema version for kubeconform (the API groups used are the same in 1.36)
CRD_CATALOG='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
KC=(kubeconform -strict -kubernetes-version "$KVER" -schema-location default -schema-location "$CRD_CATALOG" -ignore-missing-schemas)

echo "== yamllint";   yamllint -s .
echo "== kustomize build + kubeconform"
for k in $(find manifests -name kustomization.yaml -printf '%h\n' | sort); do
  "$KUBECTL" kustomize "$k" | "${KC[@]}" -summary -output text - | sed "s|^|  $k: |" | grep -v ' 0 errors' || true
  "$KUBECTL" kustomize "$k" | "${KC[@]}" - >/dev/null
done
# every Kubernetes manifest file (Helm values files in addons/ and vclusters/ are not manifests)
find manifests breakfix gitops tests/policy -name '*.yaml' ! -name kustomization.yaml -print0 | xargs -0 "${KC[@]}"
"${KC[@]}" addons/local-path-nvme.yaml kubeadm/audit-policy.yaml
echo "== one audit policy (01 Ansible installs it, 02 documents it)"
diff -q kubeadm/audit-policy.yaml "../../01 Ansible/lab/roles/kubeadm_cluster/files/audit-policy.yaml"
echo "== budgets add up (root quotas, slices, IPs, inner ceilings)"
python3 tests/budget_check.py
echo "== promtool";   promtool check rules <(python3 - <<'PY'
import yaml
for d in yaml.safe_load_all(open("manifests/root/95-observability/rules.yaml")):
    print(yaml.safe_dump({"groups": d["spec"]["groups"]}))
PY
)
echo "== dashboard up to date"
diff -q <(python3 manifests/root/95-observability/gen_dashboard.py) manifests/root/95-observability/spark-k8s-dashboard.json
echo "== python";     python3 -m py_compile tests/pod_template_check.py tests/budget_check.py \
                        manifests/root/45-controller/slice_ledger.py manifests/llms/40-ingress/mock_llm.py manifests/root/70-gpu/gemm_bench.py \
                        manifests/llms/80-distributed/base/allreduce_bench.py manifests/llms/90-serving/pd-disagg/pd_proxy.py \
                        manifests/llms/90-serving/triton/model_repository/*/1/model.py manifests/root/95-observability/gen_dashboard.py \
                        scripts/fabric_calc.py scripts/ttft_probe.py scripts/mtbf_calc.py manifests/root/70-gpu/compile_compare.py \
                        manifests/llms/80-distributed/resilient/train_resilient.py
echo "== mock-llm functional"
python3 manifests/llms/40-ingress/mock_llm.py & pid=$!; trap 'kill $pid 2>/dev/null' EXIT; sleep 1
curl -sf localhost:8000/health >/dev/null
n=$(curl -sN localhost:8000/v1/chat/completions -d '{"stream":true,"max_tokens":5}' | grep -c '^data:')
[[ $n -eq 6 ]] || { echo "expected 6 SSE events, got $n"; exit 1; }
kill $pid; trap - EXIT
echo "== shellcheck"; shellcheck -S warning scripts/*.sh tests/*.sh
if command -v helm >/dev/null && [[ "${HELM_TEMPLATE:-1}" == 1 ]]; then
  echo "== helm template (values files render against their charts)"
  source versions.env
  helm repo add loft https://charts.loft.sh --force-update >/dev/null
  helm repo add traefik https://traefik.github.io/charts --force-update >/dev/null
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
  helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ --force-update >/dev/null
  helm repo update >/dev/null
  for v in dev-lab llms; do
    helm template "$v" loft/vcluster --version "$VCLUSTER_VERSION" -n "vc-$v" -f "vclusters/$v.yaml" >/dev/null && echo "  vclusters/$v.yaml OK"
  done
  helm template traefik-lab traefik/traefik --version "$TRAEFIK_CHART_VERSION" -n ingress -f addons/traefik-values.yaml >/dev/null && echo "  traefik-values.yaml OK"
  helm template kps prometheus-community/kube-prometheus-stack --version "$KPS_CHART_VERSION" -n observability \
    -f addons/kube-prometheus-stack-values.yaml >/dev/null && echo "  kube-prometheus-stack-values.yaml OK"
  helm template metrics-server metrics-server/metrics-server --version "$METRICS_SERVER_CHART_VERSION" -n kube-system \
    -f addons/metrics-server-values.yaml >/dev/null && echo "  metrics-server-values.yaml OK"
fi
if [[ "${API:-0}" == 1 ]]; then
  echo "== server-side dry-run (API=1): root, dev-lab, llms"
  scripts/apply-lab.sh
  scripts/verify.sh vclusters tenancy admission
  echo "== pod templates vs PSA / CEL policies / quotas (server-side dry-run of the Pods)"
  M=manifests
  python3 tests/pod_template_check.py \
    $M/root/30-networking $M/root/45-controller $M/root/50-workloads $M/root/70-gpu $M/root/*/*.yaml \
    $M/dev-lab/30-networking $M/dev-lab/*/*.yaml \
    $M/llms/40-ingress $M/llms/50-workloads $M/llms/80-distributed/base $M/llms/80-distributed/two-spark $M/llms/80-distributed/resilient \
    $M/llms/90-serving/vllm $M/llms/90-serving/triton $M/llms/90-serving/sglang $M/llms/90-serving/pd-disagg $M/llms/90-serving/kserve \
    $M/llms/*/*.yaml
fi
echo "ALL LOCAL CHECKS PASSED"
