#!/usr/bin/env bash
# Create a client-certificate user through the CSR API (Volume 02).
#   scripts/make-user.sh alice team-alpha   → .cache/alice.kubeconfig
# The certificate's O= becomes the Kubernetes group that RBAC binds.
source "$(dirname "$0")/lib.sh"
user=${1:?user}; group=${2:?group}; out="$LAB_DIR/.cache"; mkdir -p "$out"
openssl req -new -newkey ed25519 -nodes -keyout "$out/$user.key" -subj "/CN=$user/O=$group" -out "$out/$user.csr" 2>/dev/null
k delete csr "$user" --ignore-not-found >/dev/null
cat <<YAML | k apply -f -
apiVersion: certificates.k8s.io/v1
kind: CertificateSigningRequest
metadata: {name: $user}
spec:
  request: $(base64 -w0 < "$out/$user.csr")
  signerName: kubernetes.io/kube-apiserver-client
  expirationSeconds: 604800
  usages: [client auth]
YAML
k certificate approve "$user"
k get csr "$user" -o jsonpath='{.status.certificate}' | base64 -d > "$out/$user.crt"
server=$(k config view --minify -o jsonpath='{.clusters[0].cluster.server}')
k config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d > "$out/ca.crt"
KUBECONFIG="$out/$user.kubeconfig" kubectl config set-cluster lab --server="$server" --certificate-authority="$out/ca.crt" --embed-certs >/dev/null
KUBECONFIG="$out/$user.kubeconfig" kubectl config set-credentials "$user" --client-certificate="$out/$user.crt" --client-key="$out/$user.key" --embed-certs >/dev/null
KUBECONFIG="$out/$user.kubeconfig" kubectl config set-context "$user" --cluster=lab --user="$user" >/dev/null
KUBECONFIG="$out/$user.kubeconfig" kubectl config use-context "$user" >/dev/null
ok "$out/$user.kubeconfig  (CN=$user, O=$group, valid 7 days)"
echo "try: KUBECONFIG=$out/$user.kubeconfig kubectl auth whoami"
