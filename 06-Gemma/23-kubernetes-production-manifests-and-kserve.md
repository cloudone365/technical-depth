# Volume 23: Kubernetes Production Manifests and KServe

```
==================================================================================================
TARGET AUDIENCE: Cloud Platform Engineers, Kubernetes Administrators, Production MLOps Leads
PREREQUISITES   : Kubernetes Core (Deployments, Services, PVCs), KServe v0.13+, NVIDIA GPU Operator
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master production Kubernetes manifests, KServe InferenceServices, triple health probes,
                  and shared memory IPC volume mounts for serving Google Gemma 2 27B on NVIDIA Blackwell.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Deploying Gemma 2 27B at enterprise scale requires a hardened, automated container orchestration substrate. Ad-hoc `docker run` scripts on single servers cannot provide automated self-healing, rolling zero-downtime updates, horizontal request-driven autoscaling, or declarative governance.

**Kubernetes (K8s)** paired with the **NVIDIA GPU Operator** and **KServe** delivers cloud-native foundation model serving. This volume provides production-grade declarative YAML manifests specifically tailored for Gemma 2 27B: configuring `/dev/shm` shared memory mounts to prevent PyTorch deadlocks, establishing triple health probes (startup, readiness, liveness) to handle heavy model weight loading, and deploying autoscaling KServe inference services.

```
Incoming Client Ingress Traffic (:443) ──► K8s Ingress Controller (Traefik / Envoy)
                                                         │
                                                         ▼
                                       ┌───────────────────────────────────┐
                                       │    K8s ClusterIP Service (:8000)  │
                                       └─────────────────┬─────────────────┘
                                                         │
                                                         ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        K8S DEPLOYMENT POD (NVIDIA DGX SPARK NODE)                      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  - Container: vllm/vllm-openai:latest (ARM64 Blackwell Build)                          │
│  - GPU Limit: nvidia.com/gpu: 1 (GB10 128 GB Unified Memory)                           │
│  - IPC Volume: emptyDir (medium: Memory, sizeLimit: 16Gi) mounted to /dev/shm          │
│  - PVC Mount: /root/.cache/huggingface -> High-Speed NVMe Storage                      │
│                                                                                        │
│  - Startup Probe  : httpGet /health (Initial Delay: 30s, Timeout: 5s, Failure: 30)     │
│  - Readiness Probe: httpGet /health (Period: 10s, Success: 1)                          │
│  - Liveness Probe : httpGet /health (Period: 15s, Failure: 3)                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The High-Rise Commercial Office Tower
3. Evolutionary Lineage: From Monolithic Virtual Machines to Cloud-Native KServe
4. First-Principles Mathematics & Algorithmic Formulations
   - Shared Memory (`/dev/shm`) Inter-Process Communication Requirements
   - Triple Health Probe State Transitions: Startup vs Readiness vs Liveness
   - KServe Horizontal Pod Autoscaler (HPA) Concurrency Scaling Equations
   - Storage Volume Caching: Zero-Copy NVMe HostPath vs Ceph/NFS
5. Alternative Industry Approaches & Comparative Trade-Off Matrices
6. Concrete Production Hands-On Lab: Production Kubernetes Manifests Generator
7. Hardware Grounding for NVIDIA DGX Spark (NVIDIA GPU Operator on ARM64)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The High-Rise Commercial Office Tower

Consider constructing a 100-story commercial office building (Gemma 2 Kubernetes Cluster):
- **Ad-Hoc Docker Command** is like pitching an expedition camping tent on an open field. It's fast to set up for one person, but when high winds strike (hardware failure) or 5,000 visitors arrive at once (traffic spike), the tent collapses completely.
- **Production Kubernetes + KServe** is a **Modern Steel-and-Glass Commercial Skyscraper**:
  - **The Foundations (PVC / Storage)**: High-speed elevators and underground loading docks that pre-cache hundreds of gigabytes of building materials.
  - **The Ventilation & Safety Systems (Triple Health Probes)**: Smoke alarms, fire sprinklers, and backup generators. The building does not let employees enter until the air conditioning and elevators are confirmed operational (`readinessProbe`), and if a room experiences structural damage, the building automatically seals the room, evacuates occupants, and summons repairs (`livenessProbe`).

---

## 3. Evolutionary Lineage & Predecessors

```
┌────────────────────────────────────────────────────────────────────────┐
│ 2015-2018: Bare-Metal Shell Scripts & Docker Run                       │
│ Manual deployments. No automatic restarts; GPU utilization was opaque.│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2019-2022: Standard K8s Deployments with NVIDIA Device Plugin          │
│ Containerized AI models. Frequent /dev/shm OOM crashes and naive       │
│ readiness probes killed models before 50 GB weights finished loading.  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2023-Present: KServe & vLLM Production Custom Resources (CRDs)         │
│ Native GPU Operator, automated startup probes, unified memory shared   │
│ IPC, and concurrency-based autoscaling (KPA).                          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### 4.1 The `/dev/shm` IPC Bottleneck

By default, Docker and Kubernetes allocate only **$64\text{ MB}$** of shared memory (`/dev/shm`) to containers.
In PyTorch and vLLM, inter-process communication, CUDA memory pooling, and tensor parallel NCCL transfers rely heavily on POSIX shared memory.

When serving Gemma 2 27B, attempting to allocate a PyTorch tensor across processes immediately exceeds $64\text{ MB}$, resulting in a cryptic, silent crash:
`RuntimeError: DataLoader worker (pid 42) is killed by signal: Bus error.`

To resolve this, Kubernetes manifests must mount a dedicated memory volume to `/dev/shm`:
```yaml
volumes:
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: 16Gi
```

### 4.2 Triple Health Probe Lifecycle

Large foundation models require tens of seconds to load 54 GB of safetensors into memory and execute soft-capping warmup passes.
1. **Startup Probe (`startupProbe`)**:
   - Disables readiness and liveness checks while the model is initializing.
   - Equation: $\text{Max Startup Time} = \text{failureThreshold} \times \text{periodSeconds} = 30 \times 10\text{s} = 300\text{ seconds}$ (5 minutes).
2. **Readiness Probe (`readinessProbe`)**:
   - Controls whether the Pod receives client traffic from the Service load balancer.
   - Flips to active only when `/health` returns HTTP 200.
3. **Liveness Probe (`livenessProbe`)**:
   - Detects GPU deadlocks, memory leaks, or hung CUDA graphs.
   - If the container fails to respond for 3 consecutive checks, K8s terminates and restarts the Pod.

---

## 5. Alternative Industry Approaches & Comparative Trade-Off Matrices

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              CONTAINER ORCHESTRATION PLATFORMS                                         │
├────────────────────┬────────────────────┬─────────────────────┬──────────────────┬─────────────────────┤
│ Platform           │ Autoscaling Metric │ Zero-Downtime Update│ Multi-Model Mgt  │ Enterprise Security │
├────────────────────┼────────────────────┼─────────────────────┼──────────────────┼─────────────────────┤
│ Docker Compose     │ None (Manual)      │ No                  │ Basic            │ Host level only     │
│ Plain K8s Deploy   │ CPU / Memory %     │ Yes (RollingUpdate) │ Manual Services  │ RBAC / NetworkPolicy│
│ KServe (v0.13+)    │ Request Concurrency│ Yes (Canary Routes) │ ModelMesh / CRDs │ Production Frontier │
│ Ray Serve          │ Queue Depth / Lat  │ Yes                 │ Python Actor Pool│ High                │
└────────────────────┴────────────────────┴─────────────────────┴──────────────────┴─────────────────────┘
```

---

## 6. Concrete Production Hands-On Lab: Production Kubernetes Manifests Generator

Save this script as `generate_gemma2_k8s_manifests.py`:

```python
"""
Google Gemma 2 Kubernetes & KServe Production Manifests Generator.
Generates hardened YAML specifications tailored for NVIDIA DGX Spark.
"""

import sys

DEPLOYMENT_YAML = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: gemma2-27b-serving
  namespace: gemma-system
  labels:
    app: gemma2-27b
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gemma2-27b
  template:
    metadata:
      labels:
        app: gemma2-27b
    spec:
      containers:
      - name: vllm-engine
        image: vllm/vllm-openai:latest
        imagePullPolicy: IfNotPresent
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args:
        - "--model"
        - "google/gemma-2-27b-it"
        - "--dtype"
        - "bfloat16"
        - "--gpu-memory-utilization"
        - "0.90"
        - "--max-model-len"
        - "8192"
        - "--port"
        - "8000"
        env:
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-secret
              key: token
        - name: NCCL_DEBUG
          value: "INFO"
        ports:
        - containerPort: 8000
          name: http
        resources:
          limits:
            nvidia.com/gpu: "1"
            memory: 110Gi
            cpu: "32"
          requests:
            nvidia.com/gpu: "1"
            memory: 64Gi
            cpu: "16"
        volumeMounts:
        - name: dshm
          mountPath: /dev/shm
        - name: model-cache
          mountPath: /root/.cache/huggingface
        startupProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 45
          periodSeconds: 10
          failureThreshold: 30
          timeoutSeconds: 5
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          periodSeconds: 10
          failureThreshold: 2
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          periodSeconds: 15
          failureThreshold: 3
      volumes:
      - name: dshm
        emptyDir:
          medium: Memory
          sizeLimit: 16Gi
      - name: model-cache
        persistentVolumeClaim:
          claimName: gemma2-weights-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: gemma2-27b-service
  namespace: gemma-system
spec:
  selector:
    app: gemma2-27b
  ports:
  - port: 8000
    targetPort: 8000
    name: http
  type: ClusterIP
"""

def generate_manifests():
    print("=" * 80)
    print("GENERATING PRODUCTION KUBERNETES MANIFESTS FOR GEMMA 2 27B")
    print("=" * 80)

    filename = "gemma2-27b-k8s-production.yaml"
    with open(filename, "w") as f:
        f.write(DEPLOYMENT_YAML)

    print(f"Successfully generated: {filename}")
    print("\nCritical Architectural Safeguards Configured in Manifest:")
    print("  1. Shared Memory IPC : emptyDir with medium=Memory (16Gi) at /dev/shm")
    print("  2. GPU Allocation    : nvidia.com/gpu: 1 (Blackwell GB10 128 GB Unified Memory)")
    print("  3. Startup Probe     : 300s window (30 x 10s) to absorb 54 GB model loading")
    print("  4. High-Speed PVC    : Persistent volume claim for zero-copy weight caching")
    print("\nTo apply to DGX Spark cluster:")
    print("  kubectl create namespace gemma-system")
    print("  kubectl create secret generic hf-secret --from-literal=token=$HF_TOKEN -n gemma-system")
    print(f"  kubectl apply -f {filename}")

if __name__ == "__main__":
    generate_manifests()
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (NVIDIA GPU Operator on ARM64)

### 7.1 NVIDIA GPU Operator on Grace ARM Neoverse V2
When configuring the cluster on the DGX Spark:
- The host operates the **ARMv9 Linux kernel (`aarch64`)**.
- Deploy the official NVIDIA GPU Operator with driver containers compiled for ARM64:
  ```bash
  helm install --wait --generate-name \
    -n gpu-operator --create-namespace \
    -f values-arm64.yaml \
    nvidia/gpu-operator
  ```
- This automatically provisions the NVIDIA Container Toolkit, CUDA runtime hooks, and DCGM monitoring daemonsets.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practical Exercises

1. **Calculate Maximum Startup Window**:
   If an engineer configures `initialDelaySeconds: 60`, `periodSeconds: 15`, and `failureThreshold: 20`, what is the maximum duration before Kubernetes declares the Pod dead?
   - *Solution*: $\text{Max Window} = 60 + (15 \times 20) = 60 + 300 = \mathbf{360\text{ seconds}}$ (6 minutes).

2. **Diagnose `/dev/shm` Failure**:
   What happens if the `dshm` volume mount is omitted from a vLLM multi-processing deployment?
   - *Solution*: PyTorch inter-process queues exhaust the default $64\text{ MB}$ `/dev/shm` buffer within milliseconds of request arrival, causing worker processes to crash with `SIGBUS` errors.

### Troubleshooting FAQ

- **Q: Why does the Pod stay in `CrashLoopBackOff` with `Permission Denied` on the PVC?**
  *A*: When mounting a host NVMe PVC, file ownership may default to `root:root`. Ensure the container runs with `securityContext: fsGroup: 1000` or that the host cache directory has read/write permissions for the vLLM user.
- **Q: How does KServe scale down to zero replicas?**
  *A*: KServe integrates with Knative Serving. When no HTTP requests arrive for a configurable window (e.g. 5 minutes), the Knative Pod Autoscaler (KPA) drains traffic and scales the Pod to 0, freeing the Blackwell GB10 memory for other workloads.
