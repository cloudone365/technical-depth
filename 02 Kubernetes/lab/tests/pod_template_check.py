#!/usr/bin/env python3
"""Admission check for pod *templates* (Deployments, StatefulSets, Jobs, LWS …).

`kubectl apply --dry-run=server` on a Deployment does not run Pod Security or
pod-level admission policies — those only fire when the ReplicaSet creates the
Pod, i.e. at runtime. This script extracts every pod template from the given
manifests/kustomizations, wraps it in a Pod in the right namespace, and creates
it with a server-side dry run, so PSA / ValidatingAdmissionPolicy / quota
violations show up in CI instead of as a stuck ReplicaSet.

  python3 tests/pod_template_check.py manifests/llms/90-serving/vllm manifests/llms/50-workloads/qdrant-statefulset.yaml …
Exit 1 if any template would be rejected.

The lab is three API servers (Volume 27). The cluster is taken from the path:
manifests/root/… → spark-root, manifests/dev-lab/… → dev-lab, manifests/llms/… → llms
(override the context names with ROOT_CTX / DEV_CTX / LLM_CTX). Admission inside
a vCluster checks that vCluster's PSA/CEL/tenant quotas; the ROOT quota on its
vc-* namespace is applied only when the syncer creates the real pod, so it
is not part of a dry run.
"""
import json
import os
import subprocess
import sys

import yaml

KUBECTL = os.environ.get("KUBECTL", "kubectl")
CONTEXTS = {"root": os.environ.get("ROOT_CTX", "spark-root"),
            "dev-lab": os.environ.get("DEV_CTX", "dev-lab"),
            "llms": os.environ.get("LLM_CTX", "llms")}


def context_for(path):
    parts = os.path.normpath(path).split(os.sep)
    for key, ctx in CONTEXTS.items():
        if key in parts:
            return ctx
    return CONTEXTS["root"]


def render(path):
    if os.path.isdir(path):
        out = subprocess.run([KUBECTL, "kustomize", path], capture_output=True, text=True, check=True).stdout
    else:
        out = open(path).read()
    return [d for d in yaml.safe_load_all(out) if d]


def templates(doc):
    k, spec = doc.get("kind"), doc.get("spec", {})
    if "metadata" not in doc or k == "Kustomization":
        return
    ns = doc["metadata"].get("namespace", "default")
    name = doc["metadata"]["name"]
    if k in ("Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet"):
        tpl = spec["template"]
        if k == "StatefulSet":                     # volumeClaimTemplates become volumes at runtime
            tpl = json.loads(json.dumps(tpl))
            tpl["spec"].setdefault("volumes", []).extend(
                {"name": v["metadata"]["name"], "emptyDir": {}} for v in spec.get("volumeClaimTemplates", []))
        yield name, ns, tpl
    elif k == "CronJob":
        yield name, ns, spec["jobTemplate"]["spec"]["template"]
    elif k == "LeaderWorkerSet":
        lwt = spec["leaderWorkerTemplate"]
        if "leaderTemplate" in lwt:
            yield name + "-leader", ns, lwt["leaderTemplate"]
        yield name + "-worker", ns, lwt["workerTemplate"]


def main(paths):
    bad = 0
    n = 0
    for p in paths:
        for doc in render(p):
            for name, ns, tpl in templates(doc):
                n += 1
                pod = {"apiVersion": "v1", "kind": "Pod",
                       "metadata": {"name": f"tplcheck-{name}"[:63], "namespace": ns,
                                    "labels": tpl.get("metadata", {}).get("labels", {})},
                       "spec": tpl["spec"]}
                r = subprocess.run([KUBECTL, "--context", context_for(p), "create", "--dry-run=server", "-f", "-"],
                                   input=json.dumps(pod), capture_output=True, text=True)
                if r.returncode != 0 and "serviceaccount" in r.stderr and "not found" in r.stderr:
                    print(f"note     {doc['kind']}/{name}: ServiceAccount is created by the same apply (dry-run can't see it)")
                elif r.returncode != 0:
                    bad += 1
                    print(f"REJECTED {p} :: {doc['kind']}/{name} in {context_for(p)}/{ns}\n   {r.stderr.strip().splitlines()[-1][:400]}")
    print(f"{n} pod templates checked, {bad} rejected")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
