#!/usr/bin/env python3
"""slice-ledger: a minimal Kubernetes controller with no dependencies (Chapter 06).

It does what every controller does, with nothing hidden behind a library:
  1. LIST pods            → build a local cache + remember resourceVersion
  2. WATCH from that RV   → apply ADDED/MODIFIED/DELETED events to the cache
  3. on 410 Gone          → cache is too old: re-LIST (the informer "relist")
  4. reconcile()          → desired state = one ConfigMap listing who holds GPU
                            slices; write it only if it differs (idempotent)
  5. periodic resync      → reconcile even without events (drift protection)

Runs on the ROOT cluster (namespace platform-tools), so it sees every GPU pod
on the Spark: the platform's own and the ones the two vClusters synced down.
A synced pod carries vCluster's annotations with its original name, so the
ledger shows both: "vc-llms/vllm-…-x-llm-serving-x-llms (llms: llm-serving/vllm-…)".

Runs in-cluster with its ServiceAccount token, or locally with
API_SERVER=https://192.168.0.100:6443 TOKEN=… CA_FILE=… .
"""
import json
import os
import ssl
import time
import urllib.error
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
API = os.environ.get("API_SERVER", "https://kubernetes.default.svc")
TOKEN = os.environ.get("TOKEN") or open(f"{SA}/token").read().strip()
CTX = ssl.create_default_context(cafile=os.environ.get("CA_FILE", f"{SA}/ca.crt"))
NS = os.environ.get("LEDGER_NAMESPACE", "platform-tools")
CM = os.environ.get("LEDGER_NAME", "gpu-slice-ledger")
RESYNC = int(os.environ.get("RESYNC_SECONDS", "60"))
CAPACITY = int(os.environ.get("SLICES_PER_NODE", "15"))

cache = {}          # "ns/name" -> pod summary
last_written = None


def call(method, path, body=None, stream=False, timeout=30):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    resp = urllib.request.urlopen(req, context=CTX, timeout=timeout)
    return resp if stream else json.loads(resp.read() or b"{}")


def gpu_slices(pod):
    return sum(int(c.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", 0))
               for c in pod["spec"].get("containers", []))


def summarize(pod):
    ann = pod["metadata"].get("annotations") or {}
    virtual = None
    if "vcluster.loft.sh/object-name" in ann:             # synced from a vCluster
        vc = (pod["metadata"].get("labels") or {}).get("vcluster.loft.sh/managed-by", "vcluster")
        virtual = f'{vc}: {ann.get("vcluster.loft.sh/object-namespace", "?")}/{ann["vcluster.loft.sh/object-name"]}'
    return {"node": pod["spec"].get("nodeName") or "<unscheduled>", "phase": pod["status"].get("phase"),
            "slices": gpu_slices(pod), "namespace": pod["metadata"]["namespace"], "virtual": virtual}


def apply_event(etype, pod):
    key = f'{pod["metadata"]["namespace"]}/{pod["metadata"]["name"]}'
    if etype == "DELETED" or pod["status"].get("phase") in ("Succeeded", "Failed") or gpu_slices(pod) == 0:
        cache.pop(key, None)
    else:
        cache[key] = summarize(pod)


def relist():
    pods = call("GET", "/api/v1/pods")
    cache.clear()
    for p in pods["items"]:
        apply_event("ADDED", p)
    rv = pods["metadata"]["resourceVersion"]
    print(f"relist: {len(cache)} GPU pods, resourceVersion={rv}", flush=True)
    return rv


def reconcile():
    global last_written
    nodes, by_ns = {}, {}
    for key, s in sorted(cache.items()):
        n = nodes.setdefault(s["node"], {"used": 0, "capacity": CAPACITY, "holders": []})
        n["used"] += s["slices"]
        who = f'{key} ({s["virtual"]})' if s["virtual"] else key
        n["holders"].append(f'{who} [{s["slices"]}, {s["phase"]}]')
        by_ns[s["namespace"]] = by_ns.get(s["namespace"], 0) + s["slices"]
    desired = {k: json.dumps(v, indent=1) for k, v in nodes.items()} or {"<none>": "no GPU pods"}
    if by_ns:                                    # vc-dev-lab / vc-llms vs their root quotas (2 / 8)
        desired["by-namespace"] = json.dumps(by_ns, indent=1, sort_keys=True)
    if desired == last_written:
        return                                   # idempotent: nothing to do
    body = {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": CM, "namespace": NS, "labels": {"app": "slice-ledger"}},
            "data": {k.replace("<", "").replace(">", ""): v for k, v in desired.items()}}
    try:
        call("PUT", f"/api/v1/namespaces/{NS}/configmaps/{CM}", body)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        call("POST", f"/api/v1/namespaces/{NS}/configmaps", body)
    last_written = desired
    print("reconciled: " + ", ".join(f'{n}={v["used"]}/{v["capacity"]}' for n, v in nodes.items()), flush=True)


def main():
    rv = relist()
    reconcile()
    next_resync = time.time() + RESYNC
    while True:
        try:
            timeout = max(1, int(next_resync - time.time()))
            stream = call("GET", f"/api/v1/pods?watch=1&allowWatchBookmarks=true&resourceVersion={rv}"
                                 f"&timeoutSeconds={timeout}", stream=True, timeout=timeout + 10)
            for line in stream:
                ev = json.loads(line)
                if ev["type"] == "ERROR":
                    if ev["object"].get("code") == 410:      # Gone: our RV was compacted away
                        print("watch: 410 Gone → relist", flush=True)
                        rv = relist()
                        break
                    raise RuntimeError(ev["object"])
                rv = ev["object"]["metadata"]["resourceVersion"]
                if ev["type"] != "BOOKMARK":
                    apply_event(ev["type"], ev["object"])
                    reconcile()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"watch error: {e}; retrying in 2s", flush=True)
            time.sleep(2)
        if time.time() >= next_resync:
            reconcile()
            next_resync = time.time() + RESYNC


if __name__ == "__main__":
    main()
