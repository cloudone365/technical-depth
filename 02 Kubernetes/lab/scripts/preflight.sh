#!/usr/bin/env bash
# Run ON the Spark before the 02 lab (Volume 15 step 0). Read-only.
source "$(dirname "$0")/lib.sh"
set +e

[[ "$(uname -m)" == aarch64 ]] && ok "arch aarch64" || bad "arch $(uname -m) (expected aarch64)"
cores=$(nproc); [[ "$cores" -eq 20 ]] && ok "20 CPU cores (10 X925 + 10 A725)" || warn "nproc=$cores (GB10 has 20)"
mem_gib=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)
[[ "$mem_gib" -ge 110 ]] && ok "MemTotal ${mem_gib} GiB (unified CPU+GPU pool)" || warn "MemTotal ${mem_gib} GiB"
[[ "$(stat -fc %T /sys/fs/cgroup)" == cgroup2fs ]] && ok "cgroup v2" || bad "cgroup v1 — kubelet QoS maths in Vol 12 assume v2"

if command -v nvidia-smi >/dev/null; then
  name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null)
  cc=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null)
  [[ "$name" == *GB10* ]] && ok "GPU $name, compute capability $cc" || bad "nvidia-smi sees '$name'"
else bad "nvidia-smi missing"; fi

systemctl is-active --quiet k3s && ok "k3s service active" || bad "k3s not active (run 01 Ansible playbooks/05-k3s.yml)"
k get --raw /readyz >/dev/null 2>&1 && ok "API server /readyz (KUBECONFIG=$KUBECONFIG)" || bad "cannot reach API server"
node=$(k get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
gpus=$(k get node "$node" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null)
[[ "${gpus:-0}" -ge 1 ]] && ok "$node allocatable nvidia.com/gpu=$gpus (time-slices)" || bad "no nvidia.com/gpu allocatable (run 01 Ansible playbooks/06-gpu-operator.yml)"
k get runtimeclass nvidia >/dev/null 2>&1 && ok "RuntimeClass nvidia present" || warn "RuntimeClass nvidia missing"

free_gib=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
[[ "$free_gib" -ge 300 ]] && ok "${free_gib} GiB free on / (models + images)" || warn "only ${free_gib} GiB free on /"
[[ -d /data/k8s ]] && ok "/data/k8s exists" || warn "/data/k8s missing: sudo mkdir -p /data/k8s"
[[ -f /etc/rancher/k3s/config.yaml.d/20-k8s-lab.yaml ]] && ok "k3s lab drop-in installed" || info "k3s lab drop-in not installed yet (scripts/install-addons.sh k3s-config)"
summary
