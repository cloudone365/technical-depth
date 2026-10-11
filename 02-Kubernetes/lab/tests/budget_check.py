#!/usr/bin/env python3
"""Static consistency check of how one DGX Spark is split (Chapter 04 §3).

Reads the files that define the split and fails if they disagree:
  * vCluster budgets       manifests/root/05-vclusters/quotas.yaml
  * the root's LLM share   manifests/root-llms/10-tenancy/quotas.yaml (serving, batch)
                           manifests/root-llms/20-scheduling/kueue.yaml (spark-cq = batch)
  * kubelet reservations   01-Ansible roles/kubeadm_cluster/defaults/main.yml
  * GPU time-slice count   01-Ansible roles/gpu_operator/defaults/main.yml
  * MetalLB pool           01-Ansible roles/metallb/defaults/main.yml
  * vCluster API/gateway IPs   vclusters/*.yaml, addons/traefik-values.yaml
  * tenant quotas inside the vClusters (must fit their vCluster)
The root keeps what the vClusters don't take. CPU and GPU slices are split
exactly (platform + serving + batch); memory is one LLM pool shared by serving
and batch, so serving's ceiling + the platform's floor must fit what the root
keeps, and batch's ceiling must fit inside the pool. The scheduler is what
stops serving + batch from overcommitting the pool, which only works if every
GPU container in the LLM stack sets memory request = limit — checked too.
Prints the table the docs quote. No cluster needed.
"""
import ipaddress
import os
import re
import sys

import yaml

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.join(LAB, "..", "..", "01-Ansible", "lab", "roles")
SPARK = {"cpu": 20.0, "memory_gi": 121.7, "gpu": None}    # gpu filled from the GPU Operator role
# What the root's own platform pods need (Cilium, MetalLB, Traefik, Kueue, KEDA,
# GPU Operator, Prometheus, CoreDNS, local-path, platform-tools): the vClusters
# and the LLM quotas must leave at least this.
ROOT_PLATFORM = {"cpu": 2.0, "memory_gi": 8.0, "gpu": 1}
VCLUSTERS = ("dev-lab", "dev-llms")
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
ROOT = {k: ALLOC[k] - sum(b[k] for b in budget.values()) for k in ("cpu", "memory_gi", "gpu")}

# ---- the root's LLM share: serving + batch (+ Kueue = batch) + platform floor <= what the root keeps
llm = {}
for d in load_all(os.path.join(LAB, "manifests", "root-llms", "10-tenancy", "quotas.yaml")):
    h = d["spec"]["hard"]
    llm[d["metadata"]["namespace"]] = {"cpu": cpu(h["requests.cpu"]), "memory_gi": gi(h["limits.memory"]),
                                       "gpu": int(h["requests.nvidia.com/gpu"]), "storage_gi": gi(h["requests.storage"])}
check(set(llm) == {"llm-serving", "batch"}, f"root-llms quotas: expected llm-serving and batch, got {sorted(llm)}")
for d in load_all(os.path.join(LAB, "manifests", "root-llms", "20-scheduling", "kueue.yaml")):
    if d.get("kind") == "ClusterQueue":
        res = {r["name"]: r["nominalQuota"] for r in d["spec"]["resourceGroups"][0]["flavors"][0]["resources"]}
        b = llm.get("batch", {})
        check(cpu(res["cpu"]) == b.get("cpu") and gi(res["memory"]) == b.get("memory_gi") and int(res["nvidia.com/gpu"]) == b.get("gpu"),
              f"Kueue spark-cq {res} must equal the batch namespace quota {b}")
serving, batch = llm.get("llm-serving", {}), llm.get("batch", {})
PLATFORM = {"cpu": ROOT["cpu"] - serving.get("cpu", 0) - batch.get("cpu", 0),
            "gpu": ROOT["gpu"] - serving.get("gpu", 0) - batch.get("gpu", 0),
            "memory_gi": ROOT["memory_gi"] - serving.get("memory_gi", 0)}      # batch memory shares the pool
for key in ("cpu", "memory_gi", "gpu"):
    check(PLATFORM[key] >= ROOT_PLATFORM[key] - 1e-6,
          f"root keeps {ROOT[key]:.1f} {key}; after the LLM quotas the platform is left {PLATFORM[key]:.1f}, it needs {ROOT_PLATFORM[key]}")
check(batch.get("memory_gi", 0) <= serving.get("memory_gi", 0),
      "batch memory must fit inside the LLM pool (serving's memory ceiling)")
for d in load_all(os.path.join(LAB, "manifests", "root-llms", "10-tenancy", "quotas.yaml")):
    h = d["spec"]["hard"]
    check(h.get("requests.memory") == h.get("limits.memory"),
          f"{d['metadata']['namespace']}: requests.memory must equal limits.memory")

# ---- training window (root-llms/training-window): serving + batch swap CPU/memory, same totals
import subprocess
try:
    out = subprocess.run([os.environ.get("KUBECTL", "kubectl"), "kustomize",
                          os.path.join(LAB, "manifests", "root-llms", "training-window")],
                         capture_output=True, text=True, check=True).stdout
    win = {d["metadata"]["namespace"]: d["spec"]["hard"] for d in yaml.safe_load_all(out)
           if d and d.get("kind") == "ResourceQuota" and d["metadata"]["name"] in ("serving-budget", "batch-budget")}
    w_cpu = sum(cpu(h["requests.cpu"]) for h in win.values())
    w_mem = sum(gi(h["limits.memory"]) for h in win.values())
    check(abs(w_cpu - (serving.get("cpu", 0) + batch.get("cpu", 0))) < 1e-6,
          f"training window: serving + batch CPU {w_cpu} must equal the everyday {serving.get('cpu', 0) + batch.get('cpu', 0)}")
    check(w_mem <= serving.get("memory_gi", 0) + 1e-6,
          f"training window: serving + batch memory {w_mem} Gi must fit the LLM pool ({serving.get('memory_gi', 0)} Gi)")
    for d in yaml.safe_load_all(out):
        if d and d.get("kind") == "ClusterQueue":
            res = {r["name"]: r["nominalQuota"] for r in d["spec"]["resourceGroups"][0]["flavors"][0]["resources"]}
            h = win["batch"]
            check(cpu(res["cpu"]) == cpu(h["requests.cpu"]) and gi(res["memory"]) == gi(h["limits.memory"]),
                  "training window: spark-cq must equal batch-budget")
except (OSError, subprocess.CalledProcessError) as e:
    print(f"(training window not checked: {e})")

# ---- every GPU container in the LLM stack: memory request = limit (the scheduler is the pool's guard)
for root_dir, _dirs, files in os.walk(os.path.join(LAB, "manifests", "root-llms")):
    for fn in files:
        if not fn.endswith(".yaml") or fn == "kustomization.yaml":
            continue
        path = os.path.join(root_dir, fn)
        for d in load_all(path):
            spec = d.get("spec") or {}
            pod = (spec.get("template") or {}).get("spec") or spec.get("predictor") or spec
            for c in (pod.get("containers") or []) + ([pod["model"]] if isinstance(pod.get("model"), dict) else []):
                r = c.get("resources") or {}
                if "nvidia.com/gpu" not in (r.get("limits") or {}):
                    continue
                req, lim = (r.get("requests") or {}).get("memory"), (r.get("limits") or {}).get("memory")
                check(req is not None and lim is not None and gi(req) == gi(lim),
                      f"{os.path.relpath(path, LAB)} {c.get('name', '?')}: GPU container needs memory request = limit (got {req} / {lim})")

# ---- IPs: vCluster APIs and the LLM gateway (Traefik on the root) inside the MetalLB pool, all distinct
ips = {}
for name in VCLUSTERS:
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
ips["LLM gateway"] = traefik["service"]["annotations"]["metallb.io/loadBalancerIPs"]
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


for name in VCLUSTERS:
    inner(os.path.join(LAB, "manifests", name, "10-tenancy", "quotas.yaml"), f"vc-{name}")
check(budget["vc-dev-lab"] == budget["vc-dev-llms"], "dev-lab and dev-llms must have identical budgets")

# ---- report
rows = [("Spark total", SPARK["cpu"], SPARK["memory_gi"], SPARK["gpu"]),
        ("kubelet resv.", RESERVED["cpu"], RESERVED["memory_gi"], 0),
        ("allocatable", ALLOC["cpu"], ALLOC["memory_gi"], ALLOC["gpu"])]
for name in VCLUSTERS:
    b = budget[f"vc-{name}"]
    rows.append((f"vc-{name}", b["cpu"], b["memory_gi"], b["gpu"]))
rows.append(("root keeps", ROOT["cpu"], ROOT["memory_gi"], ROOT["gpu"]))
rows.append(("  platform", PLATFORM["cpu"], PLATFORM["memory_gi"], PLATFORM["gpu"]))
for ns in ("llm-serving", "batch"):
    q = llm.get(ns, {"cpu": 0, "memory_gi": 0, "gpu": 0})
    rows.append((f"  {ns}", q["cpu"], q["memory_gi"], q["gpu"]))
pool_note = "  (llm-serving and batch share one memory pool; batch's memory is part of serving's)"
print(f"{'':14} {'CPU':>6} {'mem GiB':>8} {'slices':>7}")
for name, c, m, g in rows:
    print(f"{name:14} {c:6.1f} {m:8.1f} {g:7d}")
print(pool_note)
model_gi = llm.get("llm-serving", {}).get("memory_gi", 0)
print(f"model memory in llm-serving: {model_gi:.0f} GiB = {model_gi / SPARK['memory_gi']:.2f} of the GPU → keep the sum of --gpu-memory-utilization ≲ 0.72 (headroom)")
print("LoadBalancer IPs:", ", ".join(f"{k} {v}" for k, v in ips.items()), f"(pool {lo}-{hi})")
if errors:
    print("\n".join("FAIL: " + e for e in errors))
    sys.exit(1)
print("budget check OK")
