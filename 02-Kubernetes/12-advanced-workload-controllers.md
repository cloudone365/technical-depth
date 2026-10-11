# Chapter 12 · Advanced Workload Controllers: StatefulSets, DaemonSets, Indexed Jobs & Disruption Budgets for AI

> **02-Kubernetes · Part IV — Workloads, storage & tenancy · Chapter 12 of 28** · ← [Chapter 11 · Ingress controllers & Gateway API](11-ingress-controllers-and-gateway-api.md) · [All chapters](00-kubernetes-step-by-step-guide.md) · [Chapter 13 · Storage, CSI & high-performance volumes](13-storage-csi-and-high-performance-volumes.md) →

| | |
|---|---|
| **You will build** | A Qdrant vector database as a StatefulSet whose data survives pod deletion, a per-node GPU/UMA probe DaemonSet feeding Prometheus, a sharded tokenizer as an Indexed Job with per-shard retries and a corrupt-shard rehearsal, and PodDisruptionBudgets — the LLM stack's on the root and a tenant's written inside a vCluster — that make a root `kubectl drain` safe |
| **Hardware** | dgx-spark-1 |
| **Time** | 90 min |
| **Risk** | Low. §5.5 drains only with `--dry-run=server`; a real drain of the only node stops all three clusters' workloads |
| **Clusters** | `spark-root` (Qdrant + PDBs in `llm-serving`, node probe, the drain, and where every pod really runs), `dev-lab` (tokenizer Job, a tenant PDB) |
| **Lab files** | [`manifests/root-llms/50-workloads/qdrant-statefulset.yaml`](lab/manifests/root-llms/50-workloads/qdrant-statefulset.yaml), [`manifests/root/50-workloads/node-probe-daemonset.yaml`](lab/manifests/root/50-workloads/node-probe-daemonset.yaml), [`manifests/dev-lab/50-workloads/tokenizer-indexed-job.yaml`](lab/manifests/dev-lab/50-workloads/tokenizer-indexed-job.yaml), [`vclusters/dev-lab.yaml`](lab/vclusters/dev-lab.yaml) (`sync.toHost.podDisruptionBudgets`), `scripts/breakfix.sh` scenario 11 |

---

## 1. Why this matters on a Spark

A Deployment assumes pods are interchangeable cattle. Much of an AI platform isn't:

| Need | Controller | Example on the Spark |
|---|---|---|
| stable name + own disk, ordered start | **StatefulSet** | Qdrant, Milvus, Redis, Postgres for LiteLLM, etcd for Ray — and each vCluster's own control plane (`dev-lab-0`, `dev-llms-0`) |
| exactly one per node | **DaemonSet** | GPU probes, dcgm-exporter, log shippers, Cilium, NCCL/RDMA device plugins |
| N pieces of finite work | **Job** / **Indexed Job** | tokenization, embedding a corpus, eval sweeps, checkpoint conversion |
| don't let maintenance kill the only copy | **PodDisruptionBudget** | 1-replica vector DB, 2-replica model server |

### 1.1 Which cluster runs which controller

The nesting changes *where* each controller lives, and that's worth getting straight before the lab:

| Object | Its controller runs in | What reaches the root |
|---|---|---|
| StatefulSet `qdrant` (root, `llm-serving`) | the **root's** controller-manager | — (it is a root object; the pod `qdrant-0` and the PVC `data-qdrant-0` keep their names) |
| Indexed Job `tokenize-shards` (dev-lab) | dev-lab's controller-manager | one pod per running index, renamed (§3.4) |
| DaemonSet `spark-node-probe` | the **root's** controller-manager | — (it is a root object) |
| PDBs `qdrant`, `mock-llm` (root, `llm-serving`) | root objects | — enforced directly by the root's Eviction API |
| PDB `echo` (dev-lab, `lab-tools`, §5.5) | written in dev-lab, **synced to the root** | a translated PDB in `vc-dev-lab`, enforced by the root's Eviction API |

Inside a vCluster, StatefulSets, Jobs and DaemonSets are *not* synced: only their pods go down. A PDB is different, because the thing that respects it — the Eviction API that `kubectl drain` calls — belongs to the cluster that owns the node. That's the root. So a tenant's PDB has to be there too. Qdrant needs none of this: the LLM stack runs on the root, so its StatefulSet, pod, PVC and PDB are root objects from the start.

---

## 2. Architecture — HLD

```mermaid
flowchart TB
  subgraph DEV["vCluster dev-lab"]
    direction LR
    JOB["Indexed Job tokenize-shards<br/>tenant-alpha · 8 completions · 2 parallel"]
    PDBV["PDB echo · lab-tools<br/>minAvailable 2"]
  end
  subgraph ROOT["Root cluster spark-root · dgx-spark-1"]
    direction TB
    subgraph LS["namespace llm-serving (LLM stack, root objects)"]
      STS["StatefulSet qdrant<br/>(root controller-manager)"] --> Q0["qdrant-0<br/>qdrant-0.qdrant-headless.llm-serving.svc"]
      Q0 --> PVC0[("PVC data-qdrant-0<br/>local-nvme-retain 20Gi")]
      PDBR["PDB qdrant · maxUnavailable 0<br/>PDB mock-llm · minAvailable 1"]
    end
    subgraph VCD["namespace vc-dev-lab"]
      I0["tokenize-shards-0-…-x-tenant-alpha-x-dev-lab"] --- I1["index 1 … 7"]
      PDBT["PDB echo-x-lab-tools-x-dev-lab"]
    end
    subgraph PT["namespace platform-tools"]
      NP1["DaemonSet spark-node-probe<br/>pod on dgx-spark-1"]
    end
    EVICT["Eviction API"]
  end
  JOB -. "syncer" .-> I0
  PDBV -. "sync.toHost.podDisruptionBudgets" .-> PDBT
  NP1 --> TF[("/var/lib/prometheus/node-exporter<br/>k8s_node_probe.prom")]
  TF --> NE["host node-exporter :9100"] --> PROM["Prometheus (observability)"]
  DRAIN(["kubectl --context spark-root drain dgx-spark-1"]) --> EVICT
  EVICT -. "checks" .-> PDBR
  EVICT -. "checks" .-> PDBT
  PDBR -. "blocks eviction" .-> Q0
  classDef ctrl fill:#1f6feb,stroke:#0b3d91,color:#fff
  classDef store fill:#bf8700,stroke:#7a5600,color:#fff
  classDef obs fill:#fb8500,stroke:#9a5200,color:#000
  classDef sec fill:#cf222e,stroke:#82071e,color:#fff
  classDef tenant fill:#eaeef2,stroke:#57606a,color:#000
  classDef node fill:#0e7c86,stroke:#064e54,color:#fff
  classDef ext fill:#24292f,stroke:#000,color:#fff
  class STS,JOB,EVICT ctrl
  class PVC0,TF store
  class NE,PROM obs
  class PDBV,PDBR,PDBT sec
  class Q0,I0,I1 tenant
  class NP1 node
  class DRAIN ext
  style ROOT fill:#e6f4f5,stroke:#0e7c86,stroke-width:2px
  style DEV fill:#f6f8fa,stroke:#57606a,stroke-dasharray:5 3
```

---

## 3. LLD

### 3.1 Qdrant StatefulSet (on the root)

| Field | Value | Why |
|---|---|---|
| namespace | `llm-serving` on the root | counts against `serving-budget` (8 CPU · 90 Gi · 6 slices · 1 Ti), next to the model servers |
| `serviceName` | `qdrant-headless` (`publishNotReadyAddresses: true`) | stable DNS per pod, answered by the **root's** CoreDNS. Peers must find each other before they're ready |
| image | `qdrant/qdrant:v1.13.4-unprivileged`, UID 1000 | runs non-root; passes PSA `baseline` on `llm-serving` |
| resources | requests 250m · 1 Gi, limit 2 Gi | Burstable. Not a GPU container, so request = limit isn't required; `serving-budget` counts 1 Gi of requests and 2 Gi of limits |
| `volumeClaimTemplates` | `data`, `local-nvme-retain`, 20 Gi | PVC `data-qdrant-0` follows the pod forever |
| `persistentVolumeClaimRetentionPolicy` | `Retain` on delete and on scale-down | deleting the StatefulSet never deletes vectors |
| `updateStrategy.rollingUpdate.partition` | 0 | raise it to canary a new version on the highest ordinal only |
| probes | `/readyz`, `/livez` | |
| PDB `qdrant` | `maxUnavailable: 0` | one replica = only copy. Drain must stop |
| PDB `mock-llm` | `minAvailable: 1` | the 2-replica mock API (Chapter 11) keeps one pod through a drain. Same file as Qdrant |

### 3.2 Node probe DaemonSet (root)

| Field | Value |
|---|---|
| namespace | `platform-tools` on the **root** (PSA `privileged`: hostPath). `vc-dev-lab` enforces `baseline`, which forbids hostPath, so node-level tools can't live in a vCluster here |
| `nodeSelector` | `spark.lab/gpu: gb10` (set by the 01-Ansible `kubeadm_cluster` role's kubelet node labels; lab-owned, so GFD's `-SHARED` renaming can't break it) |
| tolerations | `operator: Exists` (runs on cordoned/tainted GPU nodes too) |
| GPU access | env `NVIDIA_VISIBLE_DEVICES=all`: *observe* without consuming one of the 15 slices — it works because containerd's default runtime is `nvidia` (Chapter 16 §3) |
| output | `spark_probe_gpu_temp_celsius`, `spark_probe_mem_available_bytes` into the host textfile dir the 01-Ansible `gpu_telemetry` role created |
| priority | `spark-platform` |
| resources | 10m · 16 Mi request, 64 Mi limit |

### 3.3 Tokenizer Indexed Job (inside dev-lab)

| Field | Value | Effect |
|---|---|---|
| namespace | `tenant-alpha` in vCluster `dev-lab` | quota `tenant-budget`: 500m CPU (requests *and* limits), 2 Gi |
| `completions` / `parallelism` | 8 / 2 | 8 shards, 2 at a time: 2 × 200m = 400m of tenant-alpha's 500m. A third would not fit (§5.4) |
| `completionMode` | Indexed | `$JOB_COMPLETION_INDEX` = shard id |
| `backoffLimitPerIndex` | 2 | a flaky shard retries on its own |
| `maxFailedIndexes` | 1 | more than 1 bad shard → stop the whole job early |
| `podFailurePolicy` | exit 42 → `FailIndex` | corrupt input: don't waste retries |
| `ttlSecondsAfterFinished` | 3600 | garbage-collected an hour later — by dev-lab's TTL controller |

### 3.4 Names on both sides

Qdrant needs no translation table: it's a root object, so `llm-serving/qdrant-0`, PVC `llm-serving/data-qdrant-0` and PDB `llm-serving/qdrant` are the real names, and its data lives at `/data/k8s/retain/llm-serving/data-qdrant-0`. Everything written inside a vCluster is translated:

| Inside the vCluster | On the root (`kubectl --context spark-root`) |
|---|---|
| `dev-lab` · Job `tenant-alpha/tokenize-shards` | *nothing* — only its pods, in `vc-dev-lab`, as `tokenize-shards-<i>-…-x-tenant-alpha-x-dev-lab` |
| `dev-lab` · Deployment `lab-tools/echo` | *nothing* — only its pods, `echo-…-x-lab-tools-x-dev-lab` |
| `dev-lab` · PDB `lab-tools/echo` (§5.5) | PDB `vc-dev-lab/echo-x-lab-tools-x-dev-lab` |
| `dev-llms` · PVC `llm-sandbox/<pvc>` on `local-nvme-retain` | PVC `vc-dev-llms/<pvc>-x-llm-sandbox-x-dev-llms` → PV with hostPath `/data/k8s/retain/vc-dev-llms/<pvc>-x-llm-sandbox-x-dev-llms` |
| any StatefulSet written inside a vCluster | *nothing* — `kubectl --context spark-root -n vc-dev-lab get sts` shows only `dev-lab` (the vCluster's own control plane) |

Long names are shortened with a hash, so find a host copy through the annotations `vcluster.loft.sh/object-name` and `vcluster.loft.sh/object-namespace` (that's what `scripts/lib.sh` `host_pod` does) rather than by building the string.

---

## 4. Integrations

- **Storage (Chapter 13)**: `local-nvme-retain` is a root StorageClass (the vClusters see it through `sync.fromHost.storageClasses`). Qdrant's data ends up at `/data/k8s/retain/llm-serving/data-qdrant-0` on the NVMe: namespace and PVC name. A PVC written in dev-llms would land under `vc-dev-llms/…-x-llm-sandbox-x-dev-llms` — the path always uses the *root* namespace and PVC name, because the root's provisioner is the one that creates it.
- **RAG (modules 03 and 04)**: their retrieval services use `http://qdrant.llm-serving:6333`, a root Service name. The NetworkPolicy `allow-ingress-and-same-namespace` in `llm-serving` admits only `ingress`, `observability` and pods in `llm-serving` itself, so a retrieval service that queries Qdrant runs there too. From a vCluster the name doesn't resolve (its CoreDNS knows its own Services), and the policy and the root's Cilium `vcluster-boundary` policy drop the connection anyway.
- **Observability (Chapter 17)**: the probe metrics appear next to the 01-Ansible `spark_gpu_*` metrics (scraped through `observability/spark-host-exporters`), and the Grafana dashboard uses both.
- **Kueue (Chapter 07)**: the tokenizer job can be queued too — on the root, in `batch`, not in dev-lab (the vClusters have no Kueue). Add `kueue.x-k8s.io/queue-name: train` and move it to `batch`.
- **01-Ansible**: `playbooks/29.1-emergency-drain.yml` (role `node_drain`) automates cordon → capture → stop → reboot → validate → return for a root node.

---

## 5. Lab

All commands run from `02-Kubernetes/lab` with `KUBECONFIG` set to the lab file:

```bash
cd "02-Kubernetes/lab"
export KUBECONFIG="$PWD/../../01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml"
scripts/install-addons.sh storage          # once: local-path + local-nvme classes on the root
kubectl --context dev-lab get sc           # the root's classes, seen from inside a vCluster
```

### 5.1 StatefulSet: identity and data survive the pod

#### What this lab does (manifests `lab/manifests/root-llms/00-platform`, `10-tenancy`, `50-workloads`)

- **Root, `llm-serving`:** the namespaces and tenancy (unchanged if Chapter 08 or 11 already applied them), then Qdrant: StatefulSet `qdrant` (1 replica, `qdrant/qdrant:v1.13.4-unprivileged`), PVC `data-qdrant-0` on `local-nvme-retain` (20 Gi), and the PDBs `qdrant` (`maxUnavailable: 0`) and `mock-llm` (`minAvailable: 1`).
- **Data:** creates collection `spark` with 2 vectors through a port-forward on `localhost:6333`.
- **Failure test:** deletes pod `qdrant-0` and checks the new pod finds the same data.
- Data lands on the Spark's NVMe at `/data/k8s/retain/llm-serving/data-qdrant-0`. The first run pulls the Qdrant image.

#### What to look for

1. **Rollout:** `rollout status` ends with `statefulset rolling update complete …`, then `qdrant-0` is `1/1 Running` and `data-qdrant-0` is `Bound`.
2. **dev-lab comparison:** on the root, `vc-dev-lab` has only the StatefulSet `dev-lab` and no Deployment; the echo pods have the same IPs on both sides.
3. **Writes:** both `jq .status` lines print `"ok"`, and `points_count` is `2`.
4. **After the delete:** `qdrant-0` Ready again within 120 s, `points_count` still `2`, and `sudo ls …/collections/` on the Spark lists `spark`.

**If something's off:** `data-qdrant-0` stuck `Pending` means the `local-nvme-retain` class is missing (run `scripts/install-addons.sh storage`, Chapter 13). Port-forward errors usually mean an earlier `port-forward` is still holding 6333 (`kill %1`). More in §7.

#### Run it

```bash
kubectl --context spark-root apply -k manifests/root-llms/00-platform
kubectl --context spark-root apply -k manifests/root-llms/10-tenancy
kubectl --context spark-root apply -k manifests/root-llms/50-workloads
kubectl --context spark-root -n llm-serving rollout status sts/qdrant
kubectl --context spark-root -n llm-serving get sts,pod,pvc -o wide
```

The StatefulSet, the pod `qdrant-0` and the PVC `data-qdrant-0` sit side by side in one root namespace, under the names you wrote. A workload written inside a vCluster looks different from the root — compare dev-lab's echo Deployment:

```bash
kubectl --context spark-root -n vc-dev-lab get sts,deploy                  # only the StatefulSet 'dev-lab' — the vCluster's own control plane
kubectl --context spark-root -n vc-dev-lab get pods -o custom-columns=HOST:.metadata.name,VIRTUAL:.metadata.annotations.vcluster\\.loft\\.sh/object-name,IP:.status.podIP \
  | grep -E 'HOST|echo'
kubectl --context dev-lab -n lab-tools get pods -l app=echo -o wide
```

Expected: the same pod IPs on both sides. The pod IP and the node are real; the host name is a translation, and the Deployment and ReplicaSet behind it exist only in dev-lab.

Write some vectors (port-forward goes through the root's API server to the pod):

```bash
kubectl --context spark-root -n llm-serving port-forward svc/qdrant 6333 &
sleep 2
curl -s -X PUT localhost:6333/collections/spark -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":4,"distance":"Cosine"}}' | jq .status
curl -s -X PUT 'localhost:6333/collections/spark/points?wait=true' -H 'Content-Type: application/json' -d '{"points":[
  {"id":1,"vector":[0.9,0.1,0.1,0.0],"payload":{"doc":"GB10 shares one unified memory pool between CPU and GPU"}},
  {"id":2,"vector":[0.1,0.9,0.0,0.1],"payload":{"doc":"CX-7 links two Sparks at 200 GbE"}}]}' | jq .status
curl -s localhost:6333/collections/spark | jq '.result.points_count'
kill %1
```

Kill the pod and check that the same identity and data come back:

```bash
kubectl --context spark-root -n llm-serving delete pod qdrant-0
kubectl --context spark-root -n llm-serving wait --for=condition=Ready pod/qdrant-0 --timeout=120s
kubectl --context spark-root -n llm-serving port-forward svc/qdrant 6333 & sleep 2
curl -s localhost:6333/collections/spark | jq '.result.points_count'        # 2
kill %1
sudo ls /data/k8s/retain/llm-serving/data-qdrant-0/collections/   # on the Spark: spark
```

Who recreated `qdrant-0`? The root's StatefulSet controller, which saw its pod disappear. It created a new pod with the same name and the same PVC, and the root scheduler put it back on dgx-spark-1 because that's where the local PV lives. Inside a vCluster the same recovery takes two controllers: the vCluster's StatefulSet controller recreates the virtual pod, and the syncer creates its host copy (Chapter 06 §5.6).

### 5.2 Canary a StatefulSet update with `partition`

#### What this lab does (StatefulSet `llm-serving/qdrant`)

- Sets `partition: 1`, stages a new image (`v1.13.5-unprivileged`) without rolling it, reads the two revisions, then rolls back and sets `partition: 0` again.
- With one replica, no pod is restarted at any point.

#### What to look for

1. **Pod image:** still `qdrant/qdrant:v1.13.4-unprivileged` after `set image`.
2. **Revisions:** `qdrant-<hash1> → qdrant-<hash2>`, two different hashes.
3. **After the undo:** `currentRevision` and `updateRevision` are equal again and `partition` is `0`.

**If something's off:** if `qdrant-0` restarted, `partition` was not set before `set image` (check `kubectl --context spark-root -n llm-serving get sts qdrant -o jsonpath='{.spec.updateStrategy}'`). More in §7.

#### Run it

With one replica, `partition: 1` means *no pod updates*. That's useful for staging a new image spec without rolling it:

```bash
kubectl --context spark-root -n llm-serving patch sts qdrant -p '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":1}}}}'
kubectl --context spark-root -n llm-serving set image sts/qdrant qdrant=qdrant/qdrant:v1.13.5-unprivileged
kubectl --context spark-root -n llm-serving get pod qdrant-0 -o jsonpath='{.spec.containers[0].image}{"\n"}'   # still v1.13.4
kubectl --context spark-root -n llm-serving get sts qdrant -o jsonpath='{.status.currentRevision} → {.status.updateRevision}{"\n"}'
# roll back the staged change
kubectl --context spark-root -n llm-serving rollout undo sts/qdrant
kubectl --context spark-root -n llm-serving patch sts qdrant -p '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":0}}}}'
```

The ControllerRevisions behind `currentRevision`/`updateRevision` live in the root's etcd next to the StatefulSet: `kubectl --context spark-root -n llm-serving get controllerrevisions`. For a StatefulSet written inside a vCluster they would live in that vCluster's SQLite, and `kubectl --context spark-root -n vc-dev-lab get controllerrevisions` would show only the vCluster's own. With 3 replicas (3 nodes), `partition: 2` updates only `qdrant-2`. Watch it, then lower the partition step by step.

### 5.3 DaemonSet: one probe per GPU node (root)

#### What this lab does (manifests `lab/manifests/root/00-platform`, `root/50-workloads`)

- **Root, `platform-tools`:** DaemonSet `spark-node-probe` on every node labelled `spark.lab/gpu=gb10`. Every 15 s it writes the GB10 temperature and `MemAvailable` to `/var/lib/prometheus/node-exporter/k8s_node_probe.prom` on the host, where the host node-exporter (port 9100) picks it up.
- It sees the GPU through `NVIDIA_VISIBLE_DEVICES=all`, so it takes none of the 15 GPU slices.
- **Cordon test:** cordons dgx-spark-1, restarts the DaemonSet, checks the pod comes back, uncordons. While cordoned, no new pod can start in any of the three clusters, so keep it short.
- The first run pulls `nvcr.io/nvidia/cuda:13.0.1-base-ubuntu24.04`.

#### What to look for

1. **DaemonSet:** `DESIRED 1`, `READY 1` (2 with dgx-spark-2).
2. **Textfile and `/metrics`** (on the Spark): two lines, `spark_probe_gpu_temp_celsius{node="dgx-spark-1"}` with a plausible temperature (tens of °C, not `NaN`), and `spark_probe_mem_available_bytes{node="dgx-spark-1"}`, about 1.2e+11 on an idle Spark (≈ 119 GiB), less under load.
3. **While cordoned:** the restarted probe pod is `Running` again.
4. **Afterwards:** `kubectl --context spark-root get nodes` shows `Ready` without `SchedulingDisabled`.

**If something's off:** `DESIRED 0` means the node lacks the `spark.lab/gpu=gb10` label. `NaN` for the temperature means `nvidia-smi` failed in the pod (the default runtime isn't `nvidia`, Chapter 16). No `spark_probe_` lines on :9100 means node-exporter's textfile directory differs from the hostPath. More in §7.

#### Run it

```bash
kubectl --context spark-root apply -k manifests/root/00-platform
kubectl --context spark-root apply -k manifests/root/50-workloads
kubectl --context spark-root -n platform-tools get ds spark-node-probe
cat /var/lib/prometheus/node-exporter/k8s_node_probe.prom       # on the Spark
curl -s localhost:9100/metrics | grep spark_probe_               # host node-exporter picked it up
```

```text
spark_probe_gpu_temp_celsius{node="dgx-spark-1"} 41
spark_probe_mem_available_bytes{node="dgx-spark-1"} 9.87e+10
```

`DESIRED` is the number of nodes labelled `spark.lab/gpu=gb10` — 1 now, 2 when dgx-spark-2 joins as a root worker. Both vClusters see the same nodes (`sync.fromHost.nodes`), so a DaemonSet written *inside* a vCluster would also get one pod per node; the reason this one lives on the root is the hostPath it needs, not the node count.

Cordon the node and confirm the probe keeps running (it tolerates everything). On a one-node lab, a cordon stops new pods in **all three clusters**, so uncordon straight away:

```bash
kubectl --context spark-root cordon dgx-spark-1
kubectl --context spark-root -n platform-tools rollout restart ds spark-node-probe
kubectl --context spark-root -n platform-tools rollout status ds spark-node-probe --timeout=60s
kubectl --context spark-root -n platform-tools get pods -l app=spark-node-probe
kubectl --context spark-root uncordon dgx-spark-1
```

The DaemonSet pod comes back on a cordoned node because the DaemonSet controller adds a toleration for `node.kubernetes.io/unschedulable` to every pod it creates, and `operator: Exists` covers any other taint.

### 5.4 Indexed Job: shards, per-index retries, corrupt input (dev-lab)

#### What this lab does (manifest `lab/manifests/dev-lab/50-workloads/tokenizer-indexed-job.yaml`)

- **dev-lab, `tenant-alpha`:** namespaces and tenancy (unchanged if already applied), then Job `tokenize-shards`: 8 indexes, 2 at a time, each pod 200m CPU and about 5 s of work.
- **Quota test:** raises `parallelism` to 3. The third pod would need 600m of tenant-alpha's 500m, so dev-lab's API server refuses it.
- **Corrupt shard:** re-creates the Job with `BAD_SHARD=3`. Index 3 exits 42, `podFailurePolicy` marks it failed without retries.
- Deletes the Job after each run. Nothing is created on the root except the host copies of the pods in `vc-dev-lab`.

#### What to look for

1. **Watch:** never more than 2 `tokenize-shards-…` pods Running at once.
2. **Quota event:** `FailedCreate … exceeded quota: tenant-budget, … limited: limits.cpu=500m,requests.cpu=500m`.
3. **Clean run:** `completedIndexes` is `0-7`.
4. **Corrupt run:** `completed=0-2,4-7 failed=3`, and exactly one pod for index 3, status `Error`.

**If something's off:** pods `Pending` with no events usually means the root quota on `vc-dev-lab` refused them (Chapter 14). `apply` failing with `field is immutable` on the second run means the first Job wasn't deleted yet. More in §7.

#### Run it

```bash
kubectl --context dev-lab apply -k manifests/dev-lab/00-platform
kubectl --context dev-lab apply -k manifests/dev-lab/10-tenancy
kubectl --context dev-lab apply -f manifests/dev-lab/50-workloads/tokenizer-indexed-job.yaml
kubectl --context dev-lab -n tenant-alpha get pods -l job-name=tokenize-shards -w          # 2 at a time
```

While it runs, push it past the tenant budget — parallelism is mutable on a running Job:

```bash
kubectl --context dev-lab -n tenant-alpha patch job tokenize-shards --type merge -p '{"spec":{"parallelism":3}}'
kubectl --context dev-lab -n tenant-alpha get events --field-selector involvedObject.kind=Job | grep -i quota | tail -2
```

Expected: `FailedCreate … exceeded quota: tenant-budget, requested: limits.cpu=200m,requests.cpu=200m, used: limits.cpu=400m,requests.cpu=400m, limited: limits.cpu=500m,requests.cpu=500m`. The refusal comes from **dev-lab's own API server**: the third pod never exists anywhere, so the root sees nothing. The Job keeps running at 2 and retries the third pod as shards finish.

```bash
kubectl --context dev-lab -n tenant-alpha wait --for=condition=Complete job/tokenize-shards --timeout=5m
kubectl --context dev-lab -n tenant-alpha get job tokenize-shards -o jsonpath='{.status.completedIndexes}{"\n"}'   # 0-7
kubectl --context spark-root -n vc-dev-lab get pods | grep tokenize-shards       # the finished host copies
kubectl --context dev-lab -n tenant-alpha delete job tokenize-shards
```

Rehearse a corrupt shard:

```bash
kubectl --context dev-lab apply -f manifests/dev-lab/50-workloads/tokenizer-indexed-job.yaml --dry-run=client -o json \
 | jq '.spec.template.spec.containers[0].env[0].value="3"' | kubectl --context dev-lab apply -f -
kubectl --context dev-lab -n tenant-alpha wait --for=condition=Failed job/tokenize-shards --timeout=5m
kubectl --context dev-lab -n tenant-alpha get job tokenize-shards -o jsonpath='completed={.status.completedIndexes} failed={.status.failedIndexes}{"\n"}'
kubectl --context dev-lab -n tenant-alpha get pods -l job-name=tokenize-shards,batch.kubernetes.io/job-completion-index=3
```

Expected:

```text
completed=0-2,4-7 failed=3
tokenize-shards-3-…   0/1   Error   0   …        ← exactly one pod: FailIndex skipped the retries
```

The job is marked `Failed` only after the remaining indexes have finished (one failed index is within `maxFailedIndexes: 1`), so the `wait` can take a minute. Re-run *only* shard 3 after fixing the data: launch a Job with `completions: 8` and a `JOB_COMPLETION_INDEX`-aware skip list, or a one-off pod with `JOB_COMPLETION_INDEX=3`. Clean up with `kubectl --context dev-lab -n tenant-alpha delete job tokenize-shards`.

### 5.5 PDBs and drain — across the nesting

#### What this lab does (tenant PDB in dev-lab, drill `breakfix 11`)

- **dev-lab:** creates PDB `lab-tools/echo` (`minAvailable: 2`). The syncer copies it to the root as `vc-dev-lab/echo-x-lab-tools-x-dev-lab`.
- **Drill:** `breakfix.sh inject 11` re-applies `root-llms/50-workloads` (Qdrant and its PDB) and prints a drain command. You run it with `--dry-run=server`: the root answers as if draining, but nothing is evicted.
- **Runbook (optional, only on a real maintenance day):** snapshot Qdrant, scale it to 0, drain, uncordon, scale back. A real drain stops all three clusters and every model on a one-node lab.

#### What to look for

1. **`get pdb -A` on the root:**

   | PDB | Healthy value |
   |---|---|
   | `llm-serving/mock-llm` | `MIN AVAILABLE 1`, `ALLOWED DISRUPTIONS 1` (once `40-ingress` is applied) |
   | `llm-serving/qdrant` | `MAX UNAVAILABLE 0`, `ALLOWED DISRUPTIONS 0` |
   | `vc-dev-lab/echo-x-lab-tools-x-dev-lab` | `MIN AVAILABLE 2`, `ALLOWED DISRUPTIONS 1` |

2. **Drain dry-run:** `Cannot evict pod as it would violate the pod's disruption budget` for `qdrant-0`, and `evicting pod vc-dev-lab/dev-lab-0` / `vc-dev-llms/dev-llms-0 (server dry run)` for the vCluster control planes.
3. **The node is unchanged:** `kubectl --context spark-root get nodes` shows no `SchedulingDisabled` after a dry run.
4. **Clean-up:** the last line deletes the demo PDB in dev-lab.

**If something's off:** no `echo-x-lab-tools-x-dev-lab` PDB on the root means PDB sync is off in `vclusters/dev-lab.yaml` (`sync.toHost.podDisruptionBudgets.enabled`). If the node shows `SchedulingDisabled`, a drain ran without `--dry-run`: uncordon it. More in §7.

#### Run it

Two kinds of PDB meet at the root's Eviction API: the LLM stack's, which are root objects, and tenants', which a syncer copies down. Write a tenant PDB in dev-lab for its echo Deployment (3 replicas, Chapter 09), then list both kinds on the root:

```bash
kubectl --context spark-root -n llm-serving get pdb
kubectl --context dev-lab apply -f - <<'EOF'
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: {name: echo, namespace: lab-tools}
spec:
  minAvailable: 2
  selector: {matchLabels: {app: echo}}
EOF
kubectl --context spark-root get pdb -A
```

Expected on the root, among any platform PDBs:

```text
NAMESPACE     NAME                          MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
llm-serving   mock-llm                      1               N/A               1
llm-serving   qdrant                        N/A             0                 0
vc-dev-lab    echo-x-lab-tools-x-dev-lab    2               N/A               1
```

(`mock-llm` counts pods once Chapter 11's `root-llms/40-ingress` is applied.) The syncer copied the tenant's PDB because [`vclusters/dev-lab.yaml`](lab/vclusters/dev-lab.yaml) sets `sync.toHost.podDisruptionBudgets.enabled: true` (dev-llms too), and rewrote its selector to match the host copies of the pods. Now ask the root what a drain would do:

```bash
scripts/breakfix.sh inject 11          # (re)applies root-llms/50-workloads and prints the drain command
kubectl --context spark-root drain dgx-spark-1 --ignore-daemonsets --delete-emptydir-data --dry-run=server 2>&1 | grep -E 'qdrant|dev-lab-0|dev-llms-0|disruption'
```

Expected:

```text
evicting pod llm-serving/qdrant-0 (server dry run)
error when evicting pods/"qdrant-0" -n "llm-serving" (will retry after 5s): Cannot evict pod as it would violate the pod's disruption budget.
evicting pod vc-dev-lab/dev-lab-0 (server dry run)
evicting pod vc-dev-llms/dev-llms-0 (server dry run)
```

Three things to read out of that:

1. **The block is correct.** One replica, `maxUnavailable: 0` → zero allowed disruptions. Qdrant is a root object, so the root knew its PDB without help. A tenant's data is different: without PDB sync the root would have no idea a vCluster pod mattered and would evict a tenant's only copy. That's exactly why the sync is switched on for both vClusters.
2. **The vCluster control planes are evictable.** `dev-lab-0` and `dev-llms-0` are ordinary StatefulSet pods on the root. A real drain takes both tenant API servers down; tenant pods that are already running are root pods and live on until they are evicted too. The root's own control plane is different: static pods are mirror pods, which `drain` skips.
3. **One node is every cluster's only node.** Draining dgx-spark-1 for real is a full outage of all three clusters and of every model. With dgx-spark-2 joined, the same drain moves pods instead of stopping them, and the PDBs decide the pace.

`scripts/breakfix.sh hint 11` / `answer 11` walk the same diagnosis. The runbook for planned maintenance on a single-replica store: **snapshot → scale to 0 (or delete the PDB) → drain → maintain → uncordon → restore**:

```bash
kubectl --context spark-root -n llm-serving port-forward svc/qdrant 6333 & sleep 2
curl -s -X POST localhost:6333/collections/spark/snapshots | jq -r .result.name
kill %1
kubectl --context spark-root -n llm-serving scale sts qdrant --replicas=0
kubectl --context spark-root drain dgx-spark-1 --ignore-daemonsets --delete-emptydir-data     # really, on maintenance day
# … maintain, reboot …
kubectl --context spark-root uncordon dgx-spark-1
kubectl --context spark-root -n llm-serving scale sts qdrant --replicas=1
kubectl --context dev-lab -n lab-tools delete pdb echo                                        # the demo PDB
```

Note who does what. Qdrant runs on the root, so whoever administers the root also operates it: one team scales and drains. For data inside a vCluster it's two teams — the *tenant* scales its store, the *platform* drains — and the synced PDB is their contract. The snapshot lands in the pod's `emptyDir` at `/qdrant/snapshots`, so copy it out (`kubectl --context spark-root -n llm-serving cp`) before you scale to 0.

---

## 6. Verify

| Check | Expected |
|---|---|
| `points_count` after deleting `qdrant-0` | 2 |
| `kubectl --context spark-root -n llm-serving get sts` | `qdrant`, a root object |
| `kubectl --context spark-root -n vc-dev-lab get sts` | only `dev-lab` |
| DaemonSet `DESIRED = READY` | 1 (2 with dgx-spark-2) |
| tokenizer at `parallelism: 3` | `FailedCreate … exceeded quota: tenant-budget` in dev-lab, still 2 pods running |
| `completedIndexes` of a clean run | `0-7` |
| corrupt-shard run | `failedIndexes: 3`, one pod for index 3 |
| `kubectl --context spark-root get pdb -A` | `qdrant` in `llm-serving`, `echo-x-lab-tools-x-dev-lab` in `vc-dev-lab` |
| drain dry-run | blocked by the `qdrant` PDB |

---

## 7. Troubleshooting

| Symptom | Cause | Diagnose | Fix |
|---|---|---|---|
| StatefulSet stuck at `qdrant-0` Pending | PVC can't bind (StorageClass missing on the root, or node-affinity of an old PV) | `kubectl --context spark-root -n llm-serving describe pvc data-qdrant-0` | `scripts/install-addons.sh storage`. Delete a stale PV on the root only if you mean to lose data |
| `qdrant-0` never created; StatefulSet event `FailedCreate … exceeded quota: serving-budget` | `llm-serving`'s quota spent (the models hold the memory limits, or `requests.storage`) | `kubectl --context spark-root -n llm-serving describe resourcequota serving-budget` | scale a model down or park it ([Chapter 20 §9](20-vllm-high-throughput-llm-serving.md)) |
| Rolling update stuck on the highest ordinal | new pod never Ready (bad image/config), and OrderedReady waits forever | `kubectl --context spark-root -n llm-serving rollout status sts/qdrant`, pod events | fix the spec, then **delete the stuck pod** (StatefulSets don't auto-replace a broken new revision) |
| Data "lost" after `kubectl delete sts` | you also deleted PVCs, or reclaimPolicy was Delete | `kubectl --context spark-root get pv \| grep qdrant` | use `local-nvme-retain` + retention policy Retain (as in the lab) |
| DaemonSet `DESIRED 0` | nodeSelector label missing (node joined without the role's kubelet labels) | `kubectl --context spark-root get nodes -L spark.lab/gpu` | re-run `19.1-kubernetes.yml`, or `kubectl --context spark-root label node dgx-spark-1 spark.lab/gpu=gb10` |
| DaemonSet pod rejected by PSA | applied into a `baseline` namespace (e.g. a vCluster) | the `FailedCreate` event names `hostPath` | node-level tools go in root `platform-tools` |
| Job creates fewer pods than `parallelism` | tenant quota inside dev-lab | `kubectl --context dev-lab -n tenant-alpha get events` → `exceeded quota: tenant-budget` | lower parallelism or requests; the quota is the platform team's (dev-lab/10-tenancy) |
| Job `BackoffLimitExceeded` | one bad shard exhausted a job-wide `backoffLimit` | `kubectl --context dev-lab -n tenant-alpha describe job tokenize-shards` | `backoffLimitPerIndex` + `podFailurePolicy` |
| Root drain ignores a tenant PDB | PDB sync off in that vCluster's values | `kubectl --context spark-root -n vc-<name> get pdb` is empty | `sync.toHost.podDisruptionBudgets.enabled: true`, `helm upgrade` |
| Drain hangs forever | PDB `ALLOWED DISRUPTIONS 0` | `kubectl --context spark-root get pdb -A` | follow the maintenance runbook. `--disable-eviction` bypasses PDBs, and you then own the outage |

---

## 8. Scale-out path

| Lab | Production |
|---|---|
| Qdrant ×1, `maxUnavailable: 0` | Qdrant ×3 with replication factor 2, `maxUnavailable: 1`, anti-affinity across nodes/racks, scheduled snapshots to S3 |
| tenant PDBs synced into one root | the same contract between tenant and platform teams; drains automated by the platform and paced by PDBs |
| node probe DaemonSet | dcgm-exporter + node-feature-discovery + NVIDIA health checks (GPU Operator), NPD (node-problem-detector) |
| one root node, every drain is an outage | ≥ 2 worker nodes, so a drain moves vCluster control planes, model servers and tenant pods instead of stopping them |
| Indexed Job on one node | JobSet / Kueue with many nodes. Ray Data or Spark for truly large corpora (module 05-NeMo Curator) |

---

## 9. Checklist

- [ ] I killed `qdrant-0` and got the same name, the same PVC and the same vectors back — and found its directory on the Spark's NVMe.
- [ ] I can say which cluster's controller owns a StatefulSet, a Job, a DaemonSet and a PDB in this lab.
- [ ] I staged a StatefulSet update with `partition` without restarting anything.
- [ ] My DaemonSet runs on cordoned nodes and feeds Prometheus through the host textfile collector.
- [ ] I watched a tenant quota cap a Job's parallelism, and rehearsed a corrupt shard with `FailIndex`.
- [ ] I can explain why tenant PDBs must be synced to the root, and what a drain of the only node does to all three clusters.
