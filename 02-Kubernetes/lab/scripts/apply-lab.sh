#!/usr/bin/env bash
# Apply the baseline lab objects in dependency order, cluster by cluster (Steps 05, 04).
#   scripts/apply-lab.sh            # apply
#   scripts/apply-lab.sh --dry-run  # server-side dry run only (admission + quota checks, nothing persisted)
#   scripts/apply-lab.sh root       # only one cluster: root | dev-lab | llms
# Directories whose CRDs are not installed yet are skipped with a warning.
source "$(dirname "$0")/lib.sh"
extra=(); only=()
for a in "$@"; do
  case $a in
    --dry-run) extra=(--dry-run=server) ;;
    root|dev-lab|llms) only+=("$a") ;;
    *) echo "usage: $0 [--dry-run] [root|dev-lab|llms]..."; exit 2 ;;
  esac
done
[[ ${#only[@]} -eq 0 ]] && only=(root dev-lab llms)
M="$LAB_DIR/manifests"

apply_dir() {   # apply_dir <context> <dir under manifests/> [required-crd]
  local ctx=$1 d=$2 crd=${3:-}
  if [[ -n "$crd" ]] && ! kc "$ctx" get crd "$crd" >/dev/null 2>&1; then
    warn "skip $d: CRD $crd not installed in $ctx (see scripts/install-addons.sh)"; return 0
  fi
  info "apply $d → $ctx"; kc "$ctx" apply "${extra[@]}" -k "$M/$d" >/dev/null && ok "$d ($ctx)"
}
reachable() {
  kc "$1" get --raw /readyz >/dev/null 2>&1 && return 0
  warn "context $1 unreachable — skipping its layers (scripts/install-addons.sh vclusters)"; return 1
}

for c in "${only[@]}"; do
  case $c in
    root)
      apply_dir "$ROOT_CTX" root/00-platform
      apply_dir "$ROOT_CTX" root/05-vclusters ciliumnetworkpolicies.cilium.io
      apply_dir "$ROOT_CTX" root/16-apf
      apply_dir "$ROOT_CTX" root/30-networking
      apply_dir "$ROOT_CTX" root/45-controller
      apply_dir "$ROOT_CTX" root/50-workloads
      apply_dir "$ROOT_CTX" root/70-gpu
      apply_dir "$ROOT_CTX" root/95-observability servicemonitors.monitoring.coreos.com
      ;;
    dev-lab)
      reachable "$DEV_CTX" || continue
      apply_dir "$DEV_CTX" dev-lab/00-platform
      apply_dir "$DEV_CTX" dev-lab/10-tenancy
      apply_dir "$DEV_CTX" dev-lab/15-admission
      apply_dir "$DEV_CTX" dev-lab/16-apf
      apply_dir "$DEV_CTX" dev-lab/30-networking
      ;;
    llms)
      reachable "$LLM_CTX" || continue
      apply_dir "$LLM_CTX" llms/00-platform
      apply_dir "$LLM_CTX" llms/10-tenancy
      apply_dir "$LLM_CTX" llms/15-admission
      apply_dir "$LLM_CTX" llms/60-storage
      apply_dir "$LLM_CTX" llms/40-ingress middlewares.traefik.io
      apply_dir "$LLM_CTX" llms/50-workloads
      apply_dir "$LLM_CTX" llms/20-scheduling clusterqueues.kueue.x-k8s.io
      ;;
  esac
done
summary
