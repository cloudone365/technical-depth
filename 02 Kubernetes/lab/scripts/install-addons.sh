#!/usr/bin/env bash
# Install the 02 lab add-ons (Volume 15, 27; details in 09, 11, 16, 05, 21, 23).
# Prerequisite: 01 Ansible playbooks/05-kubernetes.yml + 06-gpu-operator.yml
# (kubeadm root cluster, Cilium, MetalLB, GPU Operator) and helm on this machine.
#
#   On the ROOT cluster (context spark-root):
#     scripts/install-addons.sh storage         # local-path-provisioner at /data/k8s + local-nvme classes
#     scripts/install-addons.sh metrics-server  # kubectl top / HPAs (k3s bundled it, kubeadm doesn't)
#     scripts/install-addons.sh kps             # Prometheus/Grafana/Alertmanager
#     scripts/install-addons.sh vclusters       # vCluster dev-lab + llms, budgets, merged kubeconfig
#     scripts/install-addons.sh argocd          # GitOps (production-mlops.md)
#   Inside vCluster #2 (context llms):
#     scripts/install-addons.sh traefik         # Gateway API CRDs + Traefik (llms API gateway, 192.168.0.115)
#     scripts/install-addons.sh kueue           # batch admission
#     scripts/install-addons.sh keda            # event-driven autoscaling
#     scripts/install-addons.sh kserve          # cert-manager + KServe (RawDeployment)
#
#   scripts/install-addons.sh all               # storage metrics-server kps vclusters traefik kueue keda
source "$(dirname "$0")/lib.sh"
CACHE="$LAB_DIR/.cache"; mkdir -p "$CACHE"
helm_repo() { "$HELM" repo add "$1" "$2" --force-update >/dev/null; "$HELM" repo update "$1" >/dev/null; }

case "${1:-all}" in
  # ------------------------------------------------------------------ root
  storage)
    kr apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCAL_PATH_VERSION}/deploy/local-path-storage.yaml"
    kr -n local-path-storage patch configmap local-path-config --type merge --patch-file "$LAB_DIR/addons/local-path-config-patch.yaml"
    kr apply -f "$LAB_DIR/addons/local-path-nvme.yaml"
    kr annotate storageclass local-path storageclass.kubernetes.io/is-default-class=true --overwrite
    kr -n local-path-storage rollout restart deploy/local-path-provisioner
    kr -n local-path-storage rollout status deploy/local-path-provisioner --timeout=3m
    ;;
  metrics-server)
    helm_repo metrics-server https://kubernetes-sigs.github.io/metrics-server/
    "$HELM" --kube-context "$ROOT_CTX" upgrade --install metrics-server metrics-server/metrics-server \
      --version "$METRICS_SERVER_CHART_VERSION" -n kube-system -f "$LAB_DIR/addons/metrics-server-values.yaml" --wait
    ;;
  kps)
    kr apply -k "$LAB_DIR/manifests/root/00-platform"             # observability namespace + PriorityClasses
    helm_repo prometheus-community https://prometheus-community.github.io/helm-charts
    "$HELM" --kube-context "$ROOT_CTX" upgrade --install kps prometheus-community/kube-prometheus-stack \
      --version "$KPS_CHART_VERSION" -n observability -f "$LAB_DIR/addons/kube-prometheus-stack-values.yaml" --wait --timeout 15m
    kr -n observability rollout status deploy/kps-grafana --timeout=10m
    ;;
  vclusters)
    kr apply -k "$LAB_DIR/manifests/root/00-platform"
    kr apply -k "$LAB_DIR/manifests/root/05-vclusters"             # namespaces, budgets, Cilium boundary
    helm_repo loft https://charts.loft.sh
    for name in "$DEV_CTX" "$LLM_CTX"; do
      info "vCluster $name in vc-$name (chart $VCLUSTER_VERSION)"
      "$HELM" --kube-context "$ROOT_CTX" upgrade --install "$name" loft/vcluster --version "$VCLUSTER_VERSION" \
        -n "vc-$name" -f "$LAB_DIR/vclusters/$name.yaml" --wait --timeout 10m
      "$LAB_DIR/scripts/merge-vcluster-kubeconfig.sh" "$name" "vc-$name"
    done
    kr -n vc-dev-lab describe resourcequota vcluster-budget | sed -n '1,20p'
    kr -n vc-llms describe resourcequota vcluster-budget | sed -n '1,20p'
    ;;
  argocd)
    kr create namespace argocd --dry-run=client -o yaml | kr apply -f -
    kr -n argocd apply --server-side -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
    kr -n argocd rollout status deploy/argocd-server --timeout=10m
    ;;
  # ------------------------------------------------------------------ inside llms
  traefik)
    kl apply -k "$LAB_DIR/manifests/llms/00-platform"              # ingress namespace + PriorityClasses
    kl apply --server-side -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
    helm_repo traefik https://traefik.github.io/charts
    "$HELM" --kube-context "$LLM_CTX" upgrade --install traefik-lab traefik/traefik \
      --version "$TRAEFIK_CHART_VERSION" -n ingress -f "$LAB_DIR/addons/traefik-values.yaml" --wait
    kl -n ingress get svc traefik-lab -o jsonpath='{.status.loadBalancer.ingress[0].ip}{"\n"}'
    ;;
  kueue)
    kl apply --server-side -f "https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml"
    kl -n kueue-system rollout status deploy/kueue-controller-manager --timeout=5m
    ;;
  keda)
    kl apply --server-side -f "https://github.com/kedacore/keda/releases/download/${KEDA_VERSION}/keda-${KEDA_VERSION#v}.yaml"
    kl -n keda rollout status deploy/keda-operator --timeout=5m
    ;;
  kserve)
    kl apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml"
    kl -n cert-manager rollout status deploy/cert-manager-webhook --timeout=5m
    kl apply --server-side -f "https://github.com/kserve/kserve/releases/download/${KSERVE_VERSION}/kserve.yaml"
    kl -n kserve rollout status deploy/kserve-controller-manager --timeout=5m
    kl apply --server-side -f "https://github.com/kserve/kserve/releases/download/${KSERVE_VERSION}/kserve-cluster-resources.yaml"
    kl -n kserve patch configmap inferenceservice-config --type merge \
      -p '{"data":{"deploy":"{\"defaultDeploymentMode\":\"RawDeployment\"}"}}'
    ;;
  all)
    for a in storage metrics-server kps vclusters traefik kueue keda; do "$0" "$a"; done
    ;;
  *) echo "unknown add-on: $1"; exit 2 ;;
esac
