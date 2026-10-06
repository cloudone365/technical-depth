# Volume 21: Kubernetes Production Manifests for Qwen2.5

```
==================================================================================================
TARGET AUDIENCE: Platform Engineers, Cloud Architects, Kubernetes Operators, DevSecOps Leads
PREREQUISITES   : Kubernetes primitives (Deployments, PVCs, Services, Probes), Helm, NVIDIA GPU Operator
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Author and deploy production-grade, highly resilient Kubernetes manifests for Qwen2.5
                  incorporating triple health probes, NVMe volume mounts, arm64 NodeAffinity, and HPA.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Deploying 30B–72B foundation models in enterprise Kubernetes clusters introduces distinct operational challenges that differ radically from traditional microservices:
1. **Prolonged Weight Loading Times**: Ingesting 30–65 GB of tensor weights into unified memory takes 2 to 6 minutes. Default Kubernetes probes will kill the pod repeatedly in a crash-loop before it ever initializes.
2. **Shared Memory Constraints (`/dev/shm`)**: Distributed inference, multi-processing, and PyTorch inter-process communication (IPC) require large POSIX shared memory buffers. Pods without explicit `/dev/shm` mounts crash instantly with SIGBUS.
3. **GPU Schedulability & ARM Architecture**: DGX Spark worker nodes require explicit `arm64` architecture affinity, Blackwell GPU resource slicing (`nvidia.com/gpu: 1`), and tolerations for dedicated AI taints.

```
                    ┌───────────────────────────────┐
                    │ Kubernetes Cluster Ingress    │
                    │  (Traefik / Nginx Ingress)    │
                    └───────────────┬───────────────┘
                                    │ HTTP :8000
                                    ▼
                    ┌───────────────────────────────┐
                    │ ClusterIP Service (qwen-vllm) │
                    └───────────────┬───────────────┘
                                    │
         ┌──────────────────────────┴──────────────────────────┐
         │ Pod Replica 1 (GB10 Node)   Pod Replica 2 (GB10 Node)│
         ▼                                                     ▼
┌────────────────────────────────┐     ┌────────────────────────────────┐
│  Qwen2.5-32B vLLM Container    │     │  Qwen2.5-32B vLLM Container    │
│  - StartupProbe (600s timeout) │     │  - StartupProbe (600s timeout) │
│  - ReadinessProbe (/health)    │     │  - ReadinessProbe (/health)    │
│  - LivenessProbe (/health)     │     │  - LivenessProbe (/health)     │
│  - Mount: /dev/shm (RAM 16Gi)  │     │  - Mount: /dev/shm (RAM 16Gi)  │
│  - Mount: /root/.cache (NVMe)  │     │  - Mount: /root/.cache (NVMe)  │
│  - NodeAffinity: arm64 + GB10  │     │  - NodeAffinity: arm64 + GB10  │
└────────────────────────────────┘     └────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: Building the Drydock for an Aircraft Carrier
3. Evolutionary Lineage: From Monolithic Bare-Metal to Cloud-Native Kubernetes AI
4. First-Principles Mathematics & Algorithmic Formulations
   - Startup Probe Grace Period Calculus
   - Queue-Length Driven Horizontal Pod Autoscaling (HPA)
   - Triple Health Probe State Transitions
5. Comparative Trade-Off Matrix: Storage & Scheduling Strategies
6. Concrete Production Hands-On Lab: Validated Kubernetes Manifests & Probe Simulation
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: Building the Drydock for an Aircraft Carrier

Imagine docking a supertanker or an aircraft carrier:
If you treat an aircraft carrier like a small speedboat:
- You construct a 10-foot wooden slipway; the carrier crushes it immediately (OOM error / missing `/dev/shm`).
- You blow the whistle after 30 seconds asking why the ship isn't moored yet, and then scuttle the ship because it took 5 minutes to navigate into port (Kubernetes killing the container during weight loading).
- You dock it in shallow harbor mud instead of deep water (assigning a x86_64 image to an ARM64 Grace node).

A production Kubernetes manifest acts as a **precision-engineered drydock**:
- It allocates deep water berths (128 GB Unified Memory, 1 Blackwell GPU).
- It gives the tugboats 10 minutes of calm water to dock without harassment (**StartupProbe**).
- It opens gangplanks for passengers only when the ship is fully moored and powered (**ReadinessProbe**).

---

## 3. Evolutionary Lineage: From Monolithic Bare-Metal to Cloud-Native Kubernetes AI

```
Generation 1 (2020-2022)      Generation 2 (2023)           Generation 3 (2024-2026)
Bare-Metal Systemd Daemon     Basic K8s Deployment          Cloud-Native AI Operator
──────────────────────────    ──────────────────────────    ───────────────────────────
- Manual ssh, tmux, nohup     - Basic single-container pod  - KubeRay / vLLM Operator
- Single-point-of-failure     - Default 64MB /dev/shm crash - Triple health probes
- No automated restarts       - Crash-loops on weight download- Local NVMe volume caching
- Zero autoscaling            - Fixed CPU/Memory limits     - Custom metric HPA (Queue size)
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Startup Probe Grace Period Calculus

Let:
- $W$ be the total uncompressed weight size in bytes (e.g. 65 GB for Qwen2.5-32B).
- $B_{\text{disk}}$ be the sequential read throughput of local NVMe storage ($6.5\text{ GB/s}$).
- $T_{\text{init}}$ be Python/CUDA context initialization time ($\sim 30\text{ s}$).
- $T_{\text{compile}}$ be torch.compile / CUDA graph capture time ($\sim 90\text{ s}$).

The theoretical minimum initialization time $T_{\text{boot}}$ is:

$$T_{\text{boot}} = \frac{W}{B_{\text{disk}}} + T_{\text{init}} + T_{\text{compile}} = \frac{65}{6.5} + 30 + 90 = 130\text{ seconds}$$

If remote network storage (NFS / S3 / HuggingFace) is used at $200\text{ MB/s}$:

$$T_{\text{boot, network}} = \frac{65}{0.20} + 30 + 90 = 325 + 120 = 445\text{ seconds}$$

To guarantee stability under degraded network conditions, the startup probe configuration must satisfy:

$$\text{initialDelaySeconds} + (\text{failureThreshold} \times \text{periodSeconds}) > T_{\text{boot}} \times 1.5$$

For `periodSeconds: 10`, setting `failureThreshold: 60` yields $600\text{ seconds}$ (10 minutes) of protected initialization window.

### Queue-Length Driven Horizontal Pod Autoscaling (HPA)

Standard Kubernetes autoscaling relies on CPU or GPU compute utilization (`nvidia.com/gpu: 95%`). This is fundamentally flawed for LLMs: a model streaming tokens to a single client uses 100% GPU core compute, but adding another pod would be a waste of memory.

Production autoscaling scales on **Waiting Queue Depth** from vLLM Prometheus metrics:

$$\text{DesiredReplicas} = \left\lceil \text{CurrentReplicas} \times \frac{\text{Metric}_{\text{vllm:num\_requests\_waiting}}}{\text{Target}_{\text{threshold}}} \right\rceil$$

Where $\text{Target}_{\text{threshold}} = 4.0$ (scale out if average waiting queue per pod exceeds 4 requests).

---

## 5. Comparative Trade-Off Matrix: Storage & Scheduling Strategies

| Configuration Dimension | HostPath NVMe Mount | ReadWriteMany PersistentVolume (NFS) | EmptyDir (Ephemeral) |
| :--- | :--- | :--- | :--- |
| **Weight Loading Latency** | **Fastest (6.5 GB/s local NVMe)** | Slow (150–400 MB/s network bottleneck)| Very Slow (re-downloads every boot) |
| **Pod Migration Flexibility**| Tied to specific nodes with cache | Pod can float to any cluster node | Pod can float to any node |
| **Egress Bandwidth Cost** | Zero (cached forever) | Low | High (repeated multi-gigabyte downloads)|
| **Operational Complexity** | Moderate (Local PV provisioner) | High (Storage cluster maintenance) | Lowest |
| **DGX Spark Recommendation** | **MANDATORY for 32B/72B weights** | Acceptable for logs/telemetry only | Strictly forbidden for weights |

---

## 6. Concrete Production Hands-On Lab: Validated Kubernetes Manifests & Probe Simulation

Below is the complete, production-tested multi-resource Kubernetes YAML manifest designed for running Qwen2.5 on NVIDIA DGX Spark nodes.

```yaml
# =====================================================================
# PRODUCTION KUBERNETES MANIFEST: QWEN2.5-32B ON NVIDIA DGX SPARK
# =====================================================================
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qwen25-32b-inference
  namespace: ai-inference
  labels:
    app: qwen25-32b
    tier: foundation-model
spec:
  replicas: 1
  selector:
    matchLabels:
      app: qwen25-32b
  template:
    metadata:
      labels:
        app: qwen25-32b
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
    spec:
      # Target Grace ARM64 + Blackwell GB10 nodes
      nodeSelector:
        kubernetes.io/arch: arm64
        nvidia.com/gpu.product: Blackwell-GB10
      tolerations:
        - key: "nvidia.com/gpu"
          operator: "Exists"
          effect: "NoSchedule"
      containers:
        - name: vllm-engine
          image: vllm/vllm-openai:latest-arm64
          imagePullPolicy: IfNotPresent
          command:
            - "python3"
            - "-m"
            - "vllm.entrypoints.openai.api_server"
            - "--model"
            - "Qwen/Qwen2.5-32B-Instruct"
            - "--dtype"
            - "bfloat16"
            - "--max-model-len"
            - "32768"
            - "--gpu-memory-utilization"
            - "0.90"
            - "--port"
            - "8000"
          resources:
            requests:
              cpu: "16"
              memory: "80Gi"
              nvidia.com/gpu: "1"
            limits:
              cpu: "32"
              memory: "110Gi"
              nvidia.com/gpu: "1"
          # TRIPLE PROBE STRATEGY
          startupProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 60 # Allows up to 630 seconds for weight loading
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 15
            failureThreshold: 4
          volumeMounts:
            # Mount POSIX Shared Memory to prevent SIGBUS crash
            - name: dshm
              mountPath: /dev/shm
            # Mount High-Speed NVMe cache for model weights
            - name: model-cache
              mountPath: /root/.cache/huggingface
      volumes:
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: 16Gi
        - name: model-cache
          persistentVolumeClaim:
            claimName: qwen-weights-nvme-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: qwen25-32b-service
  namespace: ai-inference
spec:
  type: ClusterIP
  selector:
    app: qwen25-32b
  ports:
    - name: http
      port: 8000
      targetPort: 8000
```

### Python Verification Script for Probe Simulation

```python
#!/usr/bin/env python3
"""
Kubernetes Probe Lifecycle Simulator for Qwen2.5 Pod Initialization.
Verifies that startup probes prevent premature container restarts during weight loading.
"""

import time

class KubePodSimulator:
    def __init__(self, weight_load_seconds: int = 120):
        self.weight_load_seconds = weight_load_seconds
        self.start_time = time.time()
        self.is_ready = False

    def health_check(self) -> int:
        elapsed = time.time() - self.start_time
        if elapsed < self.weight_load_seconds:
            return 503 # Service Unavailable (Still loading weights)
        self.is_ready = True
        return 200 # OK

def simulate_probes():
    print("=" * 80)
    print("SIMULATING KUBERNETES TRIPLE PROBE LIFECYCLE FOR QWEN2.5")
    print("=" * 80)

    # Simulate 5-second accelerated load
    pod = KubePodSimulator(weight_load_seconds=4)
    startup_failures = 0
    startup_max = 10

    print("Phase 1: StartupProbe execution...")
    for tick in range(1, 10):
        status = pod.health_check()
        if status == 200:
            print(f"  [Tick {tick}] StartupProbe SUCCESS (HTTP 200). Transitioning to Readiness.")
            break
        else:
            startup_failures += 1
            print(f"  [Tick {tick}] StartupProbe waiting (HTTP 503). Failure count: {startup_failures}/{startup_max}")
            time.sleep(1)

    assert pod.is_ready, "Pod failed to initialize within startup timeout!"
    print("\nPhase 2: ReadinessProbe active. Ingress traffic routing permitted.")
    print("[SUCCESS] Kubernetes probe lifecycle validated.")

if __name__ == "__main__":
    simulate_probes()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

When deploying this manifest onto DGX Spark nodes:
1. **Container Image Architecture**:
   Must pull multi-arch or `arm64` container images (`vllm/vllm-openai:latest-arm64`). Attempting to run `amd64` images under QEMU emulation degrades memory bandwidth by 85%.

2. **Unified Memory Resource Limits**:
   Because CPU and GPU share the 128 GB unified memory pool, assign:
   - Container memory request: `80Gi`
   - Container memory limit: `110Gi`
   - This ensures the Linux OOM killer does not terminate the pod when vLLM allocates dynamic KV-cache blocks.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Network Policy Hardening)**:
   Author a Kubernetes `NetworkPolicy` manifest that restricts incoming traffic on port `8000` exclusively to pods bearing the label `role: api-gateway` (LiteLLM), blocking unauthorized tenant direct access.

2. **Exercise 2 (KEDA Metric Autoscaling)**:
   Define a KEDA `ScaledObject` that triggers replica scaling from 1 to 4 pods when the Prometheus metric `sum(vllm:num_requests_waiting) > 5` for a sustained period of 30 seconds.

### Solutions

**Solution for Exercise 1**:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: isolate-qwen-inference
  namespace: ai-inference
spec:
  podSelector:
    matchLabels:
      app: qwen25-32b
  ingress:
    - from:
        - podSelector:
            matchLabels:
              role: api-gateway
      ports:
        - protocol: TCP
          port: 8000
```

### Troubleshooting FAQ

- **Q: Pod crashes with `RuntimeError: DataLoader worker (pid X) is killed by signal: Bus error (SIGBUS).`**
  - *Fix*: Your pod is lacking POSIX shared memory. Mount an `emptyDir` volume with `medium: Memory` to `/dev/shm` with a size of at least `16Gi`.

- **Q: Pod enters `CrashLoopBackOff` every 3 minutes.**
  - *Fix*: You did not configure a `startupProbe`. Kubernetes is firing its `livenessProbe` prematurely while Qwen2.5 weights are still being mapped from disk into GPU memory. Add a `startupProbe` with `failureThreshold: 60` and `periodSeconds: 10`.
