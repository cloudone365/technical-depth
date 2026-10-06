#!/usr/bin/env bash
# Copy a vCluster's exported kubeconfig (Secret vc-<name> in the root) into
# $KUBECONFIG as context <name> (Chapter 04). kubectl only — no YAML tooling.
# Cluster, user and context are all renamed to <name>, so two vClusters can
# never overwrite each other's entries when merged into one file.
#   scripts/merge-vcluster-kubeconfig.sh dev-lab vc-dev-lab
source "$(dirname "$0")/lib.sh"
name=${1:?vcluster name}; ns=${2:-vc-$1}
CACHE="$LAB_DIR/.cache"; mkdir -p "$CACHE"; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
until kr -n "$ns" get secret "vc-$name" >/dev/null 2>&1; do sleep 3; done
kr -n "$ns" get secret "vc-$name" -o jsonpath='{.data.config}' | base64 -d > "$CACHE/kubeconfig-$name.yaml"
chmod 600 "$CACHE/kubeconfig-$name.yaml"
v=("$KUBECTL" --kubeconfig "$CACHE/kubeconfig-$name.yaml" config view --raw -o)
server=$("${v[@]}" jsonpath='{.clusters[0].cluster.server}')
"${v[@]}" jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d > "$tmp/ca.crt"
"$KUBECTL" config set-cluster "$name" --server="$server" --certificate-authority="$tmp/ca.crt" --embed-certs >/dev/null
if [[ -n "$("${v[@]}" jsonpath='{.users[0].user.client-certificate-data}')" ]]; then
  "${v[@]}" jsonpath='{.users[0].user.client-certificate-data}' | base64 -d > "$tmp/client.crt"
  "${v[@]}" jsonpath='{.users[0].user.client-key-data}' | base64 -d > "$tmp/client.key"
  "$KUBECTL" config set-credentials "$name" --client-certificate="$tmp/client.crt" --client-key="$tmp/client.key" --embed-certs >/dev/null
else
  "$KUBECTL" config set-credentials "$name" --token="$("${v[@]}" jsonpath='{.users[0].user.token}')" >/dev/null
fi
"$KUBECTL" config set-context "$name" --cluster="$name" --user="$name" >/dev/null
until kc "$name" get --raw /readyz >/dev/null 2>&1; do sleep 3; done
ok "context $name → $server"
