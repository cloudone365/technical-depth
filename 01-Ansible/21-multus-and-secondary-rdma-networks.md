# Chapter 21 · Multus & Secondary RDMA Networks: the CX-7 Fabric Inside Kubernetes, Chained with Cilium, Reachable from a vCluster

> **01-Ansible · Part IV — Secrets & platforms · Chapter 21 of 30** · ← [Chapter 20 · NVIDIA GPU Operator & time-slicing](20-nvidia-gpu-operator-and-time-slicing.md) · [All chapters](00-ansible-step-by-step-guide.md) · [Chapter 22 · Slurm: GRES & cgroup GPUs](22-slurm-gres-and-cgroup-gpus.md) →
>
> Requires: [Chapter 19 · Kubernetes: kubeadm root cluster & vClusters](19-kubernetes-kubeadm-root-cluster-and-vclusters.md).

| | |
|---|---|
| **You will build** | Pods with **two** interfaces: `eth0` on Cilium for control traffic, `net1` on the 200G CX-7 fabric. RDMA verbs access comes through an extended resource, proven with a pod-to-pod `ib_write_bw` across Sparks. The same fabric is then reachable from a training pod in the root's `batch` namespace, next to the models |
| **Hardware** | 2× DGX Spark in the root cluster (single Spark works too; both pods then land on one node) |
| **Time** | 60–90 min |
| **Risk** | Low–medium. Adds a CNI meta-plugin in front of Cilium; the pod network itself is untouched |
| **Clusters** | `spark-root` (Multus, device plugin, NADs, test pods in `platform-tools`, a training pod in `batch` in §3.2–§3.3) |
| **Lab files** | [`playbooks/21.1-multus-rdma.yml`](lab/playbooks/21.1-multus-rdma.yml), [`playbooks/templates/rdma-shared-dp.yaml.j2`](lab/playbooks/templates/rdma-shared-dp.yaml.j2), [`roles/cilium`](lab/roles/cilium) (`cni.exclusive=false`), 02-Kubernetes [`manifests/root-llms/85-network-operator/`](../02-Kubernetes/lab/manifests/root-llms/85-network-operator) (the Network Operator alternative) |

---

## 1. Why pods need a second network

Cilium gives every pod an overlay IP (VXLAN over the 10GbE mgmt link, Chapter 19 §2.1). That's fine for APIs and Ray control traffic, but **it can't carry RDMA**: verbs need direct access to the NIC's RDMA device and a GID on the physical fabric. Distributed training and multi-node inference pods therefore need:

1. a **second interface** on the CX-7 fabric (Multus + macvlan/host-device/SR-IOV), and
2. **access to the RDMA device** (`/dev/infiniband/*`), handed out as a schedulable resource by a device plugin, and
3. `IPC_LOCK` (RDMA pins memory).

None of this is in the pod network, so none of it is governed by Cilium. That has consequences for multi-tenancy (§2.3).

## 2. Architecture

### 2.1 HLD

```mermaid
flowchart TB
  subgraph NODE["spark-0N (root cluster node)"]
    KUBELET["kubelet → containerd"] -->|"CNI ADD"| SHIM["multus-shim<br/>/etc/cni/net.d/00-multus.conf"]
    SHIM --> MD["multus daemon<br/>DaemonSet kube-multus-ds"]
    MD -->|"default network<br/>(delegates 05-cilium.conflist)"| CIL["Cilium → eth0 10.42.x.x"]
    MD -->|"annotation cx7-a → NAD in the pod's namespace"| MACVLAN["macvlan on enp1s0f1np1 → net1<br/>192.168.100.2xx/24, MTU 9000"]
    DP["rdma-shared-dp-ds<br/>resource rdma/rdma_shared_cx7"] -->|"device plugin API"| KUBELET
    subgraph POD["Pod (root ns platform-tools or batch)"]
      E0["eth0"]
      N1["net1"]
      DEV["/dev/infiniband/uverbs*<br/>+ IPC_LOCK"]
    end
  end
  CIL --> E0
  MACVLAN --> N1
  DP -.mounts.-> DEV
  N1 ===|"CX-7 200G · RoCEv2"| OTHER["pod on the other Spark"]
  classDef node fill:#0e7c86,stroke:#064e54,color:#fff
  classDef net fill:#8250df,stroke:#4c2889,color:#fff
  classDef gpu fill:#76b900,stroke:#3d6000,color:#000
  classDef tenant fill:#eaeef2,stroke:#57606a,color:#000
  classDef ext fill:#24292f,stroke:#000,color:#fff
  class KUBELET node
  class SHIM,MD,CIL,MACVLAN net
  class DP gpu
  class E0,N1,DEV tenant
  class OTHER ext
```

### 2.2 LLD

| Component | Choice in this lab | Production alternative |
|---|---|---|
| Multus | Upstream **thick plugin** v4.3.0: the release's `deployments/multus-daemonset-thick.yml`, applied unchanged. DaemonSet `kube-system/kube-multus-ds`, a small `multus-shim` binary in `/opt/cni/bin`, the real work in the per-node daemon | NVIDIA **Network Operator** (bundles Multus, IPAM, RDMA/SR-IOV device plugins). The 02 lab ships a `NicClusterPolicy` for it ([`root/85-network-operator`](../02-Kubernetes/lab/manifests/root/85-network-operator/nicclusterpolicy.yaml)); use **one** of the two |
| Chaining with Cilium | Multus auto-generates `00-multus.conf` from the first config it finds (`05-cilium.conflist`) and makes Cilium the default network. Works because Chapter 19 installed Cilium with `cni.exclusive=false` | Same |
| Secondary CNI | `macvlan` bridge mode, master = CX-7 netdev, MTU 9000 | `host-device` (a whole netdev per pod), or SR-IOV VFs (one VF per pod, hardware isolation) |
| IPAM | `static`, IP given per pod in the annotation (deterministic for a lab) | `whereabouts` (cluster-wide ranges), as in the Network Operator path |
| RDMA exposure | `k8s-rdma-shared-dev-plugin`, selecting netdevs `enp1s0f1np1`, `enP2p1s0f1np1`, up to 64 pods per device (`rdmaHcaMax`) | SR-IOV network device plugin (exclusive VFs) |
| NADs | `cx7-a` (`enp1s0f1np1`, 192.168.100.0/24), `cx7-b` (`enP2p1s0f1np1`, 192.168.101.0/24) in **`platform-tools`** and **`batch`** | One per tenant namespace, created by the platform team |
| CNI paths | kubeadm standard: config `/etc/cni/net.d`, binaries `/opt/cni/bin` (the reference plugins `macvlan` and `static` come with the `kubernetes-cni` package that is installed alongside kubeadm) | Same |

> **Why macvlan works for RoCE:** the mlx5 driver installs RoCE GIDs for IPs on macvlan upper devices, so a pod's `net1` IP gets its own RoCEv2 GID on the physical port. Check it with `show_gids` on the host after a pod starts.

### 2.3 NADs are namespaced: `batch` on the root, `vc-*` for a vCluster

A pod asks for a secondary network with the annotation `k8s.v1.cni.cncf.io/networks: cx7-a`. Multus runs on the root, reads the pod from the **root** API server, and looks up the NetworkAttachmentDefinition named `cx7-a` **in the pod's namespace**.

The LLM stack runs directly on the root, so a training pod in `batch` is a root pod in the root namespace `batch`, and the playbook creates the NADs there. Tenants of the stack can't create NADs themselves: the platform team decides which fabrics a namespace may use by deciding which NADs exist in it.

A vCluster pod is different, because its host copy lives in the vCluster's host namespace under a translated name:

```text
inside dev-llms:  llm-sandbox / rdma-sbx                          annotation k8s.v1.cni.cncf.io/networks: [{"name":"cx7-a",…}]
on the root:      vc-dev-llms / rdma-sbx-x-llm-sandbox-x-dev-llms same annotation, copied unchanged by the syncer
Multus:           NAD vc-dev-llms/cx7-a                           ← would have to exist on the root
```

NetworkAttachmentDefinition is a CRD on the root; the vCluster neither has it nor syncs it. The lab creates no NADs in `vc-dev-lab` or `vc-dev-llms`, so a vCluster pod that asks for `cx7-a` stays in `ContainerCreating` with a Multus "NAD not found" error. To give a vCluster the fabric, add its `vc-*` namespace to `multus_nad_namespaces`.

What this does **not** give you, and what to add for real tenants:

| Gap | Why | Fix |
|---|---|---|
| A pod can name a NAD in *another* namespace (`platform-tools/cx7-a`) | The upstream daemon config doesn't enable Multus's `namespaceIsolation` | Add `"namespaceIsolation": true` (and `globalNamespaces` if needed) to the `multus-daemon-config` ConfigMap |
| Cilium policies (`vcluster-boundary`, the `llm-serving` NetworkPolicy, tenant NetworkPolicies) don't apply on `net1` | macvlan traffic never passes through Cilium | Separate subnets/VLANs per tenant, or SR-IOV VFs with switch-side ACLs |
| `rdma/rdma_shared_cx7` isn't in any budget | `batch-budget` and `vcluster-budget` list CPU, memory, GPU, storage | Add `requests.rdma/rdma_shared_cx7` to `batch-budget` |
| Static IPs collide across namespaces silently | `static` IPAM has no shared state | A convention (here: `.201–.209` platform, `.211–.229` batch) or whereabouts |

---

## 3. Hands-on

#### What this lab does (template `21.1 Multus RDMA`)

- **Where:** runs on the controller against the root only (context `spark-root`, pinned with `module_defaults`). Nothing on the Spark is changed by SSH; the DaemonSets write their own files.
- **Multus:** applies the upstream thick-plugin manifest v4.3.0 (DaemonSet `kube-system/kube-multus-ds`). On the Spark it adds `/etc/cni/net.d/00-multus.conf` in front of Cilium's `05-cilium.conflist` and the `multus-shim` binary in `/opt/cni/bin`. Every new pod's network setup now passes through Multus, which still gives `eth0` to Cilium.
- **RDMA device plugin:** DaemonSet `kube-system/rdma-shared-dp-ds` (image on `:latest`) advertises `rdma/rdma_shared_cx7` for the CX-7 netdevs `enp1s0f1np1` and `enP2p1s0f1np1`, up to 64 pods per node.
- **NADs:** `cx7-a` (macvlan on `enp1s0f1np1`, 192.168.100.0/24) and `cx7-b` (`enP2p1s0f1np1`, 192.168.101.0/24), MTU 9000, in `platform-tools` and, once `20.2 vClusters` has created it, `batch`. None in `vc-dev-lab` or `vc-dev-llms`.
- **Test pods:** `rdma-test-<node>` in `platform-tools` (`ubuntu:24.04`, perftest installed at start), with `net1` at `192.168.100.201` on dgx-spark-1 and `.202` on dgx-spark-2. With one Spark there is a single pod, `rdma-test-dgx-spark-1`. The package install inside the pods makes the first run take a few minutes.

#### What to look for

1. **Semaphore run:** `failed=0`. On a first run expect `changed` on Multus, the device plugin, the NADs and the test pods.
2. **The "Result" task** comes from a step that is allowed to fail, so read it by eye. Each entry must show `net1` `UP` with its `192.168.100.20x/24` address, followed by `uverbs…` entries from `/dev/infiniband`. An error text there instead means the pod has no `net1` or no RDMA device.
3. **Cluster and node**, with the commands under "Run it":

   | Line/Field | Healthy value |
   |---|---|
   | `get net-attach-def -A` | `cx7-a` and `cx7-b` in `platform-tools`, and in `batch` once 20.2 has run |
   | `RDMA` / `GPU` columns | `64` / `15` on every node |
   | `/etc/cni/net.d` | `00-multus.conf` listed before `05-cilium.conflist` |
   | `/opt/cni/bin` | `multus-shim`, `macvlan`, `static`, `cilium-cni` |

4. **One Spark:** the playbook's second test-pod entry lands on the same pod and only rewrites its annotation to `.202`. The interface keeps `.201`; trust the pod's `network-status` (§3.1), not its `networks` annotation.

**If something's off:** test pods stuck in `ContainerCreating` with `failed to find plugin "macvlan"` → the reference CNI plugins are missing in `/opt/cni/bin`. No `rdma/rdma_shared_cx7` on the node → `kubectl --context spark-root -n kube-system logs ds/rdma-shared-dp-ds`. No NADs in `batch` → run `20.2 vClusters`, then this template again. More in §5.

```yaml
# lab/playbooks/21.1-multus-rdma.yml
---
# Secondary CX-7 networks for pods: Multus + macvlan (static IPAM) + RDMA shared device plugin.
# Result: a pod gets eth0 (Cilium) AND net1 on the 200G fabric with RDMA verbs access.
# Cilium was installed with cni.exclusive=false (roles/cilium) so it leaves Multus's
# config alone in /etc/cni/net.d.
#
#   ansible-playbook playbooks/21.1-multus-rdma.yml
- name: Multus + RDMA on the root cluster
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    kubeconfig: "{{ playbook_dir }}/../.cache/kubeconfig-{{ lab_name | default('spark-lab') }}.yaml"
    kube_context: spark-root
    multus_version: v4.3.0                   # check https://github.com/k8snetworkplumbingwg/multus-cni/releases
    multus_rdma_resource: rdma_shared_cx7
    multus_rdma_ifnames: [enp1s0f1np1, enP2p1s0f1np1]
    multus_rdma_dp_image: ghcr.io/mellanox/k8s-rdma-shared-dev-plugin:latest   # pin a release tag
    multus_test_image: ubuntu:24.04          # perftest + verbs utils installed at start (readiness waits for it)
    # NADs are namespaced: platform-tools for the platform test pods below, batch
    # for the training pods of the root's LLM stack (02-Kubernetes manifests/root-llms).
    multus_nad_namespaces: [platform-tools, batch]
    multus_test_namespace: platform-tools
    multus_nads:
      - { name: cx7-a, master: enp1s0f1np1, subnet: 192.168.100.0/24 }
      - { name: cx7-b, master: enP2p1s0f1np1, subnet: 192.168.101.0/24 }
  module_defaults:
    group/kubernetes.core.k8s:
      kubeconfig: "{{ kubeconfig }}"
      context: "{{ kube_context }}"
  tasks:
    # ---------------------------------------------------------------- Multus (thick plugin) from upstream
    - name: Download the Multus thick-plugin manifest
      ansible.builtin.get_url:
        url: "https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/{{ multus_version }}/deployments/multus-daemonset-thick.yml"
        dest: "{{ playbook_dir }}/../.cache/multus-{{ multus_version }}.yml"
        mode: "0644"

    - name: Multus DaemonSet (standard kubeadm paths /etc/cni/net.d and /opt/cni/bin)
      kubernetes.core.k8s:
        src: "{{ playbook_dir }}/../.cache/multus-{{ multus_version }}.yml"
        state: present

    - name: Wait for Multus DaemonSet
      kubernetes.core.k8s_info:
        kubeconfig: "{{ kubeconfig }}"
        kind: DaemonSet
        namespace: kube-system
        name: kube-multus-ds
      register: multus_ds
      until: >-
        multus_ds.resources | length > 0 and
        multus_ds.resources[0].status.numberReady | default(0) == multus_ds.resources[0].status.desiredNumberScheduled | default(-1)
      retries: 30
      delay: 10

    # ---------------------------------------------------------------- RDMA shared device plugin
    - name: RDMA shared device plugin
      kubernetes.core.k8s:
        kubeconfig: "{{ kubeconfig }}"
        state: present
        template: rdma-shared-dp.yaml.j2

    # ---------------------------------------------------------------- namespaces
    - name: Platform-tools namespace (also created by the 02 lab's manifests/root/00-platform)
      kubernetes.core.k8s:
        definition:
          apiVersion: v1
          kind: Namespace
          metadata:
            name: platform-tools
            labels:
              spark.lab/tier: platform
              pod-security.kubernetes.io/enforce: privileged

    - name: Which NAD namespaces exist (batch appears once playbook 20.2 has run)?
      kubernetes.core.k8s_info:
        kind: Namespace
      register: multus_ns

    - name: Keep only existing namespaces
      ansible.builtin.set_fact:
        multus_nad_namespaces: "{{ multus_nad_namespaces | intersect(multus_ns.resources | map(attribute='metadata.name') | list) }}"

    # ---------------------------------------------------------------- NetworkAttachmentDefinitions
    - name: Macvlan NADs on the CX-7 netdevs (static IPAM — IP chosen per pod)
      kubernetes.core.k8s:
        kubeconfig: "{{ kubeconfig }}"
        definition:
          apiVersion: k8s.cni.cncf.io/v1
          kind: NetworkAttachmentDefinition
          metadata:
            name: "{{ item.0.name }}"
            namespace: "{{ item.1 }}"
          spec:
            config: >-
              {{ {'cniVersion': '0.3.1', 'type': 'macvlan', 'master': item.0.master, 'mode': 'bridge',
                  'mtu': 9000, 'capabilities': {'ips': true}, 'ipam': {'type': 'static'}} | to_json }}
      loop: "{{ multus_nads | product(multus_nad_namespaces) | list }}"
      loop_control: { label: "{{ item.1 }}/{{ item.0.name }} -> {{ item.0.master }}" }

    - name: Wait until every node advertises the RDMA resource
      kubernetes.core.k8s_info:
        kubeconfig: "{{ kubeconfig }}"
        kind: Node
      register: multus_nodes
      until: >-
        multus_nodes.resources
        | map(attribute='status.allocatable')
        | selectattr('rdma/' ~ multus_rdma_resource, 'defined')
        | list | length == multus_nodes.resources | length
      retries: 18
      delay: 10

    # ---------------------------------------------------------------- test pods, one per node
    - name: RDMA test pods
      kubernetes.core.k8s:
        kubeconfig: "{{ kubeconfig }}"
        wait: true
        wait_timeout: 600
        definition:
          apiVersion: v1
          kind: Pod
          metadata:
            name: "rdma-test-{{ item.node }}"
            namespace: "{{ multus_test_namespace }}"
            annotations:
              k8s.v1.cni.cncf.io/networks: >-
                [{"name": "cx7-a", "ips": ["{{ item.ip }}/24"]}]
          spec:
            nodeName: "{{ item.node }}"
            restartPolicy: Never
            containers:
              - name: t
                image: "{{ multus_test_image }}"
                command:
                  - sh
                  - -c
                  - apt-get update -qq && apt-get install -y -qq perftest ibverbs-utils iproute2 >/dev/null && sleep infinity
                readinessProbe:
                  exec: { command: [sh, -c, "command -v ib_write_bw"] }
                  periodSeconds: 5
                securityContext:
                  capabilities: { add: [IPC_LOCK] }
                resources:
                  limits:
                    "rdma/{{ multus_rdma_resource }}": 1
      loop:
        - { node: "{{ groups['spark'][0] }}", ip: 192.168.100.201 }
        - { node: "{{ groups['spark'][1] | default(groups['spark'][0]) }}", ip: 192.168.100.202 }
      loop_control: { label: "{{ item.node }} {{ item.ip }}" }

    - name: Show net1 inside each pod
      kubernetes.core.k8s_exec:
        kubeconfig: "{{ kubeconfig }}"
        namespace: "{{ multus_test_namespace }}"
        pod: "rdma-test-{{ item }}"
        command: sh -c "ip -br addr show net1; ls /dev/infiniband"
      loop: "{{ groups['spark'] }}"
      register: multus_exec
      changed_when: false
      failed_when: false

    - name: Result
      ansible.builtin.debug:
        msg: "{{ multus_exec.results | map(attribute='stdout') | list }}"
```

Things worth noticing:

- **`module_defaults` pins the cluster.** Every `kubernetes.core` task gets `context: spark-root` from the action group, so none of them can land in a vCluster even though the lab kubeconfig's other contexts are one typo away.
- **The NAD namespaces are discovered, not assumed.** Run 21.1 before 20.2 and you get NADs in `platform-tools` only. Run it again after 20.2 and `batch` gets its pair. `product()` builds the namespace × fabric matrix in one loop.
- **The test pods live in `platform-tools`** (privileged PSA). It is the root namespace for the platform team's node-level tools, and it holds no tenant workloads.
- **Pin what you download.** The playbook pins the *manifest* to the `v4.3.0` tag, but check the image tag inside `.cache/multus-v4.3.0.yml` (`grep image:`). Upstream manifests have pointed at a moving `snapshot` tag before; replace it with the release tag if so. The RDMA device plugin is on `:latest` until you pin it.

```yaml
# lab/playbooks/templates/rdma-shared-dp.yaml.j2
# {{ ansible_managed }}
# k8s-rdma-shared-dev-plugin: exposes the host RDMA devices behind the listed
# netdevs as an extended resource (rdma/{{ multus_rdma_resource }}) that pods request.
apiVersion: v1
kind: ConfigMap
metadata:
  name: rdma-devices
  namespace: kube-system
data:
  config.json: |
    {
      "periodicUpdateInterval": 300,
      "configList": [
        {
          "resourceName": "{{ multus_rdma_resource }}",
          "rdmaHcaMax": 64,
          "selectors": { "ifNames": {{ multus_rdma_ifnames | to_json }} }
        }
      ]
    }
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: rdma-shared-dp-ds
  namespace: kube-system
spec:
  selector:
    matchLabels: { name: rdma-shared-dp-ds }
  template:
    metadata:
      labels: { name: rdma-shared-dp-ds }
    spec:
      hostNetwork: true
      priorityClassName: system-node-critical
      containers:
        - name: k8s-rdma-shared-dp-ds
          image: {{ multus_rdma_dp_image }}
          imagePullPolicy: IfNotPresent
          securityContext:
            privileged: true
          volumeMounts:
            - { name: device-plugin, mountPath: /var/lib/kubelet/device-plugins }
            - { name: plugins-registry, mountPath: /var/lib/kubelet/plugins_registry }
            - { name: config, mountPath: /k8s-rdma-shared-dev-plugin }
            - { name: devs, mountPath: /dev/ }
      volumes:
        - { name: device-plugin, hostPath: { path: /var/lib/kubelet/device-plugins } }
        - { name: plugins-registry, hostPath: { path: /var/lib/kubelet/plugins_registry } }
        - { name: config, configMap: { name: rdma-devices, items: [{ key: config.json, path: config.json }] } }
        - { name: devs, hostPath: { path: /dev/ } }
```

#### Run it

**Semaphore UI:** run the template **`21.1 Multus RDMA`**. Break-glass is the `ansible-playbook` line below, run from the MacBook against its own `.cache/` kubeconfig. Then check from the MacBook:

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
export KUBECONFIG=$PWD/.cache/kubeconfig-spark-lab.yaml
ansible-playbook playbooks/21.1-multus-rdma.yml
kubectl --context spark-root get net-attach-def -A                  # cx7-a, cx7-b in platform-tools (and batch after 20.2)
kubectl --context spark-root get nodes -o custom-columns=NAME:.metadata.name,RDMA:.status.allocatable.rdma/rdma_shared_cx7,GPU:.status.allocatable.nvidia\\.com/gpu
ssh dgxadmin@192.168.0.100 'ls /etc/cni/net.d; ls /opt/cni/bin | grep -E "multus|macvlan|static|cilium"'
```

Expected on each node: `RDMA` 64, `GPU` 15. In `/etc/cni/net.d`, `00-multus.conf` sorts before `05-cilium.conflist`. The container runtime uses the first config in lexical order, so every pod now goes through Multus, and Multus hands the default network to Cilium. With `cni.exclusive=true` (Cilium's default) the Cilium agent would rename `00-multus.conf` out of the way on its next restart, and `net1` would silently stop appearing.

### 3.1 Pod-to-pod RDMA across Sparks

#### What this lab does (`kubectl exec` into the test pods)

- **Bandwidth test (two Sparks):** starts an `ib_write_bw` server in `rdma-test-dgx-spark-2` in the background, then runs the client in `rdma-test-dgx-spark-1` against `192.168.100.202`: device `rocep1s0f1`, 4 queue pairs, 10 seconds, result in Gb/s.
- **One Spark:** there is no `rdma-test-dgx-spark-2` pod (see §3), so skip the bandwidth test and run only the `network-status` command.
- **Read-only:** nothing is created or changed; the test pods stay for §3.3.

#### What to look for

1. **The client's result line** (`BW average[Gb/sec]`): within a few percent of the host-level perftest you recorded in Chapter 13.
2. **`network-status`** on `rdma-test-dgx-spark-1` has two entries: `cilium` on `eth0` with a `10.42.x.x` address, and `platform-tools/cx7-a` on `net1` with `192.168.100.201`.

**If something's off:** `Couldn't allocate MR` → the pod lacks `IPC_LOCK` or the locked-memory limit is too low. The client can't connect → the server wasn't up yet; raise the `sleep`. A device-name error → run `ibv_devices` in the pod and use the name it prints. More in §5.

#### Run it

```bash
# ▶ MacBook · any folder
kubectl --context spark-root -n platform-tools exec rdma-test-dgx-spark-2 -- ib_write_bw -d rocep1s0f1 -q 4 -D 10 --report_gbits -F &
sleep 3
kubectl --context spark-root -n platform-tools exec rdma-test-dgx-spark-1 -- ib_write_bw -d rocep1s0f1 -q 4 -D 10 --report_gbits -F 192.168.100.202
```

Inside the pods, `ibv_devices` shows the host's RDMA devices, because the shared plugin exposes them (not isolated). The traffic's source address is the pod's macvlan IP, `.201`/`.202`. Compare the number with the host-level perftest from Chapter 13: it should be within a few percent. On a single Spark both pods sit on dgx-spark-1 and the test runs through the NIC's internal switch; the number says nothing about the cable.

Multus also records what it did, on the root object:

```bash
# ▶ MacBook · any folder
kubectl --context spark-root -n platform-tools get pod rdma-test-dgx-spark-1 \
  -o jsonpath='{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}' | jq '.[] | {name, interface, ips}'
```

### 3.2 A GPU + RDMA pod (what real workloads request)

Real training pods belong to the LLM stack, so this one runs in the root's `batch` namespace, next to the models in `llm-serving`. `batch` enforces `privileged` PSA, because `IPC_LOCK` is not allowed under `baseline` (it warns at `baseline`, so you see the exception on every apply).

```yaml
# nccl-worker-0.yaml (apply with --context spark-root)
apiVersion: v1
kind: Pod
metadata:
  name: nccl-worker-0
  namespace: batch
  annotations:
    k8s.v1.cni.cncf.io/networks: '[{"name":"cx7-a","ips":["192.168.100.211/24"]},{"name":"cx7-b","ips":["192.168.101.211/24"]}]'
spec:
  nodeSelector: { kubernetes.io/hostname: dgx-spark-1 }
  containers:
    - name: w
      image: nvcr.io/nvidia/pytorch:25.09-py3
      command: [sleep, infinity]
      env:
        - { name: NCCL_SOCKET_IFNAME, value: eth0 }            # bootstrap over the pod network (Cilium)
        - { name: NCCL_IB_HCA,        value: "rocep1s0f1,roceP2p1s0f1" }
        - { name: NCCL_DEBUG,         value: INFO }
      securityContext: { capabilities: { add: [IPC_LOCK] } }
      resources:
        requests: { cpu: 500m, memory: 6Gi }
        limits:
          nvidia.com/gpu: 1
          rdma/rdma_shared_cx7: 1
          memory: 6Gi                                          # request = limit; batch-budget caps memory at 12 Gi
```

Every limit on this pod is checked in a different place. `nvidia.com/gpu: 1` is charged against `batch-budget`'s 3 slices by the root API server. `memory: 6Gi` counts against the 12 Gi `batch` may take from the LLM memory pool it shares with `llm-serving`; with request = limit, the scheduler places the pod only if those 6 GiB are free on the node, so it never squeezes a running model. `rdma/rdma_shared_cx7` is checked by nobody until the kubelet's device plugin, which is the quota gap from §2.3. The pod carries no Kueue queue label, so Kueue leaves it alone; a real training Job goes through the ClusterQueue `spark-cq` (02-Kubernetes Chapter 07).

### 3.3 Follow the annotation to the NIC

#### What this lab does (manifest `nccl-worker-0.yaml` from §3.2)

- **One pod in the root's `batch` namespace:** `nccl-worker-0` on dgx-spark-1, with 1 GPU slice, 1 `rdma/rdma_shared_cx7`, 6Gi memory (request = limit) and `IPC_LOCK`. Multus gives it `net1` (`cx7-a`, `192.168.100.211`) and `net2` (`cx7-b`, `192.168.101.211`) next to Cilium's `eth0`.
- **Prerequisites:** `20.2 vClusters` (the `batch` namespace) and then `21.1 Multus RDMA` (the NADs in `batch`, and the `.201` test pod to ping).
- **A ping over the fabric:** installs `iputils-ping` inside the pod and pings the `platform-tools` test pod on `192.168.100.201` through `net1`. Then the pod is deleted.
- **The image** `nvcr.io/nvidia/pytorch:25.09-py3` is large; the first pull takes a while. A PodSecurity warning on `apply` is expected (`IPC_LOCK` is above `baseline`).

#### What to look for

1. **The pod:** `Running` on dgx-spark-1 with a `10.42.x.x` IP.
2. **`network-status`**, three entries:

   | Line/Field | Healthy value |
   |---|---|
   | `cilium` | `eth0`, `10.42.x.x` |
   | `batch/cx7-a` | `net1`, `192.168.100.211` |
   | `batch/cx7-b` | `net2`, `192.168.101.211` |

3. **Inside the pod:** `ip -br addr` lists `eth0`, `net1`, `net2`, and `/dev/infiniband` holds `uverbs…` entries.
4. **The ping:** `3 packets transmitted, 3 received, 0% packet loss`. A `batch` pod reaching a `platform-tools` pod is the §2.3 gap: Cilium policies don't cover `net1`.

**If something's off:** `cannot find a network-attachment-definition (cx7-a) in namespace (batch)` → 21.1 ran before 20.2; run `21.1 Multus RDMA` again. The apply is refused with `exceeded quota: batch-budget` → other `batch` pods hold the 3 slices or the 12 Gi. More in §5.

#### Run it

```bash
# ▶ MacBook · any folder
# in the folder where you saved nccl-worker-0.yaml
kubectl --context spark-root apply -f nccl-worker-0.yaml
kubectl --context spark-root -n batch get pod nccl-worker-0 -o wide           # Running, IP 10.42.x.x (eth0)
kubectl --context spark-root -n batch get pod nccl-worker-0 \
  -o jsonpath='{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}' | jq -c '.[] | {name, interface, ips}'   # cilium/eth0, batch/cx7-a/net1, batch/cx7-b/net2
kubectl --context spark-root -n batch exec nccl-worker-0 -- sh -c 'ip -br addr; ls /dev/infiniband'
kubectl --context spark-root -n batch exec nccl-worker-0 -- bash -c \
  'apt-get update -qq && apt-get install -y -qq iputils-ping >/dev/null; ping -c3 -I net1 192.168.100.201'   # platform-tools pod from §3
kubectl --context spark-root -n batch delete pod nccl-worker-0
```

Read the result as a chain. You wrote an annotation on a root pod in `batch`. Multus on the node read that pod and resolved `cx7-a` in `batch`. It created `net1` and `net2` (macvlan), and wrote `network-status` back onto the same pod. No syncer is involved: the pod keeps its name, and the object you created is the object Multus knows about. For a pod in a vCluster, `network-status` would land on the translated host copy in `vc-*` (§2.3), and you would read it there.

The ping shows the §2.3 gap. A pod in `batch` reaches a `platform-tools` pod on the fabric subnet: Cilium policies only govern `eth0`. Two macvlan pods on the same master talk to each other in bridge mode, even on one node; only the node's own host can't reach them. Use the `.202` pod on dgx-spark-2 for a cross-node test. The 02 lab's [`rdma-test-pod.yaml`](../02-Kubernetes/lab/manifests/root-llms/85-network-operator/rdma-test-pod.yaml) does the same with the Network Operator's NAD `cx7-rdma` (whereabouts IPAM, no `ips` needed). That NAD exists only on the Network Operator path, so with this playbook use `cx7-a` as above.

---

## 4. Integrations

| With | Note |
|---|---|
| Root cluster (Chapter 19) | Cilium `cni.exclusive=false`; Multus uses the standard kubeadm CNI paths |
| GPU Operator (Chapter 20) | Independent: GPU via `nvidia.com/gpu`, RDMA via `rdma/*`. Both limits go on the same container |
| Time-slicing (Chapter 20) | Several pods can share the GPU **and** the shared RDMA device. Neither is isolated, and that's fine for a lab |
| LLM stack and vClusters (Chapter 19 §3.4) | NADs in `batch` only (none in `vc-dev-lab` / `vc-dev-llms`); workloads consume them by name, never create them |
| Slurm (Chapter 22) | Bare-metal jobs don't need any of this; it's the Kubernetes equivalent |
| Network Operator | For production, one Helm chart replaces §3: `NicClusterPolicy` with `rdmaSharedDevicePlugin`, `secondaryNetwork.multus`, `ipamPlugin` (02-Kubernetes Chapter 17 §5.6) |

## 5. Troubleshooting & diagnostics

| Symptom | Diagnose | Fix |
|---|---|---|
| Pod stuck `ContainerCreating`: `failed to find plugin "macvlan"` | `ls /opt/cni/bin` on the node | Reference plugins missing: `sudo apt-get install kubernetes-cni` (from pkgs.k8s.io; keep it at the version kubeadm pulled in) |
| Multus DS ready, but pods have no `net1` | `kubectl --context spark-root describe pod …` events; `ls /etc/cni/net.d` | `00-multus.conf` gone: Cilium renamed it (`cni.exclusive` not `false`; re-run 19.1). Or an annotation typo |
| `batch` pod: `cannot find a network-attachment-definition (cx7-a) in namespace (batch)` | `kubectl --context spark-root -n batch get net-attach-def` | Playbook 21.1 ran before 20.2 (no `batch` namespace yet). Run it again |
| vCluster pod: same error, namespace `vc-dev-llms` or `vc-dev-lab` | `kubectl --context spark-root -n vc-dev-llms get net-attach-def` | By design: the lab gives the vClusters no fabric. Add the `vc-*` namespace to `multus_nad_namespaces` if one needs it |
| All pods stuck `ContainerCreating` after installing Multus | `kubectl --context spark-root -n kube-system logs ds/kube-multus-ds` | The daemon can't reach the API server or read `05-cilium.conflist`. Fix and restart the DS. Every pod's CNI ADD now passes through it |
| `net1` exists, ping to the other pod fails | `ip -d link show net1` in the pod; host `ip link show enp1s0f1np1` | Master netdev down or wrong; macvlan can't reach **its own host** (a known macvlan property). Test pod-to-pod across nodes |
| Node doesn't advertise `rdma/rdma_shared_cx7` | `kubectl --context spark-root -n kube-system logs ds/rdma-shared-dp-ds` | `ifNames` don't match the node's netdevs; image arch (`exec format error`) → use a multi-arch tag |
| `ibv_devices` empty in the pod | Resource not requested / plugin not mounting | Add the `rdma/…` limit; check `/dev/infiniband` inside the pod |
| `ib_write_bw: Couldn't allocate MR` / `Cannot allocate memory` | Locked-memory limit | `IPC_LOCK` capability; container `ulimit -l unlimited` (containerd default may be low) |
| Pod rejected: `violates PodSecurity "baseline"` | Which namespace, which API server? | `IPC_LOCK` needs privileged PSA: `batch` has it, `llm-serving` doesn't. In a vCluster it would be needed in **both** the inner namespace and the root `vc-*` namespace |
| RDMA works but slower than on the host | MTU on the NAD vs the host netdev; QPs | NAD `mtu: 9000` must be ≤ the master's MTU; `-q 4` |

## 6. Validation

- [ ] `00-multus.conf` precedes `05-cilium.conflist` in `/etc/cni/net.d`, and survives a Cilium agent restart.
- [ ] Every node advertises `rdma/rdma_shared_cx7`.
- [ ] `cx7-a`/`cx7-b` exist in `platform-tools` and `batch`, not in `vc-dev-lab` or `vc-dev-llms`.
- [ ] `rdma-test-*` pods have `net1` with the expected IPs and `/dev/infiniband/uverbs*`.
- [ ] Pod-to-pod `ib_write_bw` is close to host-level perftest.
- [ ] A pod in the root's `batch` gets `net1` + `net2`, and you read its `network-status` on the pod itself.
- [ ] (Stretch) Enable `namespaceIsolation` in Multus and show that `platform-tools/cx7-a` is refused from `batch`; replace static IPAM with whereabouts, or swap the whole stack for the NVIDIA Network Operator.
