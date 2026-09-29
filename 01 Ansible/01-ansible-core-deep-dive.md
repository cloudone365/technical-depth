# Volume 01A — Ansible Core on DGX Spark: Control Node, Inventory & First Contact

> **Module 01 · Part I — Foundations** · Lab code: [`lab/`](lab/) · Next: [01B Execution internals & debugging](01-ansible-core-engine-and-execution-internals.md)

| | |
|---|---|
| **You will build** | A reproducible Ansible control node, an inventory that describes your Spark(s), and the first three playbooks: connectivity, custom GPU facts, OS baseline |
| **Hardware** | 1× DGX Spark (2× optional) + a control node (laptop, VM, WSL, or the Spark itself) |
| **Time** | 60–90 min |
| **Risk** | Low — read-only until `01-baseline.yml`, which only adds packages/config drop-ins |

---

## 1. Why Ansible on a single desktop box?

A DGX Spark looks like a workstation, but you'll treat it like a data-centre node: it runs DGX OS (Ubuntu 24.04 based, aarch64), a GB10 Grace Blackwell superchip (20 Arm cores + Blackwell GPU sharing **128 GB of coherent unified LPDDR5x memory**), a ConnectX-7 NIC with two 200 Gb/s QSFP cages, and eventually k3s, Slurm, Vault and a monitoring stack. Ansible gives you:

- **Rebuild in minutes after a re-image or a bad DGX OS update.** The playbooks describe the machine, so recovery doesn't depend on remembering what you changed.
- **Moving from one Spark to two (or four) is a one-line inventory change.** You don't have to repeat every manual step on the new node.
- **Drift detection:** `--check --diff` shows what changed behind your back (Volume 22).
- **The same muscle memory you'll use on DGX/HGX clusters.** Only the inventory gets bigger.

---

## 2. Architecture

### 2.1 High-level design (HLD)

```mermaid
flowchart LR
  subgraph CN["Control node"]
    direction TB
    CFG["ansible.cfg<br/>(pipelining, ControlPersist,<br/>fact cache, log_path)"]
    INV["inventory/<br/>hosts.yml · group_vars · host_vars"]
    PB["playbooks/ + roles/"]
    VENV["Python venv<br/>ansible-core 2.18 + collections"]
  end
  subgraph SP["DGX Spark (managed node)"]
    direction TB
    SSHD["sshd :22"]
    PY["/usr/bin/python3 (3.12)"]
    FACTS["/etc/ansible/facts.d/spark.fact"]
    OS["DGX OS 7 · apt · systemd · netplan"]
  end
  PB -->|"SSH (key auth)<br/>+ sudo"| SSHD --> PY --> OS
  PY --> FACTS
```

Ansible is **agentless**. Nothing runs on the Spark between plays. Each task is a small Python program that is shipped over SSH, run by `/usr/bin/python3`, and returns JSON.

### 2.2 Low-level design (LLD)

| Item | Value in this lab | Why |
|---|---|---|
| Managed-node user | `nvidia` (same on every Spark) | NVIDIA's multi-Spark playbooks and MPI assume identical usernames |
| Privilege | `become: true` via `sudo` (group_vars/spark.yml) | Everything we configure is root-owned |
| Python on target | `/usr/bin/python3` pinned in `ansible.cfg` | Avoids interpreter discovery warnings; DGX OS ships 3.12 |
| Mgmt network | `enP7s7` 10GbE, `10.10.10.0/24` | Ansible/SSH traffic never rides the CX-7 fabric |
| Fabric | CX-7 `enp1s0f1np1` / `enP2p1s0f1np1`, `192.168.100/101.0/24` | Workload traffic only (NCCL, NFS/RDMA, flannel) |
| Fact cache | `jsonfile` in `lab/.cache/facts`, 2 h | Ad-hoc runs and drift reports reuse facts |
| Run log | `lab/.cache/ansible.log` | Free audit trail (Volume 23) |

**Inventory model:** one *hardware* group (`spark`) plus *functional* groups (`k3s_server`, `slurm_compute`, `vault`, `monitoring`…). A host can belong to several. Roles target functional groups, so moving Vault to another box is an inventory edit, not a code change.

```mermaid
flowchart TB
  all --> spark
  all --> k3s_server & k3s_agent & slurm_controller & slurm_compute & vault & monitoring & nfs_server & nfs_client
  spark --> s1[spark-01] & s2[spark-02]
  k3s_server --> s1
  k3s_agent --> s2
  slurm_compute --> spark
  vault --> s1
  monitoring --> s1
  nfs_server --> s1
  nfs_client --> s2
```

**Variable precedence you'll actually use** (low → high):
`role defaults` → `inventory group_vars/all` → `group_vars/<group>` → `host_vars/<host>` → play `vars` → `-e extra vars`.
Rule of thumb: **hardware facts in `group_vars/spark.yml`, per-box addressing in `host_vars`, knobs in role `defaults`, one-off overrides with `-e`.**

---

## 3. Hands-on lab

### Step 1 — Build the control node

```bash
# Any Linux/macOS/WSL box with Python ≥ 3.11 (or spark-01 itself)
git clone https://github.com/cloudone365/technical-depth.git
cd "technical-depth/01 Ansible/lab"
python3 -m venv ~/.venvs/spark-ansible
source ~/.venvs/spark-ansible/bin/activate
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml -p ./collections
ansible --version        # expect: core 2.18.x, config file = .../lab/ansible.cfg
```

> **Using the Spark as its own control node?** That works. In `hosts.yml` set `ansible_connection: local` for `spark-01`. You lose nothing except the "rebuild from outside" property, so keep a copy of the repo elsewhere.

```ini
# lab/ansible.cfg
# ansible.cfg — DGX Spark lab control-node configuration
# Every setting here is explained in Volume 01 (core) and Volume 02 (performance).
[defaults]
inventory               = ./inventory
inventory_plugins       = ./inventory_plugins
roles_path              = ./roles
collections_path        = ./collections:~/.ansible/collections
remote_user             = nvidia
forks                   = 10
interpreter_python      = /usr/bin/python3
host_key_checking       = True
retry_files_enabled     = False
stdout_callback         = ansible.builtin.default
callback_result_format  = yaml
callbacks_enabled       = ansible.posix.profile_tasks, ansible.posix.timer
# Fact cache: lets ad-hoc runs and drift reports reuse facts without re-gathering
gathering               = smart
fact_caching            = jsonfile
fact_caching_connection = ./.cache/facts
fact_caching_timeout    = 7200
# Log every run to a file — the cheapest audit trail you will ever build (Volume 23)
log_path                = ./.cache/ansible.log
# Vault password comes from a script so it never sits in plain text (Volume 19)
# vault_password_file   = ./tools/vault-pass.sh
nocows                  = True

[inventory]
enable_plugins          = ansible.builtin.yaml, ansible.builtin.ini, spark_mdns, ansible.builtin.constructed, ansible.builtin.auto

[privilege_escalation]
become                  = False
become_method           = sudo

[ssh_connection]
# ControlPersist keeps one TCP+SSH session per host alive for 10 minutes.
ssh_args                = -o ControlMaster=auto -o ControlPersist=600s -o ServerAliveInterval=30
control_path_dir        = ~/.ansible/cp
# Pipelining removes the "copy module to /tmp then execute" round trips.
# Requires 'Defaults !requiretty' in sudoers (the Ubuntu default).
pipelining              = True

[diff]
always                  = False
context                 = 3
```

### Step 2 — SSH trust

```bash
ssh-keygen -t ed25519 -C "ansible@control"          # if you don't have a key
ssh-copy-id nvidia@10.10.10.11
ssh-copy-id nvidia@10.10.10.12                        # second Spark, if any
ssh nvidia@10.10.10.11 'hostname; uname -m; cat /etc/dgx-release | head -3'
```

Expected: `aarch64` and a `DGX_*` release line. If `/etc/dgx-release` is missing, you're not on DGX OS. The lab still runs, but the version checks in `spark_validate` will warn.

### Step 3 — Describe your hardware (inventory)

Find the real CX-7 interface names **on each Spark** before editing host_vars:

```bash
ssh nvidia@10.10.10.11 ibdev2netdev
# rocep1s0f0 port 1 ==> enp1s0f0np0 (Down)
# rocep1s0f1 port 1 ==> enp1s0f1np1 (Up)       <- cable is in this cage
# roceP2p1s0f0 port 1 ==> enP2p1s0f0np0 (Down)
# roceP2p1s0f1 port 1 ==> enP2p1s0f1np1 (Up)   <- same cage, second PCIe root
```

Each physical QSFP cage shows up as **two** netdevs (`enp1s0f1np1` and `enP2p1s0f1np1`) because the CX-7 is attached through two PCIe roots. Address both to get the full 200 Gb/s.

```yaml
# lab/inventory/hosts.yml
---
# Static inventory for the DGX Spark lab.
#
#   Single-Spark mode: delete spark-02 (or leave it commented) — every playbook
#   works on one node; 2-node sections are skipped automatically.
#
#   Management network (10GbE RJ-45, enP7s7) : 10.10.10.0/24
#   CX-7 fabric (QSFP, direct cable)          : 192.168.100.0/24 + 192.168.101.0/24
all:
  children:
    # The control node itself, declared explicitly so `--limit localhost` works
    # and it gets a sane Python (implicit localhost can't be targeted by --limit).
    control:
      hosts:
        localhost:
          ansible_connection: local
          ansible_python_interpreter: "{{ ansible_playbook_python }}"
    spark:
      hosts:
        spark-01:
          ansible_host: 10.10.10.11
        spark-02:
          ansible_host: 10.10.10.12

    # ---- functional groups (a host can be in several) -------------------
    k3s_server:
      hosts:
        spark-01:
    k3s_agent:
      hosts:
        spark-02:
    slurm_controller:
      hosts:
        spark-01:
    slurm_compute:
      children:
        spark:
    vault:
      hosts:
        spark-01:
    monitoring:
      hosts:
        spark-01:
    nfs_server:
      hosts:
        spark-01:
    nfs_client:
      hosts:
        spark-02:
```

```yaml
# lab/inventory/group_vars/spark.yml
---
# ------------------------------------------------------------------------
# Hardware invariants for every DGX Spark (GB10 Grace Blackwell).
# These are the "golden values" the validate + drift playbooks enforce.
# ------------------------------------------------------------------------
ansible_become: true

spark_expected:
  arch: aarch64
  cpu_cores: 20                  # 10x Cortex-X925 + 10x Cortex-A725
  gpu_name_regex: "GB10"
  cuda_major: 13                 # DGX OS 7.x ships CUDA 13.x
  driver_major_min: 580
  compute_capability: "12.1"     # sm_121 — use this in NVCC_GENCODE
  mem_total_gib_min: 110         # 128 GB LPDDR5x, some reserved by firmware
  os_family: Debian
  os_major: "24"                 # DGX OS 7 is Ubuntu 24.04 based

# Packages every node gets (Volume 06/07)
spark_base_packages:
  - chrony
  - jq
  - htop
  - nvtop
  - tmux
  - ethtool
  - pciutils
  - rdma-core
  - ibverbs-utils
  - infiniband-diags
  - perftest
  - python3-pip
  - python3-venv
  - acl                      # needed for become_user to unprivileged users

# NVIDIA packages we never let a random 'apt upgrade' move (Volume 07)
spark_hold_nvidia_packages: true
spark_nvidia_hold_regex: '^(nvidia-driver-|nvidia-dkms-|nvidia-kernel-|libnvidia-|nvidia-firmware-|cuda-drivers)'

# Kernel / sysctl tuning (Volume 06, 12)
spark_sysctls:
  vm.swappiness: 10
  vm.max_map_count: 1048576          # large mmap'ed model weights
  fs.inotify.max_user_watches: 1048576
  fs.inotify.max_user_instances: 8192 # k3s + many containers
  net.core.rmem_max: 268435456
  net.core.wmem_max: 268435456
  net.ipv4.tcp_rmem: "4096 87380 268435456"
  net.ipv4.tcp_wmem: "4096 65536 268435456"
  net.core.netdev_max_backlog: 250000
  net.ipv4.tcp_mtu_probing: 1
```

```yaml
# lab/inventory/host_vars/spark-01.yml
---
spark_node_index: 1

# CX-7 fabric. Each physical QSFP port shows up as TWO logical netdevs
# (one per PCIe root: enp1s0f1np1 and enP2p1s0f1np1 are the SAME cage).
# Assign both to get the full 200 Gb/s. Confirm names with `ibdev2netdev`.
cx7_interfaces:
  - name: enp1s0f1np1
    rdma_dev: rocep1s0f1
    address: 192.168.100.11/24
    mtu: 9000
  - name: enP2p1s0f1np1
    rdma_dev: roceP2p1s0f1
    address: 192.168.101.11/24
    mtu: 9000
```

Check that Ansible sees what you meant:

```bash
ansible-inventory --graph
ansible-inventory --host spark-01 --yaml | head -40     # merged vars for one host
ansible -m debug -a "var=cx7_interfaces" spark           # per-host value
```

### Step 4 — First contact

```yaml
# lab/playbooks/00-ping.yml
---
# Day-0 connectivity: SSH works, sudo works, Python works, it IS a Spark.
- name: Connectivity and identity check
  hosts: spark
  gather_facts: true
  become: true
  tasks:
    - name: Ping (tests SSH + Python, not ICMP)
      ansible.builtin.ping:

    - name: Show what we are talking to
      ansible.builtin.debug:
        msg: >-
          {{ inventory_hostname }} {{ ansible_facts.architecture }}
          {{ ansible_facts.processor_nproc }} cores
          {{ (ansible_facts.memtotal_mb / 1024) | round(1) }} GiB
          {{ ansible_facts.distribution }} {{ ansible_facts.distribution_version }}
          kernel {{ ansible_facts.kernel }}
```

```bash
ansible-playbook playbooks/00-ping.yml -K     # -K prompts for the sudo password
```

Expected output (trimmed; your exact numbers and kernel will differ):

```
ok: [spark-01] => msg: spark-01 aarch64 20 cores 119.6 GiB Ubuntu 24.04 kernel 6.x-…-nvidia
```

> The reported memory is slightly under 128 GB: firmware and carve-outs take some. The `spark_expected.mem_total_gib_min: 110` guard allows for that.

Ad-hoc commands are how you poke a box without writing a playbook:

```bash
ansible spark -m command -a "nvidia-smi --query-gpu=name,driver_version --format=csv"
ansible spark -m shell   -a "free -g | head -2"
ansible spark -m setup   -a "filter=ansible_processor*"
ansible spark -b -m apt  -a "name=nvtop state=present"        # -b = become
```

### Step 5 — Teach Ansible about the GPU: custom facts

Built-in facts know the CPU and OS but nothing about the GB10, CUDA or the CX-7. A **local fact** is an executable in `/etc/ansible/facts.d/*.fact` that prints JSON. Ansible runs it during fact gathering and exposes the result as `ansible_local.<name>`.

```python
# lab/roles/spark_facts/files/spark.fact
#!/usr/bin/env python3
"""
/etc/ansible/facts.d/spark.fact — Ansible local fact for DGX Spark.

Ansible executes every executable *.fact file during fact gathering and puts
the JSON it prints under ansible_local.<name>. This one exposes GPU, CUDA,
ConnectX-7 and DGX OS state so playbooks can branch on real hardware state
instead of re-running shell commands in every role.

Design rules:
  * never fail — a broken fact script breaks *every* play on the host
  * hard timeout on every subprocess (a wedged GPU makes nvidia-smi hang)
  * report 'error' fields instead of raising
"""
import json
import os
import re
import shutil
import subprocess

TIMEOUT = 8


def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        return out.returncode, out.stdout.strip(), out.stderr.strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def gpu():
    info = {"present": False}
    if not shutil.which("nvidia-smi"):
        info["error"] = "nvidia-smi not installed"
        return info
    fields = "name,driver_version,compute_cap,temperature.gpu,power.draw,utilization.gpu,pstate"
    rc, out, err = run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"])
    if rc != 0:
        info["error"] = err or f"nvidia-smi rc={rc}"
        return info
    first = out.splitlines()[0]
    vals = [v.strip() for v in first.split(",")]
    keys = ["name", "driver_version", "compute_cap", "temp_c", "power_w", "util_pct", "pstate"]
    info.update(dict(zip(keys, vals)))
    info["present"] = True
    info["count"] = len(out.splitlines())
    # CUDA version the *driver* supports comes from the nvidia-smi banner
    rc, banner, _ = run(["nvidia-smi"])
    m = re.search(r"CUDA Version:\s*([\d.]+)", banner)
    info["cuda_driver_api"] = m.group(1) if m else None
    return info


def cuda_toolkit():
    nvcc = shutil.which("nvcc") or "/usr/local/cuda/bin/nvcc"
    rc, out, _ = run([nvcc, "--version"])
    m = re.search(r"release ([\d.]+)", out)
    return {"nvcc_path": nvcc if rc == 0 else None, "version": m.group(1) if m else None}


def cx7():
    """Parse `ibdev2netdev` → {netdev: {rdma_dev, state, speed_mbps, mtu}}."""
    ports = {}
    rc, out, err = run(["ibdev2netdev"])
    if rc != 0:
        return {"error": err or "ibdev2netdev unavailable", "ports": ports}
    for line in out.splitlines():
        m = re.match(r"(\S+) port (\d+) ==> (\S+) \((\w+)\)", line)
        if not m:
            continue
        rdma, _port, netdev, state = m.groups()
        entry = {"rdma_dev": rdma, "state": state}
        base = f"/sys/class/net/{netdev}"
        for key, fname in (("speed_mbps", "speed"), ("mtu", "mtu")):
            try:
                with open(f"{base}/{fname}") as fh:
                    entry[key] = int(fh.read().strip())
            except (OSError, ValueError):
                entry[key] = None
        ports[netdev] = entry
    up = [n for n, p in ports.items() if p["state"] == "Up"]
    return {"ports": ports, "up": sorted(up)}


def memory():
    mem = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                if k in ("MemTotal", "MemAvailable", "Cached", "SwapTotal", "HugePages_Total"):
                    mem[k] = int(v.split()[0])
    except OSError:
        pass
    gib = lambda kb: round(kb / 1048576, 1)
    return {
        "total_gib": gib(mem.get("MemTotal", 0)),
        "available_gib": gib(mem.get("MemAvailable", 0)),
        "page_cache_gib": gib(mem.get("Cached", 0)),
        "swap_gib": gib(mem.get("SwapTotal", 0)),
        # On a UMA machine the GPU allocates from this same pool:
        "note": "unified memory: GPU allocations consume MemAvailable",
    }


def dgx_release():
    rel = {}
    try:
        with open("/etc/dgx-release") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    rel[k] = v.strip('"')
    except OSError:
        return {"present": False}
    rel["present"] = True
    return rel


def container_runtime():
    rc, out, _ = run(["nvidia-ctk", "--version"])
    return {
        "nvidia_ctk": out.splitlines()[0] if rc == 0 and out else None,
        "cdi_spec": os.path.exists("/etc/cdi/nvidia.yaml") or os.path.exists("/var/run/cdi/nvidia.yaml"),
        "docker": shutil.which("docker") is not None,
    }


print(json.dumps({
    "schema": 1,
    "gpu": gpu(),
    "cuda": cuda_toolkit(),
    "cx7": cx7(),
    "memory": memory(),
    "dgx_release": dgx_release(),
    "runtime": container_runtime(),
}))
```

```yaml
# lab/roles/spark_facts/tasks/main.yml
---
# Installs the spark.fact local fact and re-reads facts so that
# ansible_local.spark is available to every role that runs afterwards.
- name: Ensure facts.d directory exists
  ansible.builtin.file:
    path: /etc/ansible/facts.d
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Install DGX Spark local fact script
  ansible.builtin.copy:
    src: spark.fact
    dest: /etc/ansible/facts.d/spark.fact
    owner: root
    group: root
    mode: "0755"
  register: spark_fact_script

- name: Re-read local facts after install/update
  ansible.builtin.setup:
    filter: ansible_local
  when: spark_fact_script is changed or ansible_local.spark is not defined

- name: Summarise hardware (visible with -v)
  ansible.builtin.debug:
    msg: >-
      {{ inventory_hostname }}:
      GPU={{ ansible_local.spark.gpu.name | default('n/a') }}
      driver={{ ansible_local.spark.gpu.driver_version | default('n/a') }}
      cuda(driver)={{ ansible_local.spark.gpu.cuda_driver_api | default('n/a') }}
      cx7_up={{ ansible_local.spark.cx7.up | default([]) | join(',') }}
      mem={{ ansible_local.spark.memory.total_gib | default('?') }}GiB
    verbosity: 1
```

```bash
ansible-playbook playbooks/01-baseline.yml -K --tags facts -v
ansible spark -m setup -a "filter=ansible_local" | less
```

Expected (excerpt, example values):

```json
"ansible_local": { "spark": {
   "gpu":  {"present": true, "name": "NVIDIA GB10", "driver_version": "580.82.09",
            "compute_cap": "12.1", "cuda_driver_api": "13.0", ...},
   "cx7":  {"up": ["enP2p1s0f1np1", "enp1s0f1np1"], "ports": {...}},
   "memory": {"total_gib": 119.6, "available_gib": 112.3, ...},
   "dgx_release": {"present": true, ...}}}
```

Every later role reads these instead of re-running `nvidia-smi`. A typical use in a play:

```yaml
- name: Only on nodes whose fabric is cabled
  ansible.builtin.include_role: { name: cx7_fabric }
  when: ansible_local.spark.cx7.up | length > 0
```

### Step 6 — OS baseline

The `spark_baseline` role installs the tooling you'll need in every later volume, pins the NVIDIA driver stack against accidental upgrades, and applies sysctl, SSH and time settings.

```yaml
# lab/roles/spark_baseline/tasks/main.yml
---
- name: Assert we are on a supported platform
  ansible.builtin.assert:
    that:
      - ansible_facts.os_family == 'Debian'
      - ansible_facts.distribution_major_version is version('24', '>=')
    fail_msg: "spark_baseline targets DGX OS 7 / Ubuntu 24.04+, got {{ ansible_facts.distribution }} {{ ansible_facts.distribution_version }}"
    quiet: true

- name: Packages
  ansible.builtin.import_tasks: packages.yml
  tags: [baseline, packages]

- name: Users and SSH
  ansible.builtin.import_tasks: users_ssh.yml
  tags: [baseline, ssh]

- name: Kernel and sysctl
  ansible.builtin.import_tasks: kernel.yml
  tags: [baseline, sysctl]

- name: Time and logging
  ansible.builtin.import_tasks: time_logging.yml
  tags: [baseline, time]
```

```yaml
# lab/roles/spark_baseline/tasks/packages.yml
---
- name: Install baseline packages
  ansible.builtin.apt:
    name: "{{ spark_baseline_packages }}"
    state: present
    update_cache: true
    cache_valid_time: 3600
  register: spark_baseline_apt
  retries: 3
  delay: 10
  until: spark_baseline_apt is succeeded   # apt lock held by unattended-upgrades → retry

- name: Gather installed package list
  ansible.builtin.package_facts:
    manager: apt
  when: spark_baseline_hold_nvidia | bool

- name: Compute NVIDIA packages to hold
  ansible.builtin.set_fact:
    spark_baseline_nvidia_pkgs: >-
      {{ ansible_facts.packages.keys()
         | select('match', spark_baseline_hold_regex)
         | list | sort }}
  when: spark_baseline_hold_nvidia | bool

- name: Read current apt holds
  ansible.builtin.command: apt-mark showhold
  register: spark_baseline_holds
  changed_when: false
  check_mode: false        # read-only probe: must also run under --check (drift detection)
  when: spark_baseline_hold_nvidia | bool

- name: Hold NVIDIA driver stack (DGX Dashboard / planned upgrades only)
  ansible.builtin.command: "apt-mark hold {{ item }}"
  loop: "{{ spark_baseline_nvidia_pkgs | difference(spark_baseline_holds.stdout_lines) }}"
  changed_when: true
  when: spark_baseline_hold_nvidia | bool

# `command` tasks are SKIPPED (not "changed") under --check, so without this a
# missing hold would be invisible to drift detection (Volume 22).
- name: Report missing holds as drift in check mode
  ansible.builtin.debug:
    msg: "Would hold: {{ spark_baseline_nvidia_pkgs | difference(spark_baseline_holds.stdout_lines) }}"
  changed_when: true
  when:
    - ansible_check_mode
    - spark_baseline_hold_nvidia | bool
    - spark_baseline_nvidia_pkgs | difference(spark_baseline_holds.stdout_lines) | length > 0
```

```bash
ansible-playbook playbooks/01-baseline.yml -K --check --diff   # preview
ansible-playbook playbooks/01-baseline.yml -K                  # apply
ansible-playbook playbooks/01-baseline.yml -K                  # again → changed=0
```

**The second run must report `changed=0`.** If it doesn't, a task isn't idempotent. Fix it before moving on, or drift detection (Volume 22) will cry wolf forever.

---

## 4. Integrations introduced here

| Integration | How | Used again in |
|---|---|---|
| DGX OS release metadata | `/etc/dgx-release` → `ansible_local.spark.dgx_release` | 07 (upgrade gating), 22 (drift) |
| NVIDIA driver/CUDA | `nvidia-smi` → `ansible_local.spark.gpu` | 08, 17, 25 |
| CX-7 / RDMA | `ibdev2netdev` + sysfs → `ansible_local.spark.cx7` | 11, 12, 15 |
| apt holds | `package_facts` + `apt-mark hold` | 07, 10 |
| DGX Dashboard | untouched; `AllowTcpForwarding yes` keeps SSH tunnels to `localhost:11000` working | 09 |

---

## 5. Production hardening checklist

- [ ] `host_key_checking = True`. Pre-seed `known_hosts` with `ssh-keyscan` rather than turning checking off.
- [ ] Keys only: flip `spark_baseline_ssh_disable_passwords: true` **after** you've confirmed key login works from two places.
- [ ] Put the lab directory in git and never commit `.cache/` (it holds keys, the kubeconfig, and the munge key).
- [ ] Pin `ansible-core` and collection versions (`requirements.*`). An unpinned `community.general` bump is the most common source of "it worked yesterday".
- [ ] Use a dedicated automation user with `NOPASSWD` sudo **only** once Vault-signed SSH certificates are in place (Volume 19).

---

## 6. Troubleshooting & diagnostics

| Symptom | Likely cause | Diagnose | Fix |
|---|---|---|---|
| `UNREACHABLE! ... Permission denied (publickey)` | Key not on the Spark, or the wrong user | `ssh -v nvidia@10.10.10.11` | `ssh-copy-id`; check `remote_user` in `ansible.cfg` |
| `Missing sudo password` | `become` without `-K` | — | Add `-K`, or configure `NOPASSWD` for the automation user |
| `Timeout (12s) waiting for privilege escalation prompt` | sudo is slow because of a DNS lookup of the hostname | `time sudo true` on the Spark | Add the hostname to `/etc/hosts` |
| `/usr/bin/python3: not found` | Minimal image, or a container target | `ansible host -m raw -a 'which python3'` | Bootstrap with the `raw` module (see the Molecule `prepare.yml`) |
| `ansible_local` is empty | Fact file not executable, or it printed non-JSON | `sudo /etc/ansible/facts.d/spark.fact \| jq .` | `chmod 755`; the script must print a single JSON object |
| Fact gathering hangs for ~10 s | `nvidia-smi` blocked on a wedged GPU | `timeout 5 nvidia-smi; echo $?` | The fact script time-boxes itself; go to Volume 24, Runbook A |
| `E: Could not get lock /var/lib/dpkg/lock-frontend` | unattended-upgrades or the DGX Dashboard updater is running | `ps aux \| grep -E 'apt\|dpkg'` | The role retries 3× with a 10 s delay. Otherwise wait for it to finish |
| Second run is not `changed=0` | Non-idempotent task (`command`/`shell` without `changed_when`) | `ansible-playbook ... --diff -v` | Add `creates:`, `changed_when:` or a real module |

A diagnostic sequence worth memorising:

```bash
ansible spark -m ping -vvv 2>&1 | grep -E 'ESTABLISH|EXEC|SSH:'   # is it SSH, sudo or Python?
ansible-config dump --only-changed                                # which config is actually in effect
ansible-inventory --host spark-02 --yaml                          # which vars will be used
ansible-playbook playbooks/01-baseline.yml --list-tasks --list-tags
ansible-playbook playbooks/01-baseline.yml --start-at-task "Harden sshd (drop-in, validated before reload)" -K
```

---

## 7. Validation

```bash
ansible spark -m command -a "test -x /etc/ansible/facts.d/spark.fact"
ansible spark -m command -a "apt-mark showhold" -b | grep -c nvidia     # > 0
ansible spark -m command -a "sysctl -n vm.max_map_count" -b             # 1048576
ansible spark -m command -a "sshd -T" -b | grep -E 'permitrootlogin|allowtcpforwarding'
```

- [ ] `00-ping.yml` reports aarch64 / 20 cores / Ubuntu 24.04 for every Spark
- [ ] `ansible_local.spark.gpu.present == true` and `compute_cap == "12.1"`
- [ ] `01-baseline.yml` second run: `changed=0`
- [ ] `ansible.log` contains both runs

## 8. Break-it exercises

1. Make the fact script print `hello` before the JSON. What error do you get, and at which task?
2. Remove `pipelining = True` and time `01-baseline.yml` with `profile_tasks`. How many seconds does it add? (Volume 02 explains why.)
3. Hold a non-NVIDIA package by hand (`apt-mark hold jq`). Does the role release it? Should it?
