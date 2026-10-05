# shellcheck shell=bash
# Shared helpers for the 02 Kubernetes lab scripts.
#
# The lab is three Kubernetes API servers in one kubeconfig (Volume 27):
#   spark-root   the kubeadm root cluster on the Spark — platform, GPU, nodes
#   dev-lab      vCluster #1 — tenant-alpha, tenant-beta, lab-tools
#   llms         vCluster #2 — llm-serving, batch, ingress (Traefik), Kueue, KEDA
# 01 Ansible (playbooks 05, 06b) or scripts/install-addons.sh vclusters writes
# all three contexts into one file.
set -euo pipefail
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../versions.env
source "$LAB_DIR/versions.env"
LAB_KUBECONFIG_DEFAULT="$LAB_DIR/../../01 Ansible/lab/.cache/kubeconfig-spark-lab.yaml"
if [[ -z "${KUBECONFIG:-}" ]]; then
  for kc in "$LAB_KUBECONFIG_DEFAULT" "$HOME/.kube/config" /etc/kubernetes/admin.conf; do
    [[ -r "$kc" ]] && { export KUBECONFIG="$kc"; break; }
  done
fi
KUBECTL="${KUBECTL:-kubectl}"
HELM="${HELM:-helm}"
ROOT_CTX="${ROOT_CTX:-spark-root}"
DEV_CTX="${DEV_CTX:-dev-lab}"
LLM_CTX="${LLM_CTX:-llms}"
C_OK=$'\e[32m'; C_BAD=$'\e[31m'; C_WARN=$'\e[33m'; C_DIM=$'\e[2m'; C_OFF=$'\e[0m'
[[ -t 1 ]] || { C_OK=; C_BAD=; C_WARN=; C_DIM=; C_OFF=; }
PASS=0; FAIL=0; WARN=0
ok()   { PASS=$((PASS+1)); printf '%s[PASS]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '%s[FAIL]%s %s\n' "$C_BAD" "$C_OFF" "$*"; }
warn() { WARN=$((WARN+1)); printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
info() { printf '%s[....]%s %s\n' "$C_DIM" "$C_OFF" "$*"; }

# kubectl against one cluster. k() is the root, for backwards compatibility.
kc() { local ctx=$1; shift; "$KUBECTL" --context "$ctx" "$@"; }
k()  { kc "$ROOT_CTX" "$@"; }
kr() { kc "$ROOT_CTX" "$@"; }
kd() { kc "$DEV_CTX" "$@"; }
kl() { kc "$LLM_CTX" "$@"; }

# Which cluster owns a namespace in this lab?
ctx_for_ns() {
  case "$1" in
    tenant-alpha|tenant-beta|lab-tools) echo "$DEV_CTX" ;;
    llm-serving|batch|ingress|kueue-system|keda|kserve|cert-manager) echo "$LLM_CTX" ;;
    *) echo "$ROOT_CTX" ;;
  esac
}

# A pod inside a vCluster really runs on the root under another name. Print
# "<host-namespace> <host-pod>" for (context, namespace, pod), found through the
# annotations vCluster puts on every synced pod — robust even when long names
# were shortened with a hash.
host_pod() {
  local ctx=$1 ns=$2 pod=$3
  if [[ "$ctx" == "$ROOT_CTX" ]]; then echo "$ns $pod"; return; fi
  local hns="vc-$ctx"
  kr -n "$hns" get pods -o json | python3 -c '
import json, sys
ns, pod, hns = sys.argv[1:4]
for p in json.load(sys.stdin)["items"]:
    a = p["metadata"].get("annotations") or {}
    if a.get("vcluster.loft.sh/object-name") == pod and a.get("vcluster.loft.sh/object-namespace") == ns:
        print(hns, p["metadata"]["name"]); break
else:
    sys.exit(f"{ns}/{pod} not found among the pods synced to {hns}")
' "$ns" "$pod" "$hns"
}

summary() {
  printf '\n%d passed, %d warnings, %d failed\n' "$PASS" "$WARN" "$FAIL"
  [[ "$FAIL" -eq 0 ]]
}
