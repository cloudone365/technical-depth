#!/usr/bin/env python3
"""Generate the 'Spark · Kubernetes' Grafana dashboard (Chapter 17).

python3 gen_dashboard.py > spark-k8s-dashboard.json
Rows: UMA+GPU (host truth) · Tenancy · Serving SLOs · Control plane.
Colours follow the lab palette: GPU green #76b900, control plane blue #1f6feb,
storage amber #bf8700, alerts red #cf222e.
"""
import json

PANELS = []
_y = 0


def row(title):
    global _y
    PANELS.append({"type": "row", "title": title, "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y}, "collapsed": False})
    _y += 1


def ts(title, exprs, unit="short", w=8, color=None, x=0, h=7, thresholds=None):
    p = {
        "type": "timeseries", "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": _y},
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
        "targets": [{"expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)],
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 12}}, "overrides": []},
    }
    if color:
        p["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": color}
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": thresholds}
        p["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"] = {"mode": "line"}
    PANELS.append(p)
    return p


def stat(title, expr, unit="short", w=4, x=0, steps=None):
    PANELS.append({
        "type": "stat", "title": title, "gridPos": {"h": 4, "w": w, "x": x, "y": _y},
        "datasource": {"type": "prometheus", "uid": "${datasource}"},
        "targets": [{"expr": expr, "refId": "A"}],
        "options": {"colorMode": "background", "graphMode": "area"},
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps or [
            {"color": "#76b900", "value": None}]}}},
    })


def nl(h=7):
    global _y
    _y += h


row("Unified memory & GB10 (host exporters)")
stat("UMA available", 'min(spark_uma_available_bytes) / 2^30', "decgbytes", x=0,
     steps=[{"color": "#cf222e", "value": None}, {"color": "#bf8700", "value": 12}, {"color": "#76b900", "value": 24}])
stat("GPU temp", "max(spark_gpu_temperature_celsius)", "celsius", x=4,
     steps=[{"color": "#76b900", "value": None}, {"color": "#bf8700", "value": 80}, {"color": "#cf222e", "value": 90}])
stat("GPU power", "max(spark_gpu_power_watts)", "watt", x=8)
stat("Xid (24h)", "max(spark_gpu_xid_events_24h)", x=12,
     steps=[{"color": "#76b900", "value": None}, {"color": "#cf222e", "value": 1}])
stat("GPU slices in use", 'sum(kube_pod_container_resource_requests{resource="nvidia_com_gpu"} * on(namespace,pod) group_left() (kube_pod_status_phase{phase="Running"}==1))', x=16)
stat("Pending GPU pods", 'sum(kube_pod_status_phase{phase="Pending"} * on(namespace,pod) group_left() (kube_pod_container_resource_requests{resource="nvidia_com_gpu"}>0)) or vector(0)', x=20,
     steps=[{"color": "#76b900", "value": None}, {"color": "#bf8700", "value": 1}])
nl(4)
ts("UMA: available vs page cache", [("spark_uma_available_bytes", "available {{node}}"),
                                     ("spark_uma_page_cache_bytes", "page cache {{node}}")], "bytes", x=0)
ts("GPU utilisation", [("spark_gpu_utilization_ratio", "{{node}}")], "percentunit", color="#76b900", x=8)
ts("SM clock / throttle", [("spark_gpu_sm_clock_mhz", "SM MHz {{node}}"),
                           ("spark_gpu_throttle_reasons_bitmask", "throttle bitmask")], x=16)
nl()
row("Tenancy")
ts("vCluster CPU used / hard (root quotas)", [('kube_resourcequota{type="used",resource="requests.cpu"}', "used {{namespace}}"),
                                    ('kube_resourcequota{type="hard",resource="requests.cpu"}', "hard {{namespace}}")], x=0)
ts("vCluster memory used / hard (root quotas)", [('kube_resourcequota{type="used",resource="limits.memory"}', "used {{namespace}}"),
                          ('kube_resourcequota{type="hard",resource="limits.memory"}', "hard {{namespace}}")], "bytes", x=8)
ts("CPU throttling (top 5 containers)", [('topk(5, rate(container_cpu_cfs_throttled_periods_total{container!=""}[5m]) / rate(container_cpu_cfs_periods_total{container!=""}[5m]))', "{{namespace}}/{{pod}}")],
   "percentunit", x=16, color="#cf222e")
nl()
row("Serving SLOs (vLLM)")
ts("TTFT p95", [("spark:vllm_ttft_p95_seconds", "{{vcluster}}/{{vnamespace}}")], "s", x=0,
   thresholds=[{"color": "#76b900", "value": None}, {"color": "#cf222e", "value": 1}])
ts("Time per output token p95", [("spark:vllm_tpot_p95_seconds", "{{vcluster}}/{{vnamespace}}")], "s", x=8)
ts("Queue & KV cache", [("sum(vllm:num_requests_running)", "running"), ("sum(vllm:num_requests_waiting)", "waiting"),
                        ("max(vllm:kv_cache_usage_perc or vllm:gpu_cache_usage_perc)*100", "KV cache %")], x=16)
nl()
row("Control plane")
ts("etcd WAL fsync p99", [("histogram_quantile(0.99, sum by (le) (rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])))", "p99")],
   "s", x=0, color="#1f6feb", thresholds=[{"color": "#76b900", "value": None}, {"color": "#cf222e", "value": 0.05}])
ts("API server p99 latency (mutating)", [('histogram_quantile(0.99, sum by (le, verb) (rate(apiserver_request_duration_seconds_bucket{verb=~"POST|PUT|PATCH|DELETE"}[5m])))', "{{verb}}")],
   "s", x=8, color="#1f6feb")
ts("APF: rejected / queued", [('sum by (priority_level) (rate(apiserver_flowcontrol_rejected_requests_total[5m]))', "rejected {{priority_level}}"),
                              ('sum by (priority_level) (apiserver_flowcontrol_current_inqueue_requests)', "queued {{priority_level}}")], x=16)
nl()

print(json.dumps({
    "title": "Spark · Kubernetes",
    "uid": "spark-k8s",
    "tags": ["dgx-spark", "kubernetes"],
    "timezone": "browser",
    "schemaVersion": 39,
    "refresh": "30s",
    "time": {"from": "now-3h", "to": "now"},
    "templating": {"list": [{"name": "datasource", "type": "datasource", "query": "prometheus", "current": {}}]},
    "panels": [dict(p, id=i + 1) for i, p in enumerate(PANELS)],
}, indent=1))
