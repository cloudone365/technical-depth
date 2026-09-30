#!/usr/bin/env bash
# Install the 02 lab add-ons (Volume 09, 15, 16, 05, 21, 23).
#   scripts/install-addons.sh k3s-config   # etcd, audit, secrets-encryption (run ON the Spark, sudo)
#   scripts/install-addons.sh storage      # local-nvme StorageClasses
#   scripts/install-addons.sh traefik      # Ingress + Gateway API
#   scripts/install-addons.sh kps          # Prometheus/Grafana/Alertmanager
#   scripts/install-addons.sh kueue        # batch admission
#   scripts/install-addons.sh keda         # event-driven autoscaling
#   scripts/install-addons.sh kserve       # cert-manager + KServe (RawDeployment)
#   scripts/install-addons.sh all          # storage traefik kps kueue keda
source "$(dirname "$0")/lib.sh"

wait_helmchart() {   # k3s helm-controller runs a helm-install-<name> Job per HelmChart
  local name=$1
  info "waiting for helm-install-$name"
  k -n kube-system wait --for=condition=complete "job/helm-install-$name" --timeout=15m
}

case "${1:-all}" in
  k3s-config)
    sudo install -D -m 0600 "$LAB_DIR/k3s/20-k8s-lab.yaml" /etc/rancher/k3s/config.yaml.d/20-k8s-lab.yaml
    sudo install -D -m 0600 "$LAB_DIR/k3s/audit-policy.yaml" /etc/rancher/k3s/audit-policy.yaml
    sudo mkdir -p /var/log/k3s /data/k8s
    info "restarting k3s (first start with cluster-init migrates SQLite → etcd; ~1 min)"
    sudo systemctl restart k3s
    until k get --raw /readyz >/dev/null 2>&1; do sleep 3; done
    sudo k3s etcd-snapshot list 2>/dev/null | head -3 || true
    ok "k3s restarted with embedded etcd + audit log"
    ;;
  storage)
    k apply -f "$LAB_DIR/addons/local-path-nvme.yaml"
    ;;
  traefik)
    k apply -f "$LAB_DIR/addons/traefik.yaml"; wait_helmchart traefik-lab
    k -n ingress rollout status deploy/traefik-lab --timeout=5m
    ;;
  kps)
    k apply -f "$LAB_DIR/addons/kube-prometheus-stack.yaml"; wait_helmchart kps
    k -n observability rollout status deploy/kps-grafana --timeout=10m
    ;;
  kueue)
    k apply --server-side -f "https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml"
    k -n kueue-system rollout status deploy/kueue-controller-manager --timeout=5m
    ;;
  keda)
    k apply --server-side -f "https://github.com/kedacore/keda/releases/download/${KEDA_VERSION}/keda-${KEDA_VERSION#v}.yaml"
    k -n keda rollout status deploy/keda-operator --timeout=5m
    ;;
  kserve)
    k apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml"
    k -n cert-manager rollout status deploy/cert-manager-webhook --timeout=5m
    k apply --server-side -f "https://github.com/kserve/kserve/releases/download/${KSERVE_VERSION}/kserve.yaml"
    k -n kserve rollout status deploy/kserve-controller-manager --timeout=5m
    k apply --server-side -f "https://github.com/kserve/kserve/releases/download/${KSERVE_VERSION}/kserve-cluster-resources.yaml"
    k -n kserve patch configmap inferenceservice-config --type merge \
      -p '{"data":{"deploy":"{\"defaultDeploymentMode\":\"RawDeployment\"}"}}'
    ;;
  all)
    for a in storage traefik kps kueue keda; do "$0" "$a"; done
    ;;
  *) echo "unknown add-on: $1"; exit 2 ;;
esac
