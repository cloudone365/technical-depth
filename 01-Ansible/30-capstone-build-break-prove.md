# Chapter 30 · Capstone: Build, Break, Prove: the DGX Spark Automation Mastery Lab & Evidence-Based Test Harness

> **01-Ansible · Part V — Production operations · Chapter 30 of 30** · ← [Chapter 29 · Incident response & emergency drain](29-incident-response-and-emergency-drain.md) · [All chapters](00-ansible-step-by-step-guide.md)
>
> The last chapter. Back to the [module overview](README.md).

| | |
|---|---|
| **You will do** | Rebuild the whole lab from a clean state with one Semaphore template (`site`), pass 25 hands-on challenges, survive a chaos drill (random fault injected, then found and fixed with your own tooling), and earn a scorecard computed from **evidence**, not self-assessment |
| **Hardware** | 1× DGX Spark minimum; 2× for the fabric/NCCL/NFS challenges |
| **Time** | A weekend |
| **Risk** | You'll break things on purpose. Everything here is reversible with the lab's playbooks |

---

## 1. The harness

```mermaid
flowchart TB
  subgraph BUILD["Build"]
    SITE["Semaphore template site<br/>(playbooks/site.yml on sema01)"]
  end
  subgraph PROVE["Prove (three independent angles)"]
    V["spark_validate role<br/>(template 30.1 Validate, from sema01)<br/>→ validation/*.json on the state volume"]
    I["tools/spark_invariants.py<br/>(ON the node, no Ansible)<br/>exit code = failed checks"]
    D["tools/drift-cycle.sh<br/>(check mode vs desired state)"]
  end
  subgraph BREAK["Break"]
    C["template 30.2 Chaos<br/>(sealed random fault)"]
  end
  subgraph GRADE["Grade"]
    S["tools/capstone_scorecard.py<br/>reads the state folder (SPARK_LAB_CACHE)"]
  end
  SITE --> V & I & D --> S
  C --> V & I & D
```

Why three angles? Each can lie in a different way. Ansible can validate a stale fact, the on-node script can't see config intent, and drift can't see hardware state. Agreement between all three is what "healthy" means.

### 1.1 End-to-end validation role

```yaml
# lab/roles/spark_validate/tasks/main.yml
---
# Every check records a result instead of failing immediately, so one run
# produces the full picture. The final task fails if anything failed.
- name: Refresh local facts
  ansible.builtin.setup:
    filter: [ansible_local, ansible_architecture, ansible_processor_nproc, ansible_memtotal_mb, ansible_distribution_major_version]

- name: Hardware / OS invariants
  ansible.builtin.set_fact:
    spark_validate_results: >-
      {{ {
        'arch_aarch64': ansible_facts.architecture == spark_expected.arch,
        'cpu_20_cores': ansible_facts.processor_nproc | int == spark_expected.cpu_cores,
        'os_ubuntu24': ansible_facts.distribution_major_version == spark_expected.os_major,
        'mem_ge_min': (ansible_facts.memtotal_mb / 1024) >= spark_expected.mem_total_gib_min,
        'gpu_present': ansible_local.spark.gpu.present | default(false),
        'gpu_is_gb10': (ansible_local.spark.gpu.name | default('')) is search(spark_expected.gpu_name_regex),
        'driver_ge_min': (ansible_local.spark.gpu.driver_version | default('0')).split('.')[0] | int >= spark_expected.driver_major_min,
        'cuda_major': (ansible_local.spark.gpu.cuda_driver_api | default('0')).split('.')[0] | int == spark_expected.cuda_major,
        'compute_cap_12_1': ansible_local.spark.gpu.compute_cap | default('') == spark_expected.compute_capability,
      } }}

- name: Container GPU access
  ansible.builtin.command: >-
    docker run --rm --gpus all {{ spark_validate_image }} nvidia-smi -L
  register: spark_validate_docker
  changed_when: false
  check_mode: false        # read-only probe: must also run under --check (drift detection)
  failed_when: false
  when: spark_validate_container | bool

- name: Fabric state
  ansible.builtin.set_fact:
    spark_validate_fabric_ok: >-
      {{ cx7_interfaces | default([])
         | map(attribute='name')
         | map('extract', ansible_local.spark.cx7.ports | default({}))
         | selectattr('state', 'equalto', 'Up')
         | selectattr('speed_mbps', 'equalto', 200000)
         | list | length == cx7_interfaces | default([]) | length }}
  when: spark_validate_fabric | bool

- name: Kubernetes node Ready + GPU allocatable
  ansible.builtin.shell: |
    set -o pipefail
    kubectl --kubeconfig /etc/kubernetes/admin.conf get node {{ inventory_hostname }} -o json \
      | jq -r '[(.status.conditions[] | select(.type=="Ready") | .status), (.status.allocatable["nvidia.com/gpu"] // "0")] | @tsv'
  args:
    executable: /bin/bash
  delegate_to: "{{ groups['k8s_control_plane'][0] }}"
  register: spark_validate_k8s_out
  changed_when: false
  check_mode: false        # read-only probe: must also run under --check (drift detection)
  failed_when: false
  when: spark_validate_k8s | bool

- name: Slurm node state
  ansible.builtin.command: sinfo -h -n {{ inventory_hostname }} -o %T
  delegate_to: "{{ groups['slurm_controller'][0] }}"
  register: spark_validate_slurm_out
  changed_when: false
  check_mode: false        # read-only probe: must also run under --check (drift detection)
  failed_when: false
  when: spark_validate_slurm | bool

- name: Merge service-level results
  ansible.builtin.set_fact:
    spark_validate_results: >-
      {{ spark_validate_results
         | combine({'docker_gpu': spark_validate_docker.rc | default(1) == 0} if spark_validate_container | bool else {})
         | combine({'cx7_links_200g': spark_validate_fabric_ok | bool} if spark_validate_fabric | bool else {})
         | combine({'k8s_ready_gpu': (spark_validate_k8s_out.stdout | default('')).split('\t')[0] == 'True'
                                     and ((spark_validate_k8s_out.stdout | default('')).split('\t') | last | int) > 0}
                   if spark_validate_k8s | bool else {})
         | combine({'slurm_usable': (spark_validate_slurm_out.stdout | default('')) in ['idle', 'mixed', 'allocated']}
                   if spark_validate_slurm | bool else {}) }}

- name: Write JSON report on the control node
  ansible.builtin.copy:
    content: "{{ {'host': inventory_hostname, 'time': now(utc=true).isoformat(), 'results': spark_validate_results} | to_nice_json }}"
    dest: "{{ spark_validate_report_dir }}/{{ inventory_hostname }}.json"
    mode: "0644"
  delegate_to: localhost
  become: false

- name: Verdict
  ansible.builtin.assert:
    that: spark_validate_results | dict2items | rejectattr('value') | list | length == 0
    fail_msg: "FAILED checks: {{ spark_validate_results | dict2items | rejectattr('value') | map(attribute='key') | list }}"
    success_msg: "All {{ spark_validate_results | length }} checks passed"
```

### 1.2 On-node invariant checker (works when Ansible can't)

#### What this lab does (script `lab/tools/spark_invariants.py`)

- **Copy and run on the Spark:** plain Python with no Ansible and no extra packages. It only reads: `/proc/meminfo`, `/etc/os-release`, `nvidia-smi`, `docker info`, the kernel log, `ibdev2netdev`, `/sys/class/net`. Nothing is changed.
- **Checks:** platform (aarch64, 20 cores, Ubuntu 24.04, DGX OS release file), GPU (GB10, driver ≥ 580, compute capability 12.1, CUDA 13.x, Xid count in 24 h), memory (MemTotal, MemAvailable), runtime (Docker default runtime `nvidia`, CDI spec), and fabric (each CX-7 port that is Up: 200G, MTU 9000, `PORT_ACTIVE`; with `--peer`, a jumbo ping).
- **Exit code = number of FAIL lines.** WARN lines do not count.
- **One Spark:** the commands below use dgx-spark-2 and ping dgx-spark-1 over the fabric. With only dgx-spark-1, copy it to `dgxadmin@192.168.0.100` and run it without `--peer`.

#### What to look for

1. **Last line:** `N checks, 0 failed`, and exit code `0` (`echo $?`).
2. **The lines that carry numbers:**

   | Check | Healthy value |
   |---|---|
   | `20 CPU cores` | `PASS … 20` |
   | `driver >= 580` | `PASS … 580.xx.xx` |
   | `compute capability 12.1` | `PASS … 12.1` |
   | `no Xid in 24h` | `PASS … 0 events` |
   | `MemTotal >= 110 GiB` | `PASS`, about `121` GiB |
   | `UMA MemAvailable >= 16 GiB` | `PASS`, about `119` GiB available on an idle Spark |
   | `docker default runtime nvidia` | `PASS … nvidia` |
3. **Fabric with one Spark:** `[WARN] CX-7 link up … no port Up (single Spark?)`. That is expected and does not count as a failure. With two cabled Sparks, expect a `PASS` line for 200G, MTU 9000 and `PORT_ACTIVE` per Up port, plus `jumbo ping 192.168.100.11` with `--peer`.
4. **Every non-PASS line** prints a `fix:` hint underneath. Read it before you reach for Ansible.

**If something's off:** `docker reachable` FAIL → the user running the script is not in the `docker` group; run it as `dgxadmin` or with `sudo`. `jumbo ping` FAIL → MTU mismatch on the path or wrong subnet. More in Chapter 29.

#### Run it

```python
# lab/tools/spark_invariants.py
#!/usr/bin/env python3
"""
spark_invariants.py — run ON a DGX Spark (no Ansible needed) to check the
invariants the lab depends on. Good for: first boot, after a DGX OS update,
inside an incident, or as a Slurm/k8s node health probe.

  python3 spark_invariants.py            # human table
  python3 spark_invariants.py --json     # machine output
  python3 spark_invariants.py --peer 192.168.100.12   # also test fabric reachability

Exit code = number of FAILED checks (0 = healthy). WARN does not count.
"""
import argparse
import json
import os
import re
import shutil
import subprocess

T = 15
results = []


def sh(cmd, timeout=T):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def check(name, status, detail="", fix=""):
    results.append({"check": name, "status": status, "detail": detail, "fix": fix})


def c_platform():
    arch = os.uname().machine
    check("arch is aarch64", "PASS" if arch == "aarch64" else "FAIL", arch)
    n = os.cpu_count()
    check("20 CPU cores", "PASS" if n == 20 else "WARN", str(n),
          "offline cores? check `lscpu` and /sys/devices/system/cpu/online")
    rc, out, _ = sh(". /etc/os-release && echo $VERSION_ID")
    check("Ubuntu 24.04 base", "PASS" if out.startswith("24.") else "FAIL", out)
    if os.path.exists("/etc/dgx-release"):
        rc, out, _ = sh("grep -E 'DGX_(SWBUILD_VERSION|OTA_VERSION)' /etc/dgx-release | tail -1")
        check("DGX OS release file", "PASS", out)
    else:
        check("DGX OS release file", "WARN", "missing /etc/dgx-release", "not DGX OS, or image customised")


def c_gpu():
    if not shutil.which("nvidia-smi"):
        check("nvidia-smi present", "FAIL", "", "driver not installed / PATH")
        return
    rc, out, err = sh("nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader")
    if rc != 0:
        check("GPU answers nvidia-smi", "FAIL", err or f"rc={rc}",
              "see Chapter 29 Runbook A; check `dmesg | grep -i nvrm`")
        return
    name, drv, cc = [x.strip() for x in out.splitlines()[0].split(",")]
    check("GPU is GB10", "PASS" if "GB10" in name else "FAIL", name)
    check("driver >= 580", "PASS" if int(drv.split(".")[0]) >= 580 else "FAIL", drv)
    check("compute capability 12.1", "PASS" if cc == "12.1" else "WARN", cc,
          "build CUDA code with -gencode arch=compute_121,code=sm_121")
    rc, out, _ = sh("nvidia-smi | grep -o 'CUDA Version: [0-9.]*'")
    check("driver CUDA 13.x", "PASS" if "13." in out else "WARN", out)
    rc, out, _ = sh("journalctl -k --since '-24h' --no-pager | grep -c 'NVRM: Xid'")
    n = int(out or 0)
    check("no Xid in 24h", "PASS" if n == 0 else "WARN", f"{n} events",
          "journalctl -k | grep Xid ; Chapter 29 Runbook B")


def c_memory():
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
    tot = info["MemTotal"] / 1048576
    avail = info["MemAvailable"] / 1048576
    cache = info.get("Cached", 0) / 1048576
    check("MemTotal >= 110 GiB", "PASS" if tot >= 110 else "FAIL", f"{tot:.1f} GiB")
    status = "PASS" if avail >= 16 else ("WARN" if avail >= 8 else "FAIL")
    check("UMA MemAvailable >= 16 GiB", status, f"{avail:.1f} GiB avail, {cache:.1f} GiB page cache",
          "stop idle model servers; `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`")


def c_runtime():
    rc, out, _ = sh("docker info --format '{{.DefaultRuntime}} {{json .Runtimes}}'")
    if rc != 0:
        check("docker reachable", "FAIL", "", "sudo systemctl status docker; user in docker group?")
        return
    check("docker default runtime nvidia", "PASS" if out.startswith("nvidia") else "WARN", out.split()[0],
          "ansible-playbook playbooks/11.1-containers.yml")
    check("CDI spec present",
          "PASS" if os.path.exists("/etc/cdi/nvidia.yaml") or os.path.exists("/var/run/cdi/nvidia.yaml") else "WARN",
          "", "sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml")


def c_fabric(peer):
    rc, out, _ = sh("ibdev2netdev")
    if rc != 0:
        check("ibdev2netdev", "WARN", "not available", "ships with DGX OS; fallback: `rdma link show`")
        return
    up = re.findall(r"(\S+) port \d+ ==> (\S+) \(Up\)", out)
    if not up:
        check("CX-7 link up", "WARN", "no port Up (single Spark?)", "check QSFP cable; reboot both nodes")
        return
    for rdma, netdev in up:
        try:
            speed = int(open(f"/sys/class/net/{netdev}/speed").read())
            mtu = int(open(f"/sys/class/net/{netdev}/mtu").read())
        except (OSError, ValueError):
            speed, mtu = -1, -1
        check(f"{netdev} 200G", "PASS" if speed == 200000 else "FAIL", f"{speed} Mb/s",
              "switch port: disable autoneg, force 200G (e.g. 200G-baseCR4)")
        check(f"{netdev} MTU 9000", "PASS" if mtu == 9000 else "WARN", str(mtu),
              "must match on both ends; ansible-playbook playbooks/13.1-fabric.yml")
        rc, st, _ = sh(f"ibv_devinfo -d {rdma} | awk '/state:/{{print $2; exit}}'")
        check(f"{rdma} PORT_ACTIVE", "PASS" if st == "PORT_ACTIVE" else "FAIL", st)
    if peer:
        rc, _, _ = sh(f"ping -c2 -W1 -M do -s 8972 {peer}")
        check(f"jumbo ping {peer}", "PASS" if rc == 0 else "FAIL", "",
              "MTU mismatch somewhere on the path, or wrong subnet/interface")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--peer")
    a = ap.parse_args()
    for fn in (c_platform, c_gpu, c_memory, c_runtime):
        fn()
    c_fabric(a.peer)
    fails = sum(r["status"] == "FAIL" for r in results)
    if a.json:
        print(json.dumps({"failed": fails, "results": results}, indent=2))
    else:
        w = max(len(r["check"]) for r in results)
        for r in results:
            line = f"[{r['status']:4}] {r['check']:<{w}}  {r['detail']}"
            if r["status"] != "PASS" and r["fix"]:
                line += f"\n        fix: {r['fix']}"
            print(line)
        print(f"\n{len(results)} checks, {fails} failed")
    raise SystemExit(fails)


if __name__ == "__main__":
    main()
```

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
scp tools/spark_invariants.py dgxadmin@192.168.0.101:
ssh dgxadmin@192.168.0.101 'python3 spark_invariants.py --peer 192.168.100.11'
```

### 1.3 Evidence-based scorecard

#### What this lab does (script `lab/tools/capstone_scorecard.py`)

- **Reads, never runs anything:** it looks only at files in one state folder (`$SPARK_LAB_CACHE`, or `lab/.cache`) and grades 10 challenges from what earlier runs left behind.
- **The 10 checks:** at least 10 runs in `ansible.log`; GPU facts for every Spark in `facts/`; at least 7 rows in `bench.csv`; every `validation/*.json` all true; `firmware-inventory.json` with `mismatch == {}`; latest drift report clean; a `drift/recheck-*.md` exists; at least one `incidents/*.tgz`; a `kubeconfig-*.yaml` that points at a real API server address; `vault-ca.crt` present and no file named like a Vault token or AppRole secret.
- **Where to run it:** on sema01 against `/opt/spark-lab/cache`, after copying in the files that only exist on the MacBook (below).

#### What to look for

1. **Last line:** `Score: 10/10`, and exit code `0`. Any lower score exits `1`.
2. **Each line** reads `[PASS] Chapter NN  <challenge>` with an `evidence:` line under it. The evidence line tells you exactly what was found, for example `2/2 hosts with GPU facts` or `1 reports, failing: none`.
3. **The `17/18` line:** `ca=True leaks=none`. Anything listed under `leaks` is a file you must delete from the state folder.

**If something's off:**
- Both Chapter 26 lines FAIL with `no drift report` / `never healed` → `drift-cycle.sh` ran on the MacBook, so its reports are in the MacBook's `.cache/drift/`. Copy that folder to `/opt/spark-lab/cache/drift/` on sema01 the same way as `bench.csv`.
- `validation` FAIL → open the named `validation/<host>.json` and look for the `false` value. With one Spark and no QSFP cable it is `cx7_links_200g`; re-run `30.1 Validate` with `-e spark_validate_fabric=false`.
- `firmware-inventory.json` missing → run `28.1 Firmware inventory`.

#### Run it

The scorecard reads one state folder: `$SPARK_LAB_CACHE` if set, else `lab/.cache`. In this lab the evidence is split between two places:

| Where | Evidence |
|---|---|
| sema01, `/opt/spark-lab/cache` (Semaphore's state volume) | `ansible.log`, `facts/`, `validation/`, `drift/` (if you run `drift-cycle.sh` there), `incidents/`, `firmware-inventory.json`, `kubeconfig-*.yaml` |
| MacBook, `01-Ansible/lab/.cache` | `vault-ca.crt` (Chapter 04 §2), `bench.csv` (Chapter 09 runs from the MacBook), `drift/` from `drift-cycle.sh`, the fetched kubeconfig |

So grade on sema01 against the state volume, after copying in the two MacBook-only files:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
scp .cache/bench.csv sema01:/tmp/ && ssh sema01 'sudo install -o 1001 -m 0600 /tmp/bench.csv /opt/spark-lab/cache/ && rm /tmp/bench.csv'
```

```bash
# ▶ sema01 (ssh sema01)
# vault-ca.crt is the copy next to the Chapter 01 compose file; the repository clone is from Chapter 04 §4
sudo install -o 1001 -m 0644 ~/semaphore/vault-ca.crt /opt/spark-lab/cache/vault-ca.crt
sudo SPARK_LAB_CACHE=/opt/spark-lab/cache python3 ~/technical-depth/"01-Ansible/lab/tools/capstone_scorecard.py"
```

Scorecard check `17/18` passes only when vault01's CA certificate is present **and** no file that looks like Vault init output, an AppRole secret or a token sits in the folder: the lab never writes a Vault token or an AppRole secret to disk, and the check keeps it that way.

```python
# lab/tools/capstone_scorecard.py
#!/usr/bin/env python3
"""
capstone_scorecard.py — grade the 01-Ansible capstone (Chapter 30) from EVIDENCE in lab/.cache/.
Each challenge passes only if the artifact produced by the real run exists and says so.

  python3 tools/capstone_scorecard.py            # table
  python3 tools/capstone_scorecard.py --json
"""
import argparse
import glob
import json
import os
import re

CACHE = os.environ.get("SPARK_LAB_CACHE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache")


def p(*parts):
    return os.path.join(CACHE, *parts)


def jload(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def latest(pattern):
    files = sorted(glob.glob(p(pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


CHECKS = []


def check(chapter, title):
    def deco(fn):
        CHECKS.append((chapter, title, fn))
        return fn
    return deco


@check("02", "Runs are logged (ansible.log has >= 10 playbook runs)")
def _():
    n = len(re.findall(r"PLAY RECAP", read(p("ansible.log"))))
    return n >= 10, f"{n} runs logged"


@check("02", "Fact cache populated with ansible_local.spark for every Spark")
def _():
    files = [f for f in glob.glob(p("facts", "*")) if not f.endswith("localhost")]
    ok = [f for f in files if (jload(f) or {}).get("ansible_local", {}).get("spark", {}).get("gpu", {}).get("present")]
    return len(files) > 0 and len(ok) == len(files), f"{len(ok)}/{len(files)} hosts with GPU facts"


@check("09", "Fleet benchmark recorded (>= 7 runs in bench.csv)")
def _():
    rows = [line for line in read(p("bench.csv")).splitlines() if "," in line]
    return len(rows) >= 7, f"{len(rows)} rows"


@check("03-30", "End-to-end validation passes on every Spark")
def _():
    reports = glob.glob(p("validation", "*.json"))
    bad = [os.path.basename(r) for r in reports
           if not all((jload(r) or {}).get("results", {"x": False}).values())]
    return len(reports) > 0 and not bad, f"{len(reports)} reports, failing: {bad or 'none'}"


@check("28", "Firmware consistent across nodes (no mismatches)")
def _():
    inv = jload(p("firmware-inventory.json"))
    if inv is None:
        return False, "no firmware-inventory.json"
    return inv.get("mismatch") == {}, f"mismatch={inv.get('mismatch')}"


@check("26", "Latest drift check is clean")
def _():
    f = latest("drift/check-*.md") or latest("drift/recheck-*.md")
    if not f:
        return False, "no drift report"
    rows = re.findall(r"^\| (\S+) \| (\d+) \| (\d+) \|$", read(f), re.M)
    dirty = [h for h, d, fl in rows if int(d) or int(fl)]
    return bool(rows) and not dirty, f"{os.path.basename(f)} dirty={dirty or 'none'}"


@check("26", "Self-heal proven (a recheck report exists)")
def _():
    f = latest("drift/recheck-*.md")
    return f is not None, os.path.basename(f) if f else "never healed"


@check("29", "Incident drill produced an evidence bundle")
def _():
    b = glob.glob(p("incidents", "*.tgz"))
    return len(b) > 0, f"{len(b)} bundles"


@check("19", "kubeconfig fetched from the kubeadm root cluster")
def _():
    k = glob.glob(p("kubeconfig-*.yaml"))
    ok = any("https://127.0.0.1" not in read(x) and "server: https://" in read(x) for x in k)
    return ok, ", ".join(os.path.basename(x) for x in k) or "none"


@check("17/18", "vault01 CA copied; no Vault token or AppRole secret left in the cache")
def _():
    ca = os.path.exists(p("vault-ca.crt"))
    leaks = [os.path.basename(x) for x in glob.glob(p("*")) if re.search(r"(vault-init|approle|token)", os.path.basename(x))]
    return ca and not leaks, f"ca={ca} leaks={leaks or 'none'}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    results = []
    for chapter, title, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 — scorecard must never crash
            ok, detail = False, f"error: {e}"
        results.append({"chapter": chapter, "challenge": title, "pass": bool(ok), "evidence": detail})
    score = sum(r["pass"] for r in results)
    if a.json:
        print(json.dumps({"score": score, "total": len(results), "results": results}, indent=2))
    else:
        for r in results:
            print(f"[{'PASS' if r['pass'] else 'FAIL'}] Chapter {r['chapter']:<6} {r['challenge']}\n         evidence: {r['evidence']}")
        print(f"\nScore: {score}/{len(results)}")
    raise SystemExit(0 if score == len(results) else 1)


if __name__ == "__main__":
    main()
```

---

## 2. The 25 challenges

Complete them in order. "Evidence" is what the scorecard or a reviewer checks.

| # | Chapter | Challenge | Evidence |
|---|---|---|---|
| 1 | 01, 02, 04 | Management plane outside the Spark, MacBook toolchain from scratch, Spark onboarded as a target; template `04.2 Ping` shows aarch64/20 cores/GB10 facts | `facts/*` with `ansible_local.spark` on the state volume; play 1 ok in the task log |
| 2 | 05 | Explode and execute an AnsiballZ payload on the Spark | Screenshot / notes |
| 3 | 09 | 64-node fleet benchmark matrix (from the MacBook) | `bench.csv` ≥ 7 rows |
| 4 | 23 | (Optional, the alternative controller) AWX running on the root cluster `spark-root` (or hybrid), configured as code, with read-only templates only | `awx-config.yml` applied; job history |
| 5 | 06 | Target by state from the MacBook: `-l 'gpu_ready:&k8s_workers:!uma_pressure'` | `--list-hosts` output |
| 6 | 17 | vault01 operations: restart vault01 → sealed → a Semaphore task fails cleanly at play 1 → unseal by hand (Chapter 01) → the same task passes | `vault status` on vault01 before/after; the failed and the passing task in Semaphore's history |
| 7 | 07 | 7/7 Jinja katas + one kata on live data | CI green |
| 8 | 08 | Collection built; arm64 EE built on the Spark | `cloudone-spark-*.tar.gz`, `docker image inspect` arm64 |
| 9 | 03 | Wizard → bootstrap with dead-man switch (drill the rollback) | `journalctl -t bootstrap` |
| 10 | 10 | Driver audit green; one rolling upgrade | `10.1-driver-audit` output |
| 11 | 11 | sm_121 probe + PyTorch bf16 baseline | `11.2-cuda-smoke` output |
| 12 | 12 | Dashboard live; three alerts fired and resolved | Alertmanager history |
| 13 | 28 | Firmware inventory with no mismatches | `.cache/firmware-inventory.json` `mismatch == {}` |
| 14 | 13 | CX-7 verified at 200G; perftest recorded | `13.1-fabric` asserts + perftest numbers |
| 15 | 14 | NCCL `via NET/IB` across two Sparks | NCCL log |
| 16 | 21 | Pod-to-pod RDMA over Multus | `ib_write_bw` from pods |
| 17 | 16 | GDS assessment + cold/warm load comparison | `gds_summary` |
| 18 | 15 | NFS over RDMA model cache | `/proc/mounts` `proto=rdma` |
| 19 | 19 | kubeadm root cluster with Cilium (VXLAN) and MetalLB; both vClusters answer on `.111` / `.112`; reset and rebuild once with `19.2-reset-kubernetes.yml` | `ip -d link show cilium_vxlan`; `kubectl --context dev-lab get ns` and `kubectl --context dev-llms get ns` |
| 20 | 20 | 15 time-slices on one GB10, and the vCluster budget holds: with two 1-slice pods running in `dev-lab`, a third is accepted by the vCluster API but stays `Pending`, because the root quota in `vc-dev-lab` refuses the synced pod | `kubectl --context spark-root get node dgx-spark-1 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'` = 15; `kubectl --context spark-root -n vc-dev-lab describe resourcequota vcluster-budget` |
| 21 | 22 | Slurm: confinement proven; 2-node NCCL job | job output |
| 22 | 18 | Every build template runs with only the AppRole in Semaphore's variable group; SSH as `svc-ansible` via a 15-minute vault01 certificate; `18.1 Vault integration` reads `kv/spark-lab/ngc` without printing it | vault01 audit log (`auth/approle/login`, `sign/ansible`, the KV read) per task; `ED25519-CERT` lines in the Spark's sshd log; scorecard check `17/18` |
| 23 | 25 | CI green; Molecule green on the Spark runner | Actions run |
| 24 | 26–27 | Drift found (with auditd showing who), safe-healed, recheck clean; `26.1 Drift check` scheduled nightly | `drift/recheck-*.md`, `ausearch -k`, the schedule's task history |
| 25 | 29–30 | Chaos drill solved (below) + incident bundle; one drain done break-glass from the MacBook | `incidents/*.tgz`, sealed-fault reveal |

---

## 3. Chaos drill

#### What this lab does (template `30.2 Chaos`)

- **Guard:** runs only with a `--limit` that names exactly one Spark.
- **One fault:** picks one of 7 reversible faults at random (or the one in `chaos_fault`) and injects it on that Spark. The catalogue is at the end of this section, hidden on purpose.
- **Sealed answer:** writes the fault number, base64-encoded, to `chaos-<host>.sealed` in the state folder (`/opt/spark-lab/cache` on sema01, `.cache/` on the MacBook).
- **Reversible:** every fault is fixed by one of the lab's normal templates. Nothing is deleted.

#### What to look for

1. **Semaphore run:** `failed=0`, and the last task prints `A fault has been injected on dgx-spark-1. Find it. …`. **Do not scroll through the task log**: the one "Fault N" task that is not `skipping` gives the answer away.
2. **Find it with your tools only:** `30.1 Validate` (a `FAILED checks:` list), `tools/drift-cycle.sh` (exit `2` and the drifted task), Grafana alerts, Loki, `spark_invariants.py` (a FAIL or WARN line), `10.1 Driver audit`.
3. **Fixed means all three angles agree:** `30.1 Validate` passes, `drift-cycle.sh` exits `0`, and `spark_invariants.py` reports `0 failed`.
4. **Only then reveal:** the sealed file decodes to the fault number. Compare it with the catalogue.

**If something's off:** none of the tools finds anything → with one Spark and no Slurm, fault 5 (Slurm drain) has nothing to act on; the task does not fail. Check `sinfo -R`, and if Slurm is not installed, reveal and run the drill again. More in Chapter 29.

#### Run it

**Semaphore UI:** run the template `30.2 Chaos` with `--limit dgx-spark-2,localhost` (or `dgx-spark-1,localhost` with one Spark) and extra variable `chaos_fault: random`. Break-glass equivalent:

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
ansible-playbook playbooks/30.2-chaos.yml -l dgx-spark-2,localhost -K -e chaos_fault=random
# Now find it using ONLY: 30.1 Validate, tools/drift-cycle.sh, Grafana/alerts, Loki, spark_invariants.py, 10.1 Driver audit
# Then fix it with the normal templates and prove it with all three angles.
```

Reveal the fault only AFTER you've fixed it:

```bash
# ▶ sema01 (ssh sema01)
sudo base64 -d /opt/spark-lab/cache/chaos-dgx-spark-2.sealed   # reveal AFTER you've fixed it
```

(After a break-glass run the sealed file is on the MacBook instead: `base64 -d .cache/chaos-dgx-spark-2.sealed` in `01-Ansible/lab`.)

<details>
<summary><b>Fault catalogue (spoilers: open after the drill)</b></summary>

| # | Fault injected | Detected by | Fix |
|---|---|---|---|
| 1 | Runtime `ip link set <cx7> mtu 1500` (netplan file untouched) | Jumbo ping / perftest / `spark_invariants.py` MTU check; drift reports "Runtime MTU drift" | `13.1-fabric.yml` (runtime reconciliation re-applies netplan) |
| 2 | Three NVIDIA packages un-held | Drift: "Report missing holds as drift" | `04.3-baseline.yml --tags baseline` (safe auto-heal) |
| 3 | GPU metrics timer stopped + disabled | `SparkGPUMetricsStale` alert (node_exporter keeps serving the **old** file, a classic trap!); drift on "Enable collector timer" | `12.1-telemetry.yml` (safe auto-heal) |
| 4 | `vm.swappiness=60` (file + runtime) | Drift on sysctl | `04.3-baseline.yml` (safe auto-heal) |
| 5 | Slurm node drained, reason `chaos` | `30.1-validate` `slurm_usable=false`; `sinfo -R` | `scontrol update … State=RESUME` (the role won't resume it for you, on purpose) |
| 6 | `default-runtime` removed from `daemon.json` | Drift on daemon.json merge; `spark_invariants.py` warns on the default runtime | `11.1-containers.yml` |
| 7 | CDI spec corrupted (bogus libcuda path) | CDI smoke fails; drift "stale CDI spec" (hostPath check) | `11.1-containers.yml` regenerates |

The lesson from fault 3 is why the lab ships a **staleness** alert, `SparkGPUMetricsStale` (`time() - node_textfile_mtime_seconds{file=~".*spark_gpu.prom"} > 120`): a metric that stopped updating looks exactly like a healthy one.

</details>

---

## 4. Full rebuild and final proof

#### What this lab does (templates `site`, `30.1 Validate`, `19.2 Reset Kubernetes` → `19.1` → `20.1` → `20.2`, `28.1`, then the scorecard)

- **`site`:** applies every build playbook in order. The second run proves idempotence.
- **`30.1 Validate`:** runs `spark_facts` and the `spark_validate` role. It checks the platform (aarch64, 20 cores, Ubuntu 24, memory ≥ 110 GiB, GB10, driver ≥ 580, CUDA 13, compute capability 12.1), container GPU access, the CX-7 links at 200G, the Kubernetes node Ready with `nvidia.com/gpu` > 0, and the Slurm node usable. It writes `validation/<host>.json` to the state folder and fails if any check is false.
- **Kubernetes from zero:** `19.2 Reset Kubernetes` tears down `spark-root` (and with it the LLM stack and both vClusters); `19.1`, `20.1`, `20.2` rebuild them. Every model and tenant workload is gone in between.
- **`28.1 Firmware inventory`** and **the scorecard** (§1.3) close it out.

#### What to look for

1. **`site`, first run:** `failed=0`. **Second run:** `changed=0` on every host. Any `changed` task on the second run is an idempotence bug to fix before you go on.
2. **`30.1 Validate`:** the "Verdict" task prints `All N checks passed`. N is 13 with the CX-7 check on; with one Spark and no QSFP cable, run it with `-e spark_validate_fabric=false` and expect 12.
3. **Drift:** `tools/drift-cycle.sh; echo "drift exit=$?"` on the MacBook prints `drift exit=0`.
4. **After the Kubernetes rebuild:**
   ```bash
   # ▶ MacBook · 01-Ansible/lab (venv active)
   tools/fetch-kubeconfig.sh sema01
   export KUBECONFIG=$PWD/.cache/kubeconfig-spark-lab.yaml
   kubectl --context spark-root get node dgx-spark-1 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'; echo   # 15
   kubectl --context dev-lab get ns && kubectl --context dev-llms get ns                                        # both answer
   ```
5. **Scorecard on sema01:** `Score: 10/10`.

**If something's off:** Verdict lists `slurm_usable` → the Slurm node is drained or Slurm is not set up (Chapter 22); `sinfo -R` on dgx-spark-1. `k8s_ready_gpu` false right after the rebuild → the GPU Operator needs a few minutes to advertise the GPU; re-run `30.1 Validate`. More in Chapter 29 §5 and Chapter 19.

#### Run it

**Semaphore UI:** one template after the other, each ending in `failed=0`:

1. `site` (everything, idempotently), then `site` again: `changed=0` across the board.
2. `30.1 Validate`, and on the MacBook `tools/drift-cycle.sh; echo "drift exit=$?"` → 0.
3. Kubernetes from zero: `19.2 Reset Kubernetes` (extra variable `reset_confirm: RESET`), then `19.1 Kubernetes` → `20.1 GPU Operator` → `20.2 vClusters` bring back `spark-root` (with the LLM namespaces and budgets), `dev-lab` and `dev-llms`; `tools/fetch-kubeconfig.sh sema01` on the MacBook.
4. `28.1 Firmware inventory`.
5. The scorecard on sema01 (§1.3): target 10/10.

`site.yml` leaves out `03.1-bootstrap.yml`, `04.1-semaphore-target.yml` and `17.1-vault.yml` on purpose: they run from the MacBook, before Semaphore can log in or with an admin token Semaphore must never hold. The break-glass form of the same proof:

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
ansible-playbook playbooks/site.yml -l dgx-spark-1,localhost -K                 # everything, idempotently
ansible-playbook playbooks/site.yml -l dgx-spark-1,localhost -K                 # second run: changed=0 across the board
ansible-playbook playbooks/30.1-validate.yml -l dgx-spark-1,localhost -K
ansible-playbook playbooks/19.2-reset-kubernetes.yml -l dgx-spark-1,localhost -K   # type RESET
ansible-playbook playbooks/19.1-kubernetes.yml -l dgx-spark-1,localhost -K && ansible-playbook playbooks/20.1-gpu-operator.yml && ansible-playbook playbooks/20.2-vclusters.yml
```

## 5. Mastery criteria

- [ ] **Rebuild:** a re-imaged Spark returns to validated state using only playbooks (`03.1-bootstrap` and `04.1-semaphore-target` from the MacBook as in Chapter 04, then Semaphore templates), in under an hour (Chapter 03 drill), while Semaphore, vault01 and their history are untouched.
- [ ] **Idempotence:** `site.yml` second run is `changed=0`.
- [ ] **Three-angle proof:** validate + invariants + drift all green.
- [ ] **No long-lived secrets on the automation path:** Semaphore holds only the AppRole (its secret_id encrypted in the variable group); every task logs in with a 15-minute vault01 certificate; no Vault token or AppRole secret in any state folder (scorecard check `17/18`); vault01's unseal keys and root token kept off sema01 and the Sparks (Chapter 01 §11).
- [ ] **Chaos:** at least 5 of 7 faults found without the reveal.
- [ ] **Scorecard:** 10/10.

## 6. Where to go next

| Direction | Start with |
|---|---|
| More Sparks (3-ring or 4+ with a switch) | Chapter 13 §2.3, Chapter 14 QoS; NVIDIA's multi-Spark playbooks |
| Real DGX/HGX cluster | Grow the single kubeadm control plane into three (stacked etcd behind a VIP), or use Kubespray / Base Command Manager; keep vClusters for tenant isolation; Redfish modules for real BMCs (Chapter 03 §4); Fabric Manager in the driver flow (Chapter 10); InfiniBand + UFM (Chapter 13 §4) |
| Inference platform on the Spark pair | vLLM/TRT-LLM multi-node with the NCCL env from Chapter 14, models from Chapter 15, secrets from Chapter 18 |
| The rest of this repo | [`02-Kubernetes`](../02-Kubernetes/README.md), [`07-Nvidia`](../07-Nvidia/), [`08-Storage`](../08-Storage/README.md) |
