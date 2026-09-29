#!/usr/bin/env bash
# Slurm HealthCheckProgram for DGX Spark — drains the node on GPU/fabric faults.
set -u
node=$(hostname -s)
reason=""
if ! timeout 15 nvidia-smi -L >/dev/null 2>&1; then
  reason="nvidia-smi unresponsive"
elif journalctl -k --since "-10 min" --no-pager 2>/dev/null | grep -qE 'NVRM: Xid \(.*\): (48|63|64|74|79|94|95|119|120)'; then
  reason="critical Xid in last 10 min"
elif [ "$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)" -lt 4 ]; then
  reason="UMA MemAvailable < 4 GiB"
fi
if [ -n "$reason" ]; then
  scontrol update NodeName="$node" State=DRAIN Reason="healthcheck: $reason"
  exit 1
fi
exit 0
