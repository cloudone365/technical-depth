#!/usr/bin/env bash
# Run ON the Spark before the 02 lab (Step 05 §4 Task 0). Read-only.
# Checks the host, the kubeadm root cluster built by 01 Ansible playbook 05/06,
# and — if they exist yet — the two vClusters (playbook 06b).
source "$(dirname "$0")/lib.sh"
set +e

echo "── host"
[[ "$(uname -m)" == aarch64 ]] && ok "arch aarch64" || bad "arch $(uname -m) (expected aarch64)"
cores=$(nproc); [[ "$cores" -eq 20 ]] && ok "20 CPU cores (10 X925 + 10 A725)" || warn "nproc=$cores (GB10 has 20)"
mem_gib=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)
[[ "$mem_gib" -ge 110 ]] && ok "MemTotal ${mem_gib} GiB (unified CPU+GPU pool)" || warn "MemTotal ${mem_gib} GiB"
[[ "$(stat -fc %T /sys/fs/cgroup)" == cgroup2fs ]] && ok "cgroup v2" || bad "cgroup v1 — kubelet QoS maths in Step 14 assume v2"
[[ "$(swapon --noheadings 2>/dev/null | wc -l)" -eq 0 ]] && ok "swap off" || bad "swap is on — kubelet won't start (01 Ansible kubeadm_cluster turns it off)"

if command -v nvidia-smi >/dev/null; then
  name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null)
  cc=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null)
  [[ "$name" == *GB10* ]] && ok "GPU $name, compute capability $cc" || bad "nvidia-smi sees '$name'"
else bad "nvidia-smi missing"; fi

echo "── root cluster (kubeadm)"
[[ -x /usr/local/bin/k3s ]] && bad "k3s is still installed — remove it (01 Ansible playbooks/99-reset-kubernetes.yml -e reset_remove_k3s=true)"
systemctl is-active --quiet containerd && ok "containerd active" || bad "containerd not active"
grep -Eq 'default_runtime_name\s*=\s*"nvidia"' /etc/containerd/config.toml 2>/dev/null \
  && ok "containerd default runtime: nvidia" || warn "containerd default runtime is not nvidia (runtimeClassName: nvidia then required)"
systemctl is-active --quiet kubelet && ok "kubelet active" || bad "kubelet not active (run 01 Ansible playbooks/05-kubernetes.yml)"
kubeadm version -o short 2>/dev/null | grep -q "${KUBERNETES_VERSION}" \
  && ok "kubeadm ${KUBERNETES_VERSION}" || warn "kubeadm $(kubeadm version -o short 2>/dev/null) ≠ versions.env ${KUBERNETES_VERSION}"
kr get --raw /readyz >/dev/null 2>&1 && ok "root API server /readyz (context $ROOT_CTX, KUBECONFIG=$KUBECONFIG)" || bad "cannot reach the root API server as context $ROOT_CTX"
node=$(kr get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
taints=$(kr get node "$node" -o jsonpath='{.spec.taints}' 2>/dev/null)
[[ "$taints" != *control-plane* ]] && ok "$node schedulable (no control-plane taint)" || bad "$node still has the control-plane taint"
kr -n kube-system get ds cilium -o jsonpath='{.status.numberReady}' 2>/dev/null | grep -q '^[1-9]' && ok "Cilium agent ready" || bad "Cilium not ready"
kr -n metallb-system get ipaddresspool lab-pool >/dev/null 2>&1 && ok "MetalLB pool lab-pool (192.168.0.110–119)" || warn "MetalLB pool missing (01 Ansible roles/metallb)"
gpus=$(kr get node "$node" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null)
[[ "${gpus:-0}" -eq 15 ]] && ok "$node allocatable nvidia.com/gpu=$gpus (time-slices)" \
  || { [[ "${gpus:-0}" -ge 1 ]] && warn "$node advertises $gpus slices (the vCluster quotas assume 15)" || bad "no nvidia.com/gpu allocatable (run 01 Ansible playbooks/06-gpu-operator.yml)"; }
kr get runtimeclass nvidia >/dev/null 2>&1 && ok "RuntimeClass nvidia present" || warn "RuntimeClass nvidia missing"

echo "── vClusters"
for ctx in "$DEV_CTX" "$LLM_CTX"; do
  if kc "$ctx" get --raw /readyz >/dev/null 2>&1; then ok "vCluster $ctx answers"
  else info "vCluster $ctx not reachable yet (01 Ansible playbooks/06b-vclusters.yml or scripts/install-addons.sh vclusters)"; fi
done

echo "── tools and disk"
command -v helm >/dev/null && ok "helm $(helm version --short 2>/dev/null)" || bad "helm missing — the add-ons are Helm charts (https://helm.sh/docs/intro/install/)"
command -v vcluster >/dev/null && ok "vcluster CLI $(vcluster --version 2>/dev/null | awk '{print $NF}')" || info "vcluster CLI not installed (optional: vcluster list / connect / snapshot)"
free_gib=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
[[ "$free_gib" -ge 300 ]] && ok "${free_gib} GiB free on / (models + images)" || warn "only ${free_gib} GiB free on /"
[[ -d /data/k8s ]] && ok "/data/k8s exists" || warn "/data/k8s missing: sudo mkdir -p /data/k8s"
yq --version 2>/dev/null | grep -q mikefarah && ok "yq (mikefarah) present" || warn "mikefarah yq missing (used in Steps 07/12/18): see versions.env YQ_VERSION"
summary
