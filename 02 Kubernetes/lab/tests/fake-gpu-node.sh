#!/usr/bin/env bash
# Make any CPU-only node (kind, a VM, CI) look like a DGX Spark to the scheduler:
# label it like GPU Feature Discovery would and advertise 15 nvidia.com/gpu
# (the lab's time-slice count) through the node status subresource. Pods still
# can't *use* a GPU — but the vCluster budgets, tenant quotas, admission, Kueue
# gang scheduling and Pending drills all work.
#   tests/fake-gpu-node.sh [node] [slices]      (against the ROOT cluster)
set -euo pipefail
ctx=(--context "${ROOT_CTX:-spark-root}")
node=${1:-$(kubectl "${ctx[@]}" get nodes -o jsonpath='{.items[0].metadata.name}')}
n=${2:-15}
kubectl "${ctx[@]}" label node "$node" nvidia.com/gpu.product=GB10 nvidia.com/gpu.count=1 \
  nvidia.com/gpu.compute.major=12 nvidia.com/gpu.compute.minor=1 --overwrite
kubectl "${ctx[@]}" patch node "$node" --subresource=status --type=merge \
  -p "{\"status\":{\"capacity\":{\"nvidia.com/gpu\":\"$n\"},\"allocatable\":{\"nvidia.com/gpu\":\"$n\"}}}"
kubectl "${ctx[@]}" get node "$node" -o jsonpath='{.metadata.name}: allocatable nvidia.com/gpu={.status.allocatable.nvidia\.com/gpu}{"\n"}'
# On the Spark the GPU Operator creates RuntimeClass "nvidia"; kind has none.
# GPU pod specs (runtimeClassName: nvidia) need it to exist on the root.
kubectl "${ctx[@]}" get runtimeclass nvidia >/dev/null 2>&1 || kubectl "${ctx[@]}" apply -f - <<'YAML'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: {name: nvidia}
handler: nvidia
YAML
