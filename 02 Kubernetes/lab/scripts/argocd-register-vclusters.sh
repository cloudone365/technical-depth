#!/usr/bin/env bash
# Register the two vClusters as Argo CD destinations (production-mlops.md, Volume 27).
# Argo CD runs on the root; it reaches each vCluster API on its MetalLB IP with
# the admin credentials vCluster exported into Secret vc-<name>. The result is
# a declarative "cluster" Secret in argocd — the same thing `argocd cluster add`
# would create, without needing the argocd CLI.
#   scripts/argocd-register-vclusters.sh
source "$(dirname "$0")/lib.sh"
kr get ns argocd >/dev/null 2>&1 || { bad "Argo CD not installed: scripts/install-addons.sh argocd"; exit 1; }
for name in "$DEV_CTX" "$LLM_CTX"; do
  kcfg=$(kr -n "vc-$name" get secret "vc-$name" -o jsonpath='{.data.config}' | base64 -d)
  view() { "$KUBECTL" --kubeconfig <(printf '%s' "$kcfg") config view --raw -o jsonpath="$1"; }
  server=$(view '{.clusters[0].cluster.server}')
  config=$(python3 - "$(view '{.clusters[0].cluster.certificate-authority-data}')" \
                     "$(view '{.users[0].user.client-certificate-data}')" \
                     "$(view '{.users[0].user.client-key-data}')" <<'PY'
import json, sys
ca, cert, key = sys.argv[1:4]
print(json.dumps({"tlsClientConfig": {"caData": ca, "certData": cert, "keyData": key}}))
PY
)
  kr -n argocd apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: cluster-$name
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: cluster
type: Opaque
stringData:
  name: $name
  server: $server
  config: '$config'
YAML
  ok "Argo CD destination $name → $server"
done
