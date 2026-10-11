# Chapter 20 · vLLM High-Throughput LLM Serving: KV-Cache Budgeting, Probes, Autoscaling & Load Testing

> **02-Kubernetes · Part VII — LLM serving · Chapter 20 of 28** · ← [Chapter 19 · Large-scale SuperPOD & network fabrics](19-large-scale-superpod-and-network-fabrics.md) · [All chapters](00-kubernetes-step-by-step-guide.md) · [Chapter 21 · NVIDIA Triton Inference Server](21-nvidia-triton-inference-server.md) →

| | |
|---|---|
| **You will build** | A production-shaped vLLM Deployment in the root's `llm-serving` namespace: shared model cache, startup/readiness/liveness probes, graceful drain, UMA-aware memory flags, metrics scraped by the root's Prometheus, queue-depth autoscaling with KEDA, a repeatable benchmark that gives you TTFT/TPOT/throughput for your GB10 — and a sizing table that shows which engines fit `serving-budget` together |
| **Hardware** | dgx-spark-1 |
| **Time** | 90 min |
| **Risk** | Low. The engine takes ~24 GiB of UMA at `--gpu-memory-utilization 0.20`; the pod may use up to 32 Gi of the `llm-serving` budget |
| **Clusters** | `spark-root` (the Deployment and its cgroup, the `serving-budget` quota, KEDA, Traefik, Prometheus, alerts) · optionally `dev-llms` (try the manifest in `llm-sandbox` first) |
| **Lab files** | [`manifests/root-llms/90-serving/vllm/`](lab/manifests/root-llms/90-serving/vllm/) (`vllm.yaml`, `vllm-bench.yaml`), [`manifests/root-llms/60-storage/model-prefetch-job.yaml`](lab/manifests/root-llms/60-storage/model-prefetch-job.yaml), [`manifests/root-llms/90-serving/autoscaling.yaml`](lab/manifests/root-llms/90-serving/autoscaling.yaml), [`manifests/root-llms/10-tenancy/quotas.yaml`](lab/manifests/root-llms/10-tenancy/quotas.yaml), [`manifests/root/95-observability/`](lab/manifests/root/95-observability/) (`rules.yaml`, `vcluster-workloads.yaml` with the ServiceMonitor `llm-workloads`), [`scripts/uma-watch.sh`](lab/scripts/uma-watch.sh) |

---

## 1. Why this matters on a Spark

vLLM's throughput comes from **PagedAttention** (the KV cache is paged in fixed blocks, like virtual memory, so it doesn't fragment) and **continuous batching** (new requests join the running batch at every decode step). On a Spark there's one extra rule: `--gpu-memory-utilization` is a fraction of the **whole unified pool** (≈121.7 GiB), the same memory DGX OS, the kubeadm control plane, both vCluster control planes, the page cache and every other pod use. On a discrete GPU, 0.90 is normal. Here, 0.90 would starve the whole machine.

And there are **two budgets**, both of which must hold:

1. **vLLM's own**: weights + activations + KV cache ≤ fraction × 121.7 GiB. At 0.20 that's ≈ 24 GiB.
2. **The cluster's**: the pod's memory request and limit (both 32 Gi) must fit `serving-budget` in `llm-serving` (up to 90 Gi), *and* the node must still have 32 Gi of allocatable memory that no other pod has requested — the ~90 GiB LLM memory pool is shared with the `batch` namespace, and the scheduler is what keeps the two from overcommitting it. Whether CUDA allocations are charged to the pod's cgroup is something you measure (Chapter 14 §5.5); the manifests are sized as if they are.

| Flag | What it controls | Lab value | Rule of thumb on a Spark |
|---|---|---|---|
| `--gpu-memory-utilization` | weights + activations + KV cache ≤ fraction × total | 0.20 | sum over all engines running at once ≲ 0.72 (§2) |
| `--max-model-len` | longest context per sequence | 8192 | only what users need. KV grows linearly |
| `--max-num-seqs` | concurrent sequences in the batch | 64 | lower → better latency, higher → throughput |
| `--enable-prefix-caching` | reuse KV for shared prefixes (system prompts, RAG templates) | on | almost always on |
| `--download-dir` / `HF_HOME` | where weights live | `/models/hf` (PVC `model-cache`) | never inside the container layer |

---

## 2. Architecture — HLD

```mermaid
flowchart LR
  CL["clients<br/>llm.lab.local → 192.168.0.115"] --> TR
  subgraph ROOT["root cluster spark-root (one API server, one scheduler)"]
    direction LR
    TR["Traefik · ns ingress<br/>/v1 · streaming-safe"] --> SVC["Service vllm:8000<br/>ns llm-serving"]
    subgraph POD["Deployment vllm · priority spark-serving · quota serving-budget · request = limit 32 Gi"]
      direction TB
      API["OpenAI API server<br/>/v1/chat/completions · /metrics · /health"]
      SCH["scheduler<br/>continuous batching"]
      ENG["model executor<br/>CUDA graphs · sm_121"]
      KV[("paged KV cache<br/>blocks in UMA")]
      API --> SCH --> ENG --> KV
    end
    SVC --> API
    PVC[("PVC model-cache<br/>/models/hf")] --> ENG
    PRE["Job model-prefetch"] --> PVC
    KEDA["KEDA ScaledObject · ns keda<br/>num_requests_waiting"]
    PROM["Prometheus kps · ns observability<br/>ServiceMonitor llm-workloads"]
    GPU["GB10 · 1 of 15 slices"]
    KEDA -->|"query kps-prometheus:9090"| PROM
    KEDA -.->|"replicas"| POD
    PROM -->|"scrape /metrics<br/>+ vcluster=root labels"| SVC
    ENG --> GPU
  end
  classDef ctrl fill:#1f6feb,stroke:#0b3d91,color:#fff
  classDef gpu fill:#76b900,stroke:#3d6000,color:#000
  classDef net fill:#8250df,stroke:#4c2889,color:#fff
  classDef store fill:#bf8700,stroke:#7a5600,color:#fff
  classDef obs fill:#fb8500,stroke:#9a5200,color:#000
  classDef ext fill:#24292f,stroke:#000,color:#fff
  classDef tenant fill:#eaeef2,stroke:#57606a,color:#000
  class ENG,GPU gpu
  class TR,SVC net
  class KV,PVC store
  class PROM,KEDA obs
  class CL ext
  class API,SCH,PRE tenant
  style POD fill:#f4fbe8,stroke:#76b900,stroke-width:2px
  style ROOT fill:#e6f4f5,stroke:#0e7c86
```

Everything in this chapter is applied to the root (`--context spark-root`) and runs there under its real name: the vLLM pod is `vllm-…` in `llm-serving`, with no syncer in between. The root scheduler hands it a time-slice, the root's kube-proxy routes the Service, the root's Prometheus scrapes it, and KEDA (namespace `keda`, also on the root) queries that Prometheus directly at `http://kps-prometheus.observability.svc:9090`. If you want to try a changed manifest before it touches the running models, apply it to the `dev-llms` sandbox first (namespace `llm-sandbox`, a ~0.5B model at util 0.02, Chapter 04).

**Model memory on the root.** Of the ~109.7 GiB allocatable, the two vClusters take 4 Gi each and the platform (Cilium, MetalLB, GPU Operator, Prometheus, Traefik, Kueue, KEDA, platform-tools) ~11.7 GiB, which leaves an **LLM memory pool of about 90 GiB**, shared by `llm-serving` and `batch`. In `--gpu-memory-utilization` terms that is 90 / 121.7 ≈ 0.75; with some headroom the rule is: **the sum of the util values of all models running at the same time ≲ 0.72**. Each util value is a fraction of the whole pool (util × ~121.7 GiB). Sets that fit:

| Set running together | util values | Sum | ≈ UMA |
|---|---|---|---|
| three 7–8B models, 16k context | 0.20 + 0.20 + 0.20 | 0.60 | 3 × 24 GiB |
| one 32B FP8 + one 7B | 0.45 + 0.25 | 0.70 | 54 + 30 GiB |
| R1-Distill-32B BF16, alone | 0.70 | 0.70 | 84 GiB |

The quota is the second check, not the first: `serving-budget` (90 Gi of requests and of limits) holds two of the lab's 32 Gi engines plus the always-on pods, and every GPU container sets memory request = limit, so the scheduler places a model only where its full size is still free; a model that doesn't fit waits Pending instead of squeezing a running one. A set near 0.72 needs pod requests/limits sized from the Chapter 14 §5.5 measurement (is CUDA memory charged to the cgroup?), not from the util value. §9 has the lab's combinations.

---

## 3. LLD

### 3.1 KV-cache math

Per token, per sequence: `2 (K and V) × layers × kv_heads × head_dim × bytes_per_element`.

| Model | Layers | KV heads | Head dim | KV / token (BF16) | 8K context, 1 seq | 64 seqs × 2K tokens |
|---|---|---|---|---|---|---|
| Qwen2.5-0.5B-Instruct (lab smoke) | 24 | 2 | 64 | **12 KiB** | 96 MiB | 1.5 GiB |
| Qwen2.5-7B-Instruct | 28 | 4 | 128 | **56 KiB** | 448 MiB | 7 GiB |
| Llama-3.1-8B-Instruct | 32 | 8 | 128 | **128 KiB** | 1 GiB | 16 GiB |
| Qwen2.5-32B-Instruct | 64 | 8 | 128 | **256 KiB** | 2 GiB | 32 GiB |

Budget at `--gpu-memory-utilization 0.20` ≈ 24 GiB: the 0.5B model (≈1 GiB weights, a little more for activations and CUDA graphs) leaves ~21 GiB for KV — about 1.8 M tokens, far more than 64 × 8K. A 7B model in BF16 (~15 GiB weights) leaves ~7 GiB, ≈ 130 K tokens: it fits, at lower concurrency. A 32B model in BF16 (~64 GiB weights) doesn't fit 0.20 at all; it needs 0.70, nearly the whole LLM memory pool, and runs alone (§2). FP8 or AWQ-INT4 (~17–35 GiB) at 0.45 leaves room for a 7B beside it (modules 03/04). vLLM logs the exact figure at startup: `Available KV cache memory` / `GPU KV cache size: … tokens`.

### 3.2 Probes and lifecycle

| Mechanism | Setting | Protects against |
|---|---|---|
| `startupProbe` | `/health`, 10 s × 180 = 30 min | killing a pod that's still downloading or compiling (breakfix 05) |
| `readinessProbe` | `/health`, 5 s | routing to a loading or overloaded pod. Required in `llm-serving` by the CEL policy `spark-serving-needs-readiness` |
| `livenessProbe` | `/health`, 15 s × 4 | a wedged engine |
| `preStop: sleep 15` + `terminationGracePeriodSeconds: 120` | — | cutting in-flight streams on rollout (Chapter 09 §5.6) |
| `strategy: Recreate` | — | two copies of the model in UMA (and 2 × 32 Gi in the quota) during a rollout |
| `/dev/shm` 8 Gi | memory-backed emptyDir | tensor-parallel and worker IPC |

The probes are executed by the kubelet on dgx-spark-1 against the pod, and their results go straight into the pod status on the root API server — there is no second copy of the pod to update. The `preStop` sleep covers the propagation that matters for traffic: the pod leaving the Service's EndpointSlice, which is what kube-proxy and Traefik route by (Chapter 09).

### 3.3 Metrics you'll use

| Metric | Meaning |
|---|---|
| `vllm:num_requests_running` / `vllm:num_requests_waiting` | batch size / queue depth. **Scale on waiting** |
| `vllm:kv_cache_usage_perc` (older: `gpu_cache_usage_perc`) | KV-cache fill. Near 1.0 → preemptions |
| `vllm:num_preemptions` | sequences evicted and recomputed |
| `vllm:time_to_first_token_seconds` | TTFT histogram (prefill + queue) |
| `vllm:inter_token_latency_seconds` (older: `time_per_output_token_seconds`) | decode speed per token |
| `vllm:prompt_tokens` / `vllm:generation_tokens` | token throughput counters |

The root ServiceMonitor `llm-workloads` (in [`vcluster-workloads.yaml`](lab/manifests/root/95-observability/vcluster-workloads.yaml)) keeps only the engine Services in `llm-serving` and `ingress` and adds labels, so every series carries `vcluster="root"`, `vnamespace="llm-serving"`, `vservice="vllm"` and `vpod`. On the root, `vnamespace`/`vpod` simply repeat `namespace`/`pod`; the labels exist so the same queries also cover sandbox engines, which the ServiceMonitor `vcluster-workloads` in the same file labels `vcluster="dev-llms"`, `vnamespace="llm-sandbox"` (their `namespace` and `pod` are the translated root names, `vc-dev-llms` and `vllm-…-x-llm-sandbox-x-dev-llms`). The lab's rules in [`rules.yaml`](lab/manifests/root/95-observability/rules.yaml) aggregate by `vcluster, vnamespace` and use `A or B` so they work with both metric generations.

---

## 4. Integrations

- **Storage (Chapter 13)**: the prefetch Job fills `model-cache` (300 Gi, StorageClass `local-nvme-retain`), and vLLM starts from local NVMe. On the Spark the weights are under `/data/k8s/retain/llm-serving/model-cache` — the namespace and PVC name you wrote, since nothing translates them.
- **Ingress (Chapter 11)**: Traefik runs on the root next to the models (`ingress` namespace, `192.168.0.115`) and watches only `llm-serving` and `ingress`. Point an HTTPRoute at `vllm:8000` instead of `mock-llm` once you've proven the path with the mock.
- **Autoscaling**: KEDA (on the root) queries `kps-prometheus.observability` directly. On one GB10, extra replicas mostly add *queueing* capacity, not compute — and a second replica, which fits `serving-budget` (§9), costs another 0.20 of model memory for that. `maxReplicaCount: 1` until dgx-spark-2 joins.
- **Scheduling (Chapter 07)**: `spark-serving` is a root PriorityClass, and one root scheduler places every pod on the Spark, so a vLLM pod at `spark-serving` can preempt a `spark-preemptible` pod in dev-lab to start.
- **Modules 03–06**: each model family is a kustomize-style variation of this Deployment (model, quantisation, engine flags, chat template), sized against §9.

---

## 5. Lab

All commands run from `02-Kubernetes/lab` with the lab kubeconfig:

```bash
cd "02-Kubernetes/lab"
export KUBECONFIG="$PWD/../../01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml"
scripts/apply-lab.sh root                                   # includes root-llms: namespaces, quotas, model-cache PVC, mock-llm, Qdrant
kubectl --context spark-root -n llm-serving describe resourcequota serving-budget
```

### 5.1 Prefetch weights, then deploy

```bash
kubectl --context spark-root -n llm-serving create secret generic hf-token --from-literal=token="${HF_TOKEN:-none}" \
  --dry-run=client -o yaml | kubectl --context spark-root apply -f -
kubectl --context spark-root apply -f manifests/root-llms/60-storage/model-prefetch-job.yaml
kubectl --context spark-root -n llm-serving wait --for=condition=complete job/model-prefetch --timeout=30m
ssh dgxadmin@192.168.0.100 "sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'"   # start the load with a clean page cache
kubectl --context spark-root apply -k manifests/root-llms/90-serving/vllm
kubectl --context spark-root -n llm-serving logs -f deploy/vllm | grep -E 'Loading|weights|KV cache|blocks|CUDA graph|Uvicorn|ERROR'
```

`vllm.yaml` carries an `hf-token` Secret with a placeholder value, so applying it overwrites the token you just created. For the public Qwen smoke model that doesn't matter; for gated models, re-create the Secret after the apply (or remove it from the file and keep tokens in Vault — Chapter 28).

Expected log landmarks:

```text
INFO … Loading weights took 1.9 seconds
INFO … Model loading took 0.93 GiB and 3.1 seconds
INFO … Available KV cache memory: 21.2 GiB
INFO … GPU KV cache size: 1,850,000 tokens
INFO … Capturing CUDA graphs … took 12 seconds
INFO:     Uvicorn running on http://0.0.0.0:8000
```

(Numbers are illustrative. Note yours.) Then look at what the quota and the scheduler charged:

```bash
kubectl --context spark-root -n llm-serving describe resourcequota serving-budget | grep -E 'memory|requests.cpu|gpu'
kubectl --context spark-root describe node dgx-spark-1 | grep -A9 'Allocated resources'
kubectl --context spark-root -n llm-serving get pods -o wide | grep vllm
```

The pod's 32 Gi, 1.5 CPU and 1 slice appear in `serving-budget` (checked when the API server admits the pod) and in the node's *Allocated resources* (counted by the scheduler when it placed the pod). Because the request equals the limit, the 32 Gi the scheduler subtracted is the size the engine may really reach. There is one quota layer, and the pod name is the one in the Deployment's ReplicaSet — no vCluster suffix.

### 5.2 First request (streaming)

```bash
kubectl --context spark-root -n llm-serving port-forward svc/vllm 8000 &
curl -s localhost:8000/v1/models | jq -r '.data[].id'
curl -sN localhost:8000/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"qwen2.5-0.5b","stream":true,"max_tokens":64,
  "messages":[{"role":"user","content":"In one sentence: what is unified memory on the DGX Spark?"}]}' \
  | sed -n 's/^data: //p' | grep -v DONE | jq -rj '.choices[0].delta.content // empty'; echo
```

The port-forward goes laptop → root API server → the kubelet on dgx-spark-1 → the pod. (Port-forwarding to a pod in the `dev-llms` sandbox takes one more hop: its API server at `192.168.0.112` proxies the request to the root's kubelet.)

### 5.3 Watch UMA during load and restart

On the Spark (the script reads `/sys/fs/cgroup`; the context defaults to `spark-root`, and only a sandbox pod needs `dev-llms` as third argument):

```bash
scripts/uma-watch.sh llm-serving "$(kubectl --context spark-root -n llm-serving get pod -l app=vllm -o jsonpath='{.items[0].metadata.name}')" spark-root 2
```

The `pod.mem.max` column is the 32 Gi limit as the kernel sees it, in the pod's cgroup. While it runs, restart the Deployment in another terminal (`kubectl --context spark-root -n llm-serving rollout restart deploy/vllm`) and restart `uma-watch.sh` on the new pod. `Recreate` frees the old pod's memory before the new one allocates. Change the strategy to `RollingUpdate` and repeat: the rollout goes through, because a second 32 Gi pod fits `serving-budget` (66.4 of 90 Gi, §9) and the node still has 32 Gi unrequested in the LLM memory pool when no batch job runs, and MemAvailable dips by roughly *two* engines' worth (0.40 of the pool) while both copies are up. The quota no longer stops a double copy; memory is the limit, which is why the lab keeps `Recreate`. Set it back.

### 5.4 Graceful rollout with streams in flight

```bash
( for i in $(seq 5); do curl -sN localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
   -d '{"model":"qwen2.5-0.5b","stream":true,"max_tokens":1500,"messages":[{"role":"user","content":"Count slowly."}]}' \
   | tail -1 & done; wait ) &
sleep 3; kubectl --context spark-root -n llm-serving rollout restart deploy/vllm; wait
```

Expected: each stream ends with `data: [DONE]`. The preStop sleep and 120 s grace let running generations finish before SIGTERM. Streams longer than 120 s would still be cut, so size the grace to your longest generation. The grace period travels with the delete: the kubelet gives the container exactly the `terminationGracePeriodSeconds` the API server recorded. Restart the port-forward afterwards (it was bound to the old pod).

### 5.5 Benchmark

The benchmark client is small: 500m CPU and a 1.5 Gi memory limit bring `serving-budget` to 2.4 of 8 CPU and 35.9 of 90 Gi next to vLLM (§9). Check the headroom first if other engines are running:

```bash
kubectl --context spark-root -n llm-serving describe resourcequota serving-budget | grep -E 'requests.cpu|limits.memory'
kubectl --context spark-root apply -f manifests/root-llms/90-serving/vllm/vllm-bench.yaml
kubectl --context spark-root -n llm-serving logs -f job/vllm-bench
```

If it is refused (`exceeded quota: serving-budget`), other engines beside vLLM are holding the rest of the 90 Gi; park one of them (§9).

Record, per concurrency (1 / 8 / 32): **output token throughput**, **mean and P99 TTFT**, **mean and P99 TPOT/ITL**. The shape to expect:

| Concurrency | Throughput | TTFT | TPOT |
|---|---|---|---|
| 1 | lowest | lowest | lowest |
| 8 | ~5–7× higher | slightly up | slightly up |
| 32 | highest (continuous batching) | up (queueing, prefill contention) | up |

Now repeat with `gemm-contention` at 2 replicas **on the root** (`kubectl --context spark-root -n platform-tools scale deploy gemm-contention --replicas=2`, Chapter 16). Those pods don't appear in `serving-budget` — they come out of the platform's 2 slices — but they share the same GB10. The drop is what a noisy GPU neighbour from another namespace costs your SLO. Scale it back to 0.

### 5.6 Observability and alerts

```bash
curl -s localhost:8000/metrics | grep -E '^vllm:(num_requests_(running|waiting)|kv_cache_usage_perc|gpu_cache_usage_perc|num_preemptions)' | head
kubectl --context spark-root apply -k manifests/root/95-observability
kubectl --context spark-root -n observability port-forward svc/kps-prometheus 9090 &
curl -s 'localhost:9090/api/v1/query' --data-urlencode 'query=vllm:num_requests_running' \
  | jq -r '.data.result[].metric | "\(.vcluster) \(.vnamespace) \(.vpod)  ←  \(.namespace)/\(.pod)"'
```

Expected: one line, `root llm-serving vllm-… ← llm-serving/vllm-…` — on the root the `v` labels repeat the real names. A sandbox engine in dev-llms would print `dev-llms llm-sandbox vllm-… ← vc-dev-llms/vllm-…-x-llm-sandbox-x-dev-llms`: the tenant's name and the root's name for the same series. In Grafana (`http://192.168.0.100:32000`), row *Serving SLOs*: TTFT p95, TPOT p95, queue and KV cache. Force a `VLLMQueueBacklog` alert (more than 32 waiting) by setting `--max-num-seqs=16` in `vllm.yaml` and running the benchmark loop with `--max-concurrency 256`; put both back afterwards.

### 5.7 Queue-depth autoscaling (KEDA)

```bash
scripts/install-addons.sh keda                                      # on the root
kubectl --context spark-root -n observability get svc kps-prometheus  # what the ScaledObjects query
kubectl --context spark-root apply -f manifests/root-llms/90-serving/autoscaling.yaml
kubectl --context spark-root -n llm-serving get scaledobject,hpa
```

KEDA creates an HPA in `llm-serving`; the root's controller-manager scales the Deployment and creates or deletes the pods. With `maxReplicaCount: 1`, the vLLM ScaledObject only shows the metric (`kubectl --context spark-root -n llm-serving get hpa -w` → current/target). The `mock-llm` ScaledObject really scales on in-flight connections at Traefik (needs `192.168.0.115 llm.lab.local` in your laptop's `/etc/hosts`):

```bash
seq 400 | xargs -P64 -I{} curl -sN -o /dev/null http://llm.lab.local/v1/chat/completions -d '{"stream":true,"max_tokens":200}' &
kubectl --context spark-root -n llm-serving get deploy mock-llm -w        # 2 → up to 6, back after cooldown
```

From now on, KEDA owns `deploy/vllm`'s replica count: `kubectl scale` alone is undone at the next poll. To swap engines (Chapters 21–23), use the pause annotation in §9.

---

## 6. Verify

```bash
scripts/verify.sh llm-budget storage serving observability
```

| Check | Expected |
|---|---|
| `vLLM answered a chat completion` | PASS |
| startup log | `KV cache size` line recorded |
| `llm-budget` | `llm-serving serving-budget: 8 90Gi 6` · the vLLM pod counted in `serving-budget` and in the node's allocated memory (32 Gi request) |
| rollout with 5 streams | 5 × `[DONE]` |
| benchmark | table for c = 1/8/32 saved, with and without a root GPU neighbour |
| Prometheus | `vllm:*` series with `vcluster="root"`, `vnamespace="llm-serving"` |
| KEDA | `mock-llm` scales out under load and back after the cooldown |

---

## 7. Troubleshooting

| Symptom | Cause | Diagnose | Fix |
|---|---|---|---|
| `ValueError: … memory … is less than the model` / CUDA OOM at startup | fraction too small for weights + activations, **or** UMA already used by others (any cluster, or the page cache) | log line with requested vs available. `free -g` on the Spark | raise the fraction carefully, drop page cache, scale down neighbours (`kubectl --context spark-root get pods -A -o wide`), quantise |
| `No available memory for the cache blocks` | weights fit, no room for KV | startup log | raise the fraction, or lower `--max-model-len` |
| Deployment shows 0/1, `exceeded quota: serving-budget` in the ReplicaSet events | other engines already hold most of the 90 Gi in `llm-serving` | `kubectl --context spark-root -n llm-serving describe resourcequota serving-budget` | park an engine (§9) |
| Pod `Pending`, event `Insufficient memory` | the LLM memory pool is taken: other engines plus running batch pods (all request = limit) | `kubectl --context spark-root describe node dgx-spark-1 \| grep -A9 'Allocated resources'`, `kubectl --context spark-root -n batch get pods` | wait for the batch job, park an engine (§9), or lower the engine's request/limit to the measured peak |
| PVC `model-cache` refused: `exceeded quota … requests.storage` | the PVC, plus Qdrant's 20 Gi and any other claims in `llm-serving`, asks for more than `serving-budget` (1 Ti) | `kubectl --context spark-root -n llm-serving describe resourcequota serving-budget` | request a size that fits |
| Pod `OOMKilled` (137) | pod memory limit < CPU-side peak (tokenizer, CUDA graphs, host buffers, CUDA memory if charged) | `kubectl --context spark-root -n llm-serving describe pod`, Chapter 14 §5.5 result | raise request and limit together to measured peak + 20 % — and check §9 still adds up |
| Restarts during first start | liveness fires before load completes | events `Liveness probe failed` | startupProbe (as in the lab) |
| `no kernel image is available` | wheel/image without sm_121 | image tag | NGC vLLM image for DGX Spark / arm64 |
| High TTFT, low GPU use | queueing in front (ingress rate limit) or prefill-heavy prompts | `num_requests_waiting` vs running | raise `max-num-seqs`, chunked prefill (default in recent vLLM), prefix caching |
| Tokens arrive in bursts | buffering proxy | TTFB ≈ total (Chapter 11 §5.3) | remove response buffering |
| `KV cache usage` ~1.0 and preemptions rising | too many long sequences | `vllm:num_preemptions` | lower `max-num-seqs`/`max-model-len`, or add capacity |
| `kubectl scale deploy vllm --replicas=0` doesn't stick | KEDA's HPA scales it back to `minReplicaCount` | `kubectl --context spark-root -n llm-serving get scaledobject vllm` | pause the ScaledObject (§9) |
| KEDA `ScaledObject` not Ready, `connection refused` / `no such host` | kps not installed on the root, or `serverAddress` not `http://kps-prometheus.observability.svc:9090` | `kubectl --context spark-root -n observability get svc,endpoints kps-prometheus` | `scripts/install-addons.sh kps`, then re-apply `autoscaling.yaml` |

---

## 8. Scale-out path

| Lab | 2 Sparks | Datacenter |
|---|---|---|
| 1 replica, 1 slice | 2 replicas (one per Spark) behind Traefik, weights on NFS, KEDA `maxReplicaCount: 2` — `serving-budget` already holds two 32 Gi engines, and each replica's 0.20 is taken from its own Spark's pool (§9). The root scheduler places pods on dgx-spark-2 as soon as it joins | many replicas, prefix/KV-aware routing (Gateway API Inference Extension, llm-d) |
| single-GPU model | tensor/pipeline parallel across the CX-7 (`--tensor-parallel-size 2` with Ray, or `--pipeline-parallel-size 2`) for models that don't fit one Spark | TP within NVLink domains, PP/EP across nodes, disaggregated prefill/decode (Chapter 23) |
| KEDA on queue depth | same | + SLO-based scaling and admission control |
| the LLM stack in root namespaces, a dev-llms sandbox in front | same | a serving cluster per environment or business unit; budgets per cluster, promoted by GitOps (Chapter 28) |

---

## 9. The llm-serving budget: which engines fit together

Chapters 20–23 all deploy into `llm-serving` on the root, which has one ceiling ([`manifests/root-llms/10-tenancy/quotas.yaml`](lab/manifests/root-llms/10-tenancy/quotas.yaml)) and shares its memory with `batch`:

```text
llm-serving  serving-budget  8 CPU · up to 90 Gi · 6 slices · 1 Ti    ← model servers
batch        batch-budget    2 CPU · up to 12 Gi · 3 slices · 200 Gi  ← training (= Kueue spark-cq)
pool         ~90 GiB of memory shared by both; CPU and slices are split
platform     3 CPU · ~11.7 GiB · 2 slices (no quota)                   ← Cilium, MetalLB, GPU Operator, Prometheus, Traefik, Kueue, KEDA, platform-tools
```

CPU and slices are split exactly (3 + 8 + 2 = 13 CPU, 2 + 6 + 3 = 11 slices); only memory is shared. Serving may use the whole pool while nothing trains; a training job takes up to 12 Gi of the same pool. What keeps the two from overcommitting unified memory is the scheduler: every GPU container sets memory request = limit, so a pod that doesn't fit waits Pending and never squeezes a running model. What each lab object charges to `serving-budget` (CPU requests, memory **limits** — equal to the requests for the engines — slices, from the manifests), and the share of the UMA pool it asks vLLM/SGLang for:

| Object | CPU | Memory limit | Slices | util | Volume |
|---|---|---|---|---|---|
| `mock-llm` ×2 + `mock-llm-canary` (always on) | 150m | 384 Mi | 0 | – | 09 (KEDA may scale mock-llm to 6: +200m, +512 Mi) |
| `qdrant` (always on) | 250m | 2 Gi | 0 | – | 10 |
| `vllm` | 1500m | 32 Gi | 1 | 0.20 | 21 |
| `vllm-bench` Job | 500m | 1.5 Gi | 0 | – | 21 |
| `triton` + `triton-perf` | 1000m + 500m | 12 Gi + 2 Gi | 1 | small (toy models) | 22 |
| `sglang` | 1500m | 32 Gi | 1 | 0.20 (`--mem-fraction-static`) | 23 |
| KServe `qwen-small` predictor | 1500m | 32 Gi | 1 | 0.20 | 23 |
| `vllm-prefill` + `vllm-decode` + `pd-proxy` | 1600m | 40.25 Gi | 2 | 0.15 + 0.15 | 24 |

| Combination | CPU | Memory | util sum | Fits 8 CPU · 90 Gi (and ≲ 0.72)? |
|---|---|---|---|---|
| always-on + vLLM | 1.9 | 34.4 Gi | 0.20 | yes — 55.6 Gi to spare |
| always-on + vLLM + bench (§5.5) | 2.4 | 35.9 Gi | 0.20 | yes |
| always-on + vLLM + Triton + perf | 3.4 | 48.4 Gi | 0.20 + small | yes |
| always-on + Triton + perf | 1.9 | 16.4 Gi | small | yes |
| always-on + vLLM + one of SGLang / KServe | 3.4 | 66.4 Gi | 0.40 | yes |
| always-on + vLLM + SGLang + KServe | 4.9 | 98.4 Gi | 0.60 | **no** — memory → park one |
| always-on + P/D | 2.0 | 42.6 Gi | 0.30 | yes |
| always-on + vLLM + P/D | 3.5 | 74.6 Gi | 0.50 | yes — 15.4 Gi to spare |

So the rule for 21–23 is **two engines side by side**: `serving-budget` holds two 32 Gi engines (or vLLM + Triton, or vLLM + P/D), and memory, i.e. the sum of their util values, is the real limit (§2). Three 32 Gi engines overflow the quota even though their util sum (0.60) would fit; running three 7–8B models needs requests/limits trimmed to the measured peak (Chapter 14 §5.5). To make room for a third engine, or for a bigger model, park one like this:

```bash
# park vLLM (KEDA-safe: the annotation scales to 0 and keeps it there)
kubectl --context spark-root -n llm-serving annotate scaledobject vllm autoscaling.keda.sh/paused-replicas=0 --overwrite \
  || kubectl --context spark-root -n llm-serving scale deploy vllm --replicas=0
while kubectl --context spark-root -n llm-serving get pod -l app=vllm -o name | grep -q .; do sleep 5; done
# bring it back
kubectl --context spark-root -n llm-serving annotate scaledobject vllm autoscaling.keda.sh/paused-replicas- \
  || kubectl --context spark-root -n llm-serving scale deploy vllm --replicas=1
```

Two things the table doesn't show. First, **batch** draws on the same ~90 GiB: Kueue admits a training job against `spark-cq` (up to 12 Gi), but its pods still need free memory on the node. With vLLM + P/D running (74.6 Gi) a 12 Gi job fits; with more serving than ~78 Gi its pods wait Pending until an engine is parked — training waits for serving, never the reverse. Read the real state with `kubectl --context spark-root describe node dgx-spark-1 | grep -A9 'Allocated resources'` before starting a batch job next to running engines. Second, quotas and requests count *declared* bytes, the GB10 counts *real* bytes: an engine's `--gpu-memory-utilization` share of 121.7 GiB is taken from the same pool as every other pod, and no quota sees the `platform-tools` benchmarks, a sandbox model in dev-llms or a `docker run` on the host. Keep the util sum ≲ 0.72 yourself.

---

## 10. Checklist

- [ ] I can compute KV bytes per token for any model from its config and turn that into a context/concurrency budget.
- [ ] My vLLM pod starts from a pre-filled cache, drains gracefully, and never runs two copies during a rollout.
- [ ] I can show the vLLM pod in `serving-budget` and in the node's allocated memory, and find its cgroup on the Spark.
- [ ] I have TTFT/TPOT/throughput numbers for three concurrency levels, with and without a GPU neighbour.
- [ ] Alerts and autoscaling key off queue depth, not GPU utilisation — and KEDA queries the root's Prometheus directly.
- [ ] I can tell from §9 whether a new engine fits beside the running ones — by quota and by util sum — and park one without fighting KEDA.
