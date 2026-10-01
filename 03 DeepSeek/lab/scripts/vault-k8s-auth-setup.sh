#!/usr/bin/env bash
# One-time: let pods in llm-serving authenticate to the 01 Ansible Vault with
# their ServiceAccount (Kubernetes auth method) and read kv/spark-lab/deepseek/* (Volume 32).
# Run on the control node with VAULT_ADDR, VAULT_CACERT and an admin VAULT_TOKEN exported
# (01 Ansible: .cache/spark-lab-ca.crt and .cache/vault-init.json).
set -euo pipefail
NS=llm-serving
kubectl -n "$NS" create serviceaccount vault-token-reviewer --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding vault-token-reviewer --clusterrole=system:auth-delegator \
  --serviceaccount="$NS:vault-token-reviewer" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" apply -f - <<'YAML'
apiVersion: v1
kind: Secret
metadata:
  name: vault-token-reviewer
  annotations: {kubernetes.io/service-account.name: vault-token-reviewer}
type: kubernetes.io/service-account-token
YAML
sleep 2
REVIEWER_JWT=$(kubectl -n "$NS" get secret vault-token-reviewer -o jsonpath='{.data.token}' | base64 -d)
K8S_CA=$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d)
K8S_HOST=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
vault auth list -format=json | jq -e '."kubernetes/"' >/dev/null || vault auth enable kubernetes
vault write auth/kubernetes/config kubernetes_host="$K8S_HOST" kubernetes_ca_cert="$K8S_CA" token_reviewer_jwt="$REVIEWER_JWT"
vault policy write deepseek-serving - <<'HCL'
path "kv/data/spark-lab/deepseek/*"     { capabilities = ["read"] }
path "kv/metadata/spark-lab/deepseek/*" { capabilities = ["list", "read"] }
HCL
vault write auth/kubernetes/role/deepseek-serving bound_service_account_names=vault-sync \
  bound_service_account_namespaces="$NS" token_policies=deepseek-serving token_ttl=10m
echo "✓ Vault Kubernetes auth ready. Store secrets with e.g.:"
echo "  vault kv put kv/spark-lab/deepseek/hf token=hf_xxx"
echo "  vault kv put kv/spark-lab/deepseek/litellm master_key=sk-\$(openssl rand -hex 16)"
