# Chapter 16 · GPUDirect Storage & cuFile on a Unified-Memory Machine: Detect, Configure, Measure

> **01-Ansible · Part III — Fabric & storage · Chapter 16 of 30** · ← [Chapter 15 · NFS over RDMA & parallel file systems](15-nfs-rdma-and-parallel-file-systems.md) · [All chapters](00-ansible-step-by-step-guide.md) · [Chapter 17 · Vault server deep dive](17-vault-server-deep-dive.md) →

| | |
|---|---|
| **You will build** | A data-driven answer to "does GDS help on my Spark?": platform detection (`gdscheck`), a managed `/etc/cufile.json`, and a three-way `gdsio` benchmark (GPU-direct vs CPU-only vs bounce) against the local NVMe, plus a model-loading comparison |
| **Hardware** | 1× DGX Spark |
| **Time** | 45 min |
| **Risk** | Low. Writes an 8 GiB test file and drops page caches during the benchmark |

---

## 1. First principles, applied to GB10

**What GDS solves on a discrete-GPU server (DGX H100/B200):** NVMe or a NIC DMAs **directly into GPU HBM** through PCIe peer-to-peer (via `nvidia-fs.ko`), skipping the "bounce buffer" in CPU RAM. That saves a memory copy and CPU cycles, and matters at 10s of GB/s.

**What's different on DGX Spark:** there's **no separate HBM**. The Blackwell GPU and Grace CPU share one coherent LPDDR5x pool over NVLink-C2C. When NVMe DMAs a file into memory, that memory is already GPU-addressable. So:

| Question | Discrete GPU (HBM) | GB10 (UMA) |
|---|---|---|
| Is there a bounce copy without GDS? | Yes (host RAM → HBM) | Only if your loader copies explicitly (e.g. `.to('cuda')` on a pageable tensor may still copy within the same pool) |
| What does `nvidia-fs` add? | P2P DMA to HBM | Little or nothing. Expect cuFile to run in **compatibility mode** (POSIX I/O under the cuFile API) |
| Where's the bottleneck? | PCIe/NVMe vs HBM bandwidth | **NVMe read speed** and the page cache (which competes for the same unified pool) |
| What should you optimise? | Enable GDS end to end | Avoid double copies and double buffering: `mmap`/safetensors zero-copy, `O_DIRECT` reads so the page cache doesn't eat GPU-usable memory |

The cuFile **API** is still worth knowing. Libraries like KvikIO and some loaders use it, and the same code gets real GDS when it runs on a data-centre DGX.

---

## 2. Architecture

```mermaid
flowchart LR
  subgraph APP["Loader (PyTorch / KvikIO / gdsio)"]
    CF["cuFileRead()"]
    POSIX["read()/mmap"]
  end
  subgraph LIB["libcufile.so"]
    DEC{"nvidia-fs available<br/>and FS supported?"}
  end
  CF --> DEC
  DEC -- yes --> P2P["P2P DMA → GPU memory<br/>(discrete GPUs)"]
  DEC -- "no / allow_compat_mode" --> COMPAT["Compat mode:<br/>POSIX pread into a buffer → cudaMemcpy<br/>(same pool on GB10)"]
  POSIX --> PC["Page cache (unified pool!)"] --> MEM[("128 GB LPDDR5x<br/>CPU + GPU")]
  P2P --> MEM
  COMPAT --> MEM
  NVME[("Internal NVMe")] --> DEC
  NVME --> PC
```

| File | Purpose |
|---|---|
| `/etc/cufile.json` | cuFile config: `allow_compat_mode`, logging, I/O sizes (managed by the play; a backup is kept) |
| `/usr/local/cuda/gds/tools/gdscheck[-p]` | Platform capability report |
| `/usr/local/cuda/gds/tools/gdsio` | Benchmark tool with transfer types (`-x 0` GPU direct, `-x 1` CPU only, `-x 2` CPU→GPU) |

### 2.1 One GPU-direct read on the Spark, step by step

What happens during the `-x 0` read test of `16.1 GDS check` on GB10:

```mermaid
sequenceDiagram
  autonumber
  participant P as 16.1 task<br/>Read tests (item 0)
  participant G as gdsio -x 0<br/>(8 threads, 1M I/O)
  participant L as libcufile.so
  participant CJ as /etc/cufile.json
  participant FS as nvidia_fs module
  participant N as NVMe<br/>/srv/models/gdsio.bin
  participant M as LPDDR5x pool<br/>(CPU + GPU)
  P->>P: sync and echo 3 > /proc/sys/vm/drop_caches
  P->>G: gdsio -f /srv/models/gdsio.bin -x 0 -I 0 -T 15
  G->>L: cuFileDriverOpen
  L->>CJ: read allow_compat_mode true,<br/>log dir /var/log/cufile
  L->>FS: is nvidia_fs loaded?
  FS-->>L: no (gds_summary.nvidia_fs_loaded false)
  G->>L: register the file and a GPU buffer
  loop 15 s
    G->>L: cuFileRead 1M
    L->>N: compat mode: POSIX pread
    N-->>M: DMA into a host bounce buffer
    L->>M: cudaMemcpy bounce buffer → GPU buffer<br/>(same physical pool on GB10)
  end
  G-->>P: Throughput: … GiB/s → bench.gpu_direct
```

- **Step 1** makes every read test cold. Without it, the 2nd and 3rd tests would read the page cache, not the NVMe.
- **Steps 5–6** are what `nvidia_fs_loaded` reports. On a DGX H100 with `nvidia_fs` loaded, steps 9–11 become one P2P DMA into HBM.
- **Steps 9–11 are compat mode.** `-x 2` (`cpu_gpu_bounce`) does the same pread and copy in gdsio itself, so on the Spark `gpu_direct` and `cpu_gpu_bounce` land close together.
- **Step 12** is the line the *Add results* task picks with its `Throughput:` regex. An error anywhere before it gives `n/a`; the reason is in `/var/log/cufile/cufile.log` (§5).

---

## 3. Hands-on

#### What this lab does (template `16.1 GDS check`)

- **Finds the GDS tools** (`gdscheck`, `gdsio`) under `/usr/local/cuda/gds/tools`. If they're missing it installs a `gds-tools…` package when the apt repository offers one; otherwise it carries on without them.
- **Platform check:** runs `gdscheck -p` and checks whether the `nvidia_fs` kernel module is loaded. Read-only.
- **`/etc/cufile.json`:** writes the cuFile config (compat mode allowed, errors logged to `/var/log/cufile`). The previous file is kept as a backup (`cufile.json.*~`).
- **Benchmark (only if `gdsio` exists):** writes an 8 GiB test file `/srv/models/gdsio.bin` once, then reads it three times for 15 s each (GPU-direct, CPU-only, CPU→GPU bounce), dropping the page cache before each read.
- **One Spark is enough.** Nothing else is reconfigured; the test file stays in `/srv/models` until you delete it.

#### What to look for

1. **Semaphore run:** `failed=0`. A first run shows `changed` on `/etc/cufile.json` and the test file (plus the package, if it was installed); a re-run shows `changed=0` on `dgx-spark-1` (`localhost` still shows the new certificate from play 1).
2. **The *Report* task** prints `gds_summary`. Expected on GB10 (unified memory):

   | Field | Expected on this Spark |
   |---|---|
   | `nvidia_fs_loaded` | `false` |
   | `compat_mode` | `true` |
   | `nvme_supported` | `false` is normal here (no P2P path) |
   | `bench.gpu_direct`, `bench.cpu_only`, `bench.cpu_gpu_bounce` | three throughputs (`… GiB/s`), close to each other and near your NVMe's sequential read speed |
   | `raw_lines` | the `gdscheck` lines about NVMe, compat mode, IOMMU and the driver |

3. **`n/a` in `bench`:** the read tests are allowed to fail without failing the play, so check by eye. `n/a` for `gpu_direct` alone can happen here: `-x 0` may error when the file system isn't GDS-capable (§5, details in `/var/log/cufile/cufile.log`), and the other two are then your baselines. No `bench` key at all means `gdsio` wasn't found, so there was no benchmark.
4. **Record the three numbers** next to your Chapter 11 baselines. They are the "before" values for the model-load test in §3.1.

**If something's off:** `gdscheck not found` and no package → this DGX OS image has no GDS tools; the cuFile API isn't needed on this box (table below). Numbers that vary a lot between runs → another container was loading a model at the same time. More in §5.

```yaml
# lab/playbooks/16.1-gds-check.yml
---
# GPUDirect Storage / cuFile on DGX Spark: detect, configure, measure.
# On GB10 the "GPU memory" IS system memory (coherent UMA), so the question is
# not "can NVMe DMA into the GPU BAR" but "what path does cuFile take, and is it
# faster than plain POSIX reads for your loader?" This play answers that with data.
- name: Short-lived SSH certificate from vault01 (Semaphore runs only)
  ansible.builtin.import_playbook: 00-vault-cert.yml

- name: GDS / cuFile assessment
  hosts: spark
  become: true
  gather_facts: false
  vars:
    gds_test_dir: /srv/models
    gds_test_size: 8G
    gds_io_size: 1M
    gds_threads: 8
  tasks:
    - name: Find GDS tools shipped with the CUDA toolkit
      ansible.builtin.find:
        paths: [/usr/local/cuda/gds/tools]
        patterns: [gdscheck, gdscheck.py, gdsio]
      register: gds_tools

    - name: Offer the package if tools are missing
      ansible.builtin.shell: set -o pipefail; apt-cache search --names-only '^(nvidia-gds|gds-tools)' | awk '{print $1}'
      args: { executable: /bin/bash }
      register: gds_pkgs
      changed_when: false
      when: gds_tools.matched < 2

    - name: Install GDS userspace (if the repo offers it)
      ansible.builtin.apt:
        name: "{{ gds_pkgs.stdout_lines | select('match', '^gds-tools') | list | last }}"
        state: present
      when:
        - gds_tools.matched < 2
        - gds_pkgs.stdout_lines | select('match', '^gds-tools') | list | length > 0

    - name: Platform check
      ansible.builtin.shell: |
        set -o pipefail
        T=/usr/local/cuda/gds/tools
        if [ -x $T/gdscheck ]; then $T/gdscheck -p; elif [ -f $T/gdscheck.py ]; then python3 $T/gdscheck.py -p; else echo "gdscheck not found"; fi
      args: { executable: /bin/bash }
      register: gds_check
      changed_when: false
      failed_when: false

    - name: Is nvidia-fs loaded?
      ansible.builtin.command: lsmod
      register: gds_lsmod
      changed_when: false

    - name: Summarise platform
      ansible.builtin.set_fact:
        gds_summary:
          nvidia_fs_loaded: "{{ 'nvidia_fs' in gds_lsmod.stdout }}"
          nvme_supported: "{{ gds_check.stdout is search('NVMe\\s*:\\s*Supported') }}"
          compat_mode: "{{ gds_check.stdout is search('(?i)compat') }}"
          raw_lines: "{{ gds_check.stdout_lines | select('search', '(?i)nvme|compat|iommu|platform|driver') | list }}"

    - name: Configure /etc/cufile.json (allow compat mode, file logging)
      ansible.builtin.copy:
        dest: /etc/cufile.json
        backup: true
        mode: "0644"
        content: "{{ gds_cufile | to_nice_json }}"
      vars:
        gds_cufile:
          logging: { dir: /var/log/cufile, level: ERROR }
          properties:
            allow_compat_mode: true
            max_direct_io_size_kb: 16384
            max_device_cache_size_kb: 131072
            use_poll_mode: false
          fs: { generic: { posix_unaligned_writes: false } }

    - name: Benchmark (only if gdsio exists)
      when: gds_tools.files | map(attribute='path') | select('search', 'gdsio$') | list | length > 0
      block:
        - name: Test file directory
          ansible.builtin.file:
            path: "{{ gds_test_dir }}"
            state: directory
            mode: "0775"

        - name: Write the test file once (-I 1 = write)
          ansible.builtin.command: >-
            /usr/local/cuda/gds/tools/gdsio -f {{ gds_test_dir }}/gdsio.bin -d 0 -w {{ gds_threads }}
            -s {{ gds_test_size }} -i {{ gds_io_size }} -x 1 -I 1
          args:
            creates: "{{ gds_test_dir }}/gdsio.bin"

        - name: Read tests — 0=GPU_DIRECT, 1=CPU_ONLY, 2=CPU_GPU (bounce)
          ansible.builtin.shell: |
            set -o pipefail
            sync; echo 3 > /proc/sys/vm/drop_caches
            /usr/local/cuda/gds/tools/gdsio -f {{ gds_test_dir }}/gdsio.bin -d 0 -w {{ gds_threads }} \
              -s {{ gds_test_size }} -i {{ gds_io_size }} -x {{ item }} -I 0 -T 15 | grep -E 'Throughput'
          args: { executable: /bin/bash }
          loop: [0, 1, 2]
          register: gds_bench
          changed_when: false
          failed_when: false

        - name: Add results
          ansible.builtin.set_fact:
            gds_summary: >-
              {{ gds_summary | combine({'bench': dict(['gpu_direct', 'cpu_only', 'cpu_gpu_bounce']
                 | zip(gds_bench.results | map(attribute='stdout') | map('regex_search', 'Throughput: ([\d.]+ \w+/s)', '\1')
                 | map('default', ['n/a'], true) | map('first')))}) }}

    - name: Report
      ansible.builtin.debug:
        var: gds_summary
```

#### Run it

**Semaphore UI:** run the template **`16.1 GDS check`**. Break-glass from the MacBook:

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
```

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/16.1-gds-check.yml -K
```

Read the result:

| Outcome | Interpretation |
|---|---|
| `nvidia_fs_loaded: false`, `compat_mode: true`, GPU-direct ≈ CPU-only | Expected on UMA. cuFile works, but there's no true P2P path. Your loader's efficiency is what matters |
| GPU-direct noticeably slower than CPU-only | Compat-mode overhead. Use plain `O_DIRECT`/mmap loaders on the Spark |
| Everything ≈ your NVMe's rated sequential read | You're storage-bound. Faster models-per-minute means fewer bytes (quantised weights) or a cache (Chapter 15) |
| `gdscheck not found` and no package | This DGX OS image has no GDS tools. Skip; the cuFile API isn't needed on this box |

### 3.1 The comparison that actually matters: model load time

#### What this lab does (exercise: safetensors load time)

- **Loads one shard to the GPU:** inside the NGC PyTorch container, reads the first `*.safetensors` file under `/srv/models` straight to `cuda` and prints its size, the time and the GB/s. Nothing is installed or changed.
- **Needs a model in `/srv/models`:** the Qwen2.5-7B download from Chapter 15 §3.1 works on one Spark.
- **Cold vs warm:** you run it once after dropping the page cache and once again right after.

#### What to look for

1. **The printed line** `/models/…safetensors: X GB in Y s -> Z GB/s`, for a cold and a warm run.
2. **Cold run:** close to the `gdsio` numbers from the template, i.e. limited by the NVMe.
3. **Warm run:** much faster, because the weights are in the page cache.
4. **The cost of the warm run:** `free -g` on the Spark before and after: `buff/cache` grows by about the shard's size. On unified memory that cache uses the same pool the GPU allocates from.

Drop the cache between runs (on the host, not in the container):

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
sync && echo 3 | sudo tee /proc/sys/vm/drop_caches && free -g
```

**If something's off:** `IndexError` → no `*.safetensors` under `/srv/models` yet (Chapter 15 §3.1). CUDA out of memory → other models are loaded; check `free -g` and §5.

#### Run it

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
# the script runs inside an NGC PyTorch container, with /srv/models mounted
docker run --rm --gpus all -v /srv/models:/models nvcr.io/nvidia/pytorch:25.11-py3 python - <<'PY'
import time, torch, os, glob
from safetensors.torch import load_file
f = sorted(glob.glob("/models/**/*.safetensors", recursive=True))[0]
os.system("sync")  # (drop caches from the host between runs: echo 3 > /proc/sys/vm/drop_caches)
t=time.time(); sd = load_file(f, device="cuda"); torch.cuda.synchronize(); dt=time.time()-t
gb = sum(v.numel()*v.element_size() for v in sd.values())/1e9
print(f"{f}: {gb:.2f} GB in {dt:.2f}s -> {gb/dt:.2f} GB/s")
PY
```

Run it cold (after dropping caches) and warm. On a UMA machine the warm run is fast because the page cache already holds the weights, **but that cache is now occupying memory the model also needs**. That trade-off is the operational lesson of this step.

---

## 4. Integrations

| System | Relevance |
|---|---|
| Telemetry (Chapter 12) | `spark_uma_page_cache_bytes` shows how much of the unified pool the file cache holds after loads |
| Emergency runbook (Chapter 29) | "UMA pressure" remediation drops caches, which is safe but makes the next load cold |
| NFS/RDMA cache (Chapter 15) | For a second Spark, a shared model store avoids downloading twice; cuFile over NFS runs in compat mode too |
| Data-centre DGX | The same `16.1-gds-check.yml` on an H100/B200 node should show `nvidia_fs_loaded: true`, NVMe supported, and GPU-direct > bounce. Keep the playbook for that day |

## 5. Troubleshooting & diagnostics

| Symptom | Diagnose | Fix |
|---|---|---|
| `cuFileDriverOpen` fails in an app | `/var/log/cufile/cufile.log`; `gdscheck -p` | Set `allow_compat_mode: true` (the play does this) |
| `gdsio` error `-5` / `EIO` on `-x 0` | cufile log | FS/device not GDS-capable, which is expected here; use `-x 1/2` for baselines |
| Benchmarks vary wildly run to run | Page cache | The play drops caches before each read test; don't benchmark while other containers load models |
| Model loads fast, then CUDA OOM at "half-full" | `free -g` → `buff/cache` high | UMA: the page cache holds the weights. `echo 3 > /proc/sys/vm/drop_caches` after loading, or use `O_DIRECT` loaders |
| `apt` has no gds package | `apt-cache search gds` | Not shipped for this platform/release. Nothing to fix |

## 6. Validation

- [ ] `gds_summary` recorded for your Spark (flags + the three throughputs).
- [ ] Cold and warm safetensors load times recorded, with page-cache size before and after.
- [ ] You can explain to a colleague why GDS helps an H100 box and does little on a GB10.
