#!/usr/bin/env bash
# Copy the lab kubeconfig (contexts spark-root, dev-lab, llms) from sema01 to
# your MacBook, where every 02 Kubernetes command expects it (00b guide §7.4):
#   01 Ansible/lab/.cache/kubeconfig-spark-lab.yaml
# Semaphore writes it to its state volume (/opt/spark-lab/cache on sema01)
# whenever playbook 05-kubernetes.yml or 06b-vclusters.yml runs. Reading it
# through the container needs only your docker group membership on sema01.
#   tools/fetch-kubeconfig.sh [user@sema01]
set -euo pipefail
SEMA=${1:-${SEMA_HOST:-sema01}}
LAB="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$LAB/.cache/kubeconfig-spark-lab.yaml"
mkdir -p "$LAB/.cache" && chmod 0700 "$LAB/.cache"
umask 077
ssh "$SEMA" 'cd ~/semaphore && docker compose exec -T semaphore cat /var/lib/spark-lab/cache/kubeconfig-spark-lab.yaml' > "$DEST.tmp"
grep -q 'name: spark-root' "$DEST.tmp" || { rm -f "$DEST.tmp"; echo "no spark-root context in the file from $SEMA" >&2; exit 1; }
mv "$DEST.tmp" "$DEST"
echo "wrote $DEST"
KUBECONFIG="$DEST" kubectl config get-contexts -o name
