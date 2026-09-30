#!/usr/bin/env bash
# Support bundle (Volume 19 §1): everything you need to debug the lab offline.
#   scripts/collect-diag.sh [namespace...]   → ./diag-<timestamp>.tar.gz
source "$(dirname "$0")/lib.sh"
set +e
ts=$(date +%Y%m%d-%H%M%S); out="diag-$ts"; mkdir -p "$out"
nss=("$@"); [[ ${#nss[@]} -eq 0 ]] && nss=(kube-system gpu-operator llm-serving tenant-alpha tenant-beta batch ingress observability)
k version -o yaml                    > "$out/version.yaml" 2>&1
k get nodes -o wide                  > "$out/nodes.txt" 2>&1
k describe nodes                     > "$out/nodes-describe.txt" 2>&1
k get pods -A -o wide                > "$out/pods.txt" 2>&1
k get events -A --sort-by=.lastTimestamp > "$out/events.txt" 2>&1
k get resourcequota,limitrange -A -o yaml > "$out/quotas.yaml" 2>&1
k get validatingadmissionpolicies,validatingadmissionpolicybindings -o yaml > "$out/admission.yaml" 2>&1
k get --raw '/readyz?verbose'        > "$out/readyz.txt" 2>&1
k get --raw /metrics 2>/dev/null | grep -E '^apiserver_flowcontrol_(rejected|current_inqueue)' > "$out/apf.txt"
for ns in "${nss[@]}"; do
  mkdir -p "$out/ns/$ns"
  k -n "$ns" get all,pvc,cm,ingress,networkpolicy -o wide > "$out/ns/$ns/objects.txt" 2>&1
  for p in $(k -n "$ns" get pods -o name 2>/dev/null); do
    n=${p#pod/}
    k -n "$ns" describe "$p" > "$out/ns/$ns/$n.describe.txt" 2>&1
    k -n "$ns" logs "$p" --all-containers --tail=500 > "$out/ns/$ns/$n.log" 2>&1
    k -n "$ns" logs "$p" --all-containers --previous --tail=200 > "$out/ns/$ns/$n.previous.log" 2>/dev/null || rm -f "$out/ns/$ns/$n.previous.log"
  done
done
if [[ -r /proc/meminfo ]]; then                       # host facts when run ON the Spark
  cp /proc/meminfo "$out/meminfo.txt"
  nvidia-smi -q > "$out/nvidia-smi-q.txt" 2>&1
  # shellcheck disable=SC2024  # the bundle dir is ours; sudo is only for reading the journal
  sudo -n journalctl -u k3s --since "2 hours ago" --no-pager > "$out/k3s-journal.txt" 2>/dev/null
  sudo -n dmesg -T 2>/dev/null | grep -iE 'xid|nvrm|oom|nvme' > "$out/dmesg-filtered.txt"
  conntrack -C > "$out/conntrack-count.txt" 2>/dev/null
fi
tar czf "$out.tar.gz" "$out" && rm -rf "$out"
ok "wrote $out.tar.gz ($(du -h "$out.tar.gz" | cut -f1))"
