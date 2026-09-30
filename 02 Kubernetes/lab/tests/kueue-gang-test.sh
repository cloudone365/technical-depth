#!/usr/bin/env bash
# Gang admission check (Volume 05): gang-a (2 slices) is admitted whole,
# gang-b (2 more slices) waits whole because spark-cq allows 2.
set -euo pipefail
cd "$(dirname "$0")/.."
kubectl delete -f manifests/20-scheduling/gang-demo-jobs.yaml --ignore-not-found --wait=true >/dev/null
kubectl apply -f manifests/20-scheduling/gang-demo-jobs.yaml >/dev/null
for _ in $(seq 60); do
  a=$(kubectl -n batch get workloads -o json | python3 -c '
import json, sys
out = []
for w in json.load(sys.stdin)["items"]:
    owner = w["metadata"]["ownerReferences"][0]["name"]
    conds = w.get("status", {}).get("conditions", [])
    admitted = any(c["type"] == "Admitted" and c["status"] == "True" for c in conds)
    out.append(f"{owner}={admitted}")
print(" ".join(out))')
  [[ "$a" == *"gang-a=True"* || "$a" == *"gang-b=True"* ]] && break; sleep 2
done
echo "workloads: $a"
admitted=$(grep -o '=True' <<<"$a" | wc -l)
[[ $admitted -eq 1 ]] || { echo "FAIL: expected exactly 1 admitted gang, got $admitted"; exit 1; }
susp=$(kubectl -n batch get jobs -o jsonpath='{range .items[*]}{.metadata.name}={.spec.suspend} {end}')
echo "jobs: $susp"
grep -q '=true' <<<"$susp" || { echo "FAIL: second gang not held"; exit 1; }
echo "PASS: one gang admitted whole, the other held whole (no partial start)"
kubectl delete -f manifests/20-scheduling/gang-demo-jobs.yaml --wait=false >/dev/null
