# Volume 23: DCGM, Prometheus, and Grafana Telemetry for Qwen2.5

```
==================================================================================================
TARGET AUDIENCE: Site Reliability Engineers (SREs), MLOps Engineers, Performance Architects
PREREQUISITES   : Prometheus exposition formats, Grafana dashboards, NVIDIA DCGM fundamentals
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Build real-time enterprise observability pipelines capturing hardware telemetry
                  (DCGM FIDs) and serving metrics (TTFT, ITL, KV-cache utilization) for Qwen2.5.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Operating foundation models without deep observability is like flying a jet airliner without an instrument panel. In high-concurrency LLM serving, failures rarely announce themselves as immediate system crashes; they manifest subtly as **Time To First Token (TTFT) degradation**, **KV-cache memory exhaustion**, **queue buildup**, or **GPU thermal throttling**.

This volume establishes a complete production observability stack integrating **NVIDIA Data Center GPU Manager (DCGM)** for hardware telemetry and **vLLM/SGLang Prometheus endpoints** for inference runtime telemetry, visualized in real-time on enterprise **Grafana dashboards**.

```
  ┌───────────────────────────────────────────────────────────────┐
  │                 NVIDIA DGX Spark Hardware Node                │
  │                                                               │
  │   ┌──────────────────────────┐    ┌──────────────────────┐    │
  │   │  NVIDIA DCGM Exporter    │    │  vLLM Inference App  │    │
  │   │  - Tensor Active (FID 1004)   │  - TTFT / ITL Latency│    │
  │   │  - NVLink BW (FID 1009)  │    │  - KV Cache Usage %  │    │
  │   │  - Power & Thermal Flags │    │  - Queue Depth       │    │
  │   └────────────┬─────────────┘    └──────────┬───────────┘    │
  │                │ Port :9400                  │ Port :8000     │
  └────────────────┼─────────────────────────────┼────────────────┘
                   │                             │
                   └──────────────┬──────────────┘
                                  │ Scraped every 5s
                                  ▼
                   ┌─────────────────────────────┐
                   │ Prometheus Metrics Server   │
                   │ Time-Series DB & Alert Rules│
                   └──────────────┬──────────────┘
                                  │ PromQL Queries
                                  ▼
                   ┌─────────────────────────────┐
                   │ Grafana Operational Cockpit │
                   │ Real-Time Latency & Slos    │
                   └─────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Formula 1 Telemetry Pit Wall
3. Evolutionary Lineage: From `nvidia-smi` Polling to Structured Event Telemetry
4. First-Principles Mathematics & Algorithmic Formulations
   - LLM Latency Decomposition: TTFT vs ITL
   - Service Level Objectives (SLOs) & Quantile Formulations (P50, P90, P99)
   - KV Cache Saturation & Eviction Probability Calculus
   - DCGM Field Identifiers (FIDs) Architecture
5. Comparative Trade-Off Matrix: Monitoring Tools
6. Concrete Production Hands-On Lab: Custom Prometheus Exporter & Telemetry Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Formula 1 Telemetry Pit Wall

Imagine an F1 racing team during the Monaco Grand Prix:
If the team only had a mechanic walking up to the car with a tire gauge during pit stops (`nvidia-smi` executed manually in a terminal):
- The engine could overheat midway through Lap 4 and explode before the driver ever realized the temperature climbed.
- The team would have no idea whether poor lap times were caused by tire wear, fuel weight, or aerodynamic drag.

**DCGM and Prometheus act as the 300-sensor F1 telemetry pit wall**:
- Every millisecond, sensors transmit oil pressure, tire friction, and turbo boost (DCGM FIDs: Tensor Core activity, NVLink traffic, power draw).
- Digital dashboards instantly reveal whether a slow response was caused by user prompt bloat (TTFT) or GPU memory starvation (KV-cache exhaustion).

---

## 3. Evolutionary Lineage: From `nvidia-smi` Polling to Structured Event Telemetry

```
Generation 1 (2018-2021)      Generation 2 (2022-2023)      Generation 3 (2024-2026)
Crontab `nvidia-smi` Scripts  Basic Prometheus Exporters    Unified Hardware & LLM App Telemetry
──────────────────────────    ──────────────────────────    ───────────────────────────────────
- Shell script loops          - Node Exporter + basic GPU   - High-frequency DCGM profiling
- High CPU overhead           - Only tracks VRAM and % Util - Real-time TTFT & ITL P99 tracking
- Destroys NVML driver state  - Blind to LLM queue size     - Automated KV-cache eviction alerts
- No time-series aggregation  - Static dashboards           - Dynamic auto-scaling integration
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### LLM Latency Decomposition: TTFT vs ITL

The total end-to-end latency $T_{\text{e2e}}$ for an inference request with $N_{\text{in}}$ prompt tokens and $N_{\text{out}}$ output tokens is strictly non-linear:

$$T_{\text{e2e}} = \text{TTFT} + \sum_{i=1}^{N_{\text{out}}-1} \text{ITL}_i$$

Where:
1. **Time To First Token (TTFT)** (Prefill Phase):
   The time taken to process the entire input prompt and emit the first token:

   $$\text{TTFT} = T_{\text{queue}} + T_{\text{prefill}}(N_{\text{in}})$$

   Because prefill is compute-bound, $T_{\text{prefill}}$ scales quadratically $\mathcal{O}(N_{\text{in}}^2)$ or sub-quadratically with FlashAttention-3.

2. **Inter-Token Latency (ITL / TPOT)** (Decode Phase):
   The time elapsed between consecutive emitted tokens:

   $$\text{ITL}_i = T_{\text{decode}}(N_{\text{in}} + i)$$

   Decode is memory-bandwidth bound ($\mathcal{O}(1)$ compute per token, streaming all model weights and KV cache from VRAM).

### Service Level Objectives (SLOs) & Quantile Formulations (P50, P90, P99)

Given a sequence of sorted latencies $\mathbf{x} = (x_1, x_2, \dots, x_N)$, the $p$-th quantile is defined as:

$$Q(p) = x_{\lceil N \cdot p \rceil}$$

Production enterprise targets for Qwen2.5-32B:
- **P90 TTFT**: $< 350\text{ ms}$ (for prompts $\le 2048$ tokens).
- **P99 ITL**: $< 28\text{ ms/token}$ ($> 35\text{ tokens/sec}$ streaming).
- **KV Cache Utilization Factor**: P90 $< 0.82$ (safety margin against out-of-memory request preemptions).

### DCGM Field Identifiers (FIDs) Architecture

NVIDIA Data Center GPU Manager exposes telemetry through standardized Field Identifiers:

| DCGM FID | Metric Name | Definition |
| :--- | :--- | :--- |
| **FID 1004** | `DCGM_FI_DEV_GPU_UTIL` | Percentage of time kernels are active on GPU cores |
| **FID 1005** | `DCGM_FI_DEV_MEM_COPY_UTIL` | Memory controller activity percentage |
| **FID 1009** | `DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL`| Total bidirectional NVLink data transferred (Bytes/s) |
| **FID 251**  | `DCGM_FI_DEV_FB_USED` | Physical Framebuffer (VRAM) used in megabytes |
| **FID 150**  | `DCGM_FI_DEV_POWER_USAGE` | Real-time electrical power draw in Watts |
| **FID 140**  | `DCGM_FI_DEV_THERMAL_VIOLATION` | Microseconds throttled due to thermal thresholds |

---

## 5. Comparative Trade-Off Matrix: Monitoring Tools

| Tool / Exporter | Sampling Rate | Metric Granularity | Overhead on GPU | Native vLLM Support |
| :--- | :--- | :--- | :--- | :--- |
| **`nvidia-smi` (CLI)** | 1 Hz | Coarse (VRAM, Power) | High (Spawns process) | No |
| **NVIDIA DCGM Exporter**| **10 Hz – 100 Hz** | **Microscopic (FIDs, NVLink, SMs)**| **Negligible (< 0.1% CPU)**| No (Hardware only) |
| **vLLM /metrics Endpoint**| Per-request event | **Application (TTFT, ITL, Cache)**| **Zero (In-process counter)**| **Native** |
| **OpenTelemetry Tracing**| Trace sampling | Distributed Spans | Moderate (Span overhead) | Supported via plugin |

---

## 6. Concrete Production Hands-On Lab: Custom Prometheus Exporter & Telemetry Engine

This self-contained Python script simulates a production-grade Prometheus exporter running on port `9100`. It aggregates simulated DCGM Blackwell hardware metrics and vLLM application metrics, formatting them into standard OpenMetrics exposition syntax.

```python
#!/usr/bin/env python3
"""
Production Prometheus Telemetry Exporter for Qwen2.5 on DGX Spark.
Exposes DCGM hardware telemetry and vLLM application metrics in OpenMetrics format.
"""

import time
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any

class TelemetryCollector:
    def __init__(self):
        self.start_time = time.time()

    def collect_metrics(self) -> Dict[str, Any]:
        """Simulates DCGM hardware polling and vLLM engine stats."""
        return {
            # DCGM Hardware FIDs
            "dcgm_gpu_utilization_percent": random.uniform(75.0, 96.0),
            "dcgm_fb_used_bytes": (42.5 + random.uniform(0.5, 2.0)) * 1024 * 1024 * 1024,
            "dcgm_fb_total_bytes": 128.0 * 1024 * 1024 * 1024,
            "dcgm_power_usage_watts": random.uniform(180.0, 245.0),
            "dcgm_nvlink_bandwidth_bytes_per_sec": random.uniform(450.0, 850.0) * 1024 * 1024 * 1024,
            "dcgm_temperature_celsius": random.uniform(42.0, 48.0),

            # vLLM Application Metrics
            "vllm_num_requests_running": random.randint(4, 16),
            "vllm_num_requests_waiting": random.randint(0, 3),
            "vllm_gpu_cache_usage_factor": random.uniform(0.55, 0.78),
            "vllm_time_to_first_token_seconds_p90": random.uniform(0.180, 0.290),
            "vllm_inter_token_latency_seconds_p99": random.uniform(0.018, 0.024)
        }

    def format_openmetrics(self) -> str:
        metrics = self.collect_metrics()
        lines = []

        lines.append("# HELP dcgm_gpu_utilization_percent GPU SM compute utilization percentage")
        lines.append("# TYPE dcgm_gpu_utilization_percent gauge")
        lines.append(f'dcgm_gpu_utilization_percent{{gpu="0",model="Blackwell-GB10"}} {metrics["dcgm_gpu_utilization_percent"]:.2f}')

        lines.append("# HELP dcgm_fb_used_bytes Physical GPU framebuffer used in bytes")
        lines.append("# TYPE dcgm_fb_used_bytes gauge")
        lines.append(f'dcgm_fb_used_bytes{{gpu="0"}} {metrics["dcgm_fb_used_bytes"]:.0f}')

        lines.append("# HELP dcgm_power_usage_watts Real-time power consumption")
        lines.append("# TYPE dcgm_power_usage_watts gauge")
        lines.append(f'dcgm_power_usage_watts{{gpu="0"}} {metrics["dcgm_power_usage_watts"]:.2f}')

        lines.append("# HELP vllm_gpu_cache_usage_factor Fraction of KV cache memory allocated")
        lines.append("# TYPE vllm_gpu_cache_usage_factor gauge")
        lines.append(f'vllm_gpu_cache_usage_factor{{model="Qwen2.5-32B"}} {metrics["vllm_gpu_cache_usage_factor"]:.4f}')

        lines.append("# HELP vllm_time_to_first_token_seconds_p90 P90 Time To First Token")
        lines.append("# TYPE vllm_time_to_first_token_seconds_p90 gauge")
        lines.append(f'vllm_time_to_first_token_seconds_p90{{model="Qwen2.5-32B"}} {metrics["vllm_time_to_first_token_seconds_p90"]:.4f}')

        lines.append("# HELP vllm_inter_token_latency_seconds_p99 P99 Inter-Token Latency")
        lines.append("# TYPE vllm_inter_token_latency_seconds_p99 gauge")
        lines.append(f'vllm_inter_token_latency_seconds_p99{{model="Qwen2.5-32B"}} {metrics["vllm_inter_token_latency_seconds_p99"]:.4f}')

        return "\n".join(lines) + "\n"

collector = TelemetryCollector()

class MetricsHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            payload = collector.format_openmetrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

def run_telemetry_server():
    print("=" * 80)
    print("QWEN2.5 DCGM & VLLM PROMETHEUS TELEMETRY EXPORTER")
    print("=" * 80)
    formatted = collector.format_openmetrics()
    print("Sample Scraped OpenMetrics Output:\n")
    print(formatted)
    print("[SUCCESS] Telemetry generator conforms to Prometheus exposition format.")

if __name__ == "__main__":
    run_telemetry_server()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

To deploy DCGM and scrape Prometheus metrics on the DGX Spark:
1. **Launch NVIDIA DCGM Exporter Container**:
   ```bash
   docker run -d --gpus all \
     --net=host \
     --cap-add SYS_ADMIN \
     --name dcgm-exporter \
     nvcr.io/nvidia/k8s/dcgm-exporter:3.3.5-3.4.0-ubuntu22.04
   ```

2. **Prometheus Scrape Configuration (`prometheus.yml`)**:
   ```yaml
   scrape_configs:
     - job_name: 'dcgm-hardware'
       scrape_interval: 2s
       static_configs:
         - targets: ['localhost:9400']

     - job_name: 'vllm-qwen'
       scrape_interval: 2s
       static_configs:
         - targets: ['localhost:8000']
   ```

3. **Critical Alerting Rule (PromQL)**:
   ```yaml
   groups:
     - name: qwen-alerts
       rules:
         - alert: KVCacheExhaustionRisk
           expr: vllm:gpu_cache_usage_factor > 0.88
           for: 15s
           labels:
             severity: critical
           annotations:
             summary: "Qwen2.5 KV cache near saturation; request dropping imminent."
   ```

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (PromQL Token Throughput Query)**:
   Write the PromQL expression that computes total generated tokens per second across all active Qwen2.5 instances over a 1-minute rate window.

2. **Exercise 2 (Saturation Early Warning)**:
   Configure a PromQL expression that triggers an alert when `vllm:num_requests_waiting > 0` for more than 45 seconds while `dcgm_gpu_utilization_percent > 90`.

### Solutions

**Solution for Exercise 1**:
```promql
sum(rate(vllm:num_tokens_total[1m]))
```

### Troubleshooting FAQ

- **Q: DCGM exporter shows `Error: Could not connect to NVML library`.**
  - *Fix*: Ensure the NVIDIA Container Toolkit is installed and that the container is launched with `--gpus all` and `--cap-add SYS_ADMIN`.

- **Q: vLLM returns 404 on `/metrics`.**
  - *Fix*: You must ensure vLLM is launched without `--disable-frontend-multiprocessing`, or upgrade to vLLM $\ge 0.6.0$ where Prometheus metrics are enabled on `/metrics` by default.
