# Volume 21: Storage Benchmarking Masterclass — fio, IOR, and mdtest

```
====================================================================================================
MODULE 08: STORAGE & HIGH-PERFORMANCE DATA FABRIC FOR AI
VOLUME 21: Synthetic Benchmarking, MPI-IO Clustered IOR, and Metadata Stress Profiling
====================================================================================================
```

---

## 1. Executive Intuition: The Microbenchmark Trap

A disastrous mistake in AI storage qualification is relying on naive tools like `dd if=/dev/zero of=/mnt/storage/test` or single-threaded `fio` scripts. Such tests invariably measure the Linux OS Page Cache rather than physical non-volatile storage, yielding absurd numbers (e.g. $40\text{ GB/s}$ over a $1\text{ Gbps}$ Ethernet cable) and zero actionable insight into cluster scalability.

To rigorously qualify a storage fabric for foundation model training, performance engineers must benchmark across three orthogonal operational axes:

```
+-----------------------------------------------------------------------------------------+
|                        THE TRI-AXIAL AI STORAGE BENCHMARK                               |
+-----------------------------------------------------------------------------------------+
| 1. Block / Device Saturation (fio)       | Isolates drive, PCIe bus, and kernel I/O     |
|                                          | engine (io_uring, libaio, GPUDirect GDS).    |
+------------------------------------------+----------------------------------------------+
| 2. Clustered MPI-IO Streaming (IOR)      | Measures distributed coordinated checkpoint  |
|                                          | write bursts across thousands of GPU nodes.  |
+------------------------------------------+----------------------------------------------+
| 3. High-Concurrency Metadata (mdtest)   | Stresses directory locks, Inode creation,    |
|                                          | and stat() rates under massive concurrency.  |
+-----------------------------------------------------------------------------------------+
```

---

## 2. Lineage & Evolution of Storage Benchmarking Tools

```
   [1980s: The Naive Era]
                 |
           (dd, bonnie, bonnie++: Sequential block copies, heavily distorted by DRAM)
                 |
   [2005: The Flexible I/O Tester (fio)]
                 |
           (Jens Axboe authors fio: Thread/process scaling, libaio, tail latencies)
                 |
   [2010: HPC Scalable Benchmarks (IOR & mdtest)]
                 |
           (LLNL / LANL: MPI-coordinated parallel collective I/O across supercomputers)
                 |
   [2020: The io_uring & GDS Revolution]
                 |
           (fio adds io_uring engine, gdsio emerges for GPU-direct storage benchmarking)
                 |
   [2023: MLPerf Storage Benchmark Suite]
                 |
           (Standardized training-loop emulators: 3D U-Net, BERT, and ResNet data patterns)
```

---

## 3. First-Principles Mathematics: Little's Law & Queue Depth Scaling

The fundamental governing equation of storage performance is **Little's Law**, adapted for asynchronous I/O pipelines:

$$\text{Queue Depth } (QD) = \text{Throughput } (\text{IOPS}) \times \text{Latency } (L)$$

$$\text{IOPS} = \frac{QD}{L}$$

$$\text{Bandwidth } (B) = \text{IOPS} \times \text{Block Size } (S_{\text{block}}) = \frac{QD \times S_{\text{block}}}{L}$$

### 3.1 Resolving the Saturation Horizon
Consider an enterprise NVMe SSD with an average random read latency $L = 80\ \mu\text{s} = 0.00008\text{ seconds}$ at $4\text{ KB}$ block size:
- At $QD = 1$:
  $$\text{IOPS} = \frac{1}{0.00008} = 12,500\text{ IOPS}$$
  $$B = 12,500 \times 4096\text{ B} \approx 51.2\text{ MB/s}\quad (\text{Wasting } 99\%\text{ of bus capacity!})$$
- At $QD = 32$:
  $$\text{IOPS} = \frac{32}{0.00008} = 400,000\text{ IOPS}$$
  $$B = 400,000 \times 4096\text{ B} \approx 1,638.4\text{ MB/s} \approx 1.64\text{ GB/s}$$
- At $QD = 128$:
  Drive internal NAND channels saturate, latency rises to $L = 150\ \mu\text{s}$, delivering peak $\text{IOPS} \approx 850,000$.

> **Benchmark Axiom:** An AI storage test that does not explicitly declare and sweep Queue Depth ($QD$) and Block Size ($S_{\text{block}}$) produces meaningless metrics.

---

## 4. Masterclass 1: Production `fio` Harness Architecture

To bypass the Linux OS Page Cache and guarantee hardware-level measurements, every `fio` execution for AI qualification must specify:
1. `direct=1`: Bypasses kernel page cache using `O_DIRECT`.
2. `ioengine=io_uring` or `libaio`: True asynchronous kernel bypass.
3. `group_reporting=1`: Aggregates thread metrics cleanly.
4. `numa_cpu_nodes` / `numa_mem_nodes`: Binds memory and threads to the NUMA socket hosting the PCIe root complex.

### 4.1 Production `fio` Job Configuration (`ai_storage_matrix.fio`)

```ini
[global]
ioengine=io_uring
direct=1
group_reporting=1
time_based=1
runtime=60
ramp_time=10
size=32G
filename=/mnt/parallel_fs/benchmark_testfile.dat

# Test 1: Random Read Latency & Tail (Simulates DataLoader sample lookup)
[random_read_4k_qd1]
rw=randread
bs=4k
iodepth=1
numjobs=1
stonewall

# Test 2: Random Read IOPS Saturation (Simulates 64 DataLoader workers)
[random_read_64k_qd32]
rw=randread
bs=64k
iodepth=32
numjobs=8
stonewall

# Test 3: Large Sequential Write Throughput (Simulates Checkpoint commit)
[seq_write_1m_qd16]
rw=write
bs=1M
iodepth=16
numjobs=8
stonewall

# Test 4: Streaming Read Ingestion (Simulates WebDataset sequential prefetch)
[seq_read_4m_qd8]
rw=read
bs=4M
iodepth=8
numjobs=4
stonewall
```

---

## 5. Masterclass 2: MPI-IO Clustered Benchmarking with `IOR`

`fio` measures single-node storage bandwidth. In foundation model pre-training, checkpoints are saved simultaneously by thousands of ranks. **IOR (Interleaved Or Random)** uses the Message Passing Interface (MPI) to synchronize thousands of processes across a cluster, executing simultaneous read/write bursts against a shared parallel file system.

### 5.1 Access Patterns: File-Per-Process (FPP) vs. Single-Shared-File (SSF)

```
+-----------------------------------------------------------------------------+
|                     IOR ACCESS PATTERNS IN AI TRAINING                      |
+-----------------------------------------------------------------------------+
| File-Per-Process (FPP) [-F]:                                                |
|   Rank 0 -> /storage/ckpt/file_0.distcp                                     |
|   Rank 1 -> /storage/ckpt/file_1.distcp                                     |
|   Rank N -> /storage/ckpt/file_N.distcp                                     |
|   --> Advantage: Zero file lock contention. Max sequential write throughput.|
|   --> Hazard: Generates millions of files; stresses metadata servers (MDS). |
|                                                                             |
| Single-Shared-File (SSF) [Default]:                                         |
|   Rank 0 -> [Bytes 0 to 1GB]      \                                         |
|   Rank 1 -> [Bytes 1GB to 2GB]     |--> /storage/ckpt/global_model.distcp    |
|   Rank N -> [Bytes N*1GB to ...]  /                                         |
|   --> Advantage: Single clean artifact file.                                |
|   --> Hazard: Triggers severe distributed byte-range lock contention on PFS.|
+-----------------------------------------------------------------------------+
```

### 5.2 Clustered IOR Benchmark Script

```bash
#!/usr/bin/env bash
# Execute IOR benchmark across 32 compute nodes (256 MPI ranks)
# Testing checkpoint write burst followed by restart read burst

TOTAL_RANKS=256
HOSTFILE="/etc/mpi/cluster_hosts"
TARGET_DIR="/mnt/parallel_fs/ior_benchmark"

mkdir -p "${TARGET_DIR}"

echo "=== Running IOR: File-Per-Process (FPP) Checkpoint Simulation ==="
mpirun -np "${TOTAL_RANKS}" --hostfile "${HOSTFILE}" \
    ior -v \
        -a POSIX \
        -w -r \
        -F \
        -b 4G \
        -t 16M \
        -e \
        -g \
        -C \
        -o "${TARGET_DIR}/ior_fpp_test.dat"

# Flag breakdown:
#   -a POSIX: Native POSIX I/O calls (write, read)
#   -w -r: Execute write phase, then read phase
#   -F: File-per-process (emulates PyTorch DCP sharding)
#   -b 4G: Block size per rank (Total write = 256 * 4GB = 1 TB)
#   -t 16M: Transfer chunk size (16 MB buffer transfers)
#   -e: Enforce fsync() at completion of write phase
#   -g: Intra-test barrier synchronization across all ranks
#   -C: Reorder tasks for read (prevents reading from local OS page cache)
```

---

## 6. Masterclass 3: Metadata Stress Profiling with `mdtest`

While IOR stresses the storage network fabric and NAND channels, `mdtest` stresses the **Metadata Engine** (MDS in Lustre, Metanodes in GPFS, Raft in Weka, TiKV/Redis in JuiceFS) by executing millions of concurrent file creations, directory updates, `stat()` lookups, and file deletions.

```bash
#!/usr/bin/env bash
# mdtest high-concurrency metadata stress benchmark

TOTAL_RANKS=128
HOSTFILE="/etc/mpi/cluster_hosts"
TARGET_DIR="/mnt/parallel_fs/mdtest_benchmark"

mpirun -np "${TOTAL_RANKS}" --hostfile "${HOSTFILE}" \
    mdtest \
        -n 50000 \
        -d "${TARGET_DIR}" \
        -i 3 \
        -u \
        -v \
        -z 2 \
        -b 10

# Flag breakdown:
#   -n 50000: Each rank creates/stats/removes 50,000 files (Total = 6.4M operations)
#   -d: Target test directory on parallel filesystem
#   -i 3: Run 3 iterations and report mean
#   -u: Unique working directory per rank (minimizes directory lock false sharing)
#   -z 2: Directory depth of 2 levels
#   -b 10: Branching factor of 10 directories
```

### 6.1 Sample mdtest Performance Output Analysis
```
SUMMARY rate: (all times in seconds, operations/sec)
   Operation             Max            Min           Mean        Std Dev
   ---------             ---            ---           ----        -------
   Directory creation:   85,210.4       78,142.0      82,104.2     2,105.1
   Directory stat:      420,105.8      395,210.4     412,890.1     8,412.3
   Directory removal:    74,510.2       69,120.5      71,840.4     1,850.2
   File creation:       124,580.1      115,200.0     120,410.8     3,200.1
   File stat:         1,850,210.4    1,680,410.2   1,760,110.5    45,210.0
   File read:           890,410.2      820,110.4     855,100.8     21,410.2
   File removal:        145,210.8      132,100.5     139,810.1     4,102.5
```

> **SRE Interpretation:** If `File stat` throughput drops below $250,000\text{ ops/sec}$ across a 128-rank cluster, PyTorch DataLoader initialization will stall for minutes on multi-million sample datasets.

---

## 7. Comparative Benchmark Matrix: Baseline Targets for AI Fabrics

| Metric | Target Specification (Top-Tier) | Acceptable (Mid-Tier) | Degraded (Action Required) |
| :--- | :--- | :--- | :--- |
| **fio 4K RandRead QD=1 Latency** | $<85\ \mu\text{s}$ | $85–250\ \mu\text{s}$ | $>500\ \mu\text{s}$ |
| **fio 4K RandRead QD=128 IOPS (Drive)**| $>800,000\text{ IOPS}$ | $400,000–800,000$ | $<250,000\text{ IOPS}$ |
| **IOR FPP Write Throughput (Per Node)**| $\ge 25\text{ GB/s}$ | $10–25\text{ GB/s}$ | $<5\text{ GB/s}$ |
| **IOR SSF Lock Contention Penalty** | $\le 15\%$ vs FPP | $15–40\%$ vs FPP | $>60\%$ vs FPP (Lock Crash) |
| **mdtest Cluster-Wide File Stat Rate** | $>1,500,000\text{ ops/sec}$ | $500,000–1,500,000$ | $<100,000\text{ ops/sec}$ |

---

## 8. SRE Diagnostics & Troubleshooting Playbook

```
+---------------------------------------------------------------------------------------------------+
|                        STORAGE BENCHMARKING SRE DIAGNOSTIC MATRIX                                 |
+------------------------------------+--------------------------+-----------------------------------+
| Symptom / Failure Mode             | Root Cause Hypothesis    | Triage & Remediation Command      |
+------------------------------------+--------------------------+-----------------------------------+
| Benchmark reports impossibly high  | Page cache poisoning:    | Always specify `direct=1` in fio  |
| throughput ($>100\text{ GB/s}$ on  | test read/wrote from     | and `-C` (cache bypass) in IOR;   |
| single 10 GbE interface).          | Linux DRAM buffers.      | drop caches: `echo 3 > /proc/sys/ |
|                                    |                          | vm/drop_caches` before run.       |
+------------------------------------+--------------------------+-----------------------------------+
| `ior` hangs indefinitely during    | MPI barrier timeout or   | Run IOR under strace/gdb:         |
| collective close phase.            | parallel file system     | Inspect metadata locks on PFS.    |
|                                    | distributed lock deadlock| Increase MPI timeout thresholds.  |
+------------------------------------+--------------------------+-----------------------------------+
| Significant throughput difference  | NUMA socket asymmetry;   | Bind fio process to local socket: |
| across identical compute nodes.    | PCIe card on NUMA Node 1 | `numactl --cpunodebind=0          |
|                                    | crossing UPI bus.        |  --membind=0 fio ...`             |
+------------------------------------+--------------------------+-----------------------------------+
| `mdtest` errors with               | Inode exhaustion on      | Check free inodes on filesystem:  |
| `No space left on device`.         | storage pool metadata    | `df -i /mnt/parallel_fs`          |
|                                    | targets (MDT / Inodes).  | Expand metadata target volume.    |
+------------------------------------+--------------------------+-----------------------------------+
```

---

## 9. Verification & Architectural Synthesis Checklist

- [ ] **Direct I/O Enforced:** All microbenchmarks execute with `direct=1` or `O_DIRECT` to eliminate false DRAM cache hits.
- [ ] **Multi-Node MPI Testing:** Cluster tested with `ior` across all available nodes to surface cross-rail fabric congestion.
- [ ] **Metadata Horizon Mapped:** Storage validated with `mdtest` to determine exact million-file inode creation and stat limits.
- [ ] **NUMA Pinning Validated:** Benchmarking processes pinned to the identical NUMA node hosting the storage NIC / NVMe drive.
- [ ] **Production Alignment:** Benchmark block sizes match actual AI access patterns: 4 KB / 64 KB for lookups, 4 MB / 16 MB for streaming.
