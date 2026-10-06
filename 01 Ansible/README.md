# Module 01 — Ansible for AI Infrastructure, Hands-On on NVIDIA DGX Spark

Production-grade automation you can actually run: every volume is built around a **working Ansible project in [`lab/`](lab/)** targeting one or two **DGX Spark** systems (GB10 Grace Blackwell, 128 GB unified memory of which ~119.7 GiB is usable, ConnectX-7 200GbE). Each volume has HLD/LLD diagrams, the real roles and playbooks, integrations, a troubleshooting runbook and a validation checklist.

Where a data-centre concept doesn't exist on a Spark (NVSwitch/Fabric Manager, InfiniBand subnet managers, BMC/Redfish, parallel file systems), the volume says so, maps the concept to what the Spark actually has, and gives you a way to practise the data-centre version.

---

## Reference lab

The lab has two halves. The **management plane** stays outside the Spark: `sema01` (Semaphore UI) runs every playbook, and `vault01` (HashiCorp Vault) signs a 15-minute SSH certificate for each task and keeps the lab's secrets. The **DGX Spark** is only a target, so you can reset or re-image it without losing the tool that rebuilds it.

![The DGX Spark as a Semaphore target: sema01 and vault01 outside the Spark, dgx-spark-1 with the root cluster and two vClusters inside](diagrams/semaphore-dgx-spark.svg)

```mermaid
flowchart LR
  subgraph MAC["MacBook"]
    ANS["browser · git · kubectl<br/>bootstrap + break-glass playbooks<br/>lab/.cache/ (kubeconfig, vault-ca.crt)"]
  end
  subgraph MGMT["Management plane (00a), outside the Spark"]
    direction TB
    SEMA["sema01 · 192.168.0.210<br/>Semaphore UI + PostgreSQL<br/>controller: play 1, kubectl, helm<br/>state volume /opt/spark-lab/cache"]
    VAULT["vault01 · 192.168.0.211<br/>SSH CA ssh-client-signer · AppRole semaphore<br/>KV kv/spark-lab/* · audit log"]
  end
  subgraph S1["dgx-spark-1 · 192.168.0.100"]
    direction TB
    S1A["kubeadm root cluster spark-root (control plane + worker)<br/>Cilium · MetalLB · GPU Operator · AWX (optional)"]
    S1V["vClusters dev-lab (192.168.0.111) · llms (192.168.0.112)"]
    S1B["slurmctld + slurmd"]
    S1C["Prometheus · Grafana · Loki · ARA"]
    S1D["NFS/RDMA server /srv/models"]
  end
  subgraph S2["dgx-spark-2 · 192.168.0.101"]
    direction TB
    S2A["(optional) root worker · slurmd"]
    S2B["NFS/RDMA client /mnt/models"]
    S2C["(optional) AWX execution node"]
  end
  ANS -- "HTTPS :3000 (run templates)" --> SEMA
  SEMA -- "AppRole login · sign/ansible" --> VAULT
  SEMA -- "SSH as svc-ansible (15-min cert) · mgmt 10GbE" --> S1 & S2
  SEMA -- "kubectl/helm :6443" --> S1A
  ANS -. "break-glass: SSH as nvidia" .-> S1
  S1A --> S1V
  S1 <== "QSFP · CX-7 200GbE RoCEv2<br/>192.168.100.0/24 · 192.168.101.0/24" ==> S2
  classDef mgmt fill:#fff3e6,stroke:#fb8500,color:#000
  class SEMA,VAULT mgmt
```

Single Spark? Keep `dgx-spark-2` in the inventory and give every Semaphore template the CLI argument `--limit dgx-spark-1,localhost` ([00b §5.5](00b-dgx-spark-semaphore-target.md)). The fabric, NCCL and NFS/RDMA steps skip themselves, and `dgx-spark-1` alone is a complete Kubernetes cluster (no control-plane taint).

The Kubernetes end-state is one **kubeadm** root cluster (`spark-root`) with two **vClusters** inside it, `dev-lab` and `llms`. Ansible builds it in three stages: `05-kubernetes.yml` (kubeadm, Cilium, MetalLB) → `06-gpu-operator.yml` (15 GPU time-slices) → `06b-vclusters.yml` (the two vClusters, applied from the [02 Kubernetes lab](../02%20Kubernetes/lab/README.md)). All three contexts land in one file, `kubeconfig-spark-lab.yaml`, on sema01's state volume; `lab/tools/fetch-kubeconfig.sh sema01` copies it to `lab/.cache/` on your MacBook, where the 02 Kubernetes labs expect it.

> **Convention used in every volume.** A command written as `ansible-playbook playbooks/NN-….yml …` means **run the Semaphore template `NN …`** in project `spark-lab` (template names follow the playbook names: `05 Kubernetes` ↔ `05-kubernetes.yml`). The CLI form stays valid as the **break-glass** path from the MacBook, with `-l dgx-spark-1,localhost -K`: without the Semaphore variable group, play 1 is skipped and you log in as `nvidia` with your own key. Only `00-bootstrap.yml`, `00b-semaphore-target.yml` and `08-vault.yml` are run from the MacBook as the normal path.

## Start here

Build in this order:

1. **[00a · Semaphore UI + Vault](00a-semaphore-vault-lab-guide.md)**: the management plane, `vault01` and `sema01`, built by hand (Step 0a).
2. **[00b · Add dgx-spark-1 as a Semaphore target](00b-dgx-spark-semaphore-target.md)**: trust vault01's CA on the Spark (bootstrap first if it's a fresh DGX OS), the lab's Semaphore image and project, lab secrets in vault01 (Step 0b, right after the MacBook toolchain).
3. **[Step-by-step build guide](00-ansible-step-by-step-guide.md)**: the whole build order, from the MacBook toolchain (Step 0), the Spark as a Semaphore target (Step 0b) and first contact (Step 1) to the capstone, with the Semaphore template for every step.
4. **[Learning roadmap](ansible-tower-vault-roadmap.md)**: skills and checkpoints by level.
5. **[`lab/README.md`](lab/README.md)**: the project layout and quick start.

```bash
cd "01 Ansible/lab"                          # on your MacBook
pip install -r requirements.txt && ansible-galaxy collection install -r requirements.yml -p ./collections
tests/run-local-checks.sh                    # lint, syntax, katas, fixture tests: no Spark needed
```

Then, in Semaphore (project `spark-lab`), run the template `site` for the whole lab, or the stage templates one after the other.

---

## Curriculum

### Part I — Foundations

| Vol | Title | Lab pieces |
|---|---|---|
| 00a | [Management plane: Semaphore UI + Vault, automation account](00a-semaphore-vault-lab-guide.md) | `sema01`, `vault01`, `00-vault-cert.yml` (play 1) |
| 00b | [Add dgx-spark-1 as a Semaphore target](00b-dgx-spark-semaphore-target.md) | `00b-semaphore-target.yml`, `semaphore/`, `tools/fetch-kubeconfig.sh`, `08-vault.yml` |
| 01A | [Core on DGX Spark: controllers (Semaphore + MacBook), inventory, first contact](01-ansible-core-deep-dive.md) | `ansible.cfg`, inventory, `spark_facts`, `spark_baseline` |
| 01B | [Execution internals & debugging](01-ansible-core-engine-and-execution-internals.md) | AnsiballZ explode/execute, async, debugger |
| 02A | [Performance at scale: SSH mux, pipelining, forks, Mitogen](02-high-concurrency-tuning-mitogen-and-ssh-mux.md) | `13-fleet-sim`, `14-fleet-bench` |
| 02B | [AWX on the Spark: install & configure as code](02-ansible-tower-awx-deep-dive.md) (the alternative controller; this lab runs Semaphore) | awx-operator, `awx.awx` |
| 03A | [Inventory: static, constructed, mDNS discovery, NetBox](03-dynamic-inventory-and-cloud-infrastructure.md) | `inventory_plugins/spark_mdns.py`, `zz-constructed.yml` |
| 03B | [Vault server: raft, TLS, init/unseal, audit, backup](03-hashicorp-vault-deep-dive.md) | `vault01` (built by 00a, outside the Spark) |
| 04 | [Jinja2 & data transforms on real Spark output](04-advanced-jinja2-filters-and-data-transforms.md) | `15-jinja-lab.yml` (7 katas) |
| 05 | [Roles, collections & arm64 Execution Environments](05-role-architecture-collections-and-galaxy.md) | `argument_specs`, `build-collection.sh`, `ee/` |

### Part II — Node provisioning

| Vol | Title | Lab pieces |
|---|---|---|
| 06 | [Provisioning without a BMC; Redfish & PXE practice](06-bare-metal-os-provisioning-pxe-and-redfish.md) | `00-bootstrap` (dead-man switch), `12-redfish-practice` |
| 07 | [Driver stack: audit, pin, upgrade (and Fabric Manager)](07-nvidia-driver-and-fabric-manager-automation.md) | `16-driver-audit`, `17-dgxos-upgrade` |
| 08 | [CUDA 13, NGC containers & CDI](08-cuda-toolkit-cudnn-and-container-runtime.md) | `container_runtime`, `18-cuda-smoke`, `uma_probe.cu` |
| 09 | [Telemetry: GPU, unified memory, fabric; alerts; DCGM](09-dcgm-telemetry-and-exporter-orchestration.md) | `gpu_telemetry`, Grafana dashboard, 7 alert rules |
| 10 | [Firmware lifecycle & vulnerability patching](10-firmware-lifecycle-and-gpu-vulnerability-patch.md) | `19-firmware-inventory`, fwupd in the upgrade |

### Part III — Fabric & storage

| Vol | Title | Lab pieces |
|---|---|---|
| 11 | [ConnectX-7 fabric automation (RoCE), IB/OpenSM mapping](11-infiniband-fabric-automation-and-opensm.md) | `cx7_fabric`, `11-rdma-perftest` |
| 12 | [RoCEv2 done right: MTU, QoS, proving NCCL uses RDMA](12-lossless-rocev2-and-pfc-switch-host-tuning.md) | `12b-roce-qos`, `10-nccl-test` |
| 13 | [Multus & secondary RDMA networks on Kubernetes](13-multus-cni-and-secondary-rdma-networking.md) | `13-multus-rdma` |
| 14 | [GPUDirect Storage on a unified-memory machine](14-gpudirect-storage-gds-and-cufile-provisioning.md) | `14-gds-check` |
| 15 | [NFS over RDMA model cache (→ Lustre/Weka/VAST clients)](15-parallel-file-system-client-orchestration.md) | `nfs_rdma` |

### Part IV — Platforms & security

| Vol | Title | Lab pieces |
|---|---|---|
| 16 | [Kubernetes with kubeadm: root cluster, Cilium, MetalLB, vClusters](16-kubernetes-bare-metal-bootstrap-kubeadm.md) | `kubeadm_cluster`, `cilium`, `metallb`, `vclusters`, `05-kubernetes`, `06b-vclusters`, `99-reset-kubernetes` |
| 17 | [GPU Operator: host-driver mode, time-slicing](17-nvidia-gpu-operator-helm-automation.md) | `gpu_operator`, `06-gpu-operator` |
| 18 | [Slurm: GRES, cgroup v2, health checks, 2-node NCCL](18-slurm-cluster-orchestration-and-cgroup-gpus.md) | `slurm_cluster` |
| 19 | [Vault ↔ Ansible: AppRole, KV, SSH certificates](19-hashicorp-vault-approle-and-dynamic-secrets.md) | `00-vault-cert`, `vault_config` (`08-vault`), `19-vault-integration` |
| 20 | [AWX in production: execution nodes, Vault creds, workflows, backup](20-awx-tower-production-cluster-and-receptor.md) | receptor, `AWXBackup` |

### Part V — Production SRE

| Vol | Title | Lab pieces |
|---|---|---|
| 21 | [Testing & CI: lint, fixtures, Molecule on arm64](21-ansible-testing-linting-and-molecule.md) | `tests/`, `.github/workflows/ansible-lab-ci.yml` |
| 22 | [Drift detection & guarded self-healing](22-configuration-drift-detection-and-self-healing.md) | `20-drift-check`, `drift-cycle.sh`, `spark_drift_report.py` |
| 23 | [Logging & audit: auditd, Loki/Alloy, ARA](23-high-cardinality-logging-and-audit-compliance.md) | `23-logging-audit` |
| 24 | [Incident response: drain, evidence, runbooks A–E](24-cluster-wide-emergency-drain-and-remediation.md) | `node_drain`, `21-emergency-drain`, `24-uma-relief` |
| 25 | [Capstone: build, break, prove](25-hands-on-ansible-mastery-lab-and-test-harness.md) | `spark_validate`, `spark_invariants.py`, `25-chaos`, `capstone_scorecard.py` |

---

## How the lab was verified

Built and checked in a workspace **without** Spark hardware, so it was verified at every layer short of the device:

| Check | Result |
|---|---|
| `ansible-lint` (production profile) + `yamllint` | pass |
| `ansible-playbook --syntax-check` on every playbook | pass |
| Jinja katas against real Spark command output | 7/7 |
| Template rendering (netplan, slurm.conf, Prometheus) | rendered and YAML-validated |
| Prometheus config + 7 alert rules + collector output | `promtool check config/rules/metrics` pass |
| Loki config / Alloy pipeline | `loki -verify-config` / `alloy fmt` pass |
| `uma_probe.cu` | compiles for `sm_121` with nvcc 13.x |
| Vault integration (play 1: AppRole → token → SSH sign → KV v2) | exercised against a stand-in for Vault's HTTP API |
| Custom inventory plugin, drift reporter, firmware diff, argument specs, collection build | fixture-tested |

**Not yet run on a real Spark.** Your first pass through the step-by-step guide is the hardware test. Versions pinned in the lab (Kubernetes, Cilium, MetalLB, GPU Operator and vCluster charts, container images, NCCL) are current as of this writing; check them before you run.
