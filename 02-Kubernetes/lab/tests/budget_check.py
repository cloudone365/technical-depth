#!/usr/bin/env python3
"""Static consistency check of how one DGX Spark is split (Chapter 04 §3).

Reads the files that define the split and fails if they disagree:
  * root quotas            manifests/root/05-vclusters/quotas.yaml
  * kubelet reservations   01-Ansible roles/kubeadm_cluster/defaults/main.yml
  * GPU time-slice count   01-Ansible roles/gpu_operator/defaults/main.yml
  * MetalLB pool           01-Ansible roles/metallb/defaults/main.yml
  * vCluster API/gateway IPs   vclusters/*.yaml, addons/traefik-values.yaml
  * tenant quotas / Kueue queue inside the vClusters (must fit their vCluster)
Prints the table the docs quote. No cluster needed.
"""
import ipaddress
import os
import re
import sys

import yaml

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.join(LAB, "..", "..", "01-Ansible", "lab", "roles")
SPARK = {"cpu": 20.0, "memory_gi": 119.7, "gpu": None}    # gpu filled from the GPU Operator role
# What the root's own platform pods need (Cilium, MetalLB, Traefik, GPU Operator,
# Prometheus, CoreDNS, local-path): the vClusters must leave at least this.
ROOT_PLATFORM = {"cpu": 2.0, "memory_gi": 8.0, "gpu": 1}
errors = []


def load_all(path):
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def cpu(q):
    q = str(q)
    return float(q[:-1]) / 1000 if q.endswith("m") else float(q)


def gi(q):
    q = str(q)
    for suf, mul in (("Ti", 1024), ("Gi", 1), ("Mi", 1 / 1024)):
        if q.endswith(suf):
            return float(q[:-2]) * mul
    return float(q) / 2**30


def check(cond, msg):
    if not cond:
        errors.append(msg)


# ---- kubelet reservations, GPU slices and MetalLB pool from the Ansible roles
kd = yaml.safe_load(open(os.path.join(ANSIBLE, "kubeadm_cluster", "defaults", "main.yml")))
RESERVED = {"cpu": cpu(kd["kubeadm_cluster_system_reserved"]["cpu"]) + cpu(kd["kubeadm_cluster_kube_reserved"]["cpu"]),
            "memory_gi": gi(kd["kubeadm_cluster_system_reserved"]["memory"]) + gi(kd["kubeadm_cluster_kube_reserved"]["memory"])
            + gi(kd["kubeadm_cluster_eviction_hard"]["memory.available"]), "gpu": 0}
gpu_defaults = yaml.safe_load(open(os.path.join(ANSIBLE, "gpu_operator", "defaults", "main.yml")))
SPARK["gpu"] = int(gpu_defaults["gpu_operator_timeslice_replicas"])
mlb = yaml.safe_load(open(os.path.join(ANSIBLE, "metallb", "defaults", "main.yml")))
lo, hi = mlb["metallb_pool_addresses"][0].split("-")
pool = (ipaddress.ip_address(lo), ipaddress.ip_address(hi))

# ---- root quotas per vCluster
quotas = {d["metadata"]["namespace"]: d["spec"]["hard"]
          for d in load_all(os.path.join(LAB, "manifests", "root", "05-vclusters", "quotas.yaml"))}
budget = {}
for ns, h in quotas.items():
    budget[ns] = {"cpu": cpu(h["requests.cpu"]), "memory_gi": gi(h["limits.memory"]),
                  "gpu": int(h["requests.nvidia.com/gpu"]), "storage_gi": gi(h["requests.storage"])}
    check(h["requests.memory"] == h["limits.memory"], f"{ns}: requests.memory must equal limits.memory (hard memory budget)")

ALLOC = {k: SPARK[k] - RESERVED[k] for k in SPARK}
for key in ("cpu", "memory_gi", "gpu"):
    used = sum(b[key] for b in budget.values())
    left = ALLOC[key] - used
    check(left >= ROOT_PLATFORM[key],
          f"vClusters take {used} {key} of {ALLOC[key]:.1f} allocatable: the root keeps {left:.1f}, its platform needs {ROOT_PLATFORM[key]}")

# ---- IPs: vCluster APIs and the llms gateway inside the MetalLB pool, all distinct
ips = {}
for name in ("dev-lab", "llms"):
    v = yaml.safe_load(open(os.path.join(LAB, "vclusters", f"{name}.yaml")))
    ip = v["controlPlane"]["service"]["annotations"]["metallb.io/loadBalancerIPs"]
    ips[f"{name} API"] = ip
    check(ip in v["controlPlane"]["proxy"]["extraSANs"], f"{name}: {ip} missing from proxy.extraSANs (TLS would fail)")
    check(v["exportKubeConfig"]["server"] == f"https://{ip}:443", f"{name}: exportKubeConfig.server does not use {ip}")
    check(v["exportKubeConfig"]["context"] == name, f"{name}: exportKubeConfig.context must be '{name}'")
    res = v["controlPlane"]["statefulSet"]["resources"]
    ns = f"vc-{name}"
    check(gi(res["limits"]["memory"]) < budget[ns]["memory_gi"], f"{name}: control plane memory limit exceeds its budget")
traefik = yaml.safe_load(open(os.path.join(LAB, "addons", "traefik-values.yaml")))
ips["llms gateway"] = traefik["service"]["annotations"]["metallb.io/loadBalancerIPs"]
for what, ip in ips.items():
    a = ipaddress.ip_address(ip)
    check(pool[0] <= a <= pool[1], f"{what} {ip} is outside the MetalLB pool {lo}-{hi}")
check(len(set(ips.values())) == len(ips), f"duplicate LoadBalancer IPs: {ips}")

# ---- inside the vClusters: every single ceiling must fit the vCluster's root budget
def inner(path, ns_vc):
    for d in load_all(path):
        if d.get("kind") != "ResourceQuota":
            continue
        h = d["spec"]["hard"]
        b = budget[ns_vc]
        if "requests.cpu" in h:
            check(cpu(h["requests.cpu"]) <= b["cpu"], f"{d['metadata']['namespace']} cpu ceiling > {ns_vc} budget")
        if "limits.memory" in h:
            check(gi(h["limits.memory"]) <= b["memory_gi"], f"{d['metadata']['namespace']} memory ceiling > {ns_vc} budget")
        if "requests.nvidia.com/gpu" in h:
            check(int(h["requests.nvidia.com/gpu"]) <= b["gpu"], f"{d['metadata']['namespace']} GPU ceiling > {ns_vc} budget")


inner(os.path.join(LAB, "manifests", "dev-lab", "10-tenancy", "quotas.yaml"), "vc-dev-lab")
inner(os.path.join(LAB, "manifests", "llms", "10-tenancy", "quotas.yaml"), "vc-llms")
for d in load_all(os.path.join(LAB, "manifests", "llms", "20-scheduling", "kueue.yaml")):
    if d.get("kind") == "ClusterQueue":
        res = {r["name"]: r["nominalQuota"] for r in d["spec"]["resourceGroups"][0]["flavors"][0]["resources"]}
        b = budget["vc-llms"]
        check(cpu(res["cpu"]) <= b["cpu"] and gi(res["memory"]) <= b["memory_gi"] and int(res["nvidia.com/gpu"]) <= b["gpu"],
              "Kueue spark-cq does not fit the llms budget")

# ---- report
rows = [("Spark total", SPARK["cpu"], SPARK["memory_gi"], SPARK["gpu"]),
        ("kubelet resv.", RESERVED["cpu"], RESERVED["memory_gi"], 0),
        ("allocatable", ALLOC["cpu"], ALLOC["memory_gi"], ALLOC["gpu"])]
for ns in ("vc-dev-lab", "vc-llms"):
    b = budget[ns]
    rows.append((ns, b["cpu"], b["memory_gi"], b["gpu"]))
rows.append(("root keeps", ALLOC["cpu"] - sum(b["cpu"] for b in budget.values()),
             ALLOC["memory_gi"] - sum(b["memory_gi"] for b in budget.values()),
             ALLOC["gpu"] - sum(b["gpu"] for b in budget.values())))
print(f"{'':14} {'CPU':>6} {'mem GiB':>8} {'slices':>7}")
for name, c, m, g in rows:
    print(f"{name:14} {c:6.1f} {m:8.1f} {g:7d}")
print("LoadBalancer IPs:", ", ".join(f"{k} {v}" for k, v in ips.items()), f"(pool {lo}-{hi})")
if errors:
    print("\n".join("FAIL: " + e for e in errors))
    sys.exit(1)
print("budget check OK")
