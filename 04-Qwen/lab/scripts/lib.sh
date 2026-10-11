# shellcheck shell=bash
# shellcheck disable=SC2034  # variables are used by the scripts that source this file
# 04-Qwen helpers: reuse the 02-Kubernetes lab helpers (KUBECONFIG discovery,
# contexts spark-root / dev-llms, PASS/FAIL printing, k/kdl wrappers), then
# point QW_DIR at this lab and load its pinned versions.
QW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$QW_DIR/../../02-Kubernetes/lab/scripts/lib.sh"
K8S_LAB="$LAB_DIR"
DS_LAB="$QW_DIR/../../03-DeepSeek/lab"      # the application stack 04 reuses (apps, RAG, telemetry)
# shellcheck source=../versions.env
source "$QW_DIR/versions.env"
NS=llm-serving
UTIL_BUDGET="${UTIL_BUDGET:-0.72}"          # Σ --gpu-memory-utilization of engines running at once

model_field() {   # model_field <name> <field>  (from models.yaml; empty if unknown)
  python3 - "$QW_DIR/models.yaml" "$1" "$2" <<'PY'
import sys, yaml
m = {x["name"]: x for x in yaml.safe_load(open(sys.argv[1]))["models"]}
print(m[sys.argv[2]].get(sys.argv[3], "") if sys.argv[2] in m else "", end="")
PY
}

side_render() {   # side_render <name>: the catalog overlay renamed to vllm-<name> (runs NEXT TO svc/vllm)
  "$KUBECTL" kustomize "$QW_DIR/k8s/models/$1" | python3 -c '
import sys, yaml
side = "vllm-" + sys.argv[1]
docs = [d for d in yaml.safe_load_all(sys.stdin) if d]
for d in docs:
    d["metadata"]["name"] = side
    d["metadata"].setdefault("labels", {})["app"] = side
    spec = d["spec"]
    if d["kind"] == "Deployment":
        spec["selector"]["matchLabels"]["app"] = side
        spec["template"]["metadata"]["labels"]["app"] = side
    elif d["kind"] == "Service":
        spec["selector"]["app"] = side
print(yaml.safe_dump_all(docs, sort_keys=False), end="")
' "$1"
}

running_util() {  # Σ --gpu-memory-utilization over the Deployments in llm-serving with replicas > 0 (vLLM, BGE-M3 …; skip $1)
  k -n "$NS" get deploy -o json | python3 -c '
import json, re, sys
skip, total = sys.argv[1], 0.0
for d in json.load(sys.stdin)["items"]:
    name = d["metadata"]["name"]
    if name == skip or not d["spec"].get("replicas"):
        continue
    for c in d["spec"]["template"]["spec"]["containers"]:
        for a in c.get("args") or []:
            m = re.match(r"--gpu-memory-utilization=([0-9.]+)", a)
            total += float(m[1]) if m else 0
print(f"{total:.2f}")
' "${1:-}"
}
