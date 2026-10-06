# DGX Spark Ansible Lab — runnable companion to Module 01

Everything the 25 volumes teach, as a working Ansible project you run against
one or two NVIDIA DGX Spark systems. Every code block in the volumes is taken
from (or runs against) this directory.

The playbooks run as **Semaphore tasks** on `sema01` (192.168.0.210), with a
15-minute SSH certificate from `vault01` (192.168.0.211). Both stay outside the
Spark and are built by hand with the [00a guide](../00a-semaphore-vault-lab-guide.md);
the [00b guide](../00b-dgx-spark-semaphore-target.md) makes the Spark their target.
A command `ansible-playbook playbooks/NN-….yml` in the volumes means "run the
Semaphore template `NN …`"; the CLI form is the break-glass path from your MacBook
(`-l dgx-spark-1,localhost -K`).

```
lab/
├── ansible.cfg                 # pipelining, ControlPersist, fact cache, log_path, plugin paths   (Vol 01-02)
├── requirements.txt / .yml     # control-node Python + collections                               (Vol 05)
├── inventory/
│   ├── hosts.yml               # control (localhost) + spark + functional groups                   (Vol 03A)
│   ├── zz-constructed.yml      # groups from facts: gpu_ready, driver_580, uma_pressure…         (Vol 03A)
│   ├── group_vars/all.yml      # lab-wide: user, networks, NTP, lab_cache_dir, vault01/sema01 settings (vault_*, semaphore_url)
│   ├── group_vars/spark.yml    # login switch (svc-ansible + cert under Semaphore, nvidia otherwise), GB10 golden values, packages, sysctls
│   └── host_vars/dgx-spark-N.yml  # per-node CX-7 addressing
├── inventory_plugins/spark_mdns.py   # discover Sparks via Avahi/mDNS                            (Vol 03A)
├── inventory-examples/         # opt-in sources (mDNS)
├── roles/
│   ├── spark_facts             # /etc/ansible/facts.d/spark.fact (GPU, CUDA, CX-7, UMA)          (Vol 01A, 04)
│   ├── spark_baseline          # packages, NVIDIA holds, sysctl, SSH, chrony, journald + Molecule (Vol 01A, 21)
│   ├── cx7_fabric              # netplan, MTU, 200G/RDMA verify, runtime reconcile, GID, argspec (Vol 11, 05)
│   ├── container_runtime       # docker + nvidia-ctk + CDI (freshness-checked) + NGC              (Vol 08)
│   ├── gpu_telemetry           # textfile collector, Prometheus/Grafana/Alertmanager, dashboard  (Vol 09)
│   ├── kubeadm_cluster         # kubeadm root cluster spark-root: containerd + nvidia runtime,   (Vol 16)
│   │                           #   audit log, Secret encryption, etcd snapshot timer, workers join
│   ├── cilium                  # Cilium CNI (VXLAN, kube-proxy kept), Hubble UI :31235          (Vol 16)
│   ├── metallb                 # MetalLB L2 pool 192.168.0.110-119                               (Vol 16)
│   ├── gpu_operator            # Helm, driver/toolkit disabled, 15 time-slices                  (Vol 17)
│   ├── vclusters               # vClusters dev-lab + llms from ../../02 Kubernetes/lab           (Vol 16)
│   ├── slurm_cluster           # munge, slurm.conf, gres.conf, cgroup v2, health check          (Vol 18)
│   ├── vault_config            # adds to vault01: KV kv/, policy spark-lab-read → AppRole semaphore (Vol 19)
│   ├── nfs_rdma                # NFSv4.2 over RDMA model cache                                   (Vol 15)
│   ├── node_drain              # cordon → capture → stop → reboot → validate → return            (Vol 24)
│   └── spark_validate          # end-to-end invariants + JSON report                             (Vol 25)
├── playbooks/                  # 00-bootstrap … 30-validate, site.yml (see ../00-ansible-step-by-step-guide.md)
│   │                           #   one Semaphore template per playbook ("05 Kubernetes" = 05-kubernetes.yml)
│   ├── 00-vault-cert.yml       # play 1: AppRole login → 15-min cert for svc-ansible (+ kv/spark-lab/*), (00a, Vol 19)
│   │                           #   imported first by every playbook that SSHes to the Sparks; skipped off Semaphore
│   ├── 00-bootstrap.yml        # MacBook only: hostname, key, static IP with dead-man switch          (Vol 06)
│   ├── 00b-semaphore-target.yml  # MacBook, once: svc-ansible + NOPASSWD sudo, trust vault01's CA   (00b)
│   ├── 08-vault.yml            # MacBook, admin VAULT_TOKEN: lab secrets in vault01                 (00b §6, Vol 19)
│   │                           #   Kubernetes: 05-kubernetes → 06-gpu-operator → 06b-vclusters;
│   │                           #   99-reset-kubernetes wipes it for a clean rebuild
│   ├── files/uma_probe.cu      # sm_121 unified-memory probe                                     (Vol 08)
│   └── templates/              # RDMA device plugin, Loki, Alloy, auditd rules                   (Vol 13, 23)
├── semaphore/                  # the lab's Semaphore image + state volume, built on sema01         (00b §4)
│   ├── Dockerfile              # semaphoreui/semaphore + kubectl v1.36.5 + helm + Python libs + collections
│   ├── requirements-semaphore.txt
│   └── docker-compose.override.yml   # /opt/spark-lab/cache → SPARK_LAB_CACHE, ANSIBLE_CONFIG
├── tools/
│   ├── fetch-kubeconfig.sh     # sema01's state volume → MacBook .cache/kubeconfig-spark-lab.yaml  (00b §7.4)
│   ├── spark_invariants.py     # run ON a Spark, no Ansible needed                               (Vol 25)
│   ├── spark_drift_report.py   # check-mode JSON → markdown / Prometheus / exit code / hosts      (Vol 22)
│   ├── drift-cycle.sh          # detect → publish → guarded auto-heal → recheck                  (Vol 22)
│   ├── capstone_scorecard.py   # grade the capstone from the state folder's evidence             (Vol 25)
│   ├── vault-pass.sh · vault-ssh-cert.sh   # ansible-vault key from vault01; manual CA test for svc-ansible (Vol 19, 00b §3.3)
│   ├── build-collection.sh     # package roles as cloudone.spark                                 (Vol 05)
│   └── fleet-sim/Dockerfile    # fake sshd nodes for performance tuning                          (Vol 02A)
├── tests/                      # run-local-checks.sh + captured fixtures                         (Vol 21)
└── ee/execution-environment.yml  # arm64 EE for AWX / ansible-navigator                         (Vol 05, 20)
```

## Topology the defaults assume

```mermaid
flowchart LR
  subgraph MAC["MacBook"]
    A["browser · git · kubectl<br/>bootstrap + break-glass playbooks<br/>.cache/ (vault-ca.crt, kubeconfig)"]
  end
  subgraph MP["Management plane (00a), outside the Spark"]
    SEMA["sema01 · 192.168.0.210<br/>Semaphore + lab image (semaphore/)<br/>state volume /opt/spark-lab/cache"]
    VAULT["vault01 · 192.168.0.211<br/>SSH CA · AppRole semaphore<br/>KV kv/spark-lab/*"]
  end
  subgraph MGMT["Mgmt LAN 192.168.0.0/24 (10GbE enP7s7)"]
  end
  subgraph S1["dgx-spark-1 · 192.168.0.100"]
    S1a["kubeadm control plane + worker (spark-root)<br/>vClusters dev-lab · llms<br/>slurmctld+slurmd · Prometheus/Grafana<br/>NFS server /srv/models"]
  end
  subgraph S2["dgx-spark-2 · 192.168.0.101"]
    S2a["root worker (k8s_workers) · slurmd<br/>NFS client /mnt/models"]
  end
  A -- "HTTPS :3000" --> SEMA
  SEMA -- "play 1: AppRole + sign" --> VAULT
  SEMA -- "SSH 22 as svc-ansible (15-min cert) · :6443" --> MGMT
  A -. "break-glass: SSH as nvidia" .-> MGMT
  MGMT --- S1
  MGMT --- S2
  S1 <== "QSFP · CX-7 200GbE RoCE<br/>192.168.100.0/24 (enp1s0f1np1)<br/>192.168.101.0/24 (enP2p1s0f1np1)" ==> S2
  classDef mgmt fill:#fff3e6,stroke:#fb8500,color:#000
  class SEMA,VAULT mgmt
```

The inventory never lists `sema01` or `vault01`: no lab playbook configures them.
Their addresses are in `group_vars/all.yml` (`semaphore_url`, `vault_addr`).

**Single Spark?** Keep `dgx-spark-2` in `inventory/hosts.yml` and give every
Semaphore template the CLI argument `--limit dgx-spark-1,localhost` (break-glass:
`-l dgx-spark-1,localhost`). `localhost` must stay in the limit: play 1 and the
Kubernetes plays run there. Fabric, NCCL and NFS-over-RDMA steps skip
themselves; everything else runs. `dgx-spark-1` keeps no control-plane taint, so on
its own it is a complete cluster.

## Kubernetes: one root cluster, two vClusters

In Semaphore (project `spark-lab`), run the templates `05 Kubernetes` (kubeadm root
cluster + Cilium + MetalLB), `06 GPU Operator` (node advertises `nvidia.com/gpu: 15`)
and `06b vClusters` (dev-lab on 192.168.0.111, llms on 192.168.0.112). They write the
kubeconfig to sema01's state volume. Then, on your MacBook:

```bash
tools/fetch-kubeconfig.sh sema01                         # → .cache/kubeconfig-spark-lab.yaml (0600)
export KUBECONFIG=$PWD/.cache/kubeconfig-spark-lab.yaml   # contexts: spark-root, dev-lab, llms
kubectl --context spark-root get nodes -o wide
kubectl --context dev-lab get namespaces
kubectl --context llms get namespaces
```

Start over with the template `99 Reset Kubernetes` (extra variable
`reset_confirm: RESET`; `reset_remove_k3s: true` also removes an old k3s install).
Never schedule it. Break-glass from the MacBook:
`ansible-playbook playbooks/99-reset-kubernetes.yml -l dgx-spark-1,localhost -K` (type `RESET`).

`06b-vclusters.yml` applies the vCluster values and root budgets from
[`../../02 Kubernetes/lab`](../../02%20Kubernetes/lab/README.md), so that lab must
be checked out next to this one. It also writes `kubeconfig-dev-lab.yaml`
and `kubeconfig-llms.yaml` to the state folder for anyone who should only see one vCluster.

## Quick start

```bash
# 0. Management plane: vault01 + sema01 by hand (../00a-semaphore-vault-lab-guide.md)

# 1. MacBook toolchain
python3 -m venv ~/.venvs/spark-ansible && source ~/.venvs/spark-ansible/bin/activate
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml -p ./collections

# 2. Edit inventory/hosts.yml (IPs) and host_vars/*.yml (CX-7 names from `ibdev2netdev`), push
#    Fresh from the first-boot wizard? Use playbooks/00-bootstrap.yml (Volume 06).
ssh-copy-id nvidia@192.168.0.100     # and .101

# 3. Make the Spark a Semaphore target (../00b-dgx-spark-semaphore-target.md), from the MacBook
scp vault01:~/vault-ca.crt .cache/vault-ca.crt
ansible-playbook playbooks/00b-semaphore-target.yml -l dgx-spark-1,localhost -K
#    on sema01: build semaphore/ (00b §4); in Semaphore: project spark-lab, variable group vault-approle (00b §5)
export VAULT_TOKEN=<admin token> && ansible-playbook playbooks/08-vault.yml && unset VAULT_TOKEN   # 00b §6
```

4. **In Semaphore**, walk the stages as templates (each is safe to re-run; CLI args
   `--limit dgx-spark-1,localhost` while there's one Spark): `00 Ping` → `01 Baseline`
   → `02 Fabric` (two Sparks) → `03 Containers` → `05 Kubernetes` → `06 GPU Operator`
   → `06b vClusters` → `30 Validate`, or everything with `site`.
5. **Day-2 in Semaphore:** `20 Drift check` scheduled nightly, `21 Emergency drain`
   (extra variables, `--limit` on one node), `10 NCCL test` (two Sparks).

```bash
# MacBook
tools/fetch-kubeconfig.sh sema01                                 # after 05 / 06b
tools/drift-cycle.sh                                             # what drifted? (exit 0/2/3)
python3 tools/capstone_scorecard.py                              # evidence-based progress (Vol 25 §1.3)

# Break-glass, when sema01 or vault01 is down: same playbooks, as nvidia with your key
ansible-playbook playbooks/21-emergency-drain.yml -l dgx-spark-2,localhost -K
```

## Quality gates (run before every commit)

```bash
tests/run-local-checks.sh                  # yamllint, ansible-lint (production), syntax, katas, fixtures
cd roles/spark_baseline && molecule test   # on the Spark: native arm64 container
```

## Versions pinned here (check before use)

| Component | Pin | Where |
|---|---|---|
| ansible-core | 2.18.x | `requirements.txt` |
| Kubernetes (kubeadm, pkgs.k8s.io) | 1.36.5 | `roles/kubeadm_cluster/defaults` |
| Cilium chart | 1.20.2 | `roles/cilium/defaults` |
| MetalLB chart | 0.16.0 | `roles/metallb/defaults` |
| GPU Operator chart | v26.7.1 | `roles/gpu_operator/defaults` |
| vCluster chart | 0.37.1 | `roles/vclusters/defaults` |
| Multus (thick) | v4.3.0 | `playbooks/13-multus-rdma.yml` |
| Semaphore image tools (kubectl, helm) | v1.36.5 · v3.18.3 | `semaphore/Dockerfile` |
| NCCL | v2.30.7-1 (NVIDIA Spark playbook) | `playbooks/10-nccl-test.yml` |
| CUDA smoke image | `nvcr.io/nvidia/cuda:13.0.1-base-ubuntu24.04` | `container_runtime`, `spark_validate` |

The **state folder** (`lab_cache_dir`) holds the fact cache, run log, kubeconfigs (`kubeconfig-spark-lab.yaml` with all
three contexts), kubeadm join material, munge key, validation reports and incident bundles. Under Semaphore it is
sema01's state volume `/opt/spark-lab/cache` (`SPARK_LAB_CACHE`); on the MacBook it is `.cache/` (git-ignored), which
also holds vault01's TLS certificate `vault-ca.crt` and the kubeconfig you fetched. No Vault token or AppRole secret is
ever written to either. Treat both as secret.
