# Chapter 15 · NVIDIA Hardware & Driver Stack as Kubernetes Sees It: GB10, NVLink-C2C, Unified Memory, Drivers, CUDA Compatibility

> **02-Kubernetes · Part V — GPU platform · Chapter 15 of 28** · ← [Chapter 14 · Multi-tenancy, resource quotas & cgroups](14-multi-tenancy-resource-quotas-and-cgroups.md) · [All chapters](00-kubernetes-step-by-step-guide.md) · [Chapter 16 · NVIDIA Container Toolkit & GPU virtualization](16-nvidia-container-toolkit-and-gpu-virtualization.md) →

| | |
|---|---|
| **You will build** | A verified hardware and driver inventory of your Spark from the host and from inside a pod. You'll see how GPU Feature Discovery turns that inventory into node labels, set a GEMM throughput baseline, and learn the handful of `nvidia-smi` and `dmesg` checks that catch most GPU problems |
| **Hardware** | dgx-spark-1 |
| **Time** | 60 min |
| **Risk** | None |
| **Clusters** | `spark-root` (node labels, the benchmark in `platform-tools`), `dev-lab` (`gpu-smoke`, the arch test) |
| **Lab files** | [`manifests/root/70-gpu/gemm_bench.py`](lab/manifests/root/70-gpu/gemm_bench.py), [`gemm-solo.yaml`](lab/manifests/root/70-gpu/gemm-solo.yaml), [`manifests/dev-lab/70-gpu/gpu-smoke.yaml`](lab/manifests/dev-lab/70-gpu/gpu-smoke.yaml). Module [07-Nvidia](../07-Nvidia/README.md) goes to silicon depth |

---

## 1. Why this matters on a Spark

Kubernetes treats a GPU as an opaque integer (`nvidia.com/gpu: 1`). Everything that makes a GB10 different is invisible to the scheduler unless something turns it into labels and limits. And most "Kubernetes GPU problems" turn out to be host problems: driver/library mismatch, a missing kernel module, thermal throttling, an Xid. So check the host first, then the pod.

### 1.1 GB10 at a glance

| Property | DGX Spark (GB10 Grace Blackwell superchip) | Why you care in Kubernetes |
|---|---|---|
| CPU | 20 Arm cores: 10× Cortex-X925 + 10× Cortex-A725 | arm64 images only. CPU limits in whole-core terms (Chapter 14) |
| GPU | Blackwell, compute capability **12.1** (`sm_121`) | images and kernels must target sm_121 (or carry PTX) |
| Memory | **128 GB LPDDR5x, unified** (≈121.7 GiB visible to DGX OS), ~273 GB/s | no separate framebuffer. `nvidia-smi` memory reads **N/A**; the node's `memory` capacity is the GPU's memory too |
| CPU↔GPU link | NVLink-C2C (coherent, on package) | no PCIe copy between host RAM and "GPU RAM" |
| Tensor-core peak | up to 1 PFLOP FP4 (sparse) | FP4/FP8 quantised models are first-class (modules 03–06) |
| Network | ConnectX-7, 2× QSFP (200 GbE), 10 GbE RJ-45 | RDMA for 2-Spark NCCL (Chapter 18) |
| MIG | **not supported** | sharing = time-slicing / MPS (Chapter 16) |
| OS | DGX OS 7 (Ubuntu 24.04, arm64), open kernel modules | driver owned by DGX OS, not by the GPU Operator |

---

## 2. Architecture — HLD

```mermaid
flowchart TB
  subgraph PKG["GB10 superchip"]
    direction LR
    CPU["Grace CPU<br/>10× X925 + 10× A725"] <== "NVLink-C2C<br/>coherent" ==> GPU["Blackwell GPU<br/>sm_121 · 5th-gen tensor cores"]
    MEM[("128 GB LPDDR5x<br/>unified · ~273 GB/s")]
    CPU --- MEM
    GPU --- MEM
  end
  subgraph KERNEL["DGX OS kernel"]
    direction LR
    KM["nvidia.ko (open)<br/>nvidia-uvm · nvidia-modeset · nvidia-drm"]
    DEV["/dev/nvidia0 · /dev/nvidiactl<br/>/dev/nvidia-uvm*"]
  end
  subgraph USER["user space (host)"]
    direction LR
    LIB["libcuda.so / libnvidia-ml.so<br/>driver 580.x"]
    SMI["nvidia-smi · dcgmi"]
  end
  subgraph K8S["Kubernetes view"]
    direction LR
    CTK["nvidia-container-toolkit<br/>injects devices + libs"]
    DP["device plugin<br/>nvidia.com/gpu: 15 (time-sliced)"]
    GFD["GFD labels<br/>gpu.product=…GB10-SHARED · compute.major=12"]
  end
  GPU --> KM --> DEV
  KM --> LIB --> SMI
  DEV --> CTK
  LIB --> CTK
  LIB --> DP
  LIB --> GFD
  classDef gpu fill:#76b900,stroke:#3d6000,color:#000
  classDef node fill:#0e7c86,stroke:#064e54,color:#fff
  classDef ctrl fill:#1f6feb,stroke:#0b3d91,color:#fff
  classDef store fill:#bf8700,stroke:#7a5600,color:#fff
  class GPU,CTK,DP,GFD gpu
  class CPU,KM,DEV,LIB,SMI node
  class MEM store
  style PKG fill:#f4fbe8,stroke:#76b900,stroke-width:2px
  style KERNEL fill:#e6f4f5,stroke:#0e7c86
  style USER fill:#e6f4f5,stroke:#0e7c86
  style K8S fill:#f6f8fa,stroke:#57606a
```

---

## 3. LLD

### 3.1 Driver ↔ CUDA compatibility rules

| Rule | Practical meaning on the Spark |
|---|---|
| The **driver** (kernel module + `libcuda`) comes from the host | DGX OS 7 ships driver 580.x. The GPU Operator runs with `driver.enabled=false` (01-Ansible Chapter 20) |
| The **CUDA runtime/toolkit** comes from the container | `nvcr.io/nvidia/cuda:13.0.1-*`, NGC PyTorch 25.09 → CUDA 13 |
| Container CUDA major ≤ what the driver supports | driver 580 supports CUDA 13.0 → CUDA 12.x and 13.0 containers run. A CUDA 13.1+ container needs a newer driver (or forward-compat packages) |
| Kernels must include `sm_121` SASS or PTX for a lower arch | old wheels built only for sm_80/sm_90 without PTX fail with `no kernel image is available for execution on the device` |
| arm64 | an amd64-only image fails with `exec format error` before CUDA even loads |

### 3.2 What GFD/NFD labels (use them in selectors)

| Label | Example |
|---|---|
| `nvidia.com/gpu.product` | the product name the driver reports; with time-slicing GFD appends `-SHARED`. Because GFD owns and rewrites this label, the lab's selectors use `spark.lab/gpu=gb10`, which the 01-Ansible `kubeadm_cluster` role sets at join |
| `nvidia.com/gpu.compute.major` / `.minor` | `12` / `1` |
| `nvidia.com/cuda.driver-version.major` | `580` |
| `nvidia.com/gpu.count` | `1` |
| `nvidia.com/gpu.replicas` | `15` (time-slicing) |
| `nvidia.com/gpu.sharing-strategy` | `time-slicing` |
| `feature.node.kubernetes.io/cpu-model.vendor_id` | `ARM` |

### 3.3 Health signals

| Signal | Command | Healthy |
|---|---|---|
| GPU visible | `nvidia-smi -L` | `GPU 0: NVIDIA GB10 (UUID: GPU-…)` |
| Temperature / power | `nvidia-smi --query-gpu=temperature.gpu,power.draw --format=csv` | under ~85 °C under load |
| Throttle reasons | `nvidia-smi -q -d PERFORMANCE` | `Active` only for `Idle` / `Applications Clocks Setting` |
| Xid errors | `sudo dmesg -T \| grep -i xid` | none |
| Persistence / modules | `lsmod \| grep -E '^nvidia'` | `nvidia`, `nvidia_uvm`, `nvidia_modeset`, `nvidia_drm` |

---

## 4. Integrations

- **01-Ansible `spark_facts`** writes the same facts to `/etc/ansible/facts.d/spark.fact` (`ansible_local.spark.gpu.compute_cap == "12.1"`), and `playbooks/10.1-driver-audit.yml` holds NVIDIA packages so an `apt upgrade` can't break the Kubernetes layer.
- **Module 07-Nvidia** covers the silicon (SM, tensor cores, NVLink-C2C, UVM page faults) that this chapter only inventories.
- **Prometheus (Chapter 17)**: `spark_gpu_*` textfile metrics from the host, plus DCGM when GB10 support is confirmed.

---

## 5. Lab

### 5.1 Host inventory

#### What this lab does (commands on dgx-spark-1)

- **Read-only inventory:** asks the driver (`nvidia-smi`), the kernel (`lsmod`, `/proc/driver/nvidia/version`) and the OS (`lscpu`, `free`) what hardware and driver the Spark has.
- **Nothing is changed.** No package, file or Kubernetes object is touched.
- **Why first:** every later lab assumes these values. A wrong driver or a missing module here explains most GPU pod failures later.

#### What to look for

1. **Command exit:** every command prints output. `nvidia-smi` must print its table; a message like `couldn't communicate with the driver` means stop here.
2. **Compare the values with this table:**

   | Line/Field | Healthy value |
   |---|---|
   | `name`, `compute_cap` | `NVIDIA GB10`, `12.1` |
   | `driver_version` | `580.xx.xx` |
   | `pstate` at idle | `P8` (or another low state) |
   | `Memory-Usage` in the `nvidia-smi` table | `Not Supported`. This is correct on unified memory |
   | `lscpu` | `CPU(s): 20`, two model names: `Cortex-X925` and `Cortex-A725` |
   | `free -g`, `Mem:` total | about `121` |
   | `lsmod` | four lines: `nvidia`, `nvidia_uvm`, `nvidia_modeset`, `nvidia_drm` |

3. **Write down the GPU UUID** from `nvidia-smi -L` (§3.3). §5.3 checks that a pod sees the same one.

**If something's off:** no driver communication or missing modules → a DGX OS update did not finish; check `dmesg` and reboot. A driver other than 580.x → the CUDA rules in §3.1 change. More in §7.

#### Run it

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
nvidia-smi
nvidia-smi --query-gpu=name,compute_cap,driver_version,pstate,temperature.gpu,power.draw,clocks.sm,clocks.max.sm --format=csv
lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Socket'
free -g; lsmod | grep -E '^nvidia'
cat /proc/driver/nvidia/version
```

Expected highlights:

```text
| NVIDIA GB10   On  | … | Memory-Usage: Not Supported |
name, compute_cap, driver_version, pstate, …
NVIDIA GB10, 12.1, 580.xx.xx, P8, 38, 4.xx W, …
Model name: Cortex-X925  … Model name: Cortex-A725
CPU(s): 20
```

`Memory-Usage: Not Supported` is correct for a UMA GPU. Use `free -g` and `/proc/meminfo` for the shared pool.

### 5.2 What Kubernetes knows

#### What this lab does (kubectl, read-only)

- **Root view:** reads the labels GPU Feature Discovery (GFD) and Node Feature Discovery (NFD) wrote on `dgx-spark-1`, and the node's capacity and allocatable resources, from `spark-root`.
- **vCluster view:** reads the same node from inside `dev-llms`. vClusters have no real nodes; they show a copy of the root's node.
- **Nothing is changed.** These are `get` commands only.

#### What to look for

1. **Command exit:** all three commands print output. An empty label list means GFD has not run (Chapter 17).
2. **Labels** (first command):

   | Label | Healthy value |
   |---|---|
   | `nvidia.com/gpu.compute.major` / `.minor` | `12` / `1` |
   | `nvidia.com/cuda.driver-version.major` | `580` |
   | `nvidia.com/gpu.count` | `1` |
   | `nvidia.com/gpu.replicas` | `15` |
   | `nvidia.com/gpu.sharing-strategy` | `time-slicing` |
   | `feature.node.kubernetes.io/cpu-model.vendor_id` | `ARM` |

3. **Capacity vs allocatable** (second command): capacity `nvidia.com/gpu` `15`, `cpu` `20`, and `memory` close to the whole unified pool. Allocatable is lower by the kubelet reservations plus the hard-eviction threshold (3 CPU; 6 + 2 + 4 = 12 GiB): `cpu` `17`, `memory` about 109.7 GiB (shown in `Ki`), `nvidia.com/gpu` `15`.
4. **dev-llms view** (third command): one row, `dgx-spark-1`, with `GPU` `gb10` (kubectl names a label column after the last part of the key, here `spark.lab/gpu`), a `GPU.PRODUCT` ending in `-SHARED`, and `GPU.REPLICAS` `15`. dev-llms may still only use 2 slices: that limit is a quota, not a node property.

**If something's off:** `nvidia.com/gpu: 1` instead of 15 → the time-slicing ConfigMap is not applied (Chapter 16 §7). `No resources found` in dev-llms → node sync is off in the vCluster values (`lab/vclusters/dev-llms.yaml`). More in §7.

#### Run it

Only the root has real nodes; both vClusters show the same node, copied in (`sync.fromHost.nodes`):

```bash
# ▶ MacBook · technical-depth (repo root)
cd "02-Kubernetes/lab"
export KUBECONFIG="$PWD/../../01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml"
kubectl --context spark-root get node dgx-spark-1 -o json | jq '.metadata.labels | with_entries(select(.key|test("nvidia.com|cpu-model")))'
kubectl --context spark-root get node dgx-spark-1 -o jsonpath='{.status.capacity}{"\n"}{.status.allocatable}{"\n"}' | jq -c .
kubectl --context dev-llms get node dgx-spark-1 -L spark.lab/gpu,nvidia.com/gpu.product,nvidia.com/gpu.replicas
```

Capacity shows `nvidia.com/gpu: 15` and a `memory` figure that is the whole unified pool; allocatable is lower by the kubelet reservations (Chapter 14 §3.1). Inside dev-llms the node looks the same — 15 slices — although dev-llms may only use 2: budgets are quotas, not node properties. The LLM stack on the root sees the same 15 and is held to 6 (`llm-serving`) and 3 (`batch`) by namespace quotas.

### 5.3 The same view from inside a pod

#### What this lab does (manifest `lab/manifests/dev-lab/70-gpu/gpu-smoke.yaml`)

- **Creates one pod:** `gpu-smoke` in namespace `tenant-beta` inside the `dev-lab` vCluster. On the root its copy is `gpu-smoke-x-tenant-beta-x-dev-lab` in `vc-dev-lab`.
- **Asks for one GPU slice** (`nvidia.com/gpu: 1`, 100m CPU, 128Mi) and `runtimeClassName: nvidia`.
- **Runs two commands:** `nvidia-smi -L` and an `nvidia-smi` query of name, compute capability, driver and memory, then exits.
- **Cleans up:** the last command deletes the pod. The first run pulls the small `cuda:13.0.1-base` image.

#### What to look for

1. **Command exit:** `wait` prints `pod/gpu-smoke condition met` within 180 s.
2. **The log** has two parts:
   ```
   GPU 0: NVIDIA GB10 (UUID: GPU-…)
   name, compute_cap, driver_version, memory.total [MiB]
   NVIDIA GB10, 12.1, 580.xx.xx, [N/A]
   ```
   The UUID must match the one from §5.1. `[N/A]` for memory is correct on unified memory.

**If something's off:** pod `Pending` → a dev-lab GPU quota is used up: `tenant-beta` allows 1 slice and `vc-dev-lab` 2 (`kubectl --context dev-lab -n tenant-beta describe pod gpu-smoke`). `runtimeclass "nvidia" not found` → apply `manifests/dev-lab/00-platform` (Chapter 16 §5.2). `Failed to initialize NVML` → §7.

#### Run it

`gpu-smoke` is a tenant pod in dev-lab, so it exercises the whole chain: dev-lab API → syncer → root scheduler → kubelet → nvidia runtime.

```bash
# ▶ MacBook · 02-Kubernetes/lab
kubectl --context dev-lab apply -f manifests/dev-lab/70-gpu/gpu-smoke.yaml
kubectl --context dev-lab -n tenant-beta wait --for=jsonpath='{.status.phase}'=Succeeded pod/gpu-smoke --timeout=180s
kubectl --context dev-lab -n tenant-beta logs gpu-smoke
kubectl --context dev-lab -n tenant-beta delete pod gpu-smoke
```

### 5.4 Throughput baseline (single time-slice, no contention)

#### What this lab does (manifests `lab/manifests/root/70-gpu`)

- **Namespaces:** `manifests/root/00-platform` creates the root's platform namespaces (`observability`, `platform-tools`) if they are missing.
- **`manifests/root/70-gpu`:** creates the ConfigMap `gemm-bench` (holds `gemm_bench.py` and `compile_compare.py`) and the Deployment `gemm-contention` at **0 replicas**. Nothing runs yet; Chapter 16 scales it.
- **Job `gemm-solo`** in `platform-tools`: one GPU slice, 1 CPU, 4–8 GiB memory. It multiplies two 8192 × 8192 bf16 matrices 50 times and prints one JSON line with the achieved TFLOPS and the memory CUDA sees.
- **First run is slow:** it pulls the NGC PyTorch image (many GB) before the job starts.

#### What to look for

1. **Command exit:** the three `apply` commands print `created` (or `unchanged`/`configured` on a re-run). `kubectl --context spark-root -n platform-tools get job gemm-solo` ends with `COMPLETIONS 1/1`.
2. **The JSON line** from `logs -f`:

   | Field | Healthy value |
   |---|---|
   | `device`, `cc` | `NVIDIA GB10`, `12.1` |
   | `n` | `8192` |
   | `tflops` | a number above 0. Record it: this is baseline **B** for Chapter 16 |
   | `mem_total_gib` | `121.7` |
   | `mem_free_gib` | below the total; it moves as other pods in all three clusters allocate |

3. **Sanity check for B:** it is the same bf16 GEMM size that 01-Ansible template `11.2 CUDA smoke` runs (about 46.6 TFLOPS on this lab's Spark). A result far from your 11.2 figure means something else was using the GPU.

**If something's off:** `logs -f` returns `is waiting to start` → the image is still pulling; run it again in a minute. Job `Failed` with `no kernel image is available` → wrong image arch or CUDA (§5.6, §7). TFLOPS far below baseline → another pod or throttling: `kubectl --context spark-root get pods -A -o wide` and §5.5. More in §7.

#### Run it

The benchmark runs on the root in `platform-tools` — a platform job, outside any vCluster budget:

```bash
# ▶ MacBook · 02-Kubernetes/lab
kubectl --context spark-root apply -k manifests/root/00-platform
kubectl --context spark-root apply -k manifests/root/70-gpu        # ConfigMap with gemm_bench.py (+ contention Deployment at 0 replicas)
kubectl --context spark-root apply -f manifests/root/70-gpu/gemm-solo.yaml
kubectl --context spark-root -n platform-tools logs -f job/gemm-solo
```

```json
{"pod": "gemm-solo-…", "device": "NVIDIA GB10", "cc": "12.1", "n": 8192, "tflops": 46.4, "mem_total_gib": 121.7, "mem_free_gib": 101.2}
```

The TFLOPS figure is illustrative, so **record your own**. It's your "one tenant, no contention" baseline for Chapter 16. `mem_total_gib` from CUDA equals the whole UMA pool, and `mem_free_gib` moves as the OS and other pods — in all three clusters — allocate.

### 5.5 Watch clocks and power under load

#### What this lab does (job `gemm-solo` again, plus `nvidia-smi dmon`)

- **Re-runs the §5.4 benchmark:** deletes the finished `gemm-solo` job and creates it again.
- **Watches the GPU once per second** on the Spark with `nvidia-smi dmon`: power and temperature (`p`), utilization (`u`), clocks (`c`) and power/thermal violations (`v`).
- **Nothing is reconfigured.** `dmon` only reads; stop it with Ctrl-C.

#### What to look for

1. **Command exit:** `job.batch "gemm-solo" deleted`, then `job.batch/gemm-solo created`.
2. **In `dmon`, while the job runs:**

   | Column | Healthy value |
   |---|---|
   | `sm` (utilization) | near 100 during the GEMM, back near 0 after |
   | `pwr` | rises well above the idle few watts, drops after |
   | `gtemp` | stays below about 85 °C (§3.3) |
   | `pclk` | rises from idle to a steady high value and holds there |
   | `tviol` (thermal violations) | stays at 0 |

**If something's off:** `sm` stays at 0 → the job has not started yet (image pull) or failed (`kubectl --context spark-root -n platform-tools logs job/gemm-solo`). Clocks drop and thermal violations climb → airflow around the Spark; compare TFLOPS with your baseline. More in §7.

#### Run it

Start `dmon` first in its own terminal, then re-run the job from the MacBook:

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
nvidia-smi dmon -s pucv -d 1         # power, util, clocks, violations
```

```bash
# ▶ MacBook · 02-Kubernetes/lab
kubectl --context spark-root -n platform-tools delete job gemm-solo
kubectl --context spark-root apply -f manifests/root/70-gpu/gemm-solo.yaml
```

### 5.6 Break it: wrong architecture / wrong CUDA

#### What this lab does (pod `arch-test` in dev-lab)

- **Creates one plain pod** `arch-test` in `lab-tools` inside `dev-lab`, from an amd64-only busybox image. It asks for no GPU.
- **Shows the failure** you get when an image has no arm64 build: the Spark's Arm CPU cannot run the binary.
- **Cleans up:** the last command deletes the pod.

#### What to look for

1. **Pod status:** `kubectl --context dev-lab -n lab-tools get pod arch-test` shows `Error`.
2. **The log:** `exec /bin/uname: exec format error`. This failure happens before any CUDA code loads.

**If something's off:** `ErrImagePull` with `no match for platform in manifest` → containerd refused to pull an image with no arm64 variant; same root cause, caught one step earlier. The log prints `x86_64` → the Spark has amd64 emulation registered (`ls /proc/sys/fs/binfmt_misc/` on the Spark), so the test can't show the failure. More in §7.

#### Run it

```bash
# ▶ MacBook · 02-Kubernetes/lab
# amd64-only image on arm64 → exec format error (a plain pod in dev-lab, no GPU needed)
kubectl --context dev-lab -n lab-tools run arch-test --image=docker.io/amd64/busybox:1.37 --restart=Never -- uname -m
sleep 10; kubectl --context dev-lab -n lab-tools logs arch-test; kubectl --context dev-lab -n lab-tools delete pod arch-test
```

Expected: `exec /bin/uname: exec format error`, with the pod in `Error`. Everything in this lab pins multi-arch or arm64 images for this reason.

---

## 6. Verify

| Check | Expected |
|---|---|
| `nvidia-smi --query-gpu=compute_cap --format=csv,noheader` | `12.1` |
| node labels `spark.lab/gpu` / `nvidia.com/gpu.product` | `gb10` / GFD's product name with `-SHARED` (on the root, and the same in both vClusters) |
| allocatable `nvidia.com/gpu` | `15` |
| `gpu-smoke` log | lists GB10 with the same UUID as the host |
| `gemm-solo` | JSON line with `cc: 12.1`. TFLOPS recorded as your baseline |
| `sudo dmesg -T \| grep -ci xid` | `0` |

---

## 7. Troubleshooting

| Symptom | Layer | Diagnose | Fix |
|---|---|---|---|
| `nvidia-smi: NVIDIA-SMI has failed…couldn't communicate with the driver` (host) | kernel module | `lsmod`, `dmesg`, `systemctl status nvidia-persistenced` | DGX OS update finished? Reboot. Re-install the DGX OS driver packages, not a runfile |
| Pod: `Failed to initialize NVML: Unknown Error` | cgroup device access after a systemd reload | `kubectl --context <cluster> logs`. Toolkit version | upgrade nvidia-container-toolkit. Use CDI mode (Chapter 16) |
| `no kernel image is available for execution on the device` | wheel/kernel lacks sm_121 | `python -c "import torch;print(torch.cuda.get_arch_list())"` | use NGC images or wheels built for sm_120/121 (+PTX) |
| `CUDA driver version is insufficient for CUDA runtime version` | container CUDA newer than the driver | compare `nvidia-smi` "CUDA Version" and the image's CUDA | older image tag, or upgrade DGX OS |
| `exec format error` | amd64 image | `docker manifest inspect` | arm64/multi-arch tag |
| Throughput half of baseline | thermal/power throttling or another tenant | `nvidia-smi -q -d PERFORMANCE`; GPU pods of *all* clusters: `kubectl --context spark-root get pods -A -o wide` (model pods in `llm-serving`/`batch`, vCluster pods in `vc-*`) | airflow. Chapter 16 contention |
| `Xid 13/31/43` in dmesg | app fault (bad kernel, illegal address) | `dmesg -T \| grep -i xid` | fix the workload. The node is fine |
| `Xid 79` / `GPU has fallen off the bus` / `48` | hardware / severe | same | drain + reboot (01-Ansible Chapter 29, role `node_drain` — on one Spark that stops all three clusters). If it repeats, open an RMA |

---

## 8. Scale-out path

| Spark | Datacenter DGX (e.g. B200/GB200) |
|---|---|
| 1 GPU, UMA, no MIG | 8 GPUs/node with HBM, NVLink/NVSwitch domain, MIG available |
| host-managed driver | GPU Operator may own the driver (precompiled/signed), or the OS image owns it (DGX OS, BCM) |
| `nvidia.com/gpu: 15` time-sliced, split by quota between clusters | whole GPUs or MIG profiles (`nvidia.com/mig-1g.23gb`), DRA claims |
| manual health checks | DCGM diagnostics (`dcgmi diag -r 3`) as a pre-admission gate, node-problem-detector, automated remediation |

---

## 9. Checklist

- [ ] I can state GB10's compute capability, core layout and memory model, and why `nvidia-smi` shows memory N/A.
- [ ] I know which parts of the CUDA stack come from the host and which from the image.
- [ ] I have a recorded single-slice GEMM baseline.
- [ ] I can triage a GPU pod failure to host driver, toolkit, image arch or CUDA version.
