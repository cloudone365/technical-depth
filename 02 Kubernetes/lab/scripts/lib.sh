# shellcheck shell=bash
# Shared helpers for the 02 Kubernetes lab scripts.
set -euo pipefail
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../versions.env
source "$LAB_DIR/versions.env"
# Prefer the kubeconfig the 01 Ansible lab fetched; fall back to k3s's own.
if [[ -z "${KUBECONFIG:-}" ]]; then
  for k in "$LAB_DIR/../../01 Ansible/lab/.cache/kubeconfig-spark-lab.yaml" /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"; do
    [[ -r "$k" ]] && { export KUBECONFIG="$k"; break; }
  done
fi
KUBECTL="${KUBECTL:-kubectl}"
C_OK=$'\e[32m'; C_BAD=$'\e[31m'; C_WARN=$'\e[33m'; C_DIM=$'\e[2m'; C_OFF=$'\e[0m'
[[ -t 1 ]] || { C_OK=; C_BAD=; C_WARN=; C_DIM=; C_OFF=; }
PASS=0; FAIL=0; WARN=0
ok()   { PASS=$((PASS+1)); printf '%s[PASS]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '%s[FAIL]%s %s\n' "$C_BAD" "$C_OFF" "$*"; }
warn() { WARN=$((WARN+1)); printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
info() { printf '%s[....]%s %s\n' "$C_DIM" "$C_OFF" "$*"; }
k()    { "$KUBECTL" "$@"; }
summary() {
  printf '\n%d passed, %d warnings, %d failed\n' "$PASS" "$WARN" "$FAIL"
  [[ "$FAIL" -eq 0 ]]
}
