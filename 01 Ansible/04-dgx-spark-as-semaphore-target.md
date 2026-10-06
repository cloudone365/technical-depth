# Step 04 · Add dgx-spark-1 as a Semaphore Target

> **01 Ansible · Part I — Management plane & Ansible foundations · Step 04 of 30** · ← [Step 03 · Bare-metal provisioning & bootstrap](03-bare-metal-provisioning-and-bootstrap.md) · [All steps](00-ansible-step-by-step-guide.md) · [Step 05 · Execution internals & debugging](05-execution-internals-and-debugging.md) →
>
> Prerequisites: this step follows Steps 01–03. [Step 01 · Management plane](01-management-plane-semaphore-and-vault.md) works end to end (its §9 passes), and the MacBook toolchain, SSH trust and inventory from [Step 02](02-control-node-and-ansible-core.md) §3.1–3.4 are in place (a fresh DGX OS also needs the bootstrap from [Step 03](03-bare-metal-provisioning-and-bootstrap.md)). This step makes the DGX Spark the next target of that same Semaphore and Vault. Afterwards **every playbook of the Spark lab runs as a Semaphore task**. Next comes first contact, the `00 Ping` template from §5.6 ([Step 02 §3.5](02-control-node-and-ansible-core.md)), then the baseline; the [02 Kubernetes](../02%20Kubernetes/README.md) module builds on the clusters those tasks create.

**Goal:** the same rule as in Step 01: no human holds the automation credential. `sema01` and `vault01` stay outside the Spark. `dgx-spark-1` trusts vault01's SSH CA, and every Semaphore task logs in as `svc-ansible` with a 15-minute certificate. Kubernetes work (kubeadm, Cilium, the GPU Operator, the vClusters) is driven from the Semaphore container over the LAN.

Each step has a **Why**, then commands with a comment on every line, then a **Verify** block. Steps say where to type: **on your MacBook**, **on sema01**, **on vault01**, **on dgx-spark-1**, or **in Semaphore** (the web UI).

![The DGX Spark as a Semaphore target: sema01 and vault01 outside the Spark, dgx-spark-1 with the root cluster and two vClusters inside](diagrams/semaphore-dgx-spark.svg)

---

## 1. How the pieces fit

| Machine | Address | Role in the Spark lab | Built by |
|---|---|---|---|
| MacBook | DHCP | your terminal: browser to Semaphore, `git push`, `kubectl` for the 02 Kubernetes labs, and the two bootstrap playbooks | [Step 02](02-control-node-and-ansible-core.md) §3.1–3.4 |
| `sema01` | 192.168.0.210 | **runs every lab playbook** (Semaphore + PostgreSQL in Docker). Its container is the Ansible *controller*: play 1, `kubectl`, `helm` and the `kubernetes.core` modules run there | Step 01 §7, plus §4 here |
| `vault01` | 192.168.0.211 | SSH CA (`ssh-client-signer`, role `ansible`), AppRole `semaphore`, audit log; plus the lab's secrets (§6) | Step 01 §3–4, plus §6 here |
| `dgx-spark-1` | 192.168.0.100 | the target: DGX OS, then the kubeadm root cluster `spark-root` with the vClusters `dev-lab` and `llms` inside it | the lab playbooks, run by Semaphore |
| 192.168.0.201 / .202 | — | your existing Step 01 targets, unchanged (project `lab`) | Step 01 |

**One Semaphore task, step by step** (template `05 Kubernetes` as the example):

1. You click **Run**. Semaphore checks your role and clones `technical-depth` (branch `main`).
2. **Play 1** (`playbooks/00-vault-cert.yml`, the same play as Step 01 §8.3) runs inside the container. It logs in to vault01 with the AppRole from the variable group and gets a 15-minute certificate for `svc-ansible`.
3. The host plays SSH to `dgx-spark-1` as `svc-ansible` with that certificate. sshd checks the CA signature, the principal and the expiry, and `sudo` (NOPASSWD) runs the tasks.
4. The controller plays run `kubectl`/`helm` in the container against `https://192.168.0.100:6443`. The kubeconfig with the contexts `spark-root`, `dev-lab` and `llms` lives on sema01's **state volume** (§4), not in the temporary checkout.
5. The task log and history stay in Semaphore. vault01's audit log has the login and the signature, and dgx-spark-1's sshd log has `Accepted publickey … ED25519-CERT`.

**Two ways in, decided by one variable.** [`inventory/group_vars/spark.yml`](lab/inventory/group_vars/spark.yml) switches the login on whether the Semaphore variable group (`vault_role_id`) is attached:

| Run from | `ansible_user` | Credential | Used for |
|---|---|---|---|
| Semaphore (normal) | `svc-ansible` | 15-minute certificate from play 1 | every template from `00 Ping` on |
| MacBook (bootstrap, break-glass) | `dgxadmin` (the admin user) | your own SSH key, sudo password with `-K` | `00-bootstrap.yml`, `00b-semaphore-target.yml`, `08-vault.yml`, and emergencies when sema01 or vault01 is down (§10) |

> **Why the controller is outside the Spark.** `99-reset-kubernetes.yml` deletes everything Kubernetes runs; a rebuild of DGX OS deletes everything on the box. A Semaphore running *on* the Spark would delete itself halfway through its own job, and a broken cluster would take away the tool you need to fix it. With the management plane outside — like out-of-band management in a datacenter — the Spark can break freely.

---

## 2. Before you start

**Why:** the bootstrap playbooks run once from your MacBook, and they need the lab repository, a working Ansible, SSH as the admin user, and vault01's TLS certificate.

**Two one-time settings on the MacBook first.**

- **zsh and `#` comments.** macOS's zsh does **not** treat `#` as a comment when you type or paste commands: everything after it becomes arguments (errors like `zsh: unknown group`, `No such file or directory`, or a hanging quote from an apostrophe). The commented command blocks in these guides need it switched on:
  ```bash
  setopt interactivecomments                                   # this shell
  echo 'setopt interactivecomments' >> ~/.zshrc                # every new shell
  ```
- **SSH names for the management plane**, so `vault01` and `sema01` in the commands below resolve to the right address and user. Your login user on vault01 is the one from Step 01 (`vault01` in its examples); put your own sema01 user in place of `<your-user>`:
  ```bash
  cat >> ~/.ssh/config <<'EOF'
  Host vault01
    HostName 192.168.0.211
    User vault01
  Host sema01
    HostName 192.168.0.210
    User <your-user>
  Host dgx-spark-1
    HostName 192.168.0.100
    User nvidia
  EOF
  ssh vault01 hostname && ssh sema01 hostname                  # both answer
  ```

Then, on your MacBook (the repository and Ansible come from [Step 02](02-control-node-and-ansible-core.md) §3.1):

```bash
cd ~/technical-depth/"01 Ansible/lab"                       # the lab folder; every relative path below starts here
mkdir -p .cache && chmod 700 .cache                          # local state folder (git-ignored)
scp vault01:~/vault-ca.crt .cache/vault-ca.crt               # vault01 TLS certificate (the copy you made in Step 01 §3.3)
curl --cacert .cache/vault-ca.crt https://192.168.0.211:8200/v1/sys/health   # JSON = the MacBook trusts vault01's TLS
ssh -t dgxadmin@192.168.0.100 'hostname; sudo -v && echo sudo-ok'   # admin login + sudo (-t: a terminal, so sudo can ask for the password)
```

**Verify 2:**

```bash
ls -l .cache/vault-ca.crt                                    # the certificate is there
curl -s --cacert .cache/vault-ca.crt https://192.168.0.211:8200/v1/ssh-client-signer/public_key | cut -c1-20   # expect: ssh-rsa AAAA… (public, no token)
ansible -m ping dgx-spark-1 -K                              # expect: pong (as dgxadmin, your key)
```

### 2.1 Fresh DGX OS only: bootstrap

Skip this if the Spark is already named `dgx-spark-1`, sits on 192.168.0.100 and takes your key as `dgxadmin` (the last command in Verify 2 answers `pong`). On a Spark that has just finished the first-boot wizard (password login, DHCP address), `00-bootstrap.yml` sets the hostname, installs your key for `dgxadmin` and, on the second run, moves it to the static IP. A dead-man timer rolls the network back if Ansible can't reconnect.

```bash
ansible-playbook playbooks/00-bootstrap.yml -l dgx-spark-1 -k -K -e bootstrap_current_ip=<its DHCP IP>                              # -k: SSH password, still on
ansible-playbook playbooks/00-bootstrap.yml -l dgx-spark-1 -K -e bootstrap_current_ip=<its DHCP IP> -e bootstrap_static_ip=true     # move to 192.168.0.100
```

Then repeat Verify 2. Details: [Step 03](03-bare-metal-provisioning-and-bootstrap.md) and the playbook's header.

---

## 3. Make dgx-spark-1 trust vault01 (on your MacBook)

**Why:** this is Step 01 §5 for the Spark, as a playbook instead of by hand. `00b-semaphore-target.yml`:

- creates `svc-ansible` without a password;
- gives it NOPASSWD sudo (checked with `visudo -c` before it lands);
- writes vault01's CA public key to `/etc/ssh/trusted-user-ca-keys.pem`;
- adds `/etc/ssh/sshd_config.d/10-vault-ca.conf`, which trusts the CA and allows `svc-ansible` only key or certificate logins (checked with `sshd -t`);
- reloads sshd.

Your `dgxadmin` login is untouched, so you can't lock yourself out.

### 3.1 Run it

```bash
ansible-playbook playbooks/00b-semaphore-target.yml -l dgx-spark-1,localhost -K   # localhost: fetches the CA key from vault01
```

`-l dgx-spark-1,localhost` is optional while `dgx-spark-2` is commented out in [`inventory/hosts.yml`](lab/inventory/hosts.yml), as it is now. If you limit a run, keep `localhost` in the limit: the CA key is fetched there, and play 1 runs there in every Semaphore template (§5.5).

### 3.2 Verify on dgx-spark-1

```bash
id svc-ansible                                               # expect a uid line
sudo visudo -c                                               # expect: parsed OK (including /etc/sudoers.d/90-svc-ansible)
sudo sshd -T | grep -i trustedusercakeys                     # expect: trustedusercakeys /etc/ssh/trusted-user-ca-keys.pem
sudo ssh-keygen -l -f /etc/ssh/trusted-user-ca-keys.pem      # expect the SAME SHA256 fingerprint as vault01 prints below
```

On vault01: `vault read -field=public_key ssh-client-signer/config/ca > ~/ca.pub && ssh-keygen -l -f ~/ca.pub`.

### 3.3 Test the CA before involving Semaphore (on vault01)

The same test as Step 01 §6, against the Spark. If Semaphore fails later, you'll know the problem isn't the trust:

```bash
ssh-keygen -t ed25519 -f ~/semaphore_lab -N "" <<<y >/dev/null       # test key (overwrites the Step 01 one)
vault write -field=signed_key ssh-client-signer/sign/ansible \
  public_key=@$HOME/semaphore_lab.pub valid_principals=svc-ansible > ~/semaphore_lab-cert.pub
ssh -i ~/semaphore_lab -o CertificateFile=~/semaphore_lab-cert.pub svc-ansible@192.168.0.100 'hostname; sudo -n whoami'
```

Expected: `dgx-spark-1` and `root`. `01 Ansible/lab/tools/vault-ssh-cert.sh` does the same in one command if you have the repository on vault01.

---

## 4. Give Semaphore the lab's tools and a place to keep state (on sema01)

**Why:** two things differ from the Ubuntu targets.

- **Kubernetes tools.** Several Spark playbooks run on the controller: kubeadm's kubeconfig merge, `helm` installs of Cilium, MetalLB and the GPU Operator, the vCluster charts, `kubectl drain`. The stock Semaphore image has no `kubectl`, `helm`, `kubernetes` Python library or `kubernetes.core` collection.
- **State.** The lab writes files the next run needs to its state folder: the kubeconfig with three contexts, the kubeadm join material, validation reports, incident bundles. Semaphore's checkout of the repository is temporary, and `/tmp` is gone after a container restart. So the state goes to a host folder, `/opt/spark-lab/cache`, and the playbooks find it through `SPARK_LAB_CACHE` (`lab_cache_dir` in [`group_vars/all.yml`](lab/inventory/group_vars/all.yml)).

[`lab/semaphore/`](lab/semaphore/) has both pieces:

| File | What it does |
|---|---|
| [`Dockerfile`](lab/semaphore/Dockerfile) | `FROM semaphoreui/semaphore`, plus `kubectl` v1.36.5, `helm`, the Python libraries in [`requirements-semaphore.txt`](lab/semaphore/requirements-semaphore.txt) and the collections in [`requirements.yml`](lab/requirements.yml) |
| [`docker-compose.override.yml`](lab/semaphore/docker-compose.override.yml) | merged with your Step 01 `docker-compose.yml`: builds that image, mounts `/opt/spark-lab/cache`, sets `ANSIBLE_CONFIG`, `SPARK_LAB_CACHE` and the log and fact paths |

```bash
cd ~ && git clone https://github.com/cloudone365/technical-depth.git   # the lab repository, as the image's build context
sudo install -d -o 1001 -g 0 -m 0700 /opt/spark-lab/cache            # state folder, owned by the container's semaphore user (uid 1001)
cp ~/technical-depth/"01 Ansible/lab/semaphore/docker-compose.override.yml" ~/semaphore/   # next to the Step 01 compose file
cd ~/semaphore
docker compose build semaphore                                       # builds semaphore-spark-lab:local (a few minutes)
docker compose up -d                                                 # recreates the semaphore container; PostgreSQL and your data are untouched
```

`/opt/spark-lab/cache` will hold cluster-admin kubeconfigs: back it up like the `.env` file (Step 01 §7.3) and keep it readable by the container user only. To update the tools later, `git -C ~/technical-depth pull`, then `docker compose build semaphore && docker compose up -d`.

**Verify 4 (on sema01, in ~/semaphore):**

```bash
docker compose ps                                                    # expect: postgres and semaphore Up
docker compose exec semaphore kubectl version --client               # expect: Client Version: v1.36.5
docker compose exec semaphore helm version --short                   # expect: v3.18.x
docker compose exec semaphore ansible-galaxy collection list kubernetes.core   # expect: kubernetes.core 5.x or newer
docker compose exec semaphore sh -c 'echo $SPARK_LAB_CACHE; touch $SPARK_LAB_CACHE/.w && echo writable'   # expect the path, then: writable
```

Your Step 01 project `lab` keeps working: same database, same keys, and the new image is a superset of the old one.

---

## 5. A Semaphore project for the Spark lab (in Semaphore)

**Why:** a separate project keeps the Spark's repository, inventory and templates apart from the Step 01 `lab` project, and lets you give other people access to one without the other. Menu names differ slightly between Semaphore versions.

### 5.1 Project

**New Project** → name `spark-lab`. Everything below happens inside it.

### 5.2 Key for the repository

**Key Store → New Key**: name `github-token`, type *Login with password*. Login is your GitHub user; Password is a **read-only** fine-grained token for `technical-depth` (Step 01 §8.2: Contents → Read-only). If the repository is public, type *None* works too. An SSH deploy key (Step 01 §8.5) is the stronger option.

### 5.3 Variable group `vault-approle`

**Variable Groups → New**, exactly as Step 01 §8.6 (same AppRole, so the same values work):

1. Name `vault-approle`.
2. Extra variables (JSON): `{"vault_role_id": "PASTE-THE-ROLE-ID", "vault_lab_secrets_enabled": false}`.
3. Secrets → Add secret: type *Variable*, name `vault_secret_id`, value = a secret_id from vault01 (`vault write -f -field=secret_id auth/approle/role/semaphore/secret-id`).
4. Save.

`vault_role_id` being defined is what switches the lab to `svc-ansible` with a certificate (§1). Set `vault_lab_secrets_enabled` to `true` after §6.

### 5.4 Repository

**Repositories → New**: name `technical-depth`, URL `https://github.com/cloudone365/technical-depth.git`, branch `main`, access key `github-token`.

### 5.5 Inventory

**Inventory → New**: name `spark-lab`, type **File**, repository `technical-depth`, path `01 Ansible/lab/inventory/hosts.yml`, user credentials *None* (play 1 supplies the key).

The inventory file comes with its `group_vars/` and `host_vars/`, so addresses, the automation user, the certificate path and `StrictHostKeyChecking=accept-new` are all already there; you don't retype them.

**One Spark or two:** `dgx-spark-2` is commented out in the inventory, so the templates need no limit. When dgx-spark-2 joins, uncomment its lines in `hosts.yml` (groups `spark`, `k8s_workers`, `nfs_client`) and push. If you ever add a `--limit` to a template, include `localhost`: play 1 and all the Kubernetes plays run there.

### 5.6 First template: `00 Ping`

**Task Templates → New Template**, type *Ansible Playbook*:

| Field | Value |
|---|---|
| Name | `00 Ping` |
| Playbook filename | `01 Ansible/lab/playbooks/00-ping.yml` |
| Inventory | `spark-lab` |
| Repository | `technical-depth` |
| Variable group (Environment) | `vault-approle` |
| CLI args | none needed with one Spark (if you add a limit: `["--limit", "dgx-spark-1,localhost"]`) |

Save, then **Run**.

**Verify 5:** the task log shows two plays.

- **Get an SSH certificate from Vault** (on `localhost`), with its secret tasks hidden.
- **Connectivity and identity check**, with `ok` on `dgx-spark-1` and a line like `dgx-spark-1 aarch64 20 cores 119.7 GiB Ubuntu 24.04`, ending in `failed=0`.

Then check all three systems:

```bash
sudo journalctl -u ssh --since "10 minutes ago" | grep svc-ansible   # on dgx-spark-1: "Accepted publickey for svc-ansible … ED25519-CERT"
sudo grep -c 'sign/ansible' /var/log/vault_audit.log                 # on vault01: the count grows with each task run
docker compose exec semaphore ssh-keygen -L -f /tmp/lab_ssh/id_ed25519-cert.pub | grep -A1 Principals   # on sema01: svc-ansible
```

---

## 6. Lab secrets in vault01 (on your MacBook, once)

**Why:** some lab playbooks need secrets, for example the NGC API key that `03-containers.yml` uses to pull NVIDIA images. They belong in vault01, next to the SSH CA, not in the repository. Setting this up needs an **admin** token, which Semaphore must never hold, so you run it from the MacBook.

[`08-vault.yml`](lab/playbooks/08-vault.yml) (role [`vault_config`](lab/roles/vault_config/)):

- **checks** what Step 01 built (the signing role allows `svc-ansible`; AppRole `semaphore` exists) and never rewrites it;
- mounts a KV v2 engine at `kv/`;
- writes the read-only policy `spark-lab-read` (`kv/data/spark-lab/*`);
- adds it to AppRole `semaphore`, so its tokens carry `semaphore-ssh` **and** `spark-lab-read`;
- seeds a placeholder `kv/spark-lab/ngc`.

```bash
export VAULT_ADDR=https://192.168.0.211:8200 VAULT_CACERT=$PWD/.cache/vault-ca.crt   # the vault CLI on the MacBook talks to vault01
vault login                                      # an admin token (root token in the lab; a named admin in production)
export VAULT_TOKEN=$(vault print token)          # the playbook reads it from the environment; it is never written to disk
ansible-playbook playbooks/08-vault.yml          # localhost only: talks to vault01's API
vault kv put kv/spark-lab/ngc api_key=<your NGC API key>   # the real value replaces the placeholder
unset VAULT_TOKEN                                # don't leave an admin token in the shell
```

No `vault` CLI on the MacBook? Run the `vault` commands on vault01 instead, and paste the token into `export VAULT_TOKEN=…` on the MacBook for the playbook run.

Then in Semaphore: variable group `vault-approle` → change `vault_lab_secrets_enabled` to `true`.

**Verify 6:**

```bash
vault read auth/approle/role/semaphore | grep token_policies   # on vault01: [semaphore-ssh spark-lab-read]
vault kv get -field=api_key kv/spark-lab/ngc | cut -c1-6       # the first characters of your key, not REPLACE_ME
```

In Semaphore, add and run a template `19 Vault integration` (`01 Ansible/lab/playbooks/19-vault-integration.yml`, same inventory, variable group and CLI args). Its log reports `Lab secrets read from kv/spark-lab/: ['ngc']` and `NGC key present: True`, without ever printing the key.

---

## 7. Build the lab from Semaphore

**Why:** from here on, the [step-by-step guide](00-ansible-step-by-step-guide.md) and the step documents say `ansible-playbook playbooks/NN-….yml`. In this lab that means **run the template of the same name**: every template uses the same inventory, repository, variable group and CLI args as `00 Ping`, and only the playbook filename changes.

### 7.1 The templates

Create these templates. The **Explained in** column names the step document (not a template number) that explains each one; extra variables go in the template's *Extra variables* or a survey.

| Template | Playbook (`01 Ansible/lab/playbooks/…`) | Explained in | Notes |
|---|---|---|---|
| `00 Ping` | `00-ping.yml` | Step 02 §3.5 | first test (§5.6) |
| `01 Baseline` | `01-baseline.yml` | Steps 02, 03, 10 | OS, packages, sysctls, custom facts |
| `02 Fabric` | `02-fabric.yml` | Steps 13, 14 | CX-7 addressing: only with dgx-spark-2 |
| `03 Containers` | `03-containers.yml` | Step 11 | Docker, NVIDIA toolkit, NGC login (key from §6) |
| `04 Telemetry` | `04-telemetry.yml` | Step 12 | DCGM exporter, node exporter |
| `05 Kubernetes` | `05-kubernetes.yml` | Step 19 | kubeadm root cluster, Cilium, MetalLB; writes the kubeconfig to the state volume |
| `06 GPU Operator` | `06-gpu-operator.yml` | Step 20 | 15 time-slices |
| `06b vClusters` | `06b-vclusters.yml` | Step 19 §3.4, 02 Kubernetes Step 04 | `dev-lab` and `llms`; adds their contexts |
| `13 Multus RDMA` | `13-multus-rdma.yml` | Step 21 | secondary CX-7 networks |
| `07 Slurm`, `09 NFS RDMA`, `10 NCCL test`, `11 RDMA perftest`, `14 GDS check`, `16 Driver audit`, `18 CUDA smoke`, `19 Firmware inventory`, `23 Logging audit`, `24 UMA relief`, `25 Chaos` | the playbook of the same number | Steps 22, 15, 14, 13, 16, 10, 11, 28, 27, 29, 30 (in template order) | same pattern; `10`/`11` need dgx-spark-2 |
| `17 DGX OS upgrade` | `17-dgxos-upgrade.yml` | Steps 10, 28 | reboots: extra variable `vault_ssh_cert_ttl: 1h` (§12) |
| `site` | `site.yml` | — | playbooks `01`–`04`, `05`–`06b`, Slurm, NFS, validation in one task; extra variable `vault_ssh_cert_ttl: 1h` |
| `20 Drift check` | `20-drift-check.yml` | Step 26 | check mode is built in (changes nothing); schedule it nightly |
| `21 Emergency drain` | `21-emergency-drain.yml` | Step 29 | drains through context `spark-root`; with a reboot, `vault_ssh_cert_ttl: 1h` |
| `30 Validate` | `30-validate.yml` | Step 30 | the golden-value gate |
| `99 Reset Kubernetes` | `99-reset-kubernetes.yml` | Step 19 §8 | **danger zone**: see 7.3 |

### 7.2 Build order

Run `01 Baseline` → `03 Containers` → `04 Telemetry` → `05 Kubernetes` → `06 GPU Operator` → `06b vClusters`, one after the other. Or run `site` once. Each task log must end in `failed=0` before you start the next one: the same checkpoints as in Steps 02, 11, 12, 19 and 20 ([all steps](00-ansible-step-by-step-guide.md)).

### 7.3 The danger zone

`99-reset-kubernetes.yml` asks you to type `RESET`, and a Semaphore task can't answer prompts. Give the template an extra variable instead: Ansible skips a prompt when the variable is already set.

- Extra variables: `{"reset_confirm": "RESET"}`, optional `"reset_wipe_data": true`.
- Permissions: in **Team**, give other users the *Task Runner* role at most, and keep this template for yourself. The clean option is a third project `spark-danger` that only you can open.

Never schedule it.

### 7.4 Your kubeconfig on the MacBook

`05 Kubernetes` and `06b vClusters` write `kubeconfig-spark-lab.yaml` (contexts `spark-root`, `dev-lab`, `llms`) to sema01's state volume. The 02 Kubernetes labs run `kubectl` from your MacBook and expect it at `01 Ansible/lab/.cache/kubeconfig-spark-lab.yaml`. Copy it there after each of those two tasks:

```bash
tools/fetch-kubeconfig.sh sema01                                     # reads it through the container, writes .cache/kubeconfig-spark-lab.yaml (0600)
export KUBECONFIG="$PWD/.cache/kubeconfig-spark-lab.yaml"            # what every 02 Kubernetes command uses
kubectl --context spark-root get nodes                               # expect: dgx-spark-1 Ready control-plane
kubectl --context dev-lab get ns && kubectl --context llms get ns    # both vClusters answer
```

This kubeconfig holds cluster-admin certificates, the human side of the lab, and you need it for the Kubernetes labs. Keep it on the MacBook only (it's git-ignored). In an enterprise, people get short-lived OIDC logins instead (02 Kubernetes Step 03).

---

## 8. Verify the full chain

| Where | What to check | What it proves |
|---|---|---|
| Semaphore task log | Play 1 ok (secret tasks hidden), then host plays on `dgx-spark-1`, `failed=0` | Semaphore → Vault → Spark works |
| dgx-spark-1: `sudo journalctl -u ssh \| grep svc-ansible` | `Accepted publickey … ED25519-CERT ID …` | login used a certificate, not a plain key |
| dgx-spark-1: `sudo grep svc-ansible /var/log/auth.log \| grep COMMAND` | sudo lines for the tasks | privileged actions are traceable to the automation account |
| vault01: `sudo grep -c 'sign/ansible' /var/log/vault_audit.log` | grows by one per task | every certificate request is recorded |
| sema01: `docker compose exec semaphore ls /var/lib/spark-lab/cache` | `kubeconfig-spark-lab.yaml`, `kubeconfig-dev-lab.yaml`, `kubeconfig-llms.yaml`, `validation/` … | lab state survives the temporary checkout |
| MacBook: `kubectl --context llms get nodes` | `dgx-spark-1` (synced from the root) | the 02 Kubernetes labs can start |
| Expiry test | 16 minutes after a task, `docker compose exec semaphore ssh -i /tmp/lab_ssh/id_ed25519 svc-ansible@192.168.0.100 true` is refused; a new task run succeeds | credentials expire without cleanup |

---

## 9. Day-2 operations from Semaphore

- **Schedules:** `20 Drift check` nightly (it only reports, it never changes anything), and `30 Validate` weekly. A failed scheduled task is your alert (add a Telegram, Slack or e-mail alert in project settings).
- **Upgrades** (`17 DGX OS upgrade`, Steps 10/28) and **drains** (`21 Emergency drain`, Step 29): run them from Semaphore so every maintenance action has a record of who ran it, when and with what result.
- **Rebuild:** `99 Reset Kubernetes`, then `05 Kubernetes` → `06 GPU Operator` → `06b vClusters`, then `tools/fetch-kubeconfig.sh`. Semaphore, Vault and their history are untouched by any of it.
- **What Semaphore does *not* do:** workloads inside the vClusters (vLLM, tenant apps, quotas). Those are Git → Argo CD (02 Kubernetes [Step 28](../02%20Kubernetes/28-production-mlops-and-gitops.md)). One tool per object, or the two will undo each other's changes.

---

## 10. Break-glass: running from the MacBook

When sema01 or vault01 is down, you can still reach the Spark the way you did in §2:

```bash
cd ~/technical-depth/"01 Ansible/lab"
ansible-playbook playbooks/21-emergency-drain.yml -l dgx-spark-1,localhost -K   # no vault_role_id → nvidia + your key; play 1 is skipped
```

State then goes to the MacBook's `.cache/` instead of sema01's volume. After the emergency, re-run the affected template in Semaphore, so the record and the state are back in one place.

---

## 11. Security notes

| Item | Lab today | Better |
|---|---|---|
| AppRole secret_id | one static secret_id in the variable group | `secret_id_ttl`, `secret_id_bound_cidrs=192.168.0.210/32`, rotation (Step 01 §11) |
| State volume | cluster-admin kubeconfigs and kubeadm join material on sema01 | encrypt the disk, back it up, restrict who can `docker exec` into the container |
| Template permissions | everyone in the project can run everything | *Task Runner* role for others; dangerous templates in a separate project |
| Container image | `semaphoreui/semaphore:latest` + tools built locally | pin the base tag, rebuild on a schedule, scan the image |
| Lab secrets | read-only policy for `kv/spark-lab/*` | one policy per template class (build vs day-2), short token TTLs |

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `zsh: unknown group`, `No such file or directory` for words from a comment, or a `quote>` prompt | zsh on macOS doesn't treat `#` as a comment | `setopt interactivecomments` (and add it to `~/.zshrc`), §2 |
| `ssh: Could not resolve hostname vault01` / wrong user on vault01 or sema01 | no SSH names on the MacBook | `~/.ssh/config` entries, §2 |
| `Permission denied (publickey)` for svc-ansible | sshd doesn't trust vault01's CA, or the certificate expired | §3.2 fingerprints; run the task again for a fresh certificate; check clocks (Step 01 §10.2) |
| A long task fails after a reboot or a long pause: `Permission denied` / `UNREACHABLE` halfway | the 15-minute certificate expired; the open SSH connection kept working, the new one after the reboot is refused | template extra variable `vault_ssh_cert_ttl: 1h` (the vault01 role's `max_ttl`) |
| Play 1 skipped and then `Permission denied` for **nvidia** | the template has no variable group, so the lab thinks it's a MacBook run | attach `vault-approle` to the template |
| Play 1 never runs, hosts unreachable | `--limit` without `localhost` | `--limit dgx-spark-1,localhost` |
| `UNREACHABLE … 192.168.0.101` | dgx-spark-2 is uncommented in the inventory but not there yet | comment it out again, or add `--limit dgx-spark-1,localhost` (§5.5) |
| `Host key verification failed` | dgx-spark-1 was reinstalled, so its host key changed | on sema01: `docker compose exec semaphore ssh-keygen -R 192.168.0.100` (only after you know why the key changed) |
| `No module named 'kubernetes'` / `helm: not found` | the stock image is running | §4: `docker compose build semaphore && docker compose up -d`, check `docker compose ps` shows `semaphore-spark-lab:local` |
| `Could not find … kubeconfig-spark-lab.yaml` in templates `06`/`06b`/`13`/`21` | state is not on the volume (SPARK_LAB_CACHE unset) or `05 Kubernetes` never ran from Semaphore | §4 Verify; run `05 Kubernetes` from Semaphore |
| `08-vault.yml`: `export VAULT_TOKEN=…` assertion | no admin token in the environment | §6 |
| `08-vault.yml`: signing role does not allow svc-ansible | vault01's role differs from Step 01 §4 | `vault read ssh-client-signer/roles/ansible`; fix `allowed_users` there |
| Task 03: NGC login skipped | `vault_lab_secrets_enabled` false, or the key is still `REPLACE_ME` | §6 |
| 99: `reset_confirm == 'RESET'` assertion | the template has no extra variable | §7.3 |
| `A worker was found in a dead state` | sema01 out of memory during `helm`/`kubectl` work | 4 GB+ for sema01 (Step 01 §2), never two tasks at once |
