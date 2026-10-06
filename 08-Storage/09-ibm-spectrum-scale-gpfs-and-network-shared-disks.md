# Volume 09: IBM Spectrum Scale (GPFS) & Network Shared Disks

```
====================================================================================================
MODULE 08: HIGH-PERFORMANCE STORAGE & DISTRIBUTED DATA FABRICS FOR AI
VOLUME 09: IBM SPECTRUM SCALE (GPFS), NETWORK SHARED DISKS (NSD) & ILM POLICIES
====================================================================================================
```

---

## 1. Executive Architectural Overview & Scaffolding

**IBM Spectrum Scale** (historically and widely known as the **General Parallel File System / GPFS**) represents one of the most mature and resilient parallel cluster file systems in enterprise computing. Deployed at exascale supercomputing centers (such as the Oak Ridge National Laboratory Summit supercomputer), Spectrum Scale bridges traditional high-performance enterprise storage with modern AI data pipelines:
* **The Shared-Disk Heritage**: Originally developed to allow multiple mainframe and Unix nodes concurrent block-level access to shared SAN storage, GPFS evolved into a distributed Network Shared Disk (NSD) architecture spanning thousands of nodes.
* **Token-Based Distributed Locking**: Instead of relying on a single metadata server, Spectrum Scale distributes metadata management dynamically using a granular **Token Management Server**.
* **Automated Information Lifecycle Management (ILM)**: Features an SQL-like declarative policy engine that automatically tiers inactive datasets from expensive NVMe flash to secondary object storage (S3/Ceph) transparently, without altering POSIX filesystem paths.

```mermaid
graph TD
    subgraph ComputeClients["GPU Compute Nodes (Spectrum Scale Clients)"]
        GPU1["GPU Node 1 (GPFS Client Driver / verbs RDMA)"]
        GPU2["GPU Node 2 (GPFS Client Driver / verbs RDMA)"]
        GPU3["GPU Node 3 (GPFS Client Driver / verbs RDMA)"]
    end

    subgraph ClusterControl["GPFS Cluster Management & Quorum"]
        CM["Cluster Manager Node"]
        TOKEN_MGR["Token Management Server<br/>(Dynamic Byte-Range Distributed Locks)"]
        QUORUM["Node Quorum / Tiebreaker Disk"]
    end

    subgraph NSDSubsystem["Network Shared Disk (NSD) Servers"]
        NSD_SRV1["NSD Server 1 (Dual 400G RoCE/IB)"]
        NSD_SRV2["NSD Server 2 (Dual 400G RoCE/IB)"]
    end

    subgraph StoragePools["Hierarchical Storage Pools (ILM Managed)"]
        NVME_POOL["High-Performance Flash Pool (Gold Pool)<br/>(Local PCIe Gen 5 NVMe / Active Checkpoints)"]
        OBJECT_POOL["Capacity Object Pool (Silver Pool)<br/>(S3 Cloud Object Store / Cold Datasets)"]
    end

    ComputeClients <==|"RDMA verbs Transport (Zero-Copy)"|==> NSDSubsystem
    ComputeClients <--> ClusterControl
    NSDSubsystem --> NVME_POOL
    NVME_POOL <-->|"Automated ILM Policy Migration"| OBJECT_POOL
```

---

## 2. Network Shared Disks (NSD) & Token Management

### 2.1 The NSD Client-Server Architecture
In Spectrum Scale, underlying block devices (LUNs, NVMe partitions, or local SSD arrays) are defined as **Network Shared Disks (NSDs)**:
* **NSD Servers**: Dedicated storage controllers physically attached to NVMe arrays that export disk blocks across InfiniBand or Ethernet networks.
* **NSD Clients**: Compute nodes that mount the filesystem and issue block read/write requests over the network using native RDMA (`verbs`).

### 2.2 Token Management & Distributed Locking
To maintain POSIX cache consistency across thousands of nodes without a single metadata bottleneck, GPFS uses a **Token-Based Protocol**:
* **Byte-Range Tokens**: When a client writes to a file, it requests an exclusive token for bytes $[0 \dots 10\text{MB}]$. Other clients can simultaneously write to bytes $[10\text{MB} \dots 20\text{MB}]$ without lock conflict!
* **Read / Write Sharing**: If multiple GPU nodes concurrently read the same foundation model weights, the Token Manager grants shared read tokens to all nodes, enabling concurrent memory-mapped reading.

```text
TOKEN MANAGEMENT DISTRIBUTED LOCK ACQUISITION:
Client 1 ──(Request Exclusive Write Token [0..10MB])──> Token Management Server
                                                                   │
Token Manager grants token ────────────────────────────────────────┘
Client 1 executes local direct write without network lock chatter.
```

---

## 3. Information Lifecycle Management (ILM) Policy Engine

One of Spectrum Scale's greatest strengths in enterprise AI is its **declarative ILM policy engine**. Administrators define SQL-like rules that execute in parallel across billions of files:

```sql
/* GPFS ILM Policy: Automated Storage Tiering */
RULE 'TierActiveCheckpoints'
  MIGRATE FROM POOL 'system'
  TO POOL 'nvme_pool'
  FOR FILESET ('ai_checkpoints')
  WHERE DAYS(CURRENT_TIMESTAMP) - DAYS(ACCESS_TIME) < 7

RULE 'ArchiveColdDatasets'
  MIGRATE FROM POOL 'nvme_pool'
  TO POOL 'object_cloud_pool'
  WHERE DAYS(CURRENT_TIMESTAMP) - DAYS(ACCESS_TIME) > 30
```

* **Zero Namespace Disruption**: When a 10TB dataset is migrated to S3 object storage, its inode remains in the POSIX directory. If an AI engineer runs `cat` or `open()`, Spectrum Scale transparently **hydrates the data back into the NVMe pool**!

---

## 4. First-Principles Mathematics: Pagepool Memory Sizing

The **`pagepool`** is a dedicated host RAM buffer managed directly by the GPFS kernel daemon (`mmfsd`), bypassing the standard Linux Page Cache.

### 4.1 Optimal Pagepool Capacity Formula
To maximize streaming throughput and prevent Direct I/O stalls, the `pagepool` size should be configured to absorb peak streaming bandwidth across concurrent worker threads:

$$\text{Pagepool}_{\text{opt}} = \min\left( 0.25 \times \text{HostRAM}, \quad N_{\text{threads}} \times S_{\text{buffer}} \times Q_{\text{depth}} \right)$$

#### Numerical Example:
* GPU Compute Node with $512\text{ GB}$ host DDR5 RAM.
* Storage target streaming at $40\text{ GB/s}$.
* Standard safe enterprise allocation:
$$\text{Pagepool} = 0.25 \times 512 \text{ GB} = \mathbf{128 \text{ GB}}$$

Configuring `pagepool 128G` guarantees that GPFS has ample pre-registered pinned memory buffers to sustain massive parallel streaming without contending with host OS memory allocators.

---

## 5. Concrete Production Lab: Spectrum Scale Inspection & Policy Deployment

### 5.1 Cluster Health & NSD Verification
```bash
# 1. Query GPFS cluster membership and operational daemon status
$ mmlscluster
================================================================================
GPFS Cluster: ai-compute-gpfs.corp (Cluster Type: Primary)
  Daemon Node Name        IP Address      Designation
  dgx-node01.corp         192.168.10.1    client
  dgx-node02.corp         192.168.10.2    client
  storage-nsd01.corp      192.168.10.50   quorum-manager
  storage-nsd02.corp      192.168.10.51   quorum-manager
================================================================================

# 2. Inspect Network Shared Disks (NSD) status and storage pools
$ mmlsnsd -m
Disk name    NSD volume ID      Pool        Remarks
-------------------------------------------------------------------------
nsd_nvme01   0A0A0A0165B82A     gold_nvme   Direct PCIe Gen 5 Flash Pool
nsd_nvme02   0A0A0A0165B82B     gold_nvme   Direct PCIe Gen 5 Flash Pool
nsd_obj01    0A0A0A0165B82C     silver_obj  Disaggregated S3 Cloud Tier
```

### 5.2 Performance Tuning & verbs RDMA Configuration
```bash
# 1. Enforce 128GB pagepool and enable RDMA verbs transport
$ mmchconfig pagepool=128G
$ mmchconfig verbsPorts=mlx5_0:1
$ mmchconfig nsdMaxWorkerThreads=512

# 2. Restart GPFS daemon to apply high-throughput parameters
$ mmshutdown -N localhost && mmstartup -N localhost
```

---

## 6. Multi-Dimensional Correlation & Troubleshooting

```text
┌─────────────────────────────┬───────────────────────────────┬─────────────────────────────────────────────────────────┐
│ Failure Signature           │ Root Cause Layer              │ SRE Triage & Diagnostic Remediation                     │
├─────────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────────────────┤
│ Filesystem freezes; mmfsd   │ Token revoking deadlock or    │ Inspect active token waiters:                           │
│ threads stuck in lock wait  │ Token Management Server stall │ $ mmdiag --waiters                                      │
│                             │                               │ Check network latency to Token Manager node.            │
├─────────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────────────────┤
│ Low streaming throughput    │ Pagepool under-sized; I/O     │ Increase pagepool capacity:                             │
│ (< 2 GB/s on 400G link)     │ falling back to small buffers │ $ mmchconfig pagepool=64G or 128G                       │
│                             │                               │ Verify verbs RDMA is active: $ mmdiag --verbs           │
├─────────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────────────────┤
│ Node unmounted unexpectedly │ Quorum loss or network        │ Inspect GPFS log:                                       │
│ with "Expelled from cluster"│ heartbeat drop (> 30s pause)  │ $ tail -n 50 /var/adm/ras/mmfs.log.latest               │
│                             │                               │ Check for PFC pause storm deadlocks on network ports.   │
└─────────────────────────────┴───────────────────────────────┴─────────────────────────────────────────────────────────┘
```

---

## 7. Summary & Technical Takeaways

1. **Enterprise Shared-Disk Engine**: Spectrum Scale decouples physical storage from compute clients through Network Shared Disks (NSD) connected via high-speed RDMA (`verbs`).
2. **Byte-Range Token Concurrency**: Granular distributed locking enables thousands of GPU ranks to write non-overlapping offsets of a single massive file concurrently.
3. **Automated ILM Policies**: Rule-based tiering migrates multi-petabyte datasets between high-cost NVMe flash and low-cost object storage without breaking POSIX path conventions.
4. **Dedicated Pagepool Architecture**: By managing its own pinned memory buffers (`pagepool`), Spectrum Scale bypasses Linux Page Cache double-buffering and lock contention.
