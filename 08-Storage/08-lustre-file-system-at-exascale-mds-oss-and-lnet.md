# Volume 08: Lustre File System at Exascale: MDS, OSS, OST & LNet

```
====================================================================================================
MODULE 08: HIGH-PERFORMANCE STORAGE & DISTRIBUTED DATA FABRICS FOR AI
VOLUME 08: LUSTRE FILE SYSTEM AT EXASCALE: MDS, OSS, OST STRIPING & LNET
====================================================================================================
```

---

## 1. Executive Architectural Overview & Scaffolding

For over two decades, the **Lustre File System** has served as the dominant high-performance parallel file system powering the world's fastest supercomputers (such as Frontier, Aurora, and Summit). In modern AI clusters running multi-node foundation model pre-training, Lustre remains a primary choice for high-throughput sequential operations:
* While modern flash architectures like WekaFS and VAST excel at high-IOPS multi-modal random reads, Lustre is an unyielding brute-force engine for **massive sequential checkpoint writes**.
* By striping single multi-terabyte checkpoint files across hundreds of parallel **Object Storage Targets (OSTs)**, Lustre achieves aggregate cluster write throughput exceeding **multiple terabytes per second** ($>2\text{ TB/s}$).

Understanding how **Lustre Network (LNet)** routes RDMA credits across heterogeneous fabrics, and how the **Distributed Lock Manager (LDLM)** serializes byte-range extents, is critical to prevent checkpointing stalls in exascale AI environments.

```mermaid
graph TD
    subgraph LustreClients["GPU Compute Nodes (Lustre Native Clients)"]
        CLIENT1["GPU Node 1 (Lustre Client Driver)"]
        CLIENT2["GPU Node 2 (Lustre Client Driver)"]
        CLIENTn["GPU Node N (Lustre Client Driver)"]
    end

    subgraph LNetFabric["Lustre Network Layer (LNet Multi-Rail RDMA)"]
        LNET_ROUTER["LNet High-Speed InfiniBand / RoCE Fabric<br/>(Dynamic Credit Flow / Multi-Rail Routing)"]
    end

    subgraph MetadataSubsystem["Lustre Metadata Cluster"]
        MGS["Management Server (MGS / MGT)<br/>(Cluster Configuration)"]
        MDS1["Metadata Server 1 (MDS / MDT 0)<br/>(Inode Ingestion / Dentry Lookups)"]
        MDS2["Metadata Server 2 (MDS / MDT 1)<br/>(Distributed Namespace / DNE)"]
    end

    subgraph StorageSubsystem["Lustre Object Storage Cluster (Parallel OSTs)"]
        OSS1["Object Storage Server 1 (OSS 1)"]
        OSS2["Object Storage Server 2 (OSS 2)"]
        OST1["OST 0 (NVMe Pool)"]
        OST2["OST 1 (NVMe Pool)"]
        OST3["OST 2 (NVMe Pool)"]
        OST4["OST 3 (NVMe Pool)"]
    end

    CLIENT1 <==|"Parallel RDMA Striping"|==> LNET_ROUTER
    CLIENT2 <==|"Parallel RDMA Striping"|==> LNET_ROUTER
    CLIENTn <==|"Parallel RDMA Striping"|==> LNET_ROUTER
    LNET_ROUTER <--> MetadataSubsystem
    LNET_ROUTER <==> OSS1
    LNET_ROUTER <==> OSS2
    OSS1 --> OST1
    OSS1 --> OST2
    OSS2 --> OST3
    OSS2 --> OST4
```

---

## 2. Lustre Component Architecture & Roles

Lustre partitions storage responsibilities across distinct server roles:

1. **Management Server (MGS / MGT)**:
   * Stores cluster configuration parameters, server membership, and mount specifications.
   * Typically active-passive redundant; consumes minimal resources.
2. **Metadata Server (MDS / MDT)**:
   * Responsible for directory hierarchies, file names, permissions, and extended attributes.
   * **Crucial Architecture**: The MDT does **not store file data**. It stores only the **layout**: an array pointing to the specific OSTs and object IDs where file blocks reside.
   * Modern Lustre clusters employ **Distributed Namespace Architecture (DNE)**, sharding directories across multiple MDTs.
3. **Object Storage Server (OSS) & Target (OST)**:
   * **OSS**: The server daemon handling data transfer and network I/O.
   * **OST**: The physical block storage pool (typically an NVMe flash array or ZFS pool) storing file chunk data objects.

```text
HOW A LUSTRE CLIENT OPENS AND WRITES A FILE:
1. Client ──(open /path/checkpoint.bin)──> MDS / MDT
2. MDS returns File Layout Map: "File is striped across OST 0, OST 1, OST 2, OST 3."
3. Client ──(Direct Parallel RDMA Write)──┬──> OSS 1 ──> OST 0
                                          ├──> OSS 1 ──> OST 1
                                          ├──> OSS 2 ──> OST 2
                                          └──> OSS 2 ──> OST 3
(MDS is completely bypassed during actual multi-terabyte data transfers!)
```

---

## 3. Lustre File Striping (`lfs setstripe`) Mechanics

File striping is Lustre's primary performance lever. A single file is chopped into fixed-size chunks and distributed round-robin across an array of OSTs:

```text
LUSTRE FILE STRIPING ARCHITECTURE:
File: checkpoint.bin (Total Size: 128 MB | Stripe Size: 32 MB | Stripe Count: 4)
  [ Chunk 0: 0-32MB   ] ──> OST 0
  [ Chunk 1: 32-64MB  ] ──> OST 1
  [ Chunk 2: 64-96MB  ] ──> OST 2
  [ Chunk 3: 96-128MB ] ──> OST 3
```

### 3.1 Striping Parameters & SRE Guidelines
* **Stripe Size (`-s`)**: The size of each continuous chunk written to an OST before moving to the next OST (default: $1\text{ MB}$; recommended for AI checkpoints: **$32\text{ MB}\text{--}64\text{ MB}$**).
* **Stripe Count (`-c`)**: The number of distinct OSTs across which the file is striped.
  * Setting `-c 1`: File resides on a single OST (optimal for small training text/image samples).
  * Setting `-c -1`: File is striped across **ALL available OSTs in the entire cluster** (optimal for massive foundation model checkpoints!).

```bash
# Example: Optimize directory for multi-terabyte foundation model checkpoints
$ lfs setstripe -c -1 -s 32M /mnt/lustre/checkpoints/
```

---

## 4. Lustre Network (LNet) & RDMA Credit Management

The **Lustre Network (LNet)** abstraction decouples filesystem protocol operations from underlying physical networking:
* **Multi-Rail InfiniBand / RoCE**: LNet bonds multiple network interfaces on the client, spraying I/O requests across all available adapters concurrently.
* **Credit-Based Flow Control**: LNet enforces credit limits per peer. If an Object Storage Server's queues fill up, LNet pauses sender credits, preventing switch buffer drops.
* **LNet Routing**: Seamlessly bridges heterogeneous fabrics, allowing compute nodes on HDR/NDR InfiniBand to read from storage nodes deployed on 400G RoCEv2 Ethernet.

---

## 5. First-Principles Mathematics: Parallel Checkpointing Bandwidth

### 5.1 Aggregate Cluster Write Bandwidth Roofline
For a cluster containing $N_{\text{OST}}$ active targets, $N_{\text{OSS}}$ storage servers, and $N_{\text{clients}}$ GPU nodes:

$$B_{\text{Lustre\_write}} = \min\left( \sum_{i=1}^{N_{\text{OST}}} B_{\text{OST\_i}}, \sum_{j=1}^{N_{\text{OSS}}} B_{\text{OSS\_net\_j}}, \sum_{k=1}^{N_{\text{clients}}} B_{\text{client\_net\_k}} \right)$$

#### Numerical Example:
* Cluster contains $32\text{ OSS nodes}$, each hosting $4\text{ NVMe Gen 5 OSTs} = 128\text{ OSTs total}$.
* Each OST sustains $6.0\text{ GB/s}$ sequential write.
* Total Media Capacity $= 128 \times 6.0 = \mathbf{768 \text{ GB/s}}$.
* Each OSS has dual $400\text{ Gbps}$ InfiniBand links $= 100\text{ GB/s}$ per server.
* Total Network Capacity $= 32 \times 100 = \mathbf{3,200 \text{ GB/s}} = 3.2\text{ TB/s}$.
* **System Bottleneck**: Media limited to **$768 \text{ GB/s}$**.

### 5.2 Checkpoint Flush Duration
For a 405B parameter model checkpoint ($D_{\text{ckpt}} = 6.48\text{ TB}$):
$$T_{\text{flush}} = \frac{6,480 \text{ GB}}{768 \text{ GB/s}} \approx \mathbf{8.43 \text{ seconds}}$$
Striping across all 128 OSTs completes the flush in under 9 seconds, minimizing cluster GPU idle stalls!

---

## 6. Concrete Production Lab: Lustre Striping & Diagnostic Tooling

### 6.1 Striping Inspection & Configuration
```bash
# 1. Query striping configuration of a file or directory
$ lfs getstripe /mnt/lustre/checkpoints/model_step_1000.pt
/mnt/lustre/checkpoints/model_step_1000.pt
lmm_stripe_count:  128
lmm_stripe_size:   33554432 (32MB)
lmm_pattern:       raid0
lmm_layout_gen:    0
lmm_stripe_offset: 42
        obdidx           objid           objid           group
            42        14589201      0xde9b71                 0
            43        14589202      0xde9b72                 0
            ...

# 2. Benchmark OST Space and Inode Distribution
$ lfs df -h
UUID                       bytes        Used   Available Use% Mounted on
ai-OST0000_UUID            14.2T        4.8T        9.4T  34% /mnt/lustre[OST:0]
ai-OST0001_UUID            14.2T        4.9T        9.3T  35% /mnt/lustre[OST:1]
...
ai-MDT0000_UUID             3.2T      380.0G        2.8T  12% /mnt/lustre[MDT:0]
```

### 6.2 LNet Multi-Rail Diagnostic Script
```bash
# Query LNet network interface health and active credit flow
$ lnetctl net show
net:
    - net type: o2ib0
      local NI(s):
        - nid: 192.168.10.15@o2ib
          status: up
          interfaces:
              0: mlx5_0
          credits: 256
          peer-credits: 32
          max-intf-tx: 1024
```

---

## 7. Multi-Dimensional Correlation & Troubleshooting

```text
┌─────────────────────────────┬───────────────────────────────┬─────────────────────────────────────────────────────────┐
│ Failure Signature           │ Root Cause Layer              │ SRE Triage & Diagnostic Remediation                     │
├─────────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────────────────┤
│ Checkpoint write stalls;    │ File written to directory     │ Verify striping with lfs getstripe:                     │
│ aggregate bandwidth < 5GB/s │ with default stripe_count=1   │ Re-stripe target directory: $ lfs setstripe -c -1 ...   │
│                             │ (single OST hotspot)          │ Ensure clients write in parallel across ranks.          │
├─────────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────────────────┤
│ lfs command hangs           │ Metadata Server (MDS)         │ Inspect MDS system load and lock queue:                 │
│ indefinitely                │ Distributed Lock Manager stall│ $ lctl get_param ldlm.namespaces.*.lock_count           │
│                             │                               │ Check if an OST has gone offline (lfs df -h).           │
├─────────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────────────────┤
│ LNet error in dmesg:        │ Network congestion causing    │ Query LNet peer credits:                                │
│ "LNetError: peer dropped"   │ credit timeout on OSS link    │ $ lnetctl peer show                                     │
│                             │                               │ Inspect InfiniBand switch port drops via perfquery.     │
└─────────────────────────────┴───────────────────────────────┴─────────────────────────────────────────────────────────┘
```

---

## 8. Summary & Technical Takeaways

1. **Decoupled Metadata and Data**: Lustre separates metadata management (MDS/MDT) from bulk data storage (OSS/OST), allowing clients to write multi-terabyte files in parallel without MDS involvement.
2. **The Power of File Striping**: Configuring large stripe sizes ($32\text{ MB}$) across all available OSTs (`-c -1`) unleashes hundreds of gigabytes per second of sustained write throughput for AI checkpoints.
3. **LNet Multi-Rail Transport**: LNet natively handles multi-adapter bonding and credit-based flow control across InfiniBand and RoCE fabrics.
4. **Workload Alignment**: Lustre is ideal for massive sequential training checkpoints and datasets; smaller multi-modal datasets should be aggregated into WebDataset `.tar` archives before ingestion.
