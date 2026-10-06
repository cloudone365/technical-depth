#!/usr/bin/env bash
# Watch the unified memory pool while a pod loads a model (Chapters 14, 15, 20).
# Shows side by side: host MemAvailable, page cache, and the pod cgroup's
# memory.current — answering "is GPU memory charged to my pod's limit?".
#   scripts/uma-watch.sh <namespace> <pod> [context] [interval]   (run ON the Spark)
#   e.g. scripts/uma-watch.sh llm-serving vllm-7d9f… llms
source "$(dirname "$0")/lib.sh"
ns=${1:?namespace}; pod=${2:?pod}; ctx=${3:-$ROOT_CTX}; iv=${4:-2}
read -r hns hpod < <(host_pod "$ctx" "$ns" "$pod")
uid=$(kr -n "$hns" get pod "$hpod" -o jsonpath='{.metadata.uid}')
dir=$(find /sys/fs/cgroup/kubepods* -maxdepth 3 -type d \( -name "*pod${uid//-/_}*" -o -name "pod${uid}" \) | head -1)
printf '%-9s %14s %12s %16s %16s\n' time MemAvailable Cached pod.mem.current pod.mem.max
while sleep "$iv"; do
  read -r avail cached < <(awk '/MemAvailable/{a=$2} /^Cached/{c=$2} END{print a*1024, c*1024}' /proc/meminfo)
  printf '%-9s %11.1f GiB %8.1f GiB %12.2f GiB %16s\n' "$(date +%T)" \
    "$(bc -l <<<"$avail/2^30")" "$(bc -l <<<"$cached/2^30")" \
    "$(bc -l <<<"$(cat "$dir/memory.current")/2^30")" "$(cat "$dir/memory.max")"
done
