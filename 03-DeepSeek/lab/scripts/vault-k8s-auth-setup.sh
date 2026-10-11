#!/usr/bin/env bash
# One-time: let pods in llm-serving (root cluster) authenticate to the 01-Ansible Vault on
# vault01 with their ServiceAccount (Kubernetes auth method) and read kv/spark-lab/hf and
# kv/spark-lab/deepseek/* (Chapter 15). Run on the MacBook from 03-DeepSeek/lab with an admin
# token exported:
#   export VAULT_ADDR=https://192.168.0.211:8200 VAULT_CACERT="../../01-Ansible/lab/.cache/vault-ca.crt" VAULT_TOKEN=…
set -euo pipefail
NS=llm-serving
CTX=${ROOT_CTX:-spark-root}
kubectl() { command kubectl --context "$CTX" "$@"; }
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
path "kv/data/spark-lab/hf"            { capabilities = ["read"] }
path "kv/data/spark-lab/deepseek/*"     { capabilities = ["read"] }
path "kv/metadata/spark-lab/deepseek/*" { capabilities = ["list", "read"] }
HCL
vault write auth/kubernetes/role/deepseek-serving bound_service_account_names=vault-sync \
  bound_service_account_namespaces="$NS" token_policies=deepseek-serving token_ttl=10m
echo "✓ Vault Kubernetes auth ready. Store secrets with e.g.:"
echo "  vault kv put kv/spark-lab/hf token=hf_xxx"
echo "  vault kv put kv/spark-lab/deepseek/litellm master_key=sk-\$(openssl rand -hex 16)"
echo "  vault kv put kv/spark-lab/deepseek/webui secret_key=\$(openssl rand -hex 32)"
