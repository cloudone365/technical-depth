# Chapter 04 · DGX Spark as a Semaphore Target: Add dgx-spark-1 to sema01 and vault01

> **01-Ansible · Part I — Management plane & Ansible foundations · Chapter 04 of 30** · ← [Chapter 03 · Bare-metal provisioning & bootstrap](03-bare-metal-provisioning-and-bootstrap.md) · [All chapters](00-ansible-step-by-step-guide.md) · [Chapter 05 · Execution internals & debugging](05-execution-internals-and-debugging.md) →
>
> Prerequisites: this chapter follows Chapters 01–03. [Chapter 01 · Management plane: Semaphore & Vault](01-management-plane-semaphore-and-vault.md) works end to end (its §9 passes), the MacBook toolchain and inventory from [Chapter 02](02-control-node-and-ansible-core.md) are in place, and `ssh dgxadmin@192.168.0.100 hostname` answers without a password ([Chapter 03](03-bare-metal-provisioning-and-bootstrap.md)). This chapter makes the DGX Spark the next target of that same Semaphore and Vault. Afterwards **every playbook of the Spark lab runs as a Semaphore task**, starting with first contact, custom facts and the OS baseline (§6); the [02-Kubernetes](../02-Kubernetes/README.md) module builds on the clusters the later tasks create.

**Goal:** the same rule as in Chapter 01: no human holds the automation credential. `sema01` and `vault01` stay outside the Spark. `dgx-spark-1` trusts vault01's SSH CA, and every Semaphore task logs in as `svc-ansible` with a 15-minute certificate. Kubernetes work (kubeadm, Cilium, the GPU Operator, the vClusters) is driven from the Semaphore container over the LAN.

Each step has a **Why**, then commands with a comment on every line, then a **Verify** block. Steps say where to type: **on your MacBook**, **on sema01**, **on vault01**, **on dgx-spark-1**, or **in Semaphore** (the web UI).

![The DGX Spark as a Semaphore target: sema01 and vault01 outside the Spark, dgx-spark-1 with the root cluster and two vClusters inside](diagrams/semaphore-dgx-spark.svg)

---

## 1. How the pieces fit

| Machine | Address | Role in the Spark lab | Built by |
|---|---|---|---|
| MacBook | DHCP | your terminal: browser to Semaphore, `git push`, `kubectl` for the 02-Kubernetes labs, and the two bootstrap playbooks | [Chapter 02](02-control-node-and-ansible-core.md) (toolchain, inventory), [Chapter 03](03-bare-metal-provisioning-and-bootstrap.md) (SSH trust) |
| `sema01` | 192.168.0.210 | **runs every lab playbook** (Semaphore + PostgreSQL in Docker). Its container is the Ansible *controller*: play 1, `kubectl`, `helm` and the `kubernetes.core` modules run there | Chapter 01 §7, plus §4 here |
| `vault01` | 192.168.0.211 | SSH CA (`ssh-client-signer`, role `ansible`), AppRole `semaphore`, audit log; plus the lab's secrets (§7) | Chapter 01 §3–4, plus §7 here |
| `dgx-spark-1` | 192.168.0.100 | the target: DGX OS, then the kubeadm root cluster `spark-root` with the vClusters `dev-lab` and `llms` inside it | the lab playbooks, run by Semaphore |
| 192.168.0.201 / .202 | — | your existing Chapter 01 targets, unchanged (project `lab`) | Chapter 01 |

**One Semaphore task, step by step** (template `19.1 Kubernetes` as the example):

1. You click **Run**. Semaphore checks your role and clones `technical-depth` (branch `main`).
2. **Play 1** (`playbooks/00-vault-cert.yml`, the same play as Chapter 01 §8.3) runs inside the container. It logs in to vault01 with the AppRole from the variable group and gets a 15-minute certificate for `svc-ansible`.
3. The host plays SSH to `dgx-spark-1` as `svc-ansible` with that certificate. sshd checks the CA signature, the principal and the expiry, and `sudo` (NOPASSWD) runs the tasks.
4. The controller plays run `kubectl`/`helm` in the container against `https://192.168.0.100:6443`. The kubeconfig with the contexts `spark-root`, `dev-lab` and `llms` lives on sema01's **state volume** (§4), not in the temporary checkout.
5. The task log and history stay in Semaphore. vault01's audit log has the login and the signature, and dgx-spark-1's sshd log has `Accepted publickey … ED25519-CERT`.

**Two ways in, decided by one variable.** [`inventory/group_vars/spark.yml`](lab/inventory/group_vars/spark.yml) switches the login on whether the Semaphore variable group (`vault_role_id`) is attached:

| Run from | `ansible_user` | Credential | Used for |
|---|---|---|---|
| Semaphore (normal) | `svc-ansible` | 15-minute certificate from play 1 | every template from `04.2 Ping` on |
| MacBook (bootstrap, break-glass) | `dgxadmin` (the admin user) | your own SSH key, sudo password with `-K` | `03.1-bootstrap.yml`, `04.1-semaphore-target.yml`, `17.1-vault.yml`, and emergencies when sema01 or vault01 is down (§11) |

> **Why the controller is outside the Spark.** `19.2-reset-kubernetes.yml` deletes everything Kubernetes runs; a rebuild of DGX OS deletes everything on the box. A Semaphore running *on* the Spark would delete itself halfway through its own job, and a broken cluster would take away the tool you need to fix it. With the management plane outside — like out-of-band management in a datacenter — the Spark can break freely.

---

## 2. Before you start

**Why:** the bootstrap playbooks run once from your MacBook, and they need the lab repository, a working Ansible, SSH as the admin user, and vault01's TLS certificate.

**Two one-time settings on the MacBook first.**

- **zsh and `#` comments.** macOS's zsh does **not** treat `#` as a comment when you type or paste commands: everything after it becomes arguments (errors like `zsh: unknown group`, `No such file or directory`, or a hanging quote from an apostrophe). The commented command blocks in these guides need it switched on:
  ```bash
  # ▶ MacBook · any folder
  setopt interactivecomments                                   # this shell
  echo 'setopt interactivecomments' >> ~/.zshrc                # every new shell
  ```
- **SSH names for the management plane**, so `vault01` and `sema01` in the commands below resolve to the right address and user. Your login user on vault01 is the one from Chapter 01 (`vault01` in its examples); put your own sema01 user in place of `<your-user>`:
  ```bash
  # ▶ MacBook · any folder
  cat >> ~/.ssh/config <<'EOF'
  Host vault01
    HostName 192.168.0.211
    User vault01
  Host sema01
    HostName 192.168.0.210
    User <your-user>
  Host dgx-spark-1
    HostName 192.168.0.100
    User dgxadmin
  EOF
  ssh vault01 hostname && ssh sema01 hostname                  # both answer
  ```

Then, on your MacBook (the repository and Ansible come from [Chapter 02](02-control-node-and-ansible-core.md) §3.1):

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
cd ~/technical-depth/"01-Ansible/lab"                       # the lab folder; every relative path below starts here
(umask 077 && mkdir -p .cache)                              # local state folder, private to you (git-ignored); no chmod, which some Mac antivirus tools block
scp vault01:~/vault-ca.crt .cache/vault-ca.crt               # vault01 TLS certificate (the copy you made in Chapter 01 §3.3)
curl --cacert .cache/vault-ca.crt https://192.168.0.211:8200/v1/sys/health   # JSON = the MacBook trusts vault01's TLS
ssh -t dgxadmin@192.168.0.100 'hostname; sudo -v && echo sudo-ok'   # admin login + sudo (-t: a terminal, so sudo can ask for the password)
```

**Verify 2:**

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ls -l .cache/vault-ca.crt                                    # the certificate is there
curl -s --cacert .cache/vault-ca.crt https://192.168.0.211:8200/v1/ssh-client-signer/public_key | cut -c1-20   # expect: ssh-rsa AAAA… (public, no token)
ansible -m ping dgx-spark-1 -K                              # expect: pong (as dgxadmin, your key)
```

`{"errors":["Vault is sealed"]}` instead of `ssh-rsa …`? The network and TLS are fine (you got an answer from Vault), but Vault is locked. It seals itself every time vault01 restarts and stays sealed until you unseal it with two of the three unseal keys from Chapter 01 §3.4:

```bash
# ▶ MacBook · any folder
ssh vault01                                                  # or: ssh vault01@192.168.0.211
```

```bash
# ▶ vault01 (ssh vault01)
vault status                                                 # Sealed true, Unseal Progress 0/2
vault operator unseal                                        # unseal key 1
vault operator unseal                                        # unseal key 2 (a different one)
vault status                                                 # Sealed false
```

Then repeat the `curl`. While Vault is sealed, every Semaphore task fails at play 1 (*Get an SSH certificate from Vault*), so after any vault01 reboot, unseal it first. Auto-unseal removes this step (Chapter 17 §5).

No `pong`? Then the Spark doesn't take your key as `dgxadmin` on 192.168.0.100 yet: that is the end state of [Chapter 03](03-bare-metal-provisioning-and-bootstrap.md) (bootstrap for a fresh DGX OS, `ssh-copy-id` for an installed one).

---

## 3. Make dgx-spark-1 trust vault01 (on your MacBook)

**Why:** this is Chapter 01 §5 for the Spark, as a playbook instead of by hand. `04.1-semaphore-target.yml`:

- creates `svc-ansible` without a password;
- gives it NOPASSWD sudo (checked with `visudo -c` before it lands);
- writes vault01's CA public key to `/etc/ssh/trusted-user-ca-keys.pem`;
- adds `/etc/ssh/sshd_config.d/10-vault-ca.conf`, which trusts the CA and allows `svc-ansible` only key or certificate logins (checked with `sshd -t`);
- reloads sshd.

Your `dgxadmin` login is untouched, so you can't lock yourself out.

### 3.1 Run it

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/04.1-semaphore-target.yml -l dgx-spark-1,localhost -K   # localhost: fetches the CA key from vault01
```

`-l dgx-spark-1,localhost` is optional while `dgx-spark-2` is commented out in [`inventory/hosts.yml`](lab/inventory/hosts.yml), as it is now. If you limit a run, keep `localhost` in the limit: the CA key is fetched there, and play 1 runs there in every Semaphore template (§5.5).

### 3.2 Verify on dgx-spark-1

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
id svc-ansible                                               # expect a uid line
sudo visudo -c                                               # expect: parsed OK (including /etc/sudoers.d/90-svc-ansible)
sudo sshd -T | grep -i trustedusercakeys                     # expect: trustedusercakeys /etc/ssh/trusted-user-ca-keys.pem
sudo ssh-keygen -l -f /etc/ssh/trusted-user-ca-keys.pem      # expect the SAME SHA256 fingerprint as vault01 prints below
```

On vault01: `vault read -field=public_key ssh-client-signer/config/ca > ~/ca.pub && ssh-keygen -l -f ~/ca.pub`.

### 3.3 Test the CA before involving Semaphore (on vault01)

The same test as Chapter 01 §6, against the Spark. If Semaphore fails later, you'll know the problem isn't the trust:

```bash
# ▶ vault01 (ssh vault01)
ssh-keygen -t ed25519 -f ~/semaphore_lab -N "" <<<y >/dev/null       # test key (overwrites the Chapter 01 one)
vault write -field=signed_key ssh-client-signer/sign/ansible \
  public_key=@$HOME/semaphore_lab.pub valid_principals=svc-ansible > ~/semaphore_lab-cert.pub
ssh -i ~/semaphore_lab -o CertificateFile=~/semaphore_lab-cert.pub svc-ansible@192.168.0.100 'hostname; sudo -n whoami'
```

Expected: `dgx-spark-1` and `root`. `01-Ansible/lab/tools/vault-ssh-cert.sh` does the same in one command if you have the repository on vault01.

---

## 4. Give Semaphore the lab's tools and a place to keep state (on sema01)

**Why:** two things differ from the Ubuntu targets.

- **Kubernetes tools.** Several Spark playbooks run on the controller: kubeadm's kubeconfig merge, `helm` installs of Cilium, MetalLB and the GPU Operator, the vCluster charts, `kubectl drain`. The stock Semaphore image has no `kubectl`, `helm`, `kubernetes` Python library or `kubernetes.core` collection.
- **State.** The lab writes files the next run needs to its state folder: the kubeconfig with three contexts, the kubeadm join material, validation reports, incident bundles. Semaphore's checkout of the repository is temporary, and `/tmp` is gone after a container restart. So the state goes to a host folder, `/opt/spark-lab/cache`, and the playbooks find it through `SPARK_LAB_CACHE` (`lab_cache_dir` in [`group_vars/all.yml`](lab/inventory/group_vars/all.yml)).

[`lab/semaphore/`](lab/semaphore/) has both pieces:

| File | What it does |
|---|---|
| [`Dockerfile`](lab/semaphore/Dockerfile) | `FROM semaphoreui/semaphore`, plus `kubectl` v1.36.5, `helm`, the Python libraries in [`requirements-semaphore.txt`](lab/semaphore/requirements-semaphore.txt) and the collections in [`requirements.yml`](lab/requirements.yml) |
| [`docker-compose.override.yml`](lab/semaphore/docker-compose.override.yml) | merged with your Chapter 01 `docker-compose.yml`: builds that image, mounts `/opt/spark-lab/cache`, sets `ANSIBLE_CONFIG`, `SPARK_LAB_CACHE` and the log and fact paths |

```bash
# ▶ sema01 (ssh sema01)
cd ~ && git clone https://github.com/cloudone365/technical-depth.git   # the lab repository, as the image's build context
sudo install -d -o 1001 -g 0 -m 0700 /opt/spark-lab/cache            # state folder, owned by the container's semaphore user (uid 1001)
cp ~/technical-depth/"01-Ansible/lab/semaphore/docker-compose.override.yml" ~/semaphore/   # next to the Chapter 01 compose file
cd ~/semaphore
docker compose build semaphore                                       # builds semaphore-spark-lab:local (a few minutes)
docker compose up -d                                                 # recreates the semaphore container; PostgreSQL and your data are untouched
```

`/opt/spark-lab/cache` will hold cluster-admin kubeconfigs: back it up like the `.env` file (Chapter 01 §7.3) and keep it readable by the container user only. To update the tools later (on sema01, in `~/semaphore`): `git -C ~/technical-depth pull`, then `docker compose build semaphore && docker compose up -d`.

**Verify 4 (on sema01, in ~/semaphore):**

```bash
# ▶ sema01 (ssh sema01)
docker compose ps                                                    # expect: postgres and semaphore Up
docker compose exec semaphore kubectl version --client               # expect: Client Version: v1.36.5
docker compose exec semaphore helm version --short                   # expect: v3.18.x
docker compose exec semaphore ansible-galaxy collection list kubernetes.core   # expect: kubernetes.core 5.x or newer
docker compose exec semaphore sh -c 'echo $SPARK_LAB_CACHE; touch $SPARK_LAB_CACHE/.w && echo writable'   # expect the path, then: writable
```

Your Chapter 01 project `lab` keeps working: same database, same keys, and the new image is a superset of the old one.

---

## 5. A Semaphore project for the Spark lab (in Semaphore)

**Why:** a separate project keeps the Spark's repository, inventory and templates apart from the Chapter 01 `lab` project, and lets you give other people access to one without the other. Menu names differ slightly between Semaphore versions.

### 5.1 Project

**Semaphore UI:** **New Project** → name `spark-lab`. Everything below happens inside it.

### 5.2 Key for the repository

**Semaphore UI:** **Key Store → New Key**: name `github-token`, type *Login with password*. Login is your GitHub user; Password is a **read-only** fine-grained token for `technical-depth` (Chapter 01 §8.2: Contents → Read-only). If the repository is public, type *None* works too. An SSH deploy key (Chapter 01 §8.5) is the stronger option.

### 5.3 Variable group `vault-approle`

**Semaphore UI:** **Variable Groups → New**, exactly as Chapter 01 §8.6 (same AppRole, so the same values work):

1. Name `vault-approle`.
2. Extra variables (JSON): `{"vault_role_id": "PASTE-THE-ROLE-ID", "vault_lab_secrets_enabled": false}`.
3. Secrets → Add secret: type *Variable*, name `vault_secret_id`, value = a secret_id from vault01 (`vault write -f -field=secret_id auth/approle/role/semaphore/secret-id`).
4. Save.

`vault_role_id` being defined is what switches the lab to `svc-ansible` with a certificate (§1). Set `vault_lab_secrets_enabled` to `true` after §7.

### 5.4 Repository

**Semaphore UI:** **Repositories → New**: name `technical-depth`, URL `https://github.com/cloudone365/technical-depth.git`, branch `main`, access key `github-token`.

### 5.5 Inventory

**Semaphore UI:** **Inventory → New**: name `spark-lab`, type **File**, repository `technical-depth`, path `01-Ansible/lab/inventory/hosts.yml`, user credentials *None* (play 1 supplies the key).

The inventory file comes with its `group_vars/` and `host_vars/`, so addresses, the automation user, the certificate path and `StrictHostKeyChecking=accept-new` are all already there; you don't retype them.

**One Spark or two:** `dgx-spark-2` is commented out in the inventory, so the templates need no limit. When dgx-spark-2 joins, uncomment its lines in `hosts.yml` (groups `spark`, `k8s_workers`, `nfs_client`) and push. If you ever add a `--limit` to a template, include `localhost`: play 1 and all the Kubernetes plays run there.

### 5.6 First template: `04.2 Ping`

**Semaphore UI:** **Task Templates → New Template**, type *Ansible Playbook*:

| Field | Value |
|---|---|
| Name | `04.2 Ping` |
| Playbook filename | `01-Ansible/lab/playbooks/04.2-ping.yml` |
| Inventory | `spark-lab` |
| Repository | `technical-depth` |
| Variable group (Environment) | `vault-approle` |
| CLI args | none needed with one Spark (if you add a limit: `["--limit", "dgx-spark-1,localhost"]`) |

Save, then **Run**.

**Verify 5:** the task log shows two plays.

- **Get an SSH certificate from Vault** (on `localhost`), with its secret tasks hidden.
- **Connectivity and identity check**, with `ok` on `dgx-spark-1`, ending in `failed=0`. What it reports about the Spark is the subject of §6.1.

Then check all three systems: this is the proof that the login used a vault01 certificate.

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
sudo journalctl -u ssh --since "10 minutes ago" | grep svc-ansible   # "Accepted publickey for svc-ansible … ED25519-CERT"
```

```bash
# ▶ vault01 (ssh vault01)
sudo grep -c 'sign/ansible' /var/log/vault_audit.log                 # the count grows with each task run
```

```bash
# ▶ sema01 (ssh sema01)
# in ~/semaphore
docker compose exec semaphore ssh-keygen -L -f /tmp/lab_ssh/id_ed25519-cert.pub | grep -A1 Principals   # svc-ansible
```

---

## 6. First contact, custom facts and OS baseline (in Semaphore)

**Why:** §5.6 proved that Semaphore, vault01 and the Spark trust each other. Now Ansible starts working on the Spark: read what the first template reports, teach Ansible about the GPU, and apply the OS baseline every later chapter builds on. Each playbook runs as a Semaphore template; the `ansible-playbook … -K` lines are the break-glass form from the MacBook (§11), as `dgxadmin` with your own key.

### 6.1 First contact

`04.2 Ping` already ran in §5.6, where it proved the certificate chain. This time, read what it says about the Spark:

```yaml
# lab/playbooks/04.2-ping.yml
---
# Day-0 connectivity: SSH works, sudo works, Python works, it IS a Spark.
- name: Short-lived SSH certificate from vault01 (Semaphore runs only)
  ansible.builtin.import_playbook: 00-vault-cert.yml

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

**Semaphore UI:** this is the template **`04.2 Ping`** (§5.6): run it again and open the log. It shows play 1, *Get an SSH certificate from Vault*, then this play. Break-glass from the MacBook:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/04.2-ping.yml -K     # -K prompts for dgxadmin's sudo password
```

Expected output (trimmed; your exact numbers and kernel will differ):

```
ok: [dgx-spark-1] => msg: dgx-spark-1 aarch64 20 cores 119.6 GiB Ubuntu 24.04 kernel 6.x-…-nvidia
```

> The reported memory is slightly under 128 GB: firmware and carve-outs take some. The `spark_expected.mem_total_gib_min: 110` guard allows for that.

Ad-hoc commands are how you poke a box without writing a playbook. Semaphore runs playbooks, not ad-hoc commands, so these run **from your MacBook** as `dgxadmin` (no Semaphore variable group → your own key). Name the host instead of the group `spark` while the optional dgx-spark-2 isn't there:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible dgx-spark-1 -m command -a "nvidia-smi --query-gpu=name,driver_version --format=csv"
ansible dgx-spark-1 -m shell   -a "free -g | head -2"
ansible dgx-spark-1 -m setup   -a "filter=ansible_processor*"
ansible dgx-spark-1 -b -K -m apt -a "name=nvtop state=present"   # -b = become, -K = dgxadmin's sudo password
```

### 6.2 Teach Ansible about the GPU: custom facts

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

**Semaphore UI:** create the template **`04.3 Baseline`**: the same fields as `04.2 Ping` (§5.6), with playbook filename `01-Ansible/lab/playbooks/04.3-baseline.yml`. Its first role is `spark_facts` (tag `facts`), so every run installs the fact before the baseline; you run the template in §6.3. To look at the fact on its own first, run only that tag with the break-glass form, then read the result with an ad-hoc `setup` (from the MacBook, like every ad-hoc command):

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/04.3-baseline.yml -K --tags facts -v    # break-glass: only the spark_facts role
ansible dgx-spark-1 -m setup -a "filter=ansible_local" | less      # ad-hoc, from the MacBook
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

### 6.3 OS baseline

The `spark_baseline` role installs the tooling you'll need in every later chapter, pins the NVIDIA driver stack against accidental upgrades, and applies sysctl, SSH and time settings.

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
# missing hold would be invisible to drift detection (Chapter 26).
- name: Report missing holds as drift in check mode
  ansible.builtin.debug:
    msg: "Would hold: {{ spark_baseline_nvidia_pkgs | difference(spark_baseline_holds.stdout_lines) }}"
  changed_when: true
  when:
    - ansible_check_mode
    - spark_baseline_hold_nvidia | bool
    - spark_baseline_nvidia_pkgs | difference(spark_baseline_holds.stdout_lines) | length > 0
```

**Semaphore UI:** run `04.3 Baseline` with *Dry run* ticked (`--check --diff`) for the preview, then run it for real, then run it again. Break-glass from the MacBook:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/04.3-baseline.yml -K --check --diff   # preview
ansible-playbook playbooks/04.3-baseline.yml -K                  # apply
ansible-playbook playbooks/04.3-baseline.yml -K                  # again → changed=0
```

**The second run must report `changed=0`.** If it doesn't, a task isn't idempotent. Fix it before moving on, or drift detection (Chapter 26) will cry wolf forever.

### 6.4 Integrations introduced here

| Integration | How | Used again in chapter |
|---|---|---|
| DGX OS release metadata | `/etc/dgx-release` → `ansible_local.spark.dgx_release` | 10, 28 (upgrade gating), 26 (drift) |
| NVIDIA driver/CUDA | `nvidia-smi` → `ansible_local.spark.gpu` | 11, 20, 30 |
| CX-7 / RDMA | `ibdev2netdev` + sysfs → `ansible_local.spark.cx7` | 13, 14, 15 |
| apt holds | `package_facts` + `apt-mark hold` | 10, 28 |
| DGX Dashboard | untouched; `AllowTcpForwarding yes` keeps SSH tunnels to `localhost:11000` working | 12 |

### 6.5 Verify

**Verify 6** (ad-hoc, from the MacBook; `-b -K` = become with dgxadmin's sudo password):

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible dgx-spark-1 -m command -a "test -x /etc/ansible/facts.d/spark.fact"
ansible dgx-spark-1 -m command -a "apt-mark showhold" -b -K | grep -c nvidia     # > 0
ansible dgx-spark-1 -m command -a "sysctl -n vm.max_map_count" -b -K             # 1048576
ansible dgx-spark-1 -m command -a "sshd -T" -b -K | grep -E 'permitrootlogin|allowtcpforwarding'
```

- [ ] `04.2 Ping` reports `dgx-spark-1 aarch64 20 cores … Ubuntu 24.04` with `failed=0`
- [ ] `ansible_local.spark.gpu.present == true` and `compute_cap == "12.1"`
- [ ] `04.3 Baseline` second run: `changed=0`
- [ ] Semaphore's task history (and `ansible.log` in the state folder, `/opt/spark-lab/cache` on sema01) contains both runs

### 6.6 Break-it exercises

1. Make the fact script print `hello` before the JSON. What error do you get, and at which task?
2. Time `04.3-baseline.yml` with `profile_tasks`, then again without pipelining (break-glass: `ANSIBLE_PIPELINING=0 ansible-playbook playbooks/04.3-baseline.yml -K`). How many seconds does it add? (Chapter 09 explains why.)
3. Hold a non-NVIDIA package by hand (`apt-mark hold jq`). Does the role release it? Should it?

---

## 7. Lab secrets in vault01 (on your MacBook, once)

**Why:** some lab playbooks need secrets, for example the NGC API key that `11.1-containers.yml` uses to pull NVIDIA images. They belong in vault01, next to the SSH CA, not in the repository. Setting this up needs an **admin** token, which Semaphore must never hold, so you run it from the MacBook.

[`17.1-vault.yml`](lab/playbooks/17.1-vault.yml) (role [`vault_config`](lab/roles/vault_config/)):

- **checks** what Chapter 01 built (the signing role allows `svc-ansible`; AppRole `semaphore` exists) and never rewrites it;
- mounts a KV v2 engine at `kv/`;
- writes the read-only policy `spark-lab-read` (`kv/data/spark-lab/*`);
- adds it to AppRole `semaphore`, so its tokens carry `semaphore-ssh` **and** `spark-lab-read`;
- seeds a placeholder `kv/spark-lab/ngc`.

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
export VAULT_ADDR=https://192.168.0.211:8200 VAULT_CACERT=$PWD/.cache/vault-ca.crt   # the vault CLI on the MacBook talks to vault01
vault login                                      # an admin token (root token in the lab; a named admin in production)
export VAULT_TOKEN=$(vault print token)          # the playbook reads it from the environment; it is never written to disk
ansible-playbook playbooks/17.1-vault.yml          # localhost only: talks to vault01's API
vault kv put kv/spark-lab/ngc api_key=<your NGC API key>   # the real value replaces the placeholder
unset VAULT_TOKEN                                # don't leave an admin token in the shell
```

No `vault` CLI on the MacBook? Run the `vault` commands on vault01 instead, and paste the token into `export VAULT_TOKEN=…` on the MacBook for the playbook run.

**Semaphore UI:** variable group `vault-approle` → change `vault_lab_secrets_enabled` to `true`.

**Verify 7:**

```bash
# ▶ vault01 (ssh vault01)
vault read auth/approle/role/semaphore | grep token_policies   # expect: [semaphore-ssh spark-lab-read]
vault kv get -field=api_key kv/spark-lab/ngc | cut -c1-6       # the first characters of your key, not REPLACE_ME
```

**Semaphore UI:** add and run a template `18.1 Vault integration` (`01-Ansible/lab/playbooks/18.1-vault-integration.yml`, same inventory, variable group and CLI args). Its log reports `Lab secrets read from kv/spark-lab/: ['ngc']` and `NGC key present: True`, without ever printing the key.

---

## 8. Build the lab from Semaphore

**Why:** from here on, the [step-by-step guide](00-ansible-step-by-step-guide.md) and the chapter documents say `ansible-playbook playbooks/NN.n-….yml`. In this lab that means **run the template of the same number**: every template uses the same inventory, repository, variable group and CLI args as `04.2 Ping`, and only the playbook filename changes.

### 8.1 The templates

Create the remaining templates (`04.2 Ping` and `04.3 Baseline` exist already), listed in number order. A template's number is `<chapter>.<n>`: the chapter that explains it, then its place in that chapter, so `19.1 Kubernetes` runs `19.1-kubernetes.yml` and is explained in Chapter 19. Where another chapter also covers a template, the **Notes** column says so. Extra variables go in the template's *Extra variables* or a survey.

| Template | Playbook (`01-Ansible/lab/playbooks/…`) | Notes |
|---|---|---|
| `03.2 Redfish practice` | `03.2-redfish-practice.yml` | Redfish mockup BMC, practice for data-centre nodes |
| `04.2 Ping` | `04.2-ping.yml` | first test (§5.6); explained in §6.1 |
| `04.3 Baseline` | `04.3-baseline.yml` | created in §6.2: custom facts (§6.2), OS, packages, sysctls (§6.3); also Chapters 03, 10 |
| `07.1 Jinja lab` | `07.1-jinja-lab.yml` | the 7 Jinja katas; localhost only |
| `10.1 Driver audit` | `10.1-driver-audit.yml` | driver consistency |
| `10.2 DGX OS upgrade` | `10.2-dgxos-upgrade.yml` | reboots: extra variable `vault_ssh_cert_ttl: 1h` (§13); also Chapter 28 |
| `11.1 Containers` | `11.1-containers.yml` | Docker, NVIDIA toolkit, NGC login (key from §7) |
| `11.2 CUDA smoke` | `11.2-cuda-smoke.yml` | sm_121 + PyTorch smoke test |
| `12.1 Telemetry` | `12.1-telemetry.yml` | DCGM exporter, node exporter |
| `13.1 Fabric` | `13.1-fabric.yml` | CX-7 addressing: only with dgx-spark-2; also Chapter 14 |
| `13.2 RDMA perftest` | `13.2-rdma-perftest.yml` | needs dgx-spark-2 |
| `14.1 RoCE QoS` | `14.1-roce-qos.yml` | optional: DSCP/PFC/ECN; needs dgx-spark-2 |
| `14.2 NCCL test` | `14.2-nccl-test.yml` | needs dgx-spark-2 |
| `15.1 NFS RDMA` | `15.1-nfs-rdma.yml` | shared model cache; needs dgx-spark-2 |
| `16.1 GDS check` | `16.1-gds-check.yml` | GDS / cuFile assessment |
| `18.1 Vault integration` | `18.1-vault-integration.yml` | created in §7; reads `kv/spark-lab/ngc` without printing it |
| `19.1 Kubernetes` | `19.1-kubernetes.yml` | kubeadm root cluster, Cilium, MetalLB; writes the kubeconfig to the state volume |
| `19.2 Reset Kubernetes` | `19.2-reset-kubernetes.yml` | **danger zone**: see 8.3; explained in Chapter 19 §8 |
| `20.1 GPU Operator` | `20.1-gpu-operator.yml` | 15 time-slices |
| `20.2 vClusters` | `20.2-vclusters.yml` | `dev-lab` and `llms`; adds their contexts; explained in Chapter 19 §3.4 and 02-Kubernetes Chapter 04 |
| `21.1 Multus RDMA` | `21.1-multus-rdma.yml` | secondary CX-7 networks |
| `22.1 Slurm` | `22.1-slurm.yml` | Slurm with GRES and cgroup GPUs |
| `26.1 Drift check` | `26.1-drift-check.yml` | check mode is built in (changes nothing); schedule it nightly |
| `27.1 Logging audit` | `27.1-logging-audit.yml` | auditd, Loki, Alloy, ARA |
| `28.1 Firmware inventory` | `28.1-firmware-inventory.yml` | firmware + security floor |
| `29.1 Emergency drain` | `29.1-emergency-drain.yml` | drains through context `spark-root`; with a reboot, `vault_ssh_cert_ttl: 1h` |
| `29.2 UMA relief` | `29.2-uma-relief.yml` | Runbook C: diagnose, then relieve unified-memory pressure |
| `30.1 Validate` | `30.1-validate.yml` | the golden-value gate |
| `30.2 Chaos` | `30.2-chaos.yml` | capstone fault injection |
| `site` | `site.yml` | `04.3 Baseline`, `13.1 Fabric`, `11.1 Containers`, `12.1 Telemetry`, `19.1 Kubernetes`, `20.1 GPU Operator`, `20.2 vClusters`, `22.1 Slurm`, `15.1 NFS RDMA` and `30.1 Validate` in one task; extra variable `vault_ssh_cert_ttl: 1h` |

The playbooks `03.1-bootstrap.yml`, `04.1-semaphore-target.yml`, `09.1-fleet-sim.yml`, `09.2-fleet-bench.yml` and `17.1-vault.yml` have no template: they run from the MacBook. `00-vault-cert.yml` is play 1, imported by the others.

### 8.2 Build order

`04.3 Baseline` already ran in §6.3. Then run `11.1 Containers` → `12.1 Telemetry` → `19.1 Kubernetes` → `20.1 GPU Operator` → `20.2 vClusters`, one after the other. Or run `site` once (it starts with `04.3 Baseline` again, which is safe: the baseline is idempotent). Each task log must end in `failed=0` before you start the next one: the same checkpoints as in §6 and Chapters 11, 12, 19 and 20 ([all chapters](00-ansible-step-by-step-guide.md)).

### 8.3 The danger zone

`19.2-reset-kubernetes.yml` asks you to type `RESET`, and a Semaphore task can't answer prompts. Give the template an extra variable instead: Ansible skips a prompt when the variable is already set.

- Extra variables: `{"reset_confirm": "RESET"}`, optional `"reset_wipe_data": true`.
- Permissions: in **Team**, give other users the *Task Runner* role at most, and keep this template for yourself. The clean option is a third project `spark-danger` that only you can open.

Never schedule it.

### 8.4 Your kubeconfig on the MacBook

`19.1 Kubernetes` and `20.2 vClusters` write `kubeconfig-spark-lab.yaml` (contexts `spark-root`, `dev-lab`, `llms`) to sema01's state volume. The 02-Kubernetes labs run `kubectl` from your MacBook and expect it at `01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml`. Copy it there after each of those two tasks:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
tools/fetch-kubeconfig.sh sema01                                     # reads it through the container, writes .cache/kubeconfig-spark-lab.yaml (0600)
export KUBECONFIG="$PWD/.cache/kubeconfig-spark-lab.yaml"            # what every 02-Kubernetes command uses
kubectl --context spark-root get nodes                               # expect: dgx-spark-1 Ready control-plane
kubectl --context dev-lab get ns && kubectl --context llms get ns    # both vClusters answer
```

This kubeconfig holds cluster-admin certificates, the human side of the lab, and you need it for the Kubernetes labs. Keep it on the MacBook only (it's git-ignored). In an enterprise, people get short-lived OIDC logins instead (02-Kubernetes Chapter 03).

### 8.5 Renaming templates from the old numbering

The playbooks used to carry their own numbers (`05-kubernetes.yml`, template `05 Kubernetes`). They now carry the number of the chapter that explains them (`19.1-kubernetes.yml`, template `19.1 Kubernetes`). If you created templates on sema01 before the rename, Semaphore still points them at the old file names, and their runs fail because those files no longer exist in the repository. Open each template in project `spark-lab`, change its **Name** and its **Playbook** path, and save.

Edit the templates; don't delete and recreate them. Semaphore keeps the task history per template: deleting a template throws away its record of who ran what and when (Chapter 27), and a recreated template starts empty. Schedules, surveys and extra variables also stay with the edited template.

| Old template · old playbook | New template · new playbook |
|---|---|
| `00 Ping` · `00-ping.yml` | `04.2 Ping` · `04.2-ping.yml` |
| `01 Baseline` · `01-baseline.yml` | `04.3 Baseline` · `04.3-baseline.yml` |
| — (MacBook only) · `00-bootstrap.yml` | — (MacBook only) · `03.1-bootstrap.yml` |
| `12 Redfish practice` · `12-redfish-practice.yml` | `03.2 Redfish practice` · `03.2-redfish-practice.yml` |
| — (MacBook only) · `00b-semaphore-target.yml` | — (MacBook only) · `04.1-semaphore-target.yml` |
| `15 Jinja lab` · `15-jinja-lab.yml` | `07.1 Jinja lab` · `07.1-jinja-lab.yml` |
| — (MacBook only) · `13-fleet-sim.yml` | — (MacBook only) · `09.1-fleet-sim.yml` |
| — (MacBook only) · `14-fleet-bench.yml` | — (MacBook only) · `09.2-fleet-bench.yml` |
| `16 Driver audit` · `16-driver-audit.yml` | `10.1 Driver audit` · `10.1-driver-audit.yml` |
| `17 DGX OS upgrade` · `17-dgxos-upgrade.yml` | `10.2 DGX OS upgrade` · `10.2-dgxos-upgrade.yml` |
| `03 Containers` · `03-containers.yml` | `11.1 Containers` · `11.1-containers.yml` |
| `18 CUDA smoke` · `18-cuda-smoke.yml` | `11.2 CUDA smoke` · `11.2-cuda-smoke.yml` |
| `04 Telemetry` · `04-telemetry.yml` | `12.1 Telemetry` · `12.1-telemetry.yml` |
| `02 Fabric` · `02-fabric.yml` | `13.1 Fabric` · `13.1-fabric.yml` |
| `11 RDMA perftest` · `11-rdma-perftest.yml` | `13.2 RDMA perftest` · `13.2-rdma-perftest.yml` |
| `12b RoCE QoS` · `12b-roce-qos.yml` | `14.1 RoCE QoS` · `14.1-roce-qos.yml` |
| `10 NCCL test` · `10-nccl-test.yml` | `14.2 NCCL test` · `14.2-nccl-test.yml` |
| `09 NFS RDMA` · `09-nfs-rdma.yml` | `15.1 NFS RDMA` · `15.1-nfs-rdma.yml` |
| `14 GDS check` · `14-gds-check.yml` | `16.1 GDS check` · `16.1-gds-check.yml` |
| — (MacBook only) · `08-vault.yml` | — (MacBook only) · `17.1-vault.yml` |
| `19 Vault integration` · `19-vault-integration.yml` | `18.1 Vault integration` · `18.1-vault-integration.yml` |
| `05 Kubernetes` · `05-kubernetes.yml` | `19.1 Kubernetes` · `19.1-kubernetes.yml` |
| `99 Reset Kubernetes` · `99-reset-kubernetes.yml` | `19.2 Reset Kubernetes` · `19.2-reset-kubernetes.yml` |
| `06 GPU Operator` · `06-gpu-operator.yml` | `20.1 GPU Operator` · `20.1-gpu-operator.yml` |
| `06b vClusters` · `06b-vclusters.yml` | `20.2 vClusters` · `20.2-vclusters.yml` |
| `13 Multus RDMA` · `13-multus-rdma.yml` | `21.1 Multus RDMA` · `21.1-multus-rdma.yml` |
| `07 Slurm` · `07-slurm.yml` | `22.1 Slurm` · `22.1-slurm.yml` |
| `20 Drift check` · `20-drift-check.yml` | `26.1 Drift check` · `26.1-drift-check.yml` |
| `23 Logging audit` · `23-logging-audit.yml` | `27.1 Logging audit` · `27.1-logging-audit.yml` |
| `19 Firmware inventory` · `19-firmware-inventory.yml` | `28.1 Firmware inventory` · `28.1-firmware-inventory.yml` |
| `21 Emergency drain` · `21-emergency-drain.yml` | `29.1 Emergency drain` · `29.1-emergency-drain.yml` |
| `24 UMA relief` · `24-uma-relief.yml` | `29.2 UMA relief` · `29.2-uma-relief.yml` |
| `30 Validate` · `30-validate.yml` | `30.1 Validate` · `30.1-validate.yml` |
| `25 Chaos` · `25-chaos.yml` | `30.2 Chaos` · `30.2-chaos.yml` |

Unchanged: template `site` (`site.yml`, which imports the new names itself) and the `00-vault-cert.yml` import in every playbook. The rows marked *MacBook only* have no template; only your shell history and notes need the new names.

---

## 9. Verify the full chain

| Where | What to check | What it proves |
|---|---|---|
| Semaphore task log | Play 1 ok (secret tasks hidden), then host plays on `dgx-spark-1`, `failed=0` | Semaphore → Vault → Spark works |
| dgx-spark-1: `sudo journalctl -u ssh \| grep svc-ansible` | `Accepted publickey … ED25519-CERT ID …` | login used a certificate, not a plain key |
| dgx-spark-1: `sudo grep svc-ansible /var/log/auth.log \| grep COMMAND` | sudo lines for the tasks | privileged actions are traceable to the automation account |
| vault01: `sudo grep -c 'sign/ansible' /var/log/vault_audit.log` | grows by one per task | every certificate request is recorded |
| sema01: `docker compose exec semaphore ls /var/lib/spark-lab/cache` | `kubeconfig-spark-lab.yaml`, `kubeconfig-dev-lab.yaml`, `kubeconfig-llms.yaml`, `validation/` … | lab state survives the temporary checkout |
| MacBook: `kubectl --context llms get nodes` | `dgx-spark-1` (synced from the root) | the 02-Kubernetes labs can start |
| Expiry test | 16 minutes after a task, `docker compose exec semaphore ssh -i /tmp/lab_ssh/id_ed25519 svc-ansible@192.168.0.100 true` is refused; a new task run succeeds | credentials expire without cleanup |

---

## 10. Day-2 operations from Semaphore

- **Schedules:** `26.1 Drift check` nightly (it only reports, it never changes anything), and `30.1 Validate` weekly. A failed scheduled task is your alert (add a Telegram, Slack or e-mail alert in project settings).
- **Upgrades** (`10.2 DGX OS upgrade`, Chapters 10/28) and **drains** (`29.1 Emergency drain`, Chapter 29): run them from Semaphore so every maintenance action has a record of who ran it, when and with what result.
- **Rebuild:** `19.2 Reset Kubernetes`, then `19.1 Kubernetes` → `20.1 GPU Operator` → `20.2 vClusters`, then `tools/fetch-kubeconfig.sh`. Semaphore, Vault and their history are untouched by any of it.
- **What Semaphore does *not* do:** workloads inside the vClusters (vLLM, tenant apps, quotas). Those are Git → Argo CD (02-Kubernetes [Chapter 28](../02-Kubernetes/28-production-mlops-and-gitops.md)). One tool per object, or the two will undo each other's changes.

---

## 11. Break-glass: running from the MacBook

When sema01 or vault01 is down, you can still reach the Spark the way you did in §2:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
cd ~/technical-depth/"01-Ansible/lab"
ansible-playbook playbooks/29.1-emergency-drain.yml -l dgx-spark-1,localhost -K   # no vault_role_id → dgxadmin + your key; play 1 is skipped
```

State then goes to the MacBook's `.cache/` instead of sema01's volume. After the emergency, re-run the affected template in Semaphore, so the record and the state are back in one place.

---

## 12. Security notes

| Item | Lab today | Better |
|---|---|---|
| AppRole secret_id | one static secret_id in the variable group | `secret_id_ttl`, `secret_id_bound_cidrs=192.168.0.210/32`, rotation (Chapter 01 §11) |
| State volume | cluster-admin kubeconfigs and kubeadm join material on sema01 | encrypt the disk, back it up, restrict who can `docker exec` into the container |
| Template permissions | everyone in the project can run everything | *Task Runner* role for others; dangerous templates in a separate project |
| Container image | `semaphoreui/semaphore:latest` + tools built locally | pin the base tag, rebuild on a schedule, scan the image |
| Lab secrets | read-only policy for `kv/spark-lab/*` | one policy per template class (build vs day-2), short token TTLs |
| Password login on the Spark | `dgxadmin` still accepts its password over SSH | `spark_baseline_ssh_disable_passwords: true` (§6.3), **after** you've confirmed key login works from two places |
| Automation user | `svc-ansible` with NOPASSWD sudo, reachable only with a 15-minute vault01 certificate (§3) | keep NOPASSWD only where Vault-signed certificates are in place (Chapter 18) |

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `zsh: unknown group`, `No such file or directory` for words from a comment, or a `quote>` prompt | zsh on macOS doesn't treat `#` as a comment | `setopt interactivecomments` (and add it to `~/.zshrc`), §2 |
| `ssh: Could not resolve hostname vault01` / wrong user on vault01 or sema01 | no SSH names on the MacBook | `~/.ssh/config` entries, §2 |
| Can't find where to delete or rename a project (no "Settings" in the sidebar) | project settings are a tab on **Dashboard** (History · Activity · Settings), not a sidebar entry, and only for the project's Owner | open `http://192.168.0.210:3000/project/<id>/settings` (the number from the address bar); **Delete Project** is at the bottom. Deleting removes the project's templates, inventories, key store, variable groups and task history, nothing on the hosts |
| `{"errors":["Vault is sealed"]}` from `curl`, or play 1 fails with `Vault is sealed` / HTTP 503 | vault01 restarted; Vault seals itself on every restart | on vault01: `vault operator unseal` twice, with two different unseal keys (Chapter 01 §3.4); `vault status` shows `Sealed false` (§2, Verify 2) |
| `Permission denied (publickey)` for svc-ansible | sshd doesn't trust vault01's CA, or the certificate expired | §3.2 fingerprints; run the task again for a fresh certificate; check clocks (Chapter 01 §10.2) |
| A long task fails after a reboot or a long pause: `Permission denied` / `UNREACHABLE` halfway | the 15-minute certificate expired; the open SSH connection kept working, the new one after the reboot is refused | template extra variable `vault_ssh_cert_ttl: 1h` (the vault01 role's `max_ttl`) |
| Play 1 skipped and then `Permission denied` for **dgxadmin** | the template has no variable group, so the lab thinks it's a MacBook run | attach `vault-approle` to the template |
| Play 1 never runs, hosts unreachable | `--limit` without `localhost` | `--limit dgx-spark-1,localhost` |
| `UNREACHABLE … 192.168.0.101` | dgx-spark-2 is uncommented in the inventory but not there yet | comment it out again, or add `--limit dgx-spark-1,localhost` (§5.5) |
| `Host key verification failed` | dgx-spark-1 was reinstalled, so its host key changed | on sema01: `docker compose exec semaphore ssh-keygen -R 192.168.0.100` (only after you know why the key changed) |
| `No module named 'kubernetes'` / `helm: not found` | the stock image is running | §4: `docker compose build semaphore && docker compose up -d`, check `docker compose ps` shows `semaphore-spark-lab:local` |
| `Could not find … kubeconfig-spark-lab.yaml` in templates `20.1`/`20.2`/`21.1`/`29.1` | state is not on the volume (SPARK_LAB_CACHE unset) or `19.1 Kubernetes` never ran from Semaphore | §4 Verify; run `19.1 Kubernetes` from Semaphore |
| `17.1-vault.yml`: `export VAULT_TOKEN=…` assertion | no admin token in the environment | §7 |
| `17.1-vault.yml`: signing role does not allow svc-ansible | vault01's role differs from Chapter 01 §4 | `vault read ssh-client-signer/roles/ansible`; fix `allowed_users` there |
| `11.1 Containers`: NGC login skipped | `vault_lab_secrets_enabled` false, or the key is still `REPLACE_ME` | §7 |
| `19.2 Reset Kubernetes`: `reset_confirm == 'RESET'` assertion | the template has no extra variable | §8.3 |
| `A worker was found in a dead state` | sema01 out of memory during `helm`/`kubectl` work | 4 GB+ for sema01 (Chapter 01 §2), never two tasks at once |
| `Missing sudo password` | MacBook run (`dgxadmin`) without `-K` | add `-K`; Semaphore runs as `svc-ansible` with NOPASSWD |
| `Timeout (12s) waiting for privilege escalation prompt` | sudo is slow because of a DNS lookup of the hostname | `time sudo true` on the Spark; add the hostname to `/etc/hosts` (`03.1-bootstrap.yml` does) |
| `/usr/bin/python3: not found` | minimal image, or a container target | `ansible <host> -m raw -a 'which python3'`; bootstrap with the `raw` module (see the Molecule `prepare.yml`) |
| `ansible_local` is empty | fact file not executable, or it printed non-JSON | `sudo /etc/ansible/facts.d/spark.fact \| jq .`; `chmod 755`; the script must print a single JSON object (§6.2) |
| Fact gathering hangs for ~10 s | `nvidia-smi` blocked on a wedged GPU | `timeout 5 nvidia-smi; echo $?`; the fact script time-boxes itself; go to Chapter 29, Runbook A |
| `E: Could not get lock /var/lib/dpkg/lock-frontend` | unattended-upgrades or the DGX Dashboard updater is running | `ps aux \| grep -E 'apt\|dpkg'`; the role retries 3× with a 10 s delay, otherwise wait for it to finish |
| Second `04.3 Baseline` run is not `changed=0` | non-idempotent task (`command`/`shell` without `changed_when`) | `ansible-playbook … --diff -v`; add `creates:`, `changed_when:` or a real module |

A diagnostic sequence worth memorising (MacBook):

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible dgx-spark-1 -m ping -vvv 2>&1 | grep -E 'ESTABLISH|EXEC|SSH:'   # is it SSH, sudo or Python?
ansible-playbook playbooks/04.3-baseline.yml --list-tasks --list-tags
ansible-playbook playbooks/04.3-baseline.yml --start-at-task "Harden sshd (drop-in, validated before reload)" -K
```
