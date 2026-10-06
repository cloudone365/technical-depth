#!/usr/bin/env bash
# Show how Kubernetes requests/limits became cgroup v2 files (Chapter 14).
#   scripts/cgroup-inspect.sh <namespace> <pod> [context]     (run ON the Spark)
# context: spark-root (default), dev-lab or llms. A vCluster pod's cgroup
# belongs to its synced copy on the root, so that is what we look up.
source "$(dirname "$0")/lib.sh"
ns=${1:?namespace}; pod=${2:?pod}; ctx=${3:-$ROOT_CTX}
read -r hns hpod < <(host_pod "$ctx" "$ns" "$pod")
uid=$(kr -n "$hns" get pod "$hpod" -o jsonpath='{.metadata.uid}')
qos=$(kr -n "$hns" get pod "$hpod" -o jsonpath='{.status.qosClass}')
dir=$(find /sys/fs/cgroup/kubepods* -maxdepth 3 -type d \( -name "*pod${uid//-/_}*" -o -name "pod${uid}" \) 2>/dev/null | head -1)
[[ -n "$dir" ]] || { bad "cgroup for pod uid $uid not found (run on the node hosting the pod)"; exit 1; }
echo "pod $ctx $ns/$pod → root $hns/$hpod  uid=$uid  qos=$qos"
echo "cgroup $dir"
printf '  %-22s %s\n' cpu.max "$(cat "$dir/cpu.max")" cpu.weight "$(cat "$dir/cpu.weight")" \
  memory.max "$(cat "$dir/memory.max")" memory.current "$(cat "$dir/memory.current")" \
  memory.peak "$(cat "$dir/memory.peak" 2>/dev/null || echo n/a)" pids.max "$(cat "$dir/pids.max")"
echo "  cpu.stat (throttling):"; grep -E 'nr_periods|nr_throttled|throttled_usec' "$dir/cpu.stat" | sed 's/^/    /'
echo "  memory.events:";          sed 's/^/    /' "$dir/memory.events"
echo "  oom_score_adj of container processes:"
for c in "$dir"/*/; do
  for pid in $(cat "$c/cgroup.procs" 2>/dev/null); do
    printf '    pid %-8s oom_score_adj=%-6s %s\n' "$pid" "$(cat /proc/$pid/oom_score_adj)" "$(tr '\0' ' ' </proc/$pid/cmdline | cut -c1-60)"
  done
done
cat <<TXT

How to read it:
  cpu.max "100000 100000"  = quota/period µs → 1 core     (limits.cpu: 1)
  cpu.weight               from requests.cpu: shares=m*1024/1000, weight=1+((shares-2)*9999)/262142
  memory.max               = limits.memory bytes; 'max' means no limit (BestEffort/Burstable w/o limit)
  oom_score_adj            Guaranteed -997 · BestEffort 1000 · Burstable in between
TXT
