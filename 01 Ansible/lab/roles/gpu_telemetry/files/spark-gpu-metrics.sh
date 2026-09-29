#!/usr/bin/env bash
# spark-gpu-metrics.sh — node_exporter textfile collector for DGX Spark.
# Writes Prometheus metrics atomically to $OUT (tmp file + mv), run by a systemd timer.
#
# Why not only dcgm-exporter? It may not expose every GB10 field, and on a UMA
# system "GPU memory used" is not reported by nvidia-smi (shows [N/A]) — the real
# memory signal is host MemAvailable. This script is the dependency-free fallback.
set -euo pipefail
OUT_DIR=${OUT_DIR:-/var/lib/prometheus/node-exporter}
OUT="$OUT_DIR/spark_gpu.prom"
TMP="$(mktemp "$OUT_DIR/.spark_gpu.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

num() { [[ "$1" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && echo "$1" || echo "NaN"; }

{
echo "# HELP spark_gpu_up 1 if nvidia-smi answered within the timeout"
echo "# TYPE spark_gpu_up gauge"
if q=$(timeout 10 nvidia-smi --query-gpu=index,name,temperature.gpu,power.draw,utilization.gpu,clocks.sm,clocks_throttle_reasons.active \
        --format=csv,noheader,nounits 2>/dev/null); then
  echo "spark_gpu_up 1"
  echo "# HELP spark_gpu_temperature_celsius GPU die temperature"
  echo "# TYPE spark_gpu_temperature_celsius gauge"
  echo "# HELP spark_gpu_power_watts GPU power draw"
  echo "# TYPE spark_gpu_power_watts gauge"
  echo "# HELP spark_gpu_utilization_ratio GPU busy ratio 0-1"
  echo "# TYPE spark_gpu_utilization_ratio gauge"
  echo "# HELP spark_gpu_sm_clock_mhz SM clock"
  echo "# TYPE spark_gpu_sm_clock_mhz gauge"
  echo "# HELP spark_gpu_throttle_reasons_bitmask Active clock throttle reasons (0 = none)"
  echo "# TYPE spark_gpu_throttle_reasons_bitmask gauge"
  while IFS=',' read -r idx name temp power util sm thr; do
    idx=$(echo "$idx" | xargs); name=$(echo "$name" | xargs)
    l="gpu=\"$idx\",name=\"$name\""
    echo "spark_gpu_temperature_celsius{$l} $(num "$(echo "$temp" | xargs)")"
    echo "spark_gpu_power_watts{$l} $(num "$(echo "$power" | xargs)")"
    u=$(num "$(echo "$util" | xargs)"); [[ "$u" != NaN ]] && u=$(awk "BEGIN{print $u/100}")
    echo "spark_gpu_utilization_ratio{$l} $u"
    echo "spark_gpu_sm_clock_mhz{$l} $(num "$(echo "$sm" | xargs)")"
    echo "spark_gpu_throttle_reasons_bitmask{$l} $(( $(echo "$thr" | xargs) + 0 ))" 2>/dev/null \
      || echo "spark_gpu_throttle_reasons_bitmask{$l} NaN"
  done <<< "$q"
else
  echo "spark_gpu_up 0"
fi

echo "# HELP spark_gpu_xid_events_24h Kernel NVRM Xid events in the last 24h"
echo "# TYPE spark_gpu_xid_events_24h gauge"
echo "spark_gpu_xid_events_24h $(journalctl -k --since '24 hours ago' --no-pager 2>/dev/null | grep -c 'NVRM: Xid' || true)"

echo "# HELP spark_gpu_processes Number of compute processes on the GPU"
echo "# TYPE spark_gpu_processes gauge"
echo "spark_gpu_processes $(timeout 10 nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -c . || true)"

echo "# HELP spark_uma_available_bytes Unified memory available to CPU+GPU (MemAvailable)"
echo "# TYPE spark_uma_available_bytes gauge"
echo "spark_uma_available_bytes $(awk '/MemAvailable/{print $2*1024}' /proc/meminfo)"
echo "# HELP spark_uma_page_cache_bytes Page cache that can be dropped to give memory back to the GPU"
echo "# TYPE spark_uma_page_cache_bytes gauge"
echo "spark_uma_page_cache_bytes $(awk '/^Cached:/{print $2*1024}' /proc/meminfo)"

echo "# HELP spark_cx7_link_speed_mbps ConnectX-7 negotiated speed"
echo "# TYPE spark_cx7_link_speed_mbps gauge"
for n in /sys/class/net/enp1s0f*np* /sys/class/net/enP2p1s0f*np*; do
  [[ -e "$n" ]] || continue
  s=$(cat "$n/speed" 2>/dev/null || echo -1)
  echo "spark_cx7_link_speed_mbps{device=\"$(basename "$n")\"} $s"
done
} > "$TMP"

chmod 0644 "$TMP"
mv "$TMP" "$OUT"
trap - EXIT
