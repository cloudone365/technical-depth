# Chapter 29 · Incident Response & Emergency Drain: Capture Evidence, Remediate, Prove, Return to Service

> **01-Ansible · Part V — Production operations · Chapter 29 of 30** · ← [Chapter 28 · Firmware lifecycle & vulnerability patching](28-firmware-lifecycle-and-vulnerability-patching.md) · [All chapters](00-ansible-step-by-step-guide.md) · [Chapter 30 · Capstone: build, break, prove](30-capstone-build-break-prove.md) →

| | |
|---|---|
| **You will build** | One drain role that works across Kubernetes (root cluster *and* the vClusters inside it) and Slurm (`node_drain`), a forensics bundle collected **before** anything is restarted, runbooks A–E for the failure modes a Spark actually has (GPU hang, Xid, unified-memory pressure, CX-7 degradation, unreachable node), and an alert-to-automation path |
| **Hardware** | 1–2× DGX Spark |
| **Clusters** | `spark-root` (the drain itself, and the LLM stack in `llm-serving`/`batch`); `dev-lab` and `dev-llms` to watch what tenants see |
| **Time** | 90 min (including drills) |
| **Risk** | Medium. You'll deliberately take nodes out of service |

---

## 1. The drain contract

```mermaid
flowchart LR
  A["1 · Stop NEW work<br/>k8s cordon+drain · Slurm DRAIN"] --> B["2 · Capture evidence<br/>nvidia-smi -q · kernel log · Xid lines ·<br/>ibv_devinfo · docker ps · (bug report)"]
  B --> C["3 · Stop RUNNING GPU work<br/>docker stop (nvidia runtime)"]
  C --> D["4 · Remediate<br/>reboot · driver reload · cable · config"]
  D --> E["5 · Prove<br/>spark_validate (+ smoke)"]
  E -->|pass| F["6 · Return<br/>uncordon · RESUME"]
  E -->|fail| G["stay drained → escalate<br/>(bundle + ticket)"]
```

**Evidence before remediation.** A reboot destroys the most useful data (GPU state, `nvidia-smi -q`, in-memory logs). The role collects first, time-boxing every command so that a hung GPU can't hang the drain.

**`serial: 1`, and refuse to run without `-l`.** A drain that runs against every host at once is an outage you caused yourself.

**Two ways to start it.** **Semaphore UI** (the normal way): the template **`29.1 Emergency drain`** in project `spark-lab`, whose CLI args name the node (`["--limit", "dgx-spark-2,localhost"]`; keep `localhost` for play 1 and the `kubectl` steps) and whose extra variables pick the stages (`node_drain_reboot`, `node_drain_undrain_after`, `node_drain_bug_report`). The task history then records who drained which node, when, and what happened. **Break-glass** from your MacBook, when sema01 or vault01 is down (Chapter 04 §11):

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
tools/fetch-kubeconfig.sh sema01 || true          # if sema01 still answers; otherwise use the copy you fetched last
ansible-playbook playbooks/29.1-emergency-drain.yml -l dgx-spark-2,localhost -K -e node_drain_reboot=true
```

Without the Semaphore variable group, play 1 is skipped and the play logs in as `dgxadmin` with your key; the kubeconfig and the incident bundle are read from and written to the MacBook's `.cache/`. Afterwards, re-run the template in Semaphore so the record and the state are back on sema01.

> **The 15-minute certificate and the reboot.** Under Semaphore, play 1's certificate covers new logins for 15 minutes. The drain, the evidence and the containers use the already-open ControlPersist connection, but the reconnect **after** the reboot is a new login. With `node_drain_bug_report=true` (minutes of `nvidia-bug-report.sh`) plus a slow boot you can pass 15 minutes, and the reconnect fails with `Permission denied (publickey)`. Nothing is lost: the node is up and still drained. Run the template again with `node_drain_reboot=false node_drain_undrain_after=true`; play 1 issues a fresh certificate and the role validates and returns the node.

---

## 2. The role

```yaml
# lab/roles/node_drain/defaults/main.yml
---
# What the drain does, in order. Toggle stages per incident.
node_drain_k8s: "{{ inventory_hostname in groups['k8s_control_plane'] | default([]) + groups['k8s_workers'] | default([]) }}"
node_drain_slurm: "{{ inventory_hostname in groups['slurm_compute'] | default([]) }}"
node_drain_stop_containers: true        # docker containers using the GPU
node_drain_collect: true                # forensic bundle before anything is restarted
node_drain_bug_report: false            # nvidia-bug-report.sh takes minutes; enable for Xid cases
node_drain_reboot: false
node_drain_undrain_after: false         # only after reboot + validation passes
node_drain_reason: "maint: ansible drain {{ now(utc=true, fmt='%Y-%m-%dT%H:%MZ') }}"
node_drain_kubeconfig: "{{ lab_cache_dir | default(playbook_dir ~ '/../.cache') }}/kubeconfig-{{ lab_name | default('spark-lab') }}.yaml"
node_drain_context: spark-root          # the root owns the nodes; vCluster pods are drained as root pods
node_drain_bundle_dir: "{{ lab_cache_dir | default(playbook_dir ~ '/../.cache') }}/incidents"
```

```yaml
# lab/roles/node_drain/tasks/main.yml
---
# Order matters: stop NEW work → collect evidence → stop RUNNING work → remediate → prove → return.
- name: "1/6 Cordon + drain Kubernetes node"
  kubernetes.core.k8s_drain:
    kubeconfig: "{{ node_drain_kubeconfig }}"
    context: "{{ node_drain_context }}"
    name: "{{ inventory_hostname }}"
    state: drain
    delete_options:
      ignore_daemonsets: true
      delete_emptydir_data: true
      terminate_grace_period: 60
      wait_timeout: 300
  delegate_to: localhost
  become: false
  when: node_drain_k8s | bool

- name: "2/6 Drain in Slurm (running jobs finish, no new ones start)"
  ansible.builtin.command: >-
    scontrol update NodeName={{ inventory_hostname }} State=DRAIN Reason="{{ node_drain_reason }}"
  delegate_to: "{{ groups['slurm_controller'][0] }}"
  changed_when: true
  when: node_drain_slurm | bool

- name: "3/6 Collect forensic bundle"
  when: node_drain_collect | bool
  block:
    - name: Create remote bundle dir
      ansible.builtin.tempfile:
        state: directory
        prefix: incident-
      register: node_drain_tmp

    - name: Capture state (each command time-boxed; a hung GPU must not hang the drain)
      ansible.builtin.shell: |
        set -o pipefail
        cd {{ node_drain_tmp.path }}
        timeout 30 nvidia-smi -q                > nvidia-smi-q.txt 2>&1
        timeout 30 nvidia-smi                   > nvidia-smi.txt 2>&1
        journalctl -k --since "-2h" --no-pager  > kernel.log 2>&1
        journalctl -k -b -1 --no-pager          > kernel-prev-boot.log 2>&1 || true   # after a hard power-cycle
        journalctl -k --no-pager | grep -E 'NVRM|Xid|mlx5' > nvrm-xid.log 2>&1 || true
        journalctl -u docker -u containerd -u kubelet -u slurmd --since "-2h" --no-pager > services.log 2>&1
        cat /proc/meminfo > meminfo.txt
        ibdev2netdev > ibdev2netdev.txt 2>&1
        for d in $(ls /sys/class/infiniband 2>/dev/null); do ibv_devinfo -d $d; done > ibv_devinfo.txt 2>&1
        ip -s link > ip-link.txt
        docker ps -a > docker-ps.txt 2>&1
        {% if node_drain_bug_report %}
        timeout 600 nvidia-bug-report.sh --output-file nvidia-bug-report.log.gz >/dev/null 2>&1
        {% endif %}

        tar czf /tmp/{{ inventory_hostname }}-incident.tgz -C {{ node_drain_tmp.path }} .
      args:
        executable: /bin/bash
      changed_when: false
      check_mode: false        # read-only probe: must also run under --check (drift detection)

    - name: Pull bundle to the control node
      ansible.builtin.fetch:
        src: "/tmp/{{ inventory_hostname }}-incident.tgz"
        dest: "{{ node_drain_bundle_dir }}/{{ inventory_hostname }}-{{ now(fmt='%Y%m%d-%H%M%S') }}.tgz"
        flat: true

- name: "4/6 Stop GPU containers"
  ansible.builtin.shell: |
    set -o pipefail
    ids=$(docker ps -q)
    [ -z "$ids" ] && exit 0
    docker inspect $ids --format '{{ '{{' }}.Id{{ '}}' }} {{ '{{' }}.HostConfig.Runtime{{ '}}' }} {{ '{{' }}json .HostConfig.DeviceRequests{{ '}}' }}' \
      | awk '/nvidia/ {print $1}' | xargs -r docker stop -t 60
  args:
    executable: /bin/bash
  register: node_drain_stopped
  changed_when: node_drain_stopped.stdout | length > 0
  when: node_drain_stop_containers | bool

- name: "5/6 Reboot (optional)"
  ansible.builtin.reboot:
    reboot_timeout: 900
    post_reboot_delay: 30
    test_command: nvidia-smi -L
  when: node_drain_reboot | bool

- name: "6/6 Validate, then return to service"
  when: node_drain_undrain_after | bool
  block:
    - name: Run validation role
      ansible.builtin.include_role:
        name: spark_validate

    - name: Uncordon Kubernetes node
      kubernetes.core.k8s_drain:
        kubeconfig: "{{ node_drain_kubeconfig }}"
        context: "{{ node_drain_context }}"
        name: "{{ inventory_hostname }}"
        state: uncordon
      delegate_to: localhost
      become: false
      when: node_drain_k8s | bool

    - name: Resume in Slurm
      ansible.builtin.command: scontrol update NodeName={{ inventory_hostname }} State=RESUME
      delegate_to: "{{ groups['slurm_controller'][0] }}"
      changed_when: true
      when: node_drain_slurm | bool
```

```yaml
# lab/playbooks/29.1-emergency-drain.yml
---
# ansible-playbook playbooks/29.1-emergency-drain.yml -l dgx-spark-2 \
#   -e node_drain_reboot=true -e node_drain_undrain_after=true -e node_drain_bug_report=true
- name: Short-lived SSH certificate from vault01 (Semaphore runs only)
  ansible.builtin.import_playbook: 00-vault-cert.yml

- name: Emergency drain / remediation
  hosts: spark
  become: true
  serial: 1                       # never take the whole lab down at once
  max_fail_percentage: 0
  pre_tasks:
    - name: Refuse to run against every host without -l
      ansible.builtin.assert:
        that: ansible_limit is defined or (allow_all | default(false) | bool)
        fail_msg: "Use -l <host> (or -e allow_all=true if you really mean it)"
      run_once: true
  roles:
    - role: node_drain
```

### 2.1 What a drain does to the models and the nested clusters

`node_drain_kubeconfig` is the lab file `kubeconfig-spark-lab.yaml` in the controller's state folder (sema01's state volume under Semaphore, `.cache/` on the MacBook), and `node_drain_context` pins `k8s_drain` to `spark-root` — whatever the file's `current-context` happens to be (someone may have run `kubectl config use-context dev-llms` on it). Only the root has nodes to cordon. The by-hand equivalent is:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
export KUBECONFIG=$PWD/.cache/kubeconfig-spark-lab.yaml
kubectl --context spark-root drain dgx-spark-2 --ignore-daemonsets --delete-emptydir-data --grace-period=60 --timeout=300s
```

The model servers and training pods are ordinary root pods in `llm-serving` and `batch`, under their own names. A vCluster has no kubelet and no nodes of its own, so there's nothing to drain "inside" it either. Every vCluster pod is a real pod on the root, renamed `<pod>-x-<namespace>-x-<vcluster>` in `vc-dev-lab` or `vc-dev-llms`, and the root drain evicts it like any other pod:

| What runs on the node | What the drain does | What you see |
|---|---|---|
| Static control-plane pods (kube-apiserver, etcd, …) on dgx-spark-1 | Skipped: they are mirror pods, owned by the kubelet, not the API | The root API stays up |
| DaemonSets (cilium, kube-proxy, GPU Operator device plugin, MetalLB speaker) | Skipped (`ignore_daemonsets`) | Networking and the GPU stay advertised |
| Model servers in `llm-serving`, training pods in `batch` | Evicted | Their Deployments recreate them; the new pods stay `Pending` while the node is cordoned, and the LLM API on `.115` has no backend for that model until it is Ready again |
| vCluster control planes (`dev-lab`, `dev-llms` StatefulSets in `vc-*`) | Evicted | That vCluster's API (`https://192.168.0.111` / `.112`) is down until it reschedules. On a single Spark that means until you uncordon |
| Tenant pods synced from a vCluster | Evicted | The tenant's controller recreates its pod; the syncer creates a new root pod, which stays `Pending` while the node is cordoned |
| PodDisruptionBudgets | Respected: root PDBs directly, tenant PDBs because vCluster syncs them to the root | A tight PDB (a model server's, or a tenant's) can block the drain until `wait_timeout` (§5) |

```bash
# ▶ MacBook · any folder
kubectl --context spark-root get pods -A -o wide --field-selector spec.nodeName=dgx-spark-2   # what is still there
kubectl --context spark-root -n llm-serving get pods                                       # model servers, their own names
kubectl --context spark-root -n vc-dev-llms get pods                                       # sandbox pods, translated root names
kubectl --context spark-root get pdb -A                                                    # root PDBs; synced tenant PDBs show up in vc-*
```

**Single Spark:** `dgx-spark-1` is the only node, so draining it stops *every* workload: the models and the pods of both vClusters (the root API itself keeps running). That's correct for a GPU hang, but it isn't a rolling drain. Use `node_drain_k8s=false` if you only need the evidence bundle.

---

## 3. Runbooks

### Runbook A — GPU hang (`nvidia-smi` doesn't answer)

#### What this lab does (template `29.1 Emergency drain`)

- **Guard:** refuses to run without `--limit` (or `-e allow_all=true`), and works on one Spark at a time (`serial: 1`).
- **1 · Stop new work:** cordons and drains the node in `spark-root` (the kubeconfig in the state folder, context pinned to `spark-root`), and sets it to `DRAIN` in Slurm. With one Spark this evicts every model pod and both vCluster control planes (§2.1).
- **2 · Evidence:** collects `nvidia-smi -q`, kernel logs (this boot and the previous one), NVRM/Xid/mlx5 lines, service logs, `/proc/meminfo`, RDMA and link state and `docker ps -a` into a `.tgz`, each command time-boxed. It is copied to `incidents/<host>-<time>.tgz` in the state folder (`/opt/spark-lab/cache` on sema01, `.cache/` on the MacBook). With `node_drain_bug_report=true` it adds `nvidia-bug-report.log.gz`, which takes minutes.
- **3 · Stop GPU containers:** `docker stop` on every running container that uses the NVIDIA runtime.
- **4 · Reboot** (only with `node_drain_reboot=true`): waits up to 15 minutes for `nvidia-smi -L` to answer.
- **5 · Prove and return** (only with `node_drain_undrain_after=true`): runs the `spark_validate` role, then uncordons in Kubernetes and resumes in Slurm. If validation fails, the node stays drained.

#### What to look for

1. **Semaphore run:** `failed=0`. Expect `changed` on the drain, on "Pull bundle to the control node" and, if GPU containers were running, on "4/6 Stop GPU containers".
2. **The bundle exists** and is not empty:
   ```bash
   # ▶ sema01 (ssh sema01)
   sudo ls -lh /opt/spark-lab/cache/incidents/
   sudo tar tzf "$(sudo ls -t /opt/spark-lab/cache/incidents/*.tgz | head -1)"
   ```
   The list contains `nvidia-smi-q.txt`, `kernel.log`, `nvrm-xid.log`, `services.log`, `meminfo.txt`, `docker-ps.txt` and the other files from the capture task.
3. **While drained** (a run without `node_drain_undrain_after`): `kubectl --context spark-root get node dgx-spark-1` shows `Ready,SchedulingDisabled`, and `sinfo -N` on dgx-spark-1 shows the node `drain` or `drained`.
4. **With `node_drain_undrain_after=true`:** the "Verdict" task prints `All N checks passed`, then the node shows `Ready` again and the model pods in `llm-serving` go back to `Running`. The JSON result is in `validation/dgx-spark-1.json` in the state folder.
5. **Without `--limit`:** the run fails at once with `Use -l <host> (or -e allow_all=true if you really mean it)`. That is the guard working.

**If something's off:**
- Verdict fails on `cx7_links_200g` with one Spark → the CX-7 ports have no peer cable. Add `-e spark_validate_fabric=false` and run again with `node_drain_reboot=false node_drain_undrain_after=true`.
- Verdict fails on `slurm_usable` → validation runs *before* the role resumes the node, so a node still in Slurm `DRAIN` (or a Spark without Slurm) fails this check. Add `-e spark_validate_slurm=false`, then resume by hand: `sudo scontrol update NodeName=dgx-spark-1 State=RESUME` on dgx-spark-1.
- `Permission denied (publickey)` after the reboot → the 15-minute certificate ran out (§1). More in §5.

#### Run it

**Signals:** `SparkGPUUnresponsive` alert; Slurm health check drains the node (`healthcheck: nvidia-smi unresponsive`); workloads stuck in CUDA calls.

**Semaphore UI:** template `29.1 Emergency drain`, `--limit dgx-spark-2,localhost`, extra variables `node_drain_bug_report: true`, `node_drain_reboot: true`, `node_drain_undrain_after: true` (see the certificate note in §1). Break-glass:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/29.1-emergency-drain.yml -l dgx-spark-2,localhost -K \
  -e node_drain_bug_report=true -e node_drain_reboot=true -e node_drain_undrain_after=true
```

If it happens again after the reboot, keep the node drained and open a case with the bundle (`incidents/dgx-spark-2-*.tgz` in the state folder, `/opt/spark-lab/cache` on sema01; it includes `nvidia-bug-report.log.gz`), and check for a driver/firmware update (Chapters 10, 28).

### Runbook B — Xid triage

#### What this lab does (ad hoc command, read-only)

- **One command over SSH:** reads the last 24 hours of the kernel log on the Spark and keeps only the `NVRM: Xid` lines. Nothing is changed.
- **Then a decision:** you look up each Xid number in the table below to decide between "tell the workload owner" and "drain + bug report" (Runbook A).
- **One Spark:** the command targets `dgx-spark-2`; with one Spark use `dgx-spark-1` and add `-K`.

#### What to look for

1. **On a healthy Spark:** Ansible reports the host as `FAILED` with `rc=1` and empty output. That is `grep` finding no Xid line, not a problem with the Spark.
2. **If there are lines:** each has the form `NVRM: Xid (PCI:…): <number>, …`. The number after `Xid (…):` is the code to look up.
3. **Count and spread:** one Xid 13 or 31 from one application is a workload bug. The same hardware-class code (48, 63, 64, 74, 79, 94, 95, 119, 120) twice, or across different applications, means drain.
4. **Cross-check:** `spark_gpu_xid_events_24h` in `spark_gpu.prom` (Chapter 12) shows the same count.

**If something's off:** the command fails with a sudo error → add `-K`. More in §5.

#### Run it

**Signals:** `SparkGPUXid` alert; Loki `|= "NVRM: Xid"`.

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible dgx-spark-2 -b -m shell -a "journalctl -k --since '-24h' --no-pager | grep 'NVRM: Xid'"
```

| Xid (common meaning, per NVIDIA's Xid catalogue) | Usually | Action |
|---|---|---|
| 13 Graphics engine exception | Application (bad kernel, OOB) | Tell the workload owner; no drain unless repeated across apps |
| 31 GPU memory page fault | Application (bad pointer) | Same |
| 43 GPU stopped processing | Application / driver | Watch; drain if it repeats with different apps |
| 45 Preemptive cleanup | Follow-on to another error | Look at the preceding Xid |
| 48, 63, 64, 94, 95 ECC / memory-remap family | Hardware / memory | **Drain + bug report.** Tolerate no repeats |
| 74 NVLink error | Interconnect | **Drain**, bug report |
| 79 GPU has fallen off the bus | Hardware / power / PCIe | **Drain + reboot**; if it recurs → RMA path |
| 119 / 120 GSP RPC timeout / error | Driver / firmware | Drain + reboot; check the driver/firmware update level |

The Slurm health check (Chapter 22) auto-drains on the hardware-class codes; the kata in Chapter 07 (K3) is the same classification in Jinja. Check NVIDIA's Xid documentation for the codes and fields your driver branch reports.

### Runbook C — Unified-memory pressure

#### What this lab does (template `29.2 UMA relief`)

- **Diagnose (always):** prints a memory picture of the Spark: `MemTotal`, `MemAvailable`, page cache, shared and anonymous memory in GiB, the top processes by RSS, the GPU compute processes, and the containers by memory use. Nothing is changed by this part.
- **Relieve (opt-in):** with `uma_drop_caches=true` it runs `sync` and drops the page cache. With `uma_stop_label=<key=value>` (for example `spark.lab/idle=true`) it stops the running Docker containers that carry that label.
- **What is not touched:** Kubernetes pods. Model servers in `llm-serving` are not stopped by this playbook; scale them down with `kubectl` if they are the cause.
- **Before and after:** it records `MemAvailable` before and after the relief step.

#### What to look for

1. **Semaphore run:** `failed=0`. A diagnose-only run is `changed=0`; a relief run shows `changed` on the step you turned on.
2. **"Show diagnosis"**, the `== meminfo (GiB)` part:

   | Line | Healthy value on an idle Spark |
   |---|---|
   | `MemTotal` | about `121` GiB |
   | `MemAvailable` | about `119` GiB idle; lower by the size of every loaded model |
   | `Cached` | large is fine: page cache is given back on demand |

   The GPU section lists process names only; its memory column is `N/A` on UMA, so use the RSS list to find the big consumer.
3. **"Result"** prints `MemAvailable <before> GiB → <after> GiB`. After `uma_drop_caches=true` the second number should be equal or higher. If it barely moves, the memory is held by processes, not by cache.
4. **Prometheus:** `spark_uma_available_bytes` (Chapter 12) shows the same change, and `SparkUnifiedMemoryLow` clears once `MemAvailable` is back above 8 GiB.

**If something's off:** "containers by memory" is empty → no Docker containers run; the memory is in Kubernetes pods or host processes (see the RSS list and the `kubectl` commands below). More in §5.

#### Run it

**Signals:** `SparkUnifiedMemoryLow`; CUDA OOM "while nvidia-smi shows nothing"; kubelet `MemoryPressure` evictions (the kubelet evicts below `memory.available` 4Gi, `roles/kubeadm_cluster/defaults`); the OOM killer in `dmesg`.

Kubernetes side first: `kubectl --context spark-root get pods -A --field-selector=status.phase=Failed` lists evicted pods, model pods under their own names, vCluster pods under their root names. The budgets cap memory with `limits.memory` (`llm-serving` 90Gi, `batch` 12Gi from the same pool, each vCluster 4Gi), but they are ceilings, not reservations. Every GPU container in the LLM stack sets request = limit, so the scheduler won't place a pod whose memory isn't free; it can't see a process outside Kubernetes. A model server started outside Kubernetes (Docker, Slurm) still takes from the same pool.

```yaml
# lab/playbooks/29.2-uma-relief.yml
---
# Runbook C — unified-memory pressure on a DGX Spark.
# Diagnose first (always), relieve second (opt-in flags).
#   ansible-playbook playbooks/29.2-uma-relief.yml -l dgx-spark-1 -K                       # diagnose only
#   ansible-playbook playbooks/29.2-uma-relief.yml -l dgx-spark-1 -K -e uma_drop_caches=true
#   ansible-playbook playbooks/29.2-uma-relief.yml -l dgx-spark-1 -K -e uma_stop_label=spark.lab/idle=true
- name: Short-lived SSH certificate from vault01 (Semaphore runs only)
  ansible.builtin.import_playbook: 00-vault-cert.yml

- name: UMA pressure diagnosis and relief
  hosts: spark
  become: true
  gather_facts: false
  vars:
    uma_drop_caches: false
    uma_stop_label: ""          # stop running containers carrying this label (key=value)
  tasks:
    - name: Memory picture
      ansible.builtin.shell: |
        set -o pipefail
        echo "== meminfo (GiB)"
        awk '/MemTotal|MemAvailable|^Cached|Shmem:|AnonPages|Mlocked/{printf "%-14s %6.1f\n",$1,$2/1048576}' /proc/meminfo
        echo "== top processes by RSS"
        ps -eo pid,user,rss,comm --sort=-rss | head -8 | awk 'NR==1{print;next}{printf "%-8s %-10s %6.1fG %s\n",$1,$2,$3/1048576,$4}'
        echo "== GPU compute processes (memory column is N/A on UMA)"
        timeout 10 nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader || echo "nvidia-smi unavailable"
        echo "== containers by memory"
        docker stats --no-stream --format '{{ "{{" }}.MemUsage{{ "}}" }}\t{{ "{{" }}.Name{{ "}}" }}' 2>/dev/null | sort -h -r | head -8
      args: { executable: /bin/bash }
      register: uma_diag
      changed_when: false
      check_mode: false

    - name: Show diagnosis
      ansible.builtin.debug:
        msg: "{{ uma_diag.stdout_lines }}"

    - name: Record MemAvailable before
      ansible.builtin.shell: awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo
      register: uma_before
      changed_when: false
      check_mode: false

    - name: Stop labelled containers
      ansible.builtin.shell: |
        set -o pipefail
        ids=$(docker ps -q --filter "label={{ uma_stop_label }}")
        [ -z "$ids" ] && exit 0
        docker stop -t 30 $ids && echo "stopped: $ids"
      args: { executable: /bin/bash }
      register: uma_stopped
      changed_when: "'stopped' in uma_stopped.stdout"
      when: uma_stop_label | length > 0

    - name: Drop page cache (safe; next model load will be cold)
      ansible.builtin.shell: sync && echo 3 > /proc/sys/vm/drop_caches
      changed_when: true
      when: uma_drop_caches | bool

    - name: Record MemAvailable after
      ansible.builtin.shell: awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo
      register: uma_after
      changed_when: false
      check_mode: false

    - name: Result
      ansible.builtin.debug:
        msg: "MemAvailable {{ uma_before.stdout }} GiB → {{ uma_after.stdout }} GiB"
```

**Semaphore UI:** template `29.2 UMA relief` (`--limit dgx-spark-1,localhost`; extra variable `uma_drop_caches: true` to relieve). Break-glass:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/29.2-uma-relief.yml -l dgx-spark-1,localhost -K                          # diagnose
ansible-playbook playbooks/29.2-uma-relief.yml -l dgx-spark-1,localhost -K -e uma_drop_caches=true   # relieve
```

Prevent it from recurring: set memory limits on model-server containers, keep the kubelet reserve (Chapter 19), give idle services the `spark.lab/idle=true` label so this runbook can stop them, and don't run Kubernetes and Slurm GPU jobs on the same node at the same time.

### Runbook D — CX-7 link degraded / down

#### What this lab does (ad hoc check, then templates `13.1 Fabric` and `13.2 RDMA perftest`)

- **Ad hoc check (read-only):** runs `ibdev2netdev` and `ethtool enp1s0f1np1` on every Spark to show each RDMA device's netdev and state, and the port's speed and link.
- **`13.1 Fabric`:** re-applies the CX-7 netplan file and the runtime settings (live MTU), then verifies the links. See Chapter 13.
- **`13.2 RDMA perftest`:** measures RDMA bandwidth and latency between the two Sparks after the fix.
- **This runbook needs two Sparks.** With one Spark and no QSFP cable, the ports are down by design, and `13.2` ends at once ("Needs two Sparks"). Read the steps, and run the ad hoc check to see what a down port looks like.

#### What to look for

1. **Ad hoc check, healthy link:**

   | Line | Healthy value |
   |---|---|
   | `ibdev2netdev` | `rocep1s0f1 port 1 ==> enp1s0f1np1 (Up)` (and the same for the second logical port) |
   | `Speed:` | `200000Mb/s` |
   | `Link detected:` | `yes` |
2. **Degraded:** `Speed:` shows `100000Mb/s` or less, or `Unknown!` with `Link detected: no`. That is what `SparkCX7Degraded` fires on.
3. **After the fix:** `13.1 Fabric` ends `failed=0`, and `13.2 RDMA perftest` reports a bandwidth in line with your Chapter 13 numbers.

**If something's off:** still `Down` after `13.1` → it is physical: reseat the cable, check the cage, then reboot both nodes (the fix order below). More in Chapter 13.

#### Run it

**Signals:** `SparkCX7Degraded` (speed < 200G), NCCL falls back to `NET/Socket`, NFS falls back to TCP.

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible spark -b -K -m shell -a "ibdev2netdev; ethtool enp1s0f1np1 | grep -E 'Speed|Link detected'"   # MacBook, ad hoc
```

**Semaphore UI:** then run **13.1 Fabric** (re-assert config + verify) and **13.2 RDMA perftest** (measure after fixing). Break-glass:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/13.1-fabric.yml -K
ansible-playbook playbooks/13.2-rdma-perftest.yml -K
```

Fix order: reseat the cable, then check that the same cage is used on both ends, then check the switch port speed (forced 200G), and finally reboot both nodes (NVIDIA's documented step when links won't come up). Drain dependent Slurm or Kubernetes multi-node jobs first (the root's `batch` jobs use the CX-7 through the Multus NADs in `batch`). A 2-node job can't run on a broken link.

### Runbook E — Node unreachable (no BMC)

A Spark has **no out-of-band management**. When SSH and ping fail:

1. Check from the other Spark over the fabric (`ping 192.168.100.12`). If that works, the problem is on the management network, not the node.
2. Check the local console (monitor/keyboard), or the power LED.
3. Power-cycle. For a desk lab, a **smart plug** with an API is the practical stand-in for a BMC power action. Ansible can drive it (e.g. a Home Assistant or Tasmota HTTP call from `delegate_to: localhost`).
4. After it boots: the template `29.1 Emergency drain` (default `node_drain_collect=true`) still captures the *previous boot's* kernel log (`journalctl -k -b -1`) because journald is persistent (Chapter 04 §6.3 baseline).

---

## 4. From alert to automation

```mermaid
sequenceDiagram
  participant P as Prometheus
  participant AM as Alertmanager
  participant AWX as Controller (Semaphore integration / AWX workflow)
  participant OPS as On-call human
  participant S as Spark
  P->>AM: SparkGPUXid (host=dgx-spark-2)
  AM->>AWX: webhook → launch the drain template, limit=dgx-spark-2
  AWX->>S: drain + evidence (no reboot)
  AWX->>OPS: approval: "Reboot dgx-spark-2?" (bundle link attached)
  OPS-->>AWX: approve
  AWX->>S: reboot → validate → return to service
```

Automate **steps 1–3** (safe and reversible). Gate **step 4** (reboot or reload) behind a human. Semaphore has no approval node: give the webhook-triggered run only the drain-and-evidence stages (`node_drain_reboot=false`), and keep the reboot as a separate run a person starts. AWX can express the whole flow with an approval node (Chapter 24). On a single-user lab you can skip the approval, but keep the structure.

---

## 5. Troubleshooting the drain itself

| Symptom | Diagnose | Fix |
|---|---|---|
| k8s drain times out | `kubectl --context spark-root get pods -A -o wide --field-selector spec.nodeName=dgx-spark-2`; `kubectl --context spark-root get pdb -A` | PodDisruptionBudgets (including tenant PDBs synced from a vCluster into `vc-*`) or unmanaged pods; `terminate_grace_period`; delete stuck pods with the owner's consent. For a tenant pod, ask the tenant to delete it through their own context (`--context dev-llms`), or the syncer may fight you |
| Drain fails with "node not found" / context error | `kubectl --kubeconfig .cache/kubeconfig-spark-lab.yaml config get-contexts` | `node_drain_context` must name the root (`spark-root`); a vCluster context has synced nodes you can't cordon from there. Fix the variable or re-run the template `19.1 Kubernetes` to restore the context. Break-glass from the MacBook: `tools/fetch-kubeconfig.sh sema01` first, or the drain uses a stale or missing `.cache/` copy |
| Reconnect after the reboot: `Permission denied (publickey)` (Semaphore) | Task duration vs. the 15-minute certificate | Run the template again with `node_drain_reboot=false node_drain_undrain_after=true` (§1) |
| Slurm DRAIN never reaches DRAINED | `squeue -w dgx-spark-2` | Running jobs finish first (by design); `scancel` only if agreed |
| Evidence capture hangs | Which command? Everything is wrapped in `timeout` | A new command without `timeout` → add it |
| Reboot task times out | Console | Capsule/firmware work on boot takes long (Chapter 28), or the node didn't come back: Runbook E |
| Returned to service but alerts fire again | Loki/Prometheus since the reboot | Root cause not fixed; re-drain with `node_drain_undrain_after=false` |

## 6. Validation (drills)

- [ ] Drill A: drain dgx-spark-2 with evidence and a reboot; the bundle exists; the node returns only after validation passes.
- [ ] Drill C: load a model until `SparkUnifiedMemoryLow` fires; relieve with the runbook; record before/after GiB.
- [ ] Drill D: pull the QSFP cable during a perftest; alert, then diagnosis, then recovery.
- [ ] The playbook refuses to run without `-l`.
- [ ] Break-glass drill: with the Semaphore container stopped (`docker compose stop semaphore` on sema01), drain and return a node from the MacBook, then start Semaphore again and re-run the template.
