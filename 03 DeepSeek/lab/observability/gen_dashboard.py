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
