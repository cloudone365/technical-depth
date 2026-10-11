# Chapter 08 · Roles, Collections & Execution Environments for the Spark Lab

> **01-Ansible · Part I — Management plane & Ansible foundations · Chapter 08 of 30** · ← [Chapter 07 · Jinja2 filters & data transforms](07-jinja2-filters-and-data-transforms.md) · [All chapters](00-ansible-step-by-step-guide.md) · [Chapter 09 · Performance at scale: SSH mux & Mitogen](09-performance-at-scale-ssh-mux-and-mitogen.md) →

| | |
|---|---|
| **You will build** | Roles with typed input contracts (`argument_specs`), the lab packaged as a versioned collection (`cloudone.spark`), and an arm64 Execution Environment that runs it anywhere (CLI, AWX, CI) |
| **Hardware** | Your MacBook (collection build); 1× Spark to build the arm64 EE natively |
| **Time** | 90 min |
| **Risk** | None |

---

## 1. Architecture: from playbooks to a shippable product

```mermaid
flowchart LR
  subgraph DEV["lab/ (source)"]
    R["roles/*<br/>defaults · tasks · handlers ·<br/>templates · files · meta/argument_specs"]
    P["playbooks/*.yml"]
    IP["inventory_plugins/spark_mdns.py"]
  end
  subgraph PKG["Collection cloudone.spark (versioned tarball)"]
    CR["roles/"] & CP["plugins/inventory/"] & CPB["playbooks/"]
    G["galaxy.yml · meta/runtime.yml"]
  end
  subgraph EE["Execution Environment (OCI image, linux/arm64 + amd64)"]
    AC["ansible-core 2.18 + runner"]
    COL["collections: cloudone.spark,<br/>community.*, kubernetes.core"]
    PY["python: hvac, kubernetes, jmespath, netaddr"]
    BIN["helm, ssh, git"]
  end
  DEV -- tools/build-collection.sh --> PKG -- ansible-builder --> EE
  EE --> CLI["ansible-navigator run"] & AWX["AWX job pods"] & CI["CI runner"]
  DEV -- "semaphore/Dockerfile<br/>(roles from the git checkout)" --> SEMA["Semaphore image on sema01<br/>semaphore-spark-lab:local"]
  classDef mgmt fill:#fff3e6,stroke:#fb8500,color:#000
  class SEMA mgmt
```

**Why three layers?** Roles are the unit of **logic**. Collections are the unit of **versioning and distribution**. EEs are the unit of **runtime**, which ends "works on my laptop" because the Python, collection and binary versions are frozen together in one image.

**This lab's runtime today is simpler.** Semaphore on `sema01` runs the playbooks straight from its git checkout of the repository (roles via `ANSIBLE_CONFIG="01-Ansible/lab/ansible.cfg"`), in an image built from [`lab/semaphore/Dockerfile`](lab/semaphore/Dockerfile): the stock `semaphoreui/semaphore` plus kubectl, helm, the Python libraries in `requirements-semaphore.txt` and the collections in `requirements.yml` ([Chapter 04 §4](04-dgx-spark-as-semaphore-target.md)). It is the same idea as an EE, frozen tools in one image, without ansible-builder. The collection and the EE below are what you'd ship when other teams, AWX or CI consume the lab.

---

## 2. Role anatomy: conventions this lab follows

```
roles/cx7_fabric/
├── defaults/main.yml          # every knob, documented, prefixed with the role name
├── meta/main.yml              # galaxy_info, platforms, dependencies (kept empty on purpose)
├── meta/argument_specs.yml    # typed input contract — validated before tasks run
├── tasks/main.yml             # pre-flight → configure → verify → publish facts
├── templates/40-cx7.yaml.j2   # {{ ansible_managed }} header in every template
└── handlers/main.yml
```

| Convention | Why |
|---|---|
| **Prefix every variable with the role name** (`cx7_fabric_*`) | No collisions when 13 roles share one play. ansible-lint's `var-naming[no-role-prefix]` enforces it; this lab skips it only for *inventory-level* shared facts like `cx7_interfaces` |
| **Defaults read inventory, never the reverse** (`cx7_fabric_interfaces: "{{ cx7_interfaces \| default([]) }}"`) | The role works standalone *and* inside the lab |
| **Structure: pre-flight → configure → verify → publish** | Each role proves its own result; `set_fact` exports (e.g. `cx7_fabric_hca_list`) feed later roles |
| **No `meta` dependencies** | Hidden ordering is a debugging nightmare, so ordering lives in playbooks |
| **Split `tasks/` by concern** once it passes ~80 lines (`spark_baseline/tasks/{packages,users_ssh,kernel,time_logging}.yml`) | Readable diffs; tags per file |

### 2.1 Typed inputs with `argument_specs`

```yaml
# lab/roles/cx7_fabric/meta/argument_specs.yml
---
# Validated automatically before the role's tasks run (ansible-core >= 2.11).
# A typo in host_vars now fails in <1 s with a clear message instead of
# half-configuring a 200G link.
argument_specs:
  main:
    short_description: Configure and verify ConnectX-7 fabric interfaces on DGX Spark
    options:
      cx7_fabric_interfaces:
        type: list
        elements: dict
        required: true
        description: CX-7 logical interfaces to address (see `ibdev2netdev`).
        options:
          name:
            type: str
            required: true
            description: netdev name, e.g. enp1s0f1np1
          rdma_dev:
            type: str
            required: true
            description: RDMA device, e.g. rocep1s0f1
          address:
            type: str
            required: true
            description: IPv4 CIDR, e.g. 192.168.100.11/24
          mtu:
            type: int
            default: 9000
            choices: [1500, 4200, 9000]
      cx7_fabric_expected_speed_mbps:
        type: int
        default: 200000
      cx7_fabric_strict:
        type: raw
        default: auto
        description: "auto (strict only when a fabric peer is in the inventory), true or false"
      cx7_fabric_verify_peers:
        type: bool
        default: true
      cx7_fabric_netplan_file:
        type: path
        default: /etc/netplan/40-cx7.yaml
      cx7_fabric_gid_index:
        type: str
        default: ""
```

A typo in `host_vars` (`adress:` instead of `address:`) now fails immediately:

```
TASK [cx7_fabric : Validating arguments against arg spec 'main' ...]
fatal: [dgx-spark-1]: FAILED! => argument_errors:
  - 'missing required arguments: address found in cx7_fabric_interfaces'
```

`ansible-doc -t role -r roles cx7_fabric` renders the same spec as documentation.

### 2.2 `import_role` vs `include_role` vs `roles:`

| Form | Parsed | Tags/when apply to | Use for |
|---|---|---|---|
| `roles:` | static, at play load | every task in the role | The normal case (all lab playbooks) |
| `import_role` | static | every task | Static reuse inside `tasks:`, where `--list-tasks` should show it |
| `include_role` | **dynamic**, at runtime | the include itself only | Conditional/looped roles (`spark_validate` inside `node_drain`), loops over roles |

---

## 3. Hands-on

### 3.1 Package the lab as a collection

#### What this lab does (script `lab/tools/build-collection.sh`)

- **Build:** copies every role in `lab/roles/` (minus `molecule/` folders), the `spark_mdns` inventory plugin and six playbooks (`04.3-baseline`, `11.1-containers`, `13.1-fabric`, `26.1-drift-check`, `29.1-emergency-drain`, `30.1-validate`) into `.cache/build/ansible_collections/cloudone/spark/`. Inside the copied playbooks, `- role: x` becomes `- role: cloudone.spark.x`.
- **Metadata:** writes `galaxy.yml` (namespace `cloudone`, name `spark`, the version you pass, default `0.1.0`) with its collection dependencies, and `meta/runtime.yml` (`requires_ansible: ">=2.17.0"`).
- **Output:** `ansible-galaxy collection build` writes `.cache/dist/cloudone-spark-<version>.tar.gz`.
- **Test install:** the next commands install the tarball into `/tmp/colltest` on the MacBook and list its roles. Your lab folder and `./collections` are not touched. The install may also download the dependency collections named in `galaxy.yml`.

#### What to look for

1. **Script exit:** the last line is `Created collection for cloudone.spark at …/.cache/dist/cloudone-spark-0.1.0.tar.gz`.
2. **Install:** `ansible-galaxy collection install` ends with `cloudone.spark:0.1.0 was installed successfully`.
3. **Roles in the collection:**
   ```bash
   # ▶ MacBook · 01-Ansible/lab (venv active)
   tar -tzf .cache/dist/cloudone-spark-0.1.0.tar.gz | grep -c '^roles/[a-z_]*/$'      # one per role in lab/roles/
   ANSIBLE_COLLECTIONS_PATH=/tmp/colltest ansible-doc -t role cloudone.spark.cx7_fabric | head
   ```
   The `ansible-doc` output shows the `cx7_fabric` entry point and its options, because that role has `meta/argument_specs.yml` (§2.1).
4. **Trap, the last command:** it names `cloudone.spark.30-validate`, but the build packages the playbook as `30.1-validate.yml`, and that playbook imports `00-vault-cert.yml`, which the build does not package. Treat steps 1–3 as the check for this lab; running a packaged playbook needs those two things lined up first.

**If something's off:** `sed: … invalid command code` comes from macOS's BSD `sed`, which reads `sed -i 's/…/'` differently from GNU `sed`; run the script on the Spark (or any Linux machine with a clone of the repository), or with GNU `sed` first in `PATH`. `ansible-galaxy: command not found` means the venv isn't active. More in §5.

#### Run it

```bash
# lab/tools/build-collection.sh
#!/usr/bin/env bash
# Package the lab roles as an Ansible collection: cloudone.spark
#   tools/build-collection.sh 1.2.0        → .cache/dist/cloudone-spark-1.2.0.tar.gz
set -euo pipefail
VERSION=${1:-0.1.0}
LAB=$(cd "$(dirname "$0")/.." && pwd)
OUT="$LAB/.cache/build/ansible_collections/cloudone/spark"
rm -rf "$OUT" && mkdir -p "$OUT"/{roles,plugins/inventory,playbooks,meta}
cp -r "$LAB"/roles/* "$OUT/roles/"
find "$OUT/roles" -type d -name molecule -prune -exec rm -rf {} +
cp "$LAB"/inventory_plugins/spark_mdns.py "$OUT/plugins/inventory/"
cp "$LAB"/playbooks/{04.3-baseline,13.1-fabric,11.1-containers,26.1-drift-check,29.1-emergency-drain,30.1-validate}.yml "$OUT/playbooks/"
# roles referenced by short name inside playbooks resolve inside the collection namespace
sed -i 's/- role: \([a-z_]*\)/- role: cloudone.spark.\1/' "$OUT"/playbooks/*.yml
cat > "$OUT/galaxy.yml" <<YML
namespace: cloudone
name: spark
version: $VERSION
readme: README.md
authors: [cloudone365]
description: DGX Spark automation — baseline, CX-7 fabric, containers, telemetry, kubeadm + Cilium + vCluster, Slurm, Vault, drain, validation
license: [MIT]
tags: [nvidia, dgx, gpu, rdma, infrastructure]
dependencies:
  ansible.posix: ">=1.5.4"
  community.general: ">=9.0.0"
  community.docker: ">=3.10.0"
  community.crypto: ">=2.20.0"
  kubernetes.core: ">=5.0.0"
repository: https://github.com/cloudone365/technical-depth
build_ignore: ['*.retry', '.cache']
YML
printf 'requires_ansible: ">=2.17.0"\n' > "$OUT/meta/runtime.yml"
cp "$LAB/README.md" "$OUT/README.md"
mkdir -p "$LAB/.cache/dist"
ansible-galaxy collection build "$OUT" --output-path "$LAB/.cache/dist" --force
```

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
```

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
tools/build-collection.sh 0.1.0
ansible-galaxy collection install .cache/dist/cloudone-spark-0.1.0.tar.gz -p /tmp/colltest
ANSIBLE_COLLECTIONS_PATH=/tmp/colltest ansible-doc -t role -l cloudone.spark
ANSIBLE_COLLECTIONS_PATH=/tmp/colltest ansible-playbook cloudone.spark.30-validate -i inventory -K
```

**Versioning:** follow SemVer. Adding a role is a minor bump. Renaming a variable or changing a default that alters behaviour on existing nodes (say, MTU) is **major**. Consumers pin versions:

```yaml
# someone else's requirements.yml
collections:
  - name: https://github.com/cloudone365/technical-depth/releases/download/spark-v1.2.0/cloudone-spark-1.2.0.tar.gz
    type: url
```

### 3.2 Build an arm64 Execution Environment on the Spark

#### What this lab does (definition `lab/ee/execution-environment.yml`)

- **Where:** on dgx-spark-1, from a clone of the repository, so the image is native `linux/arm64` with no emulation.
- **Tools:** installs `ansible-builder` and `ansible-navigator` with `pip` on the Spark.
- **Image `spark-ee:1.0`:** based on `quay.io/fedora/python-312:latest`, with `ansible-core~=2.18.0`, `ansible-runner`, every collection from `requirements.yml`, the Python libraries `jmespath`, `netaddr`, `hvac`, `kubernetes`, SSH/rsync/git, and `helm` 3.18.3. It lands in the Spark's local Docker image store; nothing is pushed unless you run the optional buildx step.
- **First build is slow:** it pulls the base image and downloads every collection and Python package.
- **Last step:** runs `30.1-validate.yml` through the EE with `ansible-navigator`, the way AWX will run it.

#### What to look for

1. **Build exit:** `ansible-builder build` ends with `Complete! The build context can be found at: …/context`.
2. **The image checks** in the block below:
   | Command | Healthy value |
   |---|---|
   | `docker image inspect … Architecture` | `arm64` |
   | `ansible --version` (first line) | `ansible [core 2.18.x]` |
   | `ansible-galaxy collection list \| grep -cE …` | 5 or more (the `community.*`, `kubernetes.core` and `community.hashi_vault` collections, plus any dependency they pulled in) |
3. **The navigator run:** the play reaches the `Verdict` task and prints `All N checks passed`, with `failed=0` in the recap. A `FAILED checks: […]` list there is about the Spark's state (Chapter 30), not the EE: the EE did its job if the play got that far.
4. Optional buildx step: `docker buildx imagetools inspect ghcr.io/cloudone365/spark-ee:1.0` lists both `linux/arm64` and `linux/amd64`.

**If something's off:** `error: externally-managed-environment` from `pip install` means DGX OS's system Python refuses user installs; create a venv first (`python3 -m venv ~/.venvs/ee && . ~/.venvs/ee/bin/activate`). `permission denied` on the Docker socket means your user isn't in the `docker` group (use `sudo` or add the group). For the navigator run, `$SUDO_PW` must be set in that shell; with `--mode stdout --pae false` you can use `-K` instead and type the password. More in §5.

#### Run it

```yaml
# lab/ee/execution-environment.yml
---
# ansible-builder build -t spark-ee:1.0 -f ee/execution-environment.yml --container-runtime docker
# Build ON the Spark to get a native linux/arm64 image (or use buildx for multi-arch).
version: 3
images:
  base_image:
    name: quay.io/fedora/python-312:latest
dependencies:
  ansible_core:
    package_pip: ansible-core~=2.18.0
  ansible_runner:
    package_pip: ansible-runner
  galaxy: ../requirements.yml
  python:
    - jmespath
    - netaddr
    - hvac
    - kubernetes
  system:
    - openssh-clients [platform:rpm]
    - sshpass [platform:rpm]
    - rsync [platform:rpm]
    - git-core [platform:rpm]
additional_build_steps:
  append_final:
    - RUN curl -fsSL -o /usr/local/bin/helm.tgz https://get.helm.sh/helm-v3.18.3-linux-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/').tar.gz
        && tar -xzf /usr/local/bin/helm.tgz -C /tmp && mv /tmp/linux-*/helm /usr/local/bin/helm && rm -rf /usr/local/bin/helm.tgz /tmp/linux-*
    - LABEL org.opencontainers.image.description="Ansible EE for the DGX Spark lab"
```

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
# native arm64 build, no emulation; run from a clone of technical-depth on the Spark
pip install ansible-builder ansible-navigator
cd "01-Ansible/lab"
ansible-builder build -t spark-ee:1.0 -f ee/execution-environment.yml --container-runtime docker -v 3
docker image inspect spark-ee:1.0 --format '{{.Architecture}}'      # arm64
docker run --rm spark-ee:1.0 ansible --version
docker run --rm spark-ee:1.0 ansible-galaxy collection list | grep -cE 'community|kubernetes|hashi'
```

Multi-arch (so AWX on x86 and the Spark can both pull it):

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
ansible-builder create -f ee/execution-environment.yml --output-filename Containerfile
docker buildx build --platform linux/arm64,linux/amd64 -t ghcr.io/cloudone365/spark-ee:1.0 \
  -f context/Containerfile context --push
```

Run playbooks through the EE, exactly as AWX will:

```bash
# ▶ dgx-spark-1 (ssh dgx-spark-1)
ansible-navigator run playbooks/30.1-validate.yml --eei spark-ee:1.0 --mode stdout \
  --pae false -i inventory --become-password-file <(echo "$SUDO_PW")
```

### 3.3 Dependency hygiene

| File | Pins | Consumed by |
|---|---|---|
| `requirements.txt` | ansible-core, lint, molecule, python libs | control-node venv |
| `requirements.yml` | collections (min versions) | `ansible-galaxy`, ansible-builder |
| `ee/execution-environment.yml` | base image, core, runner, system pkgs | ansible-builder |
| `semaphore/Dockerfile` · `semaphore/requirements-semaphore.txt` | Semaphore base tag, kubectl, helm, Python libs (+ `requirements.yml`) | `docker compose build semaphore` on sema01 |
| collection `galaxy.yml` | collection dependencies | consumers of `cloudone.spark` |

Freeze what actually ran: `pip freeze > .cache/pip.lock` and `ansible-galaxy collection list --format yaml > .cache/collections.lock`. Commit these to the release tag.

---

## 4. Integrations

- **Semaphore (Chapter 04):** after changing `requirements.yml` or `requirements-semaphore.txt`, rebuild the image on sema01 (`git -C ~/technical-depth pull && docker compose build semaphore && docker compose up -d`). Pin the base tag there the way you pin the EE digest.
- **AWX (Chapters 23, 24):** set `spark-ee:1.0` as the org's default EE. The job pods then have every collection the lab needs.
- **CI (Chapter 25):** the same EE image runs lint, syntax-check and Molecule, so CI and prod can't drift.
- **Release flow:** tag → build the collection → build the EE with that collection → AWX points at the EE digest.

## 5. Troubleshooting & diagnostics

| Symptom | Diagnose | Fix |
|---|---|---|
| `the role 'x' was not found` | `ansible-config dump \| grep ROLES_PATH`; which `ansible.cfg` loaded? | Run from `lab/`, or use FQCN `cloudone.spark.x` from a collection |
| `couldn't resolve module/action` | `ansible-galaxy collection list` in the **same** environment/EE | Install into `collections_path`; for an EE, rebuild with the collection |
| Collection installed but an old version is used | Several `collections_path` entries | `ansible-galaxy collection list -p ./collections`; the first match wins |
| `exec format error` running the EE | `docker image inspect ... Architecture` | You built amd64 and ran it on the Spark: rebuild natively or with buildx |
| ansible-builder fails on `bindep` | Build log | Add `[platform:rpm]`/`[platform:dpkg]` markers correctly for the base image's distro |
| Argument spec error on a var you *did* set | `ansible-inventory --host <h>` | Wrong precedence level, or `default` vs `required`. Specs validate the **merged** value |

## 6. Validation

- [ ] `ansible-doc -t role -r roles cx7_fabric` shows the spec.
- [ ] A misspelled key in host_vars fails at the arg-spec task.
- [ ] `cloudone-spark-0.1.0.tar.gz` installs and `cloudone.spark.30-validate` runs.
- [ ] `spark-ee:1.0` is arm64 and `ansible-navigator run` works with it.
