#!/usr/bin/env bash
# Apply the baseline lab objects in dependency order (Volume 15).
#   scripts/apply-lab.sh            # apply
#   scripts/apply-lab.sh --dry-run  # server-side dry run only (admission + quota checks, nothing persisted)
# Directories whose CRDs are not installed yet are skipped with a warning.
source "$(dirname "$0")/lib.sh"
extra=()
[[ "${1:-}" == --dry-run ]] && extra=(--dry-run=server)
M="$LAB_DIR/manifests"

apply_dir() {   # apply_dir <dir> [required-crd]
  local d=$1 crd=${2:-}
  if [[ -n "$crd" ]] && ! k get crd "$crd" >/dev/null 2>&1; then
    warn "skip $d: CRD $crd not installed (see scripts/install-addons.sh)"; return 0
  fi
  info "apply $d"; k apply "${extra[@]}" -k "$M/$d" >/dev/null && ok "$d"
}

apply_dir 00-platform
apply_dir 10-tenancy
apply_dir 15-admission
apply_dir 16-apf
apply_dir 60-storage
apply_dir 30-networking
apply_dir 40-ingress   middlewares.traefik.io
apply_dir 50-workloads
apply_dir 70-gpu
apply_dir 20-scheduling clusterqueues.kueue.x-k8s.io
apply_dir 95-observability servicemonitors.monitoring.coreos.com
summary
