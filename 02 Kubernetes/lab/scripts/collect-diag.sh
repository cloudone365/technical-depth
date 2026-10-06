#!/usr/bin/env bash
# Support bundle (Step 26 §1): everything you need to debug the lab offline,
# from all three API servers (root, dev-lab, llms).
#   scripts/collect-diag.sh [root-namespace...]   → ./diag-<timestamp>.tar.gz
source "$(dirname "$0")/lib.sh"
set +e
ts=$(date +%Y%m%d-%H%M%S); out="diag-$ts"; mkdir -p "$out"
nss=("$@"); [[ ${#nss[@]} -eq 0 ]] && nss=(kube-system gpu-operator metallb-system observability platform-tools vc-dev-lab vc-llms)

dump_ns() {   # dump_ns <context> <namespace> <outdir>
  local ctx=$1 ns=$2 d=$3; mkdir -p "$d"
  kc "$ctx" -n "$ns" get all,pvc,cm,ingress,networkpolicy -o wide > "$d/objects.txt" 2>&1
  for p in $(kc "$ctx" -n "$ns" get pods -o name 2>/dev/null); do
    n=${p#pod/}
    kc "$ctx" -n "$ns" describe "$p" > "$d/$n.describe.txt" 2>&1
    kc "$ctx" -n "$ns" logs "$p" --all-containers --tail=500 > "$d/$n.log" 2>&1
    kc "$ctx" -n "$ns" logs "$p" --all-containers --previous --tail=200 > "$d/$n.previous.log" 2>/dev/null || rm -f "$d/$n.previous.log"
  done
}

# ---- root
r="$out/root"; mkdir -p "$r"
kr version -o yaml                    > "$r/version.yaml" 2>&1
kr get nodes -o wide                  > "$r/nodes.txt" 2>&1
kr describe nodes                     > "$r/nodes-describe.txt" 2>&1
kr get pods -A -o wide                > "$r/pods.txt" 2>&1
kr get events -A --sort-by=.lastTimestamp > "$r/events.txt" 2>&1
kr get resourcequota,limitrange -A -o yaml > "$r/quotas.yaml" 2>&1
kr get ciliumnetworkpolicies,networkpolicies -A -o yaml > "$r/netpol.yaml" 2>&1
kr get --raw '/readyz?verbose'        > "$r/readyz.txt" 2>&1
kr get --raw /metrics 2>/dev/null | grep -E '^apiserver_flowcontrol_(rejected|current_inqueue)' > "$r/apf.txt"
for ns in "${nss[@]}"; do dump_ns "$ROOT_CTX" "$ns" "$r/ns/$ns"; done

# ---- inside each vCluster (if reachable)
for v in "$DEV_CTX" "$LLM_CTX"; do
  kc "$v" get --raw /readyz >/dev/null 2>&1 || { echo "context $v unreachable" > "$out/$v-UNREACHABLE.txt"; continue; }
  d="$out/$v"; mkdir -p "$d"
  kc "$v" get pods -A -o wide          > "$d/pods.txt" 2>&1
  kc "$v" get events -A --sort-by=.lastTimestamp > "$d/events.txt" 2>&1
  kc "$v" get resourcequota,limitrange -A -o yaml > "$d/quotas.yaml" 2>&1
  kc "$v" get validatingadmissionpolicies,validatingadmissionpolicybindings -o yaml > "$d/admission.yaml" 2>&1
  for ns in $(kc "$v" get ns -o jsonpath='{.items[*].metadata.name}'); do
    [[ $ns == kube-* || $ns == default ]] && continue
    dump_ns "$v" "$ns" "$d/ns/$ns"
  done
done

if [[ -r /proc/meminfo ]]; then                       # host facts when run ON the Spark
  cp /proc/meminfo "$out/meminfo.txt"
  nvidia-smi -q > "$out/nvidia-smi-q.txt" 2>&1
  # shellcheck disable=SC2024  # the bundle dir is ours; sudo is only for reading the journal / CRI
  sudo -n journalctl -u kubelet -u containerd --since "2 hours ago" --no-pager > "$out/kubelet-containerd-journal.txt" 2>/dev/null
  # shellcheck disable=SC2024
  sudo -n crictl ps -a > "$out/crictl-ps.txt" 2>/dev/null
  sudo -n dmesg -T 2>/dev/null | grep -iE 'xid|nvrm|oom|nvme' > "$out/dmesg-filtered.txt"
  conntrack -C > "$out/conntrack-count.txt" 2>/dev/null
fi
tar czf "$out.tar.gz" "$out" && rm -rf "$out"
ok "wrote $out.tar.gz ($(du -h "$out.tar.gz" | cut -f1))"
