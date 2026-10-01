#!/usr/bin/env bash
# Make any CPU-only node (kind, a VM, CI) look like a DGX Spark to the scheduler:
# label it like GPU Feature Discovery would and advertise 4 nvidia.com/gpu
# through the node status subresource. Pods still can't *use* a GPU — but
# quotas, admission, Kueue gang scheduling and Pending/Insufficient drills work.
#   tests/fake-gpu-node.sh [node] [slices]
set -euo pipefail
node=${1:-$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')}
n=${2:-4}
kubectl label node "$node" nvidia.com/gpu.product=GB10 nvidia.com/gpu.count=1 \
  nvidia.com/gpu.compute.major=12 nvidia.com/gpu.compute.minor=1 --overwrite
kubectl patch node "$node" --subresource=status --type=merge \
  -p "{\"status\":{\"capacity\":{\"nvidia.com/gpu\":\"$n\"},\"allocatable\":{\"nvidia.com/gpu\":\"$n\"}}}"
kubectl get node "$node" -o jsonpath='{.metadata.name}: allocatable nvidia.com/gpu={.status.allocatable.nvidia\.com/gpu}{"\n"}'
# k3s creates RuntimeClass "nvidia" automatically; kind does not. Admission of
# GPU pod specs (runtimeClassName: nvidia) needs it to exist.
kubectl get runtimeclass nvidia >/dev/null 2>&1 || kubectl apply -f - <<'YAML'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: {name: nvidia}
handler: nvidia
YAML
