#!/usr/bin/env python3
"""Generate the 'Spark · LLM serving (DeepSeek)' Grafana dashboard (Volume 38).
Per served model (`model_name` label from vLLM): throughput, latency, KV cache,
preemptions, finish reasons (reasoning truncation), speculative decoding.
  python3 gen_dashboard.py > deepseek-serving-dashboard.json
"""
import json

P, Y = [], [0]


def panel(title, exprs, unit="short", w=12, h=8, x=0, kind="timeseries"):
    PANEL = {"type": kind, "title": title, "gridPos": {"h": h, "w": w, "x": x, "y": Y[0]},
             "datasource": {"type": "prometheus", "uid": "${datasource}"},
             "targets": [{"expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)],
             "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10}}, "overrides": []}}
    P.append(PANEL)


def row(t):
    P.append({"type": "row", "title": t, "gridPos": {"h": 1, "w": 24, "x": 0, "y": Y[0]}, "collapsed": False})
    Y[0] += 1


M = '{model_name=~"$model"}'
row("Throughput")
panel("Output tokens/s", [(f"sum by (model_name) (rate(vllm:generation_tokens_total{M}[1m]))", "{{model_name}}")], "short", x=0)
panel("Prompt tokens/s (incl. cached)", [(f"sum by (model_name) (rate(vllm:prompt_tokens_total{M}[1m]))", "{{model_name}}")], x=12)
Y[0] += 8
row("Latency")
panel("TTFT p50 / p95", [
    (f"histogram_quantile(0.5, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket{M}[5m])))", "p50 {{model_name}}"),
    (f"histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket{M}[5m])))", "p95 {{model_name}}")], "s", x=0)
panel("Inter-token latency p50 / p95", [
    (f"histogram_quantile(0.5, sum by (le, model_name) (rate(vllm:inter_token_latency_seconds_bucket{M}[5m]) or rate(vllm:time_per_output_token_seconds_bucket{M}[5m])))", "p50 {{model_name}}"),
    (f"histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:inter_token_latency_seconds_bucket{M}[5m]) or rate(vllm:time_per_output_token_seconds_bucket{M}[5m])))", "p95 {{model_name}}")], "s", x=12)
Y[0] += 8
panel("End-to-end request p95", [(f"histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:e2e_request_latency_seconds_bucket{M}[5m])))", "{{model_name}}")], "s", x=0)
panel("Generated tokens per request p50 / p95 (reasoning length)", [
    (f"histogram_quantile(0.5, sum by (le, model_name) (rate(vllm:request_generation_tokens_bucket{M}[15m])))", "p50 {{model_name}}"),
    (f"histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:request_generation_tokens_bucket{M}[15m])))", "p95 {{model_name}}")], x=12)
Y[0] += 8
row("Capacity")
panel("Running / waiting requests", [(f"sum by (model_name) (vllm:num_requests_running{M})", "running {{model_name}}"),
                                     (f"sum by (model_name) (vllm:num_requests_waiting{M})", "waiting {{model_name}}")], x=0, w=8)
panel("KV cache usage", [(f"max by (model_name) (vllm:kv_cache_usage_perc{M} or vllm:gpu_cache_usage_perc{M})", "{{model_name}}")], "percentunit", x=8, w=8)
panel("Preemptions/s", [(f"sum by (model_name) (rate(vllm:num_preemptions_total{M}[5m]))", "{{model_name}}")], x=16, w=8)
Y[0] += 8
row("Quality signals")
panel("Finish reasons (length = truncated reasoning)", [(f"sum by (finished_reason) (rate(vllm:request_success_total{M}[5m]))", "{{finished_reason}}")], x=0)
panel("Prefix-cache hit rate", [(f"sum(rate(vllm:prefix_cache_hits_total{M}[5m])) / sum(rate(vllm:prefix_cache_queries_total{M}[5m]))", "hit rate")], "percentunit", x=12)
Y[0] += 8
panel("Speculative decoding acceptance", [(f"sum(rate(vllm:spec_decode_num_accepted_tokens_total{M}[5m])) / sum(rate(vllm:spec_decode_num_draft_tokens_total{M}[5m]))", "acceptance")], "percentunit", x=0)
# energy efficiency: output tokens per joule = tokens/s ÷ watts (DCGM if enabled, else the 01 Ansible textfile metric)
POWER = "(sum(DCGM_FI_DEV_POWER_USAGE) or sum(spark_gpu_power_watts))"
panel("Output tokens per joule (GPU power)", [(f"sum(rate(vllm:generation_tokens_total{M}[5m])) / {POWER}", "tokens/J")], x=12)
Y[0] += 8
row("GB10 and unified memory")
panel("GPU utilisation", [("max(DCGM_FI_DEV_GPU_UTIL) / 100 or max(spark_gpu_utilization_ratio)", "busy")], "percentunit", x=0, w=8)
panel("GPU power (W) and temperature (°C)", [(POWER, "W"),
                                             ("max(DCGM_FI_DEV_GPU_TEMP) or max(spark_gpu_temperature_celsius)", "°C")], x=8, w=8)
panel("Throttle reasons bitmask / Xid events (24h)", [("max(spark_gpu_throttle_reasons_bitmask)", "throttle bitmask"),
                                                      ("max(spark_gpu_xid_events_24h)", "Xid 24h")], x=16, w=8)
Y[0] += 8
panel("UMA available vs droppable page cache", [("max(spark_uma_available_bytes) or max(node_memory_MemAvailable_bytes)", "available"),
                                               ("max(spark_uma_page_cache_bytes) or max(node_memory_Cached_bytes)", "page cache")], "bytes", x=0)
panel("Pod memory working set (llm-serving, batch)", [('sum by (namespace) (container_memory_working_set_bytes{namespace=~"llm-serving|batch", container!=""})', "{{namespace}}")], "bytes", x=12)
Y[0] += 8
row("Gateway and day-2 jobs")
panel("Requests/s through Traefik by route", [('sum by (service) (rate(traefik_service_requests_total{service=~"llm-serving-.*"}[5m]))', "{{service}}")], x=0)
panel("Failed day-2 Jobs (llm-serving)", [('sum by (job_name) (kube_job_status_failed{namespace="llm-serving"} > 0)', "{{job_name}}")], x=12)
Y[0] += 8
panel("Hours since last successful CronJob run", [('(time() - kube_cronjob_status_last_successful_time{namespace="llm-serving"}) / 3600', "{{cronjob}}")], "h", x=0, w=24)
Y[0] += 8

print(json.dumps({
    "title": "Spark · LLM serving (DeepSeek)", "uid": "spark-llm-serving", "tags": ["dgx-spark", "vllm", "deepseek"],
    "schemaVersion": 39, "refresh": "30s", "time": {"from": "now-3h", "to": "now"},
    "templating": {"list": [
        {"name": "datasource", "type": "datasource", "query": "prometheus", "current": {}},
        {"name": "model", "type": "query", "datasource": {"type": "prometheus", "uid": "${datasource}"},
         "query": "label_values(vllm:num_requests_running, model_name)", "includeAll": True, "multi": True,
         "current": {"text": "All", "value": "$__all"}, "allValue": ".*"}]},
    "panels": [dict(p, id=i + 1) for i, p in enumerate(P)]}, indent=1))
