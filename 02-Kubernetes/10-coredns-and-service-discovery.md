# Chapter 10 · CoreDNS & Service Discovery: Records, the `ndots:5` Tax, Custom Zones, DNS Failures

> **02-Kubernetes · Part III — Networking · Chapter 10 of 28** · ← [Chapter 09 · kube-proxy & ClusterIP mechanics](09-kube-proxy-and-cluster-ip-mechanics.md) · [All chapters](00-kubernetes-step-by-step-guide.md) · [Chapter 11 · Ingress controllers & Gateway API](11-ingress-controllers-and-gateway-api.md) →

| | |
|---|---|
| **You will build** | A map of the lab's **three** DNS servers (the root's CoreDNS and one per vCluster), a measured before/after of DNS query amplification for model downloads and external APIs, query logging and a forward zone for `lab.local` in the root's kubeadm-managed Corefile, and a rehearsed DNS outage whose blast radius is uneven by design |
| **Hardware** | dgx-spark-1 |
| **Time** | 60 min |
| **Risk** | Low. The outage drill (`breakfix 07`) breaks name resolution for root pods for ~5 min |
| **Clusters** | `spark-root` (CoreDNS at 10.43.0.10, its Corefile, `netshoot-host`, and the LLM stack's pods in `llm-serving`, which use it), `dev-lab` (its own CoreDNS, the ndots pods, echo) |
| **Lab files** | [`manifests/root/30-networking/coredns-corefile.yaml`](lab/manifests/root/30-networking/coredns-corefile.yaml), [`manifests/dev-lab/30-networking/dns-lab.yaml`](lab/manifests/dev-lab/30-networking/dns-lab.yaml), [`manifests/dev-lab/30-networking/echo-service.yaml`](lab/manifests/dev-lab/30-networking/echo-service.yaml), [`manifests/root/30-networking/netshoot-host.yaml`](lab/manifests/root/30-networking/netshoot-host.yaml), [`vclusters/dev-lab.yaml`](lab/vclusters/dev-lab.yaml) (a vCluster's own CoreDNS, Chapter 04) |

---

## 1. Why this matters on a Spark

Model servers resolve names constantly: `huggingface.co` and `cdn-lfs.hf.co` at startup, `nvcr.io` for pulls, `qdrant.llm-serving` on every RAG call, and the rendezvous host for torchrun. With the Kubernetes default `ndots:5`, any name with fewer than five dots is first tried against every search domain. **One lookup of `huggingface.co` costs 6–8 extra queries** (3–4 search domains × A + AAAA) before the real answer. On a busy gateway, that's most of CoreDNS's load, and every one of those queries is also a conntrack entry (Chapter 09).

The nesting adds a second lesson: **"cluster DNS" is not one thing here.** The root's CoreDNS answers for root pods. Each vCluster runs its own CoreDNS that reads *its* API server, so `echo.lab-tools.svc.cluster.local` exists in dev-lab and nowhere else, and a tenant can't even name a root Service. When DNS breaks, which pods notice depends on which server they use.

---

## 2. Architecture — HLD

```mermaid
flowchart LR
  subgraph RP["root pod (platform-tools/netshoot-host)"]
    RRC["nameserver 10.43.0.10<br/>search platform-tools.svc.cluster.local …<br/>options ndots:5"]
  end
  subgraph TP["tenant pod (dev-lab lab-tools/dns-default)<br/>= root pod in vc-dev-lab"]
    TRC["nameserver = dev-lab's kube-dns ClusterIP<br/>search lab-tools.svc.cluster.local svc.cluster.local cluster.local<br/>options ndots:5 (written by the syncer)"]
  end
  RRC -->|"UDP/TCP 53"| RSVC["root Service kube-system/kube-dns<br/>10.43.0.10"]
  RSVC --> RCD["root CoreDNS ×2<br/>kube-system"]
  subgraph CF["root Corefile (kubeadm-owned ConfigMap)"]
    direction TB
    K8S["kubernetes cluster.local<br/>→ root Services, pods"]
    LOG["log (lab addition)"]
    LAB["lab.local:53 → forward 192.168.0.1"]
    FWD["forward . /etc/resolv.conf<br/>(node upstream)"]
  end
  RCD --- CF
  RCD -. "watch" .-> RAPI["root API server"]
  TRC -->|"UDP/TCP 53"| VSVC["dev-lab Service kube-system/kube-dns<br/>(synced: kube-dns-x-kube-system-x-dev-lab)"]
  VSVC --> VCD["dev-lab CoreDNS<br/>(root pod in vc-dev-lab)"]
  VCD -. "watch" .-> VAPI["dev-lab API server"]
  VCD --> VUP["forward . upstream<br/>(per its own Corefile)"]
  FWD --> UP["site DNS 192.168.0.1 → internet"]
  LAB --> UP
  VUP --> UP
  classDef net fill:#8250df,stroke:#4c2889,color:#fff
  classDef ctrl fill:#1f6feb,stroke:#0b3d91,color:#fff
  classDef ext fill:#24292f,stroke:#000,color:#fff
  classDef tenant fill:#eaeef2,stroke:#57606a,color:#000
  class RSVC,RCD,VSVC,VCD net
  class K8S,LOG,LAB,FWD,RAPI,VAPI,VUP ctrl
  class UP ext
  class RRC,TRC tenant
  style CF fill:#f6f8fa,stroke:#57606a
  style TP fill:#f6f8fa,stroke:#57606a,stroke-dasharray:5 3
```

Two resolvers, one network: the dev-lab CoreDNS pod and its Service are ordinary root objects in `vc-dev-lab` with a root ClusterIP. The difference is the API server each one reads.

### 2.1 One external lookup from a dev-lab pod, step by step

`getent ahosts huggingface.co` in `dns-default` (dev-lab `lab-tools`, `ndots:5`), as in §5.3. Each query below goes out as an A and an AAAA packet.

```mermaid
sequenceDiagram
  autonumber
  participant P as dns-default pod<br/>dev-lab lab-tools · ndots 5
  participant V as dev-lab CoreDNS<br/>kube-dns ClusterIP of dev-lab
  participant VAPI as dev-lab API server
  participant U as upstream<br/>per the forward line of dev-lab's Corefile
  VAPI-->>V: watch: Services and EndpointSlices of dev-lab only
  P->>V: huggingface.co.lab-tools.svc.cluster.local<br/>1 dot, fewer than 5, so the search list comes first
  V-->>P: NXDOMAIN, answered from its watch cache
  P->>V: huggingface.co.svc.cluster.local
  V-->>P: NXDOMAIN
  P->>V: huggingface.co.cluster.local
  V-->>P: NXDOMAIN
  P->>V: huggingface.co.
  V->>U: forward, not a cluster.local name
  U-->>V: A and AAAA records
  V-->>P: NOERROR with the addresses
```

- **Steps 2–7** are the three search-domain queries. As A + AAAA they are the six packets `tcpdump` counts before the real name in §5.3. `dns-ndots1` (`ndots:1`) starts at step 8 and sends only 2.
- **Step 1** is why `kps-prometheus.observability` and every other root name get NXDOMAIN here (§5.4): this CoreDNS knows only dev-lab's objects.
- **The nameserver** in step 2 is dev-lab's `kube-dns` ClusterIP, written into the pod by the syncer (§5.1). A root pod sends the same queries to `10.43.0.10`, and the root's query log shows them (§5.2–5.3).
- **Step 9** depends on the `forward` line you read in §5.1. That line decides whether the root DNS outage in §5.5 also breaks a tenant's external names.

---

## 3. LLD

### 3.1 Record types you'll use

| Query (asked inside the cluster that owns the Service) | Answer | Example |
|---|---|---|
| `svc.ns.svc.cluster.local` A | ClusterIP | on the root: `vllm.llm-serving.svc.cluster.local → 10.43.x.y` |
| headless `svc.ns.svc.cluster.local` A | every ready pod IP | in dev-lab: `echo-headless.lab-tools… → 3 IPs` |
| `pod-hostname.subdomain.ns.svc.cluster.local` A | that pod | on the root: `ddp-0.ddp-workers.batch.svc.cluster.local` (Chapter 18) |
| `qdrant-0.qdrant-headless.llm-serving.svc.cluster.local` | StatefulSet pod | on the root, Chapter 12 |
| `_http._tcp.echo.lab-tools.svc.cluster.local` SRV | port + target | in dev-lab, named ports |
| `kps-prometheus.observability.svc.cluster.local` | the root's Prometheus ClusterIP | on the root (KEDA, the model servers); NXDOMAIN from inside a vCluster — that namespace exists only on the root |

### 3.2 Query amplification with `ndots:5`

For `huggingface.co` (1 dot < 5) from a pod in dev-lab's `lab-tools`:

| # | Name tried | Result |
|---|---|---|
| 1-2 | `huggingface.co.lab-tools.svc.cluster.local` A/AAAA | NXDOMAIN |
| 3-4 | `huggingface.co.svc.cluster.local` | NXDOMAIN |
| 5-6 | `huggingface.co.cluster.local` | NXDOMAIN |
| 7-8 | `huggingface.co.lab.local`, only if the pod's search list has `lab.local` (root pods that inherit the host's search domains, e.g. `ClusterFirstWithHostNet`) | NXDOMAIN (forwarded upstream!) |
| 9-10 | `huggingface.co` | ✅ |

Three fixes, in order of preference:

| Fix | Where | Trade-off |
|---|---|---|
| Use FQDNs with a trailing dot: `huggingface.co.` | app config / env | zero cost. Some HTTP libraries mishandle the dot in `Host:` |
| `dnsConfig.options: ndots: "1"` | pod spec (as in `dns-ndots1`) — the vCluster syncer keeps your options when it rewrites the pod's DNS | short in-cluster names (`qdrant`) still work via search. `qdrant.llm-serving` needs `.svc` |
| NodeLocal DNSCache | DaemonSet on every root node | caches on the node, avoids conntrack for UDP. Worth it at DC scale; vCluster pods need their nameserver pointed at it |

### 3.3 kubeadm and vCluster specifics

| Item | Root (kubeadm) | Inside a vCluster |
|---|---|---|
| Server | Deployment `kube-system/coredns`, 2 replicas, PriorityClass `system-cluster-critical` | Deployment `kube-system/coredns` *inside* the vCluster; on the root a pod in `vc-<vc>` |
| Service | `kube-system/kube-dns` = **10.43.0.10** (kubelet `clusterDNS`) | `kube-system/kube-dns` inside, synced as `kube-dns-x-kube-system-x-<vc>` with its own root ClusterIP |
| How pods find it | kubelet writes `/etc/resolv.conf` (`dnsPolicy: ClusterFirst`) | the syncer rewrites the root pod to `dnsPolicy: None` + a `dnsConfig` with the vCluster's DNS IP and the **inner** namespace's search path |
| Corefile | ConfigMap `kube-system/coredns`, owned by kubeadm. **No import hook: you edit the Corefile itself**, and `kubeadm upgrade` rewrites it (re-apply [`coredns-corefile.yaml`](lab/manifests/root/30-networking/coredns-corefile.yaml) after an upgrade) | managed by vCluster; change it with `controlPlane.coredns.overwriteConfig` in [`vclusters/<vc>.yaml`](lab/vclusters/dev-lab.yaml) and `helm upgrade` (Chapter 04) |
| Reload | the `reload` plugin re-reads the ConfigMap within ~30 s, no restart | same plugin, after vCluster updates its ConfigMap |
| Upstream | `forward . /etc/resolv.conf` → the kubelet's `resolvConf`; on DGX OS with systemd-resolved, kubeadm points that at `/run/systemd/resolve/resolv.conf` (the real servers), not `127.0.0.53` | whatever the vCluster's Corefile forwards to — read it in §5.1 |
| Who sees `lab.local` | root pods (the lab's `lab.local:53` block) | only if the upstream it forwards to resolves `lab.local` |

---

## 4. Integrations

- **01-Ansible site DNS (`dns_servers: [192.168.0.1, …]`)**: the root's `lab.local` forward zone lets platform pods resolve `dgx-spark-2.lab.local` and your NAS by name.
- **Model downloads (Chapter 20, modules 03–06)**: set `HF_ENDPOINT`/`HF_HUB_*` and any registry mirrors as FQDNs. The model servers run in the root's `llm-serving`, so it's the root's CoreDNS that pays their ndots tax — it shows up in the query log of §5.3 and in the root's CoreDNS metrics. A small model tried in dev-llms' `llm-sandbox` pays it to dev-llms' CoreDNS instead.
- **KEDA on the root (Chapter 20)** queries `http://kps-prometheus.observability.svc:9090`: an ordinary root name, because KEDA and Prometheus are in the same cluster. A vCluster could only reach a root Service by name if the root Service were replicated into it (`networking.replicateServices.fromHost`); this lab replicates none, so the vClusters see their own Services and nothing else.
- **Prometheus (Chapter 17)**: the root's CoreDNS exposes `coredns_dns_requests_total{type}` and `coredns_dns_responses_total{rcode}`. An NXDOMAIN ratio > 50 % is the ndots tax showing up in a graph. The vClusters' CoreDNS pods are not in the root's `vcluster-workloads` ServiceMonitor; adding them is an exercise.
- **NetworkPolicy (Chapter 08)**: a tenant that restricts egress must allow UDP/TCP 53 to *its vCluster's* CoreDNS. Inside the vCluster that's `kube-system`, the same selector a tenant would write in a normal cluster.

---

## 5. Lab

Run from `02-Kubernetes/lab` with the lab kubeconfig (`export KUBECONFIG="$PWD/../../01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml"`).

### 5.1 Inspect the resolvers

#### What this lab does (manifests `lab/manifests/root/30-networking`, `lab/manifests/dev-lab/30-networking`)

- **Applies** the network toolbox (unchanged if Chapter 08 or 09 already did): `netshoot-host` on the root in `platform-tools`, and in dev-lab `lab-tools`: `netshoot`, `echo`, and the two DNS test pods `dns-default` (inherits `ndots:5`) and `dns-ndots1` (`ndots:1`, `single-request-reopen`).
- **Reads** the `resolv.conf` of a root pod and the two tenant pods, dev-lab's `kube-dns` Service and its root copy, the `dnsPolicy` the syncer wrote on the root pods, and both Corefiles.
- **Read-only:** nothing in CoreDNS is changed yet.

#### What to look for

| Item | Healthy value |
|---|---|
| `netshoot-host` `resolv.conf` | `nameserver 10.43.0.10`, search starts with `platform-tools.svc.cluster.local` |
| `dns-default` `resolv.conf` | `nameserver` = dev-lab's `kube-dns` ClusterIP (a `10.43.x.y`, not `10.43.0.10`), search starts with `lab-tools.svc.cluster.local`, `options ndots:5` |
| `dns-ndots1` `resolv.conf` | same nameserver, `options ndots:1 single-request-reopen` |
| root copies of the `dns-lab` pods | `None` and that same nameserver |
| dev-lab Corefile | note its `forward` line, §5.5 needs it |

**If something's off:** a tenant pod with `nameserver 10.43.0.10` means the syncer didn't rewrite its DNS (check the vCluster's CoreDNS pod in `vc-dev-lab` is Running). More in §7.

#### Run it

```bash
# ▶ MacBook · 02-Kubernetes/lab
kubectl --context spark-root apply -k manifests/root/30-networking         # netshoot-host
kubectl --context dev-lab apply -k manifests/dev-lab/30-networking         # dns-default, dns-ndots1, echo, netshoot

# a root pod
kubectl --context spark-root -n platform-tools exec deploy/netshoot-host -- cat /etc/resolv.conf
# two tenant pods in dev-lab
kubectl --context dev-lab -n lab-tools exec dns-default -- cat /etc/resolv.conf
kubectl --context dev-lab -n lab-tools exec dns-ndots1  -- cat /etc/resolv.conf
# who answers for dev-lab?
kubectl --context dev-lab -n kube-system get svc kube-dns
kubectl --context spark-root -n vc-dev-lab get pods,svc | grep -i -E 'coredns|kube-dns'
kubectl --context spark-root -n vc-dev-lab get pod -l app=dns-lab -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.dnsPolicy}{" "}{.spec.dnsConfig.nameservers}{"\n"}{end}'
# the two Corefiles
kubectl --context spark-root -n kube-system get cm coredns -o jsonpath='{.data.Corefile}'
kubectl --context dev-lab -n kube-system get cm coredns -o jsonpath='{.data.Corefile}'
```

Expected: the root pod's nameserver is `10.43.0.10` with a `platform-tools.svc.cluster.local` search path (plus the host's search domains, because of `ClusterFirstWithHostNet`). The dev-lab pods' nameserver is dev-lab's `kube-dns` ClusterIP — another 10.43.x.y — with a `lab-tools.svc.cluster.local` search path, although on the root they run in `vc-dev-lab`. On the root, their spec says `dnsPolicy: None` with that nameserver: the syncer wrote it. `dns-ndots1` keeps its `ndots:1` and `single-request-reopen`.

Note the `forward` line in dev-lab's Corefile: it tells you whether a tenant's external lookups go straight to the node's upstream or through the root's CoreDNS. You'll need it in §5.5.

### 5.2 Turn on query logging and add the `lab.local` zone (root)

#### What this lab does (manifest `lab/manifests/root/30-networking/coredns-corefile.yaml`)

- **Saves** kubeadm's Corefile ConfigMap to `/tmp/coredns-before.yaml`, then **replaces** the root's Corefile with kubeadm's default plus `log` (every query) and a `lab.local:53` block forwarding to `192.168.0.1`.
- CoreDNS's `reload` plugin picks it up within about 30 s, no restart.
- **Starts** a background `kubectl logs -f` of the root CoreDNS pods into `/tmp/coredns.log` for §5.3.
- Only the root's CoreDNS changes. The vClusters' CoreDNS is untouched.

#### What to look for

1. **Command exit:** `configmap/coredns configured`, plus a warning about the missing last-applied annotation. The warning is expected.
2. **Reload:** the `grep -i reload` line shows a `Reloading` / `Reloading complete` message from the `reload` plugin.
3. **Background job:** `jobs` lists the `kubectl logs` job as Running.

**If something's off:** no reload line after 35 s usually means the Corefile has a syntax error and CoreDNS kept the old one: read `kubectl --context spark-root -n kube-system logs -l k8s-app=kube-dns --tail=20`. More in §7.

#### Run it

The root's Corefile is kubeadm's ConfigMap; the lab file is kubeadm's default plus `log` and a `lab.local:53` block:

```bash
# ▶ MacBook · 02-Kubernetes/lab
kubectl --context spark-root -n kube-system get cm coredns -o yaml > /tmp/coredns-before.yaml   # keep kubeadm's version
kubectl --context spark-root apply -f manifests/root/30-networking/coredns-corefile.yaml
sleep 35                                                                                        # 'reload' picks it up
kubectl --context spark-root -n kube-system logs -l k8s-app=kube-dns --tail=5 | grep -i reload
kubectl --context spark-root -n kube-system logs -l k8s-app=kube-dns -f --tail=0 --prefix=false >> /tmp/coredns.log &   # append mode: §5.3 empties the file while this runs
```

`kubectl apply` warns that the ConfigMap lacks the last-applied annotation — expected, kubeadm created it.

### 5.3 Measure the amplification

#### What this lab does (root query log, `tcpdump` in the dev-lab DNS pods)

- **Root:** one lookup of `huggingface.co` from `netshoot-host`, counted in the root CoreDNS query log from §5.2.
- **dev-lab:** the same lookup from `dns-default` and `dns-ndots1`, counted with `tcpdump` on each pod's `eth0` (those queries go to dev-lab's CoreDNS, so the root log can't see them). Then 50 lookups each, timed.
- **Read-only:** nothing is changed. `kill %1` stops the background log job.

#### What to look for

1. **Root:** `netshoot-host: 8 queries`, all NXDOMAIN except the last `A IN huggingface.co. NOERROR`. A `huggingface.co.lab.local` pair (if the host has `search lab.local`) is answered by the `lab.local:53` block, which has no `log`, so it isn't counted.
2. **`dns-default`:** 8 queries, A and AAAA for each of the 3 search domains and the real name.
3. **`dns-ndots1`:** 2 queries, `A? huggingface.co.` and `AAAA? huggingface.co.`.
4. **Timing:** `dns-ndots1`'s `real` time is lower than `dns-default`'s. The gap depends on the upstream, so expect a difference rather than a fixed number.

**If something's off:** extra pairs in the dev-lab counts mean the vCluster also appended the node's search domains, which is expected on some releases. An empty root count means the log job isn't running (re-run the last line of §5.2). More in §7.

#### Run it

**Root pod, from the root CoreDNS's query log:**

```bash
# ▶ MacBook · any folder
: > /tmp/coredns.log; sleep 1
kubectl --context spark-root -n platform-tools exec deploy/netshoot-host -- sh -c 'getent ahosts huggingface.co >/dev/null'
sleep 2; echo "netshoot-host: $(grep -c 'huggingface' /tmp/coredns.log) queries"
grep huggingface /tmp/coredns.log | awk '{print $5, $6, $7, $12}' | head -12
kill %1
```

Expected:

```text
netshoot-host: 8 queries         # A + AAAA for 3 cluster domains and the real name
"AAAA IN huggingface.co.platform-tools.svc.cluster.local. NXDOMAIN
"A IN huggingface.co.platform-tools.svc.cluster.local. NXDOMAIN
…
"A IN huggingface.co. NOERROR
```

**Tenant pods in dev-lab.** Their queries go to dev-lab's CoreDNS, not the root's, so the root log doesn't see the search-domain walk at all. Count them on the wire instead — the netshoot image has `tcpdump`:

```bash
# ▶ MacBook · any folder
for p in dns-default dns-ndots1; do
  echo "── $p"
  kubectl --context dev-lab -n lab-tools exec $p -- sh -c \
    'tcpdump -nli eth0 udp dst port 53 2>/dev/null & sleep 1; getent ahosts huggingface.co >/dev/null; sleep 2; kill $!' \
    | awk '{print $(NF-2), $(NF-1)}' | sort | uniq -c
done
```

Expected:

```text
── dns-default
      1 A? huggingface.co.
      1 A? huggingface.co.cluster.local.
      1 A? huggingface.co.lab-tools.svc.cluster.local.
      1 A? huggingface.co.svc.cluster.local.
      1 AAAA? huggingface.co.
      1 AAAA? huggingface.co.cluster.local.
      1 AAAA? huggingface.co.lab-tools.svc.cluster.local.
      1 AAAA? huggingface.co.svc.cluster.local.
── dns-ndots1
      1 A? huggingface.co.
      1 AAAA? huggingface.co.
```

Eight queries for one lookup with `ndots:5` (three search domains × A + AAAA, then the real name), two with `ndots:1`. The count follows the `search` line you printed in §5.1: if your vCluster release also appends the node's search domains (e.g. `lab.local`), you'll see one more pair per extra domain. Drop the `awk` to see the destination of each packet: dev-lab's DNS ClusterIP.

Time it too:

```bash
# ▶ MacBook · any folder
for p in dns-default dns-ndots1; do
  kubectl --context dev-lab -n lab-tools exec $p -- sh -c 'time (for i in $(seq 50); do getent ahosts huggingface.co >/dev/null; done)' 2>&1 | grep real | sed "s/^/$p /"
done
```

### 5.4 Service discovery records — and where they stop

#### What this lab does (`dig` from `netshoot` in dev-lab and `netshoot-host` on the root)

- Resolves the `echo` Services in dev-lab (A, headless and SRV records) and dev-lab's own `kubernetes` Service.
- Tries names across the boundary: a root name from dev-lab, a dev-lab name from the root, the root's own name for the echo Service, and `dgx-spark-2.lab.local` through the `lab.local` zone.
- Optional: compares a root name from `mock-llm-canary` in `llm-serving` with the Service's ClusterIP.
- **Read-only.**

#### What to look for

| Query | Healthy value |
|---|---|
| `echo.lab-tools…` | one ClusterIP |
| `echo-headless.lab-tools…` | three `10.42.0.x` pod IPs |
| `SRV _http._tcp.echo…` | a record pointing at `echo.lab-tools.svc.cluster.local.` port 80 |
| `kps-prometheus.observability…` from dev-lab | empty |
| `echo.lab-tools…` from the root | empty |
| `echo-x-lab-tools-x-dev-lab.vc-dev-lab…` from the root | the same ClusterIP as the first line |
| `dgx-spark-2.lab.local` | the address your site DNS has for it; empty is fine with only one Spark if there's no record |
| `mock-llm-canary` lookup vs `get svc kps-prometheus` | the same IP |

**If something's off:** `dgx-spark-2.lab.local` empty while your site DNS has the record means the `lab.local` block wasn't applied (§5.2). More in §7.

#### Run it

```bash
# ▶ MacBook · any folder
kubectl --context dev-lab -n lab-tools exec deploy/netshoot -- dig +short echo.lab-tools.svc.cluster.local
kubectl --context dev-lab -n lab-tools exec deploy/netshoot -- dig +short echo-headless.lab-tools.svc.cluster.local
kubectl --context dev-lab -n lab-tools exec deploy/netshoot -- dig +short SRV _http._tcp.echo.lab-tools.svc.cluster.local
kubectl --context dev-lab -n lab-tools exec deploy/netshoot -- dig +short kubernetes.default.svc.cluster.local      # dev-lab's own API Service
# names that belong to other clusters
kubectl --context dev-lab -n lab-tools exec deploy/netshoot -- dig +short kps-prometheus.observability.svc.cluster.local   # empty: root-only
kubectl --context spark-root -n platform-tools exec deploy/netshoot-host -- dig +short echo.lab-tools.svc.cluster.local    # empty: dev-lab-only
kubectl --context spark-root -n platform-tools exec deploy/netshoot-host -- dig +short echo-x-lab-tools-x-dev-lab.vc-dev-lab.svc.cluster.local  # the root's name for it
kubectl --context spark-root -n platform-tools exec deploy/netshoot-host -- dig +short dgx-spark-2.lab.local                   # via the root's lab.local zone
```

Expected: a ClusterIP, three pod IPs, an SRV record pointing at `echo.lab-tools.svc.cluster.local` port 80, dev-lab's own `kubernetes` ClusterIP — and empty answers for names that belong to another cluster. The last-but-one shows the same ClusterIP as the first: one Service, two names, two DNS servers.

The model servers are on the other side of that boundary: they run in the root's `llm-serving`, so the root's CoreDNS answers them and root names resolve. If you applied `root-llms/40-ingress` (Chapter 08 §5.5), compare:

```bash
# ▶ MacBook · any folder
kubectl --context spark-root -n llm-serving exec deploy/mock-llm-canary -- \
  python3 -c "import socket;print(socket.gethostbyname('kps-prometheus.observability.svc.cluster.local'))"   # a root ClusterIP
kubectl --context spark-root -n observability get svc kps-prometheus -o jsonpath='{.spec.clusterIP}{"\n"}'     # the same IP
kubectl --context spark-root -n llm-serving exec deploy/mock-llm-canary -- cat /etc/resolv.conf             # nameserver 10.43.0.10, search llm-serving.svc.cluster.local …
```

### 5.5 Outage drill

#### What this lab does (drill `breakfix 07`)

- `scripts/breakfix.sh inject 07` scales the root's `kube-system/coredns` Deployment to 0. The vClusters' own CoreDNS keeps running.
- Tests name and IP access from a root pod and from a dev-lab pod, then `answer 07` explains, and `reset 07` scales CoreDNS back to 2 replicas.
- At the end, turns query logging off again (or restores `/tmp/coredns-before.yaml`).
- While injected, every root pod loses name resolution, including Traefik, KEDA and the model servers. Keep the drill short.

#### What to look for

1. **Root, by name:** `name lookup failed`.
2. **Root, by IP:** an HTTP code (`200` or a redirect such as `302` from Grafana): the network is fine, DNS isn't.
3. **dev-lab:** `echo.lab-tools` still answers with a pod name (`← cluster names still work`). Whether `huggingface.co` fails depends on the `forward` line you read in §5.1.
4. **After `reset 07`:** `kubectl --context spark-root -n kube-system get deploy coredns` shows `2/2`, and `scripts/verify.sh platform` reports `CoreDNS ready`.

**If something's off:** if the root name lookup still works during the drill, the CoreDNS pods may still be shutting down (`lameduck 5s`); check `get deploy coredns` shows `0/0` and retry. More in §7.

#### Run it

```bash
# ▶ MacBook · 02-Kubernetes/lab
scripts/breakfix.sh inject 07
# root pods: by name, then by IP
kubectl --context spark-root -n platform-tools exec deploy/netshoot-host -- curl -s -m3 http://kps-grafana.observability || echo "name lookup failed"
kubectl --context spark-root -n platform-tools exec deploy/netshoot-host -- curl -s -m3 -o /dev/null -w '%{http_code}\n' \
  "http://$(kubectl --context spark-root -n observability get svc kps-grafana -o jsonpath='{.spec.clusterIP}')"   # by IP → works
# tenant pods: same drill in dev-lab
kubectl --context dev-lab -n lab-tools exec deploy/netshoot -- curl -s -m3 http://echo.lab-tools && echo " ← cluster names still work"
kubectl --context dev-lab -n lab-tools exec deploy/netshoot -- dig +short +time=2 huggingface.co || echo "external names fail"
# diagnose and fix it yourself; then:
scripts/breakfix.sh answer 07
scripts/breakfix.sh reset 07
```

The key triage move: **try by IP**. If the IP works and the name doesn't, it's DNS, not networking.

The second lesson is the blast radius. Root pods lose all name resolution at once — and on this lab the root pods include the model servers, Traefik and KEDA. KEDA's query to `kps-prometheus.observability.svc` fails, so autoscaling stops; a model that is already loaded keeps answering, because Traefik reaches it by pod IP from its EndpointSlices; a model server that restarts now can't resolve `huggingface.co`. Tenants keep resolving their own cluster names because their vCluster's CoreDNS is still up. Whether their *external* names break depends on the `forward` line you read in §5.1. A shared dependency with an uneven, delayed failure is the hardest kind to spot from a tenant's ticket: "some lookups fail, the cluster looks fine".

Turn query logging off when you're done (it's chatty) — re-apply the lab Corefile without the `log` line, or restore kubeadm's original:

```bash
# ▶ MacBook · 02-Kubernetes/lab
sed '/^        log$/d' manifests/root/30-networking/coredns-corefile.yaml | kubectl --context spark-root apply -f -
# or: kubectl --context spark-root apply -f /tmp/coredns-before.yaml   (drops lab.local too)
```

---

## 6. Verify

| Check | Expected |
|---|---|
| `scripts/verify.sh platform` | `CoreDNS ready` PASS |
| tenant pod's nameserver | dev-lab's `kube-dns` ClusterIP, not 10.43.0.10 |
| `dns-default` names tried for one external lookup | every search domain in its resolv.conf, then the real name (8 queries with 3 domains) |
| `dns-ndots1` names tried | the real name only |
| headless lookup (dev-lab) | 3 A records |
| `kps-prometheus.observability` from dev-lab | no answer |
| `coredns_dns_responses_total{rcode="NXDOMAIN"}` rate (root) | drops after you apply `ndots: "1"` to hot clients |

---

## 7. Troubleshooting

| Symptom | Cause | Diagnose | Fix |
|---|---|---|---|
| `Could not resolve host`, everything by name fails in root pods | root CoreDNS down / 0 replicas / NetworkPolicy blocks egress to `kube-system` | `kubectl --context spark-root -n kube-system get deploy coredns`. Try by IP | restore CoreDNS (`breakfix 07`). Allow UDP/TCP 53 to `kube-system` in egress policies |
| Names fail only inside one vCluster | that vCluster's CoreDNS pod not running (e.g. its root quota is spent, or the control plane is down) | `kubectl --context spark-root -n vc-<vc> get pods \| grep coredns`; `kubectl --context <vc> -n kube-system get pods` | free budget (Chapter 04 §8); restart the vCluster's CoreDNS |
| CoreDNS `CrashLoopBackOff`, log `Loop … detected` | upstream is a local stub (127.0.0.53) forwarding back to itself | `kubectl --context spark-root -n kube-system logs deploy/coredns` | kubelet `resolvConf: /run/systemd/resolve/resolv.conf` (kubeadm sets it when systemd-resolved is active) |
| Corefile change "didn't take" | edited the vCluster's ConfigMap (vCluster owns it), or `kubeadm upgrade` reset the root's | compare `get cm coredns` before/after; CoreDNS logs `Reloading` | root: re-apply `coredns-corefile.yaml`. vCluster: `controlPlane.coredns.overwriteConfig` + `helm upgrade` |
| Root's `lab.local` zone works on the root, not in tenants | tenants use their vCluster's CoreDNS | §5.1 resolv.conf | add the zone to the vCluster's CoreDNS config, or use FQDNs the upstream resolves |
| Tenant can't resolve a root Service | by design: vCluster DNS only knows its own API | `dig` from inside | use the published entry point (the gateway at 192.168.0.115), or replicate the Service into the vCluster (`networking.replicateServices.fromHost` in `vclusters/<vc>.yaml`; this lab replicates none) |
| Intermittent `EAI_AGAIN` / 5 s stalls in Python | UDP conntrack race on parallel A+AAAA queries | `conntrack -S` `insert_failed` counter rises | `single-request-reopen` option (in `dns-ndots1`, glibc images), NodeLocal DNSCache |
| Slow first request to HF/NGC, fast afterwards | ndots amplification + cold cache | §5.3 | trailing dots / `ndots:1` |
| `ddp-0.ddp-workers…` NXDOMAIN at job start | pod not Ready yet and Service lacks `publishNotReadyAddresses` | `dig` from another pod in the root's `batch` namespace | set `publishNotReadyAddresses: true` on rendezvous Services (the lab's does) |

---

## 8. Scale-out path

| Lab | Datacenter |
|---|---|
| 2 root CoreDNS replicas, 1 per vCluster | ≥ 2 replicas with anti-affinity everywhere, HPA or cluster-proportional-autoscaler |
| query logging on demand | CoreDNS metrics + sampled logs to Loki, for the root and every virtual cluster |
| `ndots` per pod | NodeLocal DNSCache on every node, plus an admission policy that sets `ndots:2` on serving namespaces |
| forward zone to site DNS | split-horizon DNS, ExternalDNS publishing Ingress/Gateway hosts |
| no replicated Services: a vCluster sees only its own | a deliberate catalogue of platform services exposed into tenant clusters, everything else invisible |

---

## 9. Checklist

- [ ] I can name the DNS server a root pod and a vCluster pod each use, and show where the syncer set it.
- [ ] I measured how many names one external lookup tries with `ndots:5`, and with `ndots:1`.
- [ ] I added a zone and query logging to the kubeadm-managed root Corefile, and know `kubeadm upgrade` resets it.
- [ ] I resolved a Service, a headless Service and an SRV record by name — and showed that names stop at the cluster boundary.
- [ ] I can tell a DNS outage from a network outage in one command, and explain why the root outage hit tenants unevenly.
