# Chapter 25 · Ultra-Scale Cluster Resilience & Fault Tolerance: Failure Math, Async Checkpoints, SDC Canaries, Quarantine (Practised Small)

> **02-Kubernetes · Part VIII — Scale & resilience · Chapter 25 of 28** · ← [Chapter 24 · Hyperscaler silicon & compilers](24-hyperscaler-silicon-and-compilers.md) · [All chapters](00-kubernetes-step-by-step-guide.md) · [Chapter 26 · Cluster diagnostics & failure scenarios](26-cluster-diagnostics-and-failure-scenarios.md) →

| | |
|---|---|
| **You will build** | The operating model hyperscalers use to keep 10k–100k-GPU jobs productive, rehearsed on one Spark. You'll have a failure-rate and checkpoint-interval calculator, a training Job in the root's `batch` namespace that survives pod kills by resuming from **async** distributed checkpoints, a **silent-data-corruption canary** that fails fast, and node quarantine by taint — on the root, where the real node lives, seen by both vClusters |
| **Hardware** | dgx-spark-1 |
| **Time** | 75 min |
| **Risk** | Low. §5.4 taints the only node: nothing new schedules on the root or in either vCluster until you remove it |
| **Clusters** | `spark-root` (the Job, its retry policy, the checkpoint PVC, the pod, the eviction, the node taint, the checkpoint directory on the NVMe) · `dev-lab`, `dev-llms` (how vCluster tenants see the quarantine) |
| **Lab files** | [`scripts/mtbf_calc.py`](lab/scripts/mtbf_calc.py), [`manifests/root-llms/80-distributed/resilient/`](lab/manifests/root-llms/80-distributed/resilient/) (`train_resilient.py`, `resilient-job.yaml`, `kustomization.yaml`) |

---

## 1. Why this matters

At scale, failure is the normal state. With independent failures, cluster MTBF = component MTBF / N:

```bash
cd "02-Kubernetes/lab"
python3 scripts/mtbf_calc.py --gpus 2      --gpu-mtbf-h 50000
python3 scripts/mtbf_calc.py --gpus 16384  --ckpt-s 60 --restart-s 600
python3 scripts/mtbf_calc.py --gpus 100000 --ckpt-s 60 --restart-s 600
```

| GPUs | Cluster MTBF | Interruptions/day | Optimal ckpt interval | Goodput (60 s ckpt) | Goodput (6 s async ckpt) |
|---|---|---|---|---|---|
| 2 (your Sparks) | ~25,000 h | ~0 | — | ~100 % | ~100 % |
| 16,384 | ~3 h | ~8 | ~19 min | ~84 % | ~91 % |
| 100,000 | ~0.5 h | ~48 | ~8 min | ~41 % | ~59 % |

(The 16k row matches the order of magnitude reported publicly for frontier training runs: several unexpected interruptions per day.) Two levers dominate: **make checkpoints cheap and non-blocking**, and **make recovery fast and automatic**. Both are rehearsed below.

### 1.1 Failure taxonomy (what actually stops big jobs)

| Class | Examples | Detection | Response |
|---|---|---|---|
| GPU hardware | ECC/Xid 48/94/95/79, fallen off bus, thermal | dmesg Xid, DCGM | drain, replace, restart job from checkpoint |
| Network | link flaps, symbol errors, degraded rails | NIC counters, NCCL timeouts | fabric health gate, reroute, drain node |
| Host / software | kernel panic, OOM, driver hang, filesystem stalls | node NotReady, watchdogs | reboot / reimage, restart job |
| **Silent data corruption** | a GPU returns wrong numbers without errors | canaries, loss spikes, cross-replica checksums | quarantine the node, roll back to a good checkpoint |
| Stragglers | throttling, noisy neighbours | per-rank step-time spread | cordon, replace with a hot spare |
| Control plane | API server or etcd down, a tenant cluster's control plane restarting | API errors, controller lag | running pods keep running; recovery waits for the controllers |

The last row is easy to rehearse here. If the root's API server or controller manager restarts, the training pod keeps running (the kubelet doesn't need them), but nothing can *react* — no retry, no new pod — until they're back. The vClusters show the same thing one layer down: if dev-lab's control plane restarts, its pods keep running on the root, but nothing in dev-lab reacts until it returns.

### 1.2 Who reacts to what

| Event | Detected by | Reaction happens in |
|---|---|---|
| training process exits (kill, OOM, exit 86) | the root's kubelet | pod status → the root's Job controller (kube-controller-manager) applies `podFailurePolicy` / `backoffLimit` |
| eviction / drain | the root's API server (adds `DisruptionTarget`) | the same: the condition is on the pod the Job controller watches |
| node quarantine | you, on the **root** (nodes are only real there) | the root scheduler stops placing pods — for the root's namespaces and both vClusters |

A job in a vCluster (dev-lab, dev-llms) would add a hop to the first two rows: the syncer copies the root pod's status back to the virtual pod, and only then does the vCluster's own Job controller react.

---

## 2. Architecture — HLD

```mermaid
flowchart TB
  subgraph LLMS["root spark-root · ns batch"]
    direction TB
    JC["Job controller (kube-controller-manager)<br/>backoffLimit 6 · podFailurePolicy"]
    subgraph JOB["Job resilient-train · priority spark-batch"]
      direction LR
      T["train loop<br/>step N"] -->|"every 50 steps"| AS["dcp.async_save<br/>(blocks ~ms, writes in background)"]
      T -->|"every 25 steps"| CAN{"SDC canary<br/>fixed GEMM checksum<br/>= reference?"}
      CAN -- no --> X86["exit 86"]
    end
    JC --> JOB
  end
  subgraph ROOT["node dgx-spark-1"]
    direction TB
    HP["pod resilient-train-…<br/>ns batch · 12 Gi request = limit"]
    PVC[("PV local-nvme<br/>/data/k8s/batch/ckpt<br/>step-N/ + COMPLETE · keep newest 3")]
    KILL["pod killed / evicted / OOM"]
    Q["quarantine node dgx-spark-1<br/>taint spark.lab/sdc=suspect:NoSchedule"]
  end
  JOB -. "scheduled to" .-> HP
  AS --> PVC
  PVC -->|"on (re)start: newest COMPLETE"| T
  KILL -->|"pod status + DisruptionTarget"| JC
  X86 -->|"podFailurePolicy: FailJob"| Q
  classDef gpu fill:#76b900,stroke:#3d6000,color:#000
  classDef store fill:#bf8700,stroke:#7a5600,color:#fff
  classDef sec fill:#cf222e,stroke:#82071e,color:#fff
  classDef ctrl fill:#1f6feb,stroke:#0b3d91,color:#fff
  class T,AS gpu
  class PVC store
  class CAN,X86,Q sec
  class KILL,JC,HP ctrl
  style JOB fill:#f4fbe8,stroke:#76b900
  style LLMS fill:#f6f8fa,stroke:#57606a,stroke-dasharray:5 3
  style ROOT fill:#e6f4f5,stroke:#0e7c86
```

---

## 3. LLD

| Mechanism | Implementation in the lab | At scale |
|---|---|---|
| Checkpoint format | `torch.distributed.checkpoint` (DCP): sharded, reshardable | same (Megatron/FSDP/TorchTitan all use DCP-style) |
| Non-blocking save | `dcp.async_save` → wait for the previous save before starting the next | + staging to pinned host memory, multi-tier (local NVMe → object store) |
| Atomicity | `COMPLETE` marker written only after the save finishes. Resume only from marked dirs | same, or manifest/metadata commit |
| Retention | keep newest 3 complete | tiered: frequent local, sparse remote |
| Resume | newest `step-*/COMPLETE` on start | + elastic re-sharding when world size changes |
| Retry policy | `backoffLimit: 6`. `DisruptionTarget` (drain/preemption) **ignored** | job-level restarts without rescheduling (hot spares) |
| SDC | deterministic FP32 GEMM checksum vs reference, every 25 steps → exit 86 → `FailJob` | per-node burn-in, cross-replica gradient checksums, loss-spike detectors |
| Quarantine | manual taint on the root node (§5.4). The Chapter 06 controller pattern can automate it | remediation controllers + DCGM diag before re-admission |

| Resources (from `resilient-job.yaml`) | Value | Charged to |
|---|---|---|
| Job pod | 1 CPU · 12 Gi (request = limit) · 1 slice, `spark-batch` | `batch-budget` (2 CPU · 12 Gi · 3 slices), then the node's memory: the LLM pool it shares with `llm-serving` |
| PVC `ckpt` | 20 Gi, `local-nvme` (Delete) | `batch-budget` `requests.storage` (200 Gi) |
| Kueue | the Job has no `kueue.x-k8s.io/queue-name` label, so Kueue leaves it alone | add `queue-name: train` to run it under `spark-cq` (Chapter 07) |

`spark-batch` has `preemptionPolicy: Never`: the job waits for capacity instead of evicting anyone. The pod takes all of batch's 12 Gi, so it can't run beside the `ddp` gang (Chapter 18): the quota refuses it, and the Job shows `FailedCreate … exceeded quota: batch-budget`. Its 12 Gi also come out of the pool serving uses. vLLM alone leaves plenty (32 + 12 = 44 of ~90 GiB, Chapter 20 §9); when the models hold more than ~78 GiB, the pod waits Pending with `Insufficient memory`. Training waits for serving, never the reverse.

---

## 4. Integrations

- **Storage (Chapter 13, module 08)**: checkpoint PVC on `local-nvme`, created by the root's provisioner at `/data/k8s/batch/ckpt` (`<namespace>/<pvc>`). Watch the root's etcd fsync (Chapter 02) while checkpoints write — same NVMe.
- **Kueue (Chapter 07)**: `DisruptionTarget` pods (preempted by Kueue or evicted on the root) don't burn retries.
- **Controllers (Chapter 06)**: turn §5.4's manual quarantine into a controller on the root that watches for Jobs failing with exit 86. The training Jobs live on the root, so it reads them directly; to cover a vCluster tenant's job too, it would have to read that vCluster's API or the synced pods' exit codes on the root.
- **Disruption budgets (Chapter 12)**: a root `kubectl drain` evicts the root's own pods (models, training) and both vClusters' synced pods, and respects their PDBs.
- **01-Ansible `node_drain` / `spark_validate`**: the drain → validate → return loop for a quarantined node.

---

## 5. Lab

```bash
cd "02-Kubernetes/lab"
export KUBECONFIG="$PWD/../../01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml"
kubectl --context spark-root -n batch describe resourcequota batch-budget | grep -E 'requests.cpu|limits.memory|gpu'
```

If less than 1 CPU / 12 Gi is free in `batch-budget`, delete the other batch job first (the Chapter 18 `ddp` gang uses all of it). If the pod later waits Pending with `Insufficient memory`, park one engine for this step (Chapter 20 §9).

### 5.1 Start the resilient job

```bash
kubectl --context spark-root apply -k manifests/root-llms/80-distributed/resilient
kubectl --context spark-root -n batch logs -f job/resilient-train
```

```text
canary reference recorded: 4ecd9864353a5b02
chapter 25 loss … canary 4ecd9864353a5b02 OK
chapter 50 checkpoint started (blocking 31 ms)
…
```

The **blocking** time is what the training loop pays per checkpoint. With async save it's milliseconds. The write itself continues in the background. Find where the bytes really go:

```bash
kubectl --context spark-root -n batch get pvc ckpt -o jsonpath='{.status.phase} {.spec.volumeName}{"\n"}'   # Bound pvc-…
ssh dgxadmin@192.168.0.100 'ls /data/k8s/batch/ckpt/'                                                      # step-* + canary.json
```

### 5.2 Kill it mid-run and watch it resume

```bash
sleep 20; kubectl --context spark-root -n batch delete pod -l job-name=resilient-train --wait=false
kubectl --context spark-root -n batch get pods -l job-name=resilient-train -w          # a new pod starts (backoffLimit)
kubectl --context spark-root -n batch logs -f job/resilient-train | grep -E 'RESUMED|DONE'
```

Expected: `RESUMED from /ckpt/step-100 at step 100` (or whichever was the newest *complete* checkpoint), then `DONE at step 400`. Lost work per failure ≤ one checkpoint interval. The replacement pod was created by the root's Job controller, in the same cluster whose kubelet reported the old one gone.

### 5.3 An eviction doesn't count as a failure

A `kubectl drain` is, per pod, a call to the Eviction API — on the **root**, because that's where nodes and the real pods are. Draining the only node would evict everything — the models, the platform, both vClusters — so evict just the training pod, exactly as drain would:

```bash
kubectl --context spark-root -n batch delete job resilient-train; kubectl --context spark-root apply -k manifests/root-llms/80-distributed/resilient
sleep 30
POD=$(kubectl --context spark-root -n batch get pod -l job-name=resilient-train -o jsonpath='{.items[0].metadata.name}')
kubectl --context spark-root create --raw "/api/v1/namespaces/batch/pods/$POD/eviction" -f - <<EOF
{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"$POD","namespace":"batch"}}
EOF
kubectl --context spark-root -n batch get job resilient-train -o jsonpath='failed={.status.failed} {"\n"}'   # 0: DisruptionTarget ignored
```

The root's API server marks the evicted pod with the `DisruptionTarget` condition; the Job controller reads the condition and applies the `Ignore` rule. Two components in one cluster, one retry policy. In a vCluster the syncer would sit between them, copying the condition back to the virtual pod. To see what a real drain would take with it: `kubectl --context spark-root drain dgx-spark-1 --ignore-daemonsets --delete-emptydir-data --dry-run=server`.

### 5.4 Simulate silent data corruption → fail fast → quarantine

Corrupt the stored reference (a stand-in for the GPU computing a different answer):

```bash
kubectl --context spark-root -n batch delete job resilient-train
ssh dgxadmin@192.168.0.100 "echo '{\"sha\": \"deadbeefdeadbeef\"}' | sudo tee /data/k8s/batch/ckpt/canary.json"
kubectl --context spark-root apply -k manifests/root-llms/80-distributed/resilient
kubectl --context spark-root -n batch logs -f job/resilient-train | grep -E 'canary|SDC'
kubectl --context spark-root -n batch get job resilient-train -o jsonpath='{.status.conditions[?(@.type=="Failed")].reason}{"\n"}'   # PodFailurePolicy
```

Expected: `SDC SUSPECTED` at the first canary, exit 86, and the Job fails **immediately** with reason `PodFailurePolicy`. There are no retries, because retrying on a lying GPU makes things worse. Quarantine the node — on the root — and record why:

```bash
kubectl --context spark-root taint node dgx-spark-1 spark.lab/sdc=suspect:NoSchedule
kubectl --context spark-root annotate node dgx-spark-1 spark.lab/quarantine-reason="canary mismatch job batch/resilient-train $(date -Is)"
kubectl --context dev-lab get node dgx-spark-1 -o jsonpath='{.spec.taints}{"\n"}'    # vCluster tenants see the quarantine
kubectl --context dev-llms get node dgx-spark-1 -o jsonpath='{.spec.taints}{"\n"}'   # so does the other vCluster
```

Both vClusters show the taint because they sync the real node from the root. Neither can lift it: the virtual node is a copy, and edits to it are not written back to the root. With one node, the taint blocks *all* new scheduling on the root and in both vClusters — a model that restarts, a restarting vCluster control plane — while running pods carry on. That's what quarantine means, and why large clusters keep **hot spares** so a job restarts instantly on a replacement node.

After investigation (DCGM diag / vendor) — and restoring the real reference:

```bash
ssh dgxadmin@192.168.0.100 'sudo rm /data/k8s/batch/ckpt/canary.json'
kubectl --context spark-root taint node dgx-spark-1 spark.lab/sdc-
kubectl --context spark-root annotate node dgx-spark-1 spark.lab/quarantine-reason-
```

### 5.5 Size your own checkpoint policy

Measure the blocking time from §5.1 logs and your restart time (pod kill → `RESUMED` line), then:

```bash
python3 scripts/mtbf_calc.py --gpus 2 --ckpt-s 0.05 --restart-s 90
python3 scripts/mtbf_calc.py --gpus 4096 --ckpt-s 0.05 --restart-s 90
```

On the root, your restart time is what a bare-metal cluster pays: Job controller, scheduler, kubelet, image already cached. A job inside a vCluster would add two hops: the syncer creating the root pod, and the status copy back. Kill a pod in the dev-llms sandbox (`llm-sandbox`) the same way and compare to see how much they add.

### 5.6 Clean up

```bash
kubectl --context spark-root delete -k manifests/root-llms/80-distributed/resilient    # Job + PVC (local-nvme deletes the directory)
```

---

## 6. Verify

| Check | Expected |
|---|---|
| async save blocking time | tens of ms |
| pod kill | `RESUMED from … step-N`, then `DONE at step 400` |
| root eviction | Job `failed=0` |
| SDC drill | Job `Failed` with reason `PodFailurePolicy` after one pod |
| quarantine | taint on the root node, visible from dev-lab and dev-llms, removed afterwards |
| checkpoint dir | exactly 3 `step-*` directories + `canary.json` under `/data/k8s/batch/ckpt` |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| no Job pod; `FailedCreate … exceeded quota: batch-budget` on the Job | another batch job (the Chapter 18 `ddp` gang) holds batch's 2 CPU / 12 Gi | wait for it or delete it |
| Job pod Pending with `Insufficient memory` | the models in `llm-serving` hold the shared memory pool (engines at 32 Gi each) | park an engine (Chapter 20 §9) or wait — `spark-batch` never preempts |
| Job pod Pending with `untolerated taint {spark.lab/sdc: suspect}` | the quarantine from §5.4 is still on | remove it on the root |
| resume picks a half-written checkpoint | no completion marker | only trust `COMPLETE` (as the script does) |
| checkpoints fill the disk | no retention | keep-N pruning of complete checkpoints. Alert on PVC usage |
| each restart burns retries during maintenance | `DisruptionTarget` not ignored (in a vCluster: the condition didn't reach the virtual pod) | `podFailurePolicy` rule (lab); `kubectl --context spark-root -n batch get pod -o jsonpath='{..conditions}'` |
| eviction returns `429 Too Many Requests` | a PDB covers the pod (in `batch`, or synced from a vCluster) | expected behaviour of drain (Chapter 12 §5.5) |
| `ls /data/k8s/...` finds nothing | the PV directory is `<namespace>/<pvc>` on the root (a vCluster's PVC uses its translated root name) | `kubectl --context spark-root get pv <volumeName> -o jsonpath='{.spec.hostPath.path}'` |
| resumed loss jumps | optimizer/RNG/dataloader state not saved | save optimizer + step (lab), plus RNG and dataloader position in real jobs |
| async save slows training anyway | NVMe saturated / page cache pressure on UMA | smaller or less frequent checkpoints. `ionice`. Watch `SparkUMAPressure` |

---

## 8. Scale-out path

| Lab | Hyperscale |
|---|---|
| single-rank DCP to local NVMe | sharded DCP from thousands of ranks → local NVMe → object store. Checkpoint every few minutes |
| pod-level retries by the Job controller | in-place job restart with hot-spare nodes (seconds, not minutes). Elastic torchrun / TorchFT for membership changes |
| one GEMM canary | burn-in + periodic canaries on every node, cross-replica checksums, automated quarantine and vendor RMA pipeline |
| manual taint on the root | remediation controllers on the cluster that owns the nodes (NPD conditions → taint → drain → diag → return); workload namespaces and tenant clusters only see the result |
| one node = quarantine stops everything | dgx-spark-2 joins the root (01-Ansible `k8s_workers`): a quarantined Spark leaves the other schedulable for the models, batch and both vClusters |

---

## 9. Checklist

- [ ] I can estimate cluster MTBF, optimal checkpoint interval and goodput for any GPU count.
- [ ] My training job survives pod kills and root evictions, and resumes from the newest *complete* checkpoint.
- [ ] I can say which cluster detects a failure and which one reacts to it.
- [ ] My SDC canary makes the job fail fast instead of retrying.
- [ ] I know what quarantine means operationally — on the root, for every cluster — and why hot spares exist.
