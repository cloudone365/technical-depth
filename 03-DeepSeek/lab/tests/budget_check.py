#!/usr/bin/env python3
"""Static budget check of every 03 pod template against the root's LLM budgets.

No cluster needed (the server-side counterpart is API=1 in run-local-checks.sh).
Renders every kustomization / manifest of the lab and checks each container
that asks for a GPU slice:

  * memory request = limit — the scheduler guards the shared ~90 GiB LLM pool
    (02-Kubernetes lab, root-llms/10-tenancy/quotas.yaml)
  * llm-serving pods fit serving-budget (everyday): ≤ 8 CPU · ≤ 90 Gi · ≤ 6 slices
  * batch pods fit the TRAINING WINDOW (root-llms/training-window):
    ≤ 8 CPU requests · ≤ 80 Gi · exactly 1 slice per pod, and the file says so
    in its header comment
  * the model catalog keeps Σ util of the sets that run together ≲ 0.72

The numbers are read from the 02 manifests, not copied here.
"""
import glob
import os
import re
import subprocess
import sys

import yaml

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K8S = os.path.join(LAB, "..", "..", "02-Kubernetes", "lab", "manifests", "root-llms")
KUBECTL = os.environ.get("KUBECTL", "kubectl")
errors = []


def gi(q):
    q = str(q)
    for suf, mul in (("Ti", 1024), ("Gi", 1), ("Mi", 1 / 1024)):
        if q.endswith(suf):
            return float(q[:-2]) * mul
    return float(q) / 2**30


def cpu(q):
    q = str(q)
    return float(q[:-1]) / 1000 if q.endswith("m") else float(q)


def render(path):
    if os.path.isdir(path):
        out = subprocess.run([KUBECTL, "kustomize", path], capture_output=True, text=True, check=True).stdout
    else:
        out = open(path).read()
    return [d for d in yaml.safe_load_all(out) if d]


def quotas(path):
    return {d["metadata"]["name"]: d["spec"]["hard"] for d in render(path) if d.get("kind") == "ResourceQuota"}


everyday = quotas(os.path.join(K8S, "10-tenancy"))
window = quotas(os.path.join(K8S, "training-window"))
SERVING = everyday["serving-budget"]
WINDOW = window["batch-budget"]


def pod_specs(doc):
    k, spec = doc.get("kind"), doc.get("spec") or {}
    if k in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
        yield spec["template"]["spec"]
    elif k == "CronJob":
        yield spec["jobTemplate"]["spec"]["template"]["spec"]
    elif k == "LeaderWorkerSet":
        lwt = spec["leaderWorkerTemplate"]
        for t in ("leaderTemplate", "workerTemplate"):
            if t in lwt:
                yield lwt[t]["spec"]
    elif k == "InferenceService":
        p = spec.get("predictor", {})
        yield {"containers": [p["model"]] if "model" in p else p.get("containers", [])}


def check_pod(where, ns, pod, header):
    gpu_pod = False
    req_cpu = mem = gpus = 0.0
    for c in pod.get("containers", []) + pod.get("initContainers", []):
        r = c.get("resources") or {}
        lim, req = r.get("limits") or {}, r.get("requests") or {}
        req_cpu += cpu(req.get("cpu", 0))
        mem += gi(lim.get("memory", req.get("memory", 0)))
        if "nvidia.com/gpu" not in lim:
            continue
        gpu_pod = True
        gpus += int(lim["nvidia.com/gpu"])
        if req.get("memory") is None or lim.get("memory") is None or gi(req["memory"]) != gi(lim["memory"]):
            errors.append(f"{where}: GPU container {c.get('name')} needs memory request = limit "
                          f"(got {req.get('memory')} / {lim.get('memory')})")
    if not gpu_pod:
        return None
    if ns == "llm-serving":
        if req_cpu > cpu(SERVING["requests.cpu"]) or mem > gi(SERVING["limits.memory"]) \
                or gpus > int(SERVING["requests.nvidia.com/gpu"]):
            errors.append(f"{where}: {req_cpu:g} CPU · {mem:g} Gi · {gpus:g} slices exceeds serving-budget")
    elif ns == "batch":
        if req_cpu > cpu(WINDOW["requests.cpu"]) or mem > gi(WINDOW["limits.memory"]) or gpus != 1:
            errors.append(f"{where}: {req_cpu:g} CPU · {mem:g} Gi · {gpus:g} slices does not fit the training "
                          f"window ({WINDOW['requests.cpu']} CPU · {WINDOW['limits.memory']} · 1 slice per pod)")
        if "root-llms/training-window" not in header:
            errors.append(f"{where}: training job without the 'needs the training window "
                          f"(02-Kubernetes manifests/root-llms/training-window)' header comment")
    return ns, req_cpu, mem, gpus


rows = []
paths = sorted(glob.glob(os.path.join(LAB, "k8s", "*", "*.yaml")) + glob.glob(os.path.join(LAB, "k8s", "*", "*", "kustomization.yaml")))
for p in paths:
    if os.path.basename(p) == "kustomization.yaml":
        src = os.path.dirname(p)
    elif os.path.exists(os.path.join(os.path.dirname(p), "kustomization.yaml")):
        continue                                   # rendered through its kustomization
    else:
        src = p
    header = open(p).read().split("apiVersion", 1)[0]
    rel = os.path.relpath(src, LAB)
    for doc in render(src):
        ns = (doc.get("metadata") or {}).get("namespace", "default")
        for pod in pod_specs(doc):
            r = check_pod(f"{rel} {doc['kind']}/{doc['metadata']['name']}", ns, pod, header)
            if r:
                rows.append((rel, doc["metadata"]["name"], *r))

# ---- the catalog: the sets models.yaml documents as "fit together" stay under the pool rule
cat = {m["name"]: m for m in yaml.safe_load(open(os.path.join(LAB, "models.yaml")))["models"]}
EMBED = 0.06
nsets = 0
text = open(os.path.join(LAB, "models.yaml")).read()
sets = text.split("# Sets that fit together", 1)[-1].split("\nmodels:", 1)[0]
if sets == text:
    errors.append("models.yaml: the '# Sets that fit together' table is missing")
for line in sets.splitlines()[1:]:
    m = re.match(r"#\s+(.+?)\s{3,}(0\.\d\d)\s{3,}", line)
    if not m:
        continue
    names = [n.strip().removesuffix(" alone") for n in re.sub(r"\(.*?\)", "", m.group(1)).split("+")]
    nsets += 1
    if not any(n in cat for n in names):
        errors.append(f"models.yaml: set '{m.group(1)}' names no catalog model")
    util = sum(cat[n]["util"] for n in names if n in cat) + (EMBED if "bge-m3" in names else 0)
    if abs(util - float(m.group(2))) > 1e-6:
        errors.append(f"models.yaml: set '{m.group(1)}' is documented as {m.group(2)} but sums to {util:.2f}")
    if util > 0.72 + 1e-6:
        errors.append(f"models.yaml: set '{m.group(1)}' has Σ util {util:.2f} > 0.72")

print(f"{'template':44} {'object':22} {'namespace':12} {'CPU':>5} {'mem Gi':>7} {'slices':>6}")
for rel, name, ns, c, m, g in rows:
    print(f"{rel:44} {name:22} {ns:12} {c:5g} {m:7g} {g:6g}")
print(f"serving-budget {SERVING['requests.cpu']} CPU · {SERVING['limits.memory']} · {SERVING['requests.nvidia.com/gpu']} slices; "
      f"training window batch-budget {WINDOW['requests.cpu']} CPU · {WINDOW['limits.memory']}")
for e in errors:
    print("FAIL", e)
print(f"{len(rows)} GPU pod templates and {nsets} catalog sets checked, {len(errors)} problems")
if nsets == 0:
    errors.append("models.yaml: no sets parsed")
sys.exit(1 if errors else 0)
