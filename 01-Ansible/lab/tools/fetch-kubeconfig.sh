#!/usr/bin/env bash
# Copy the lab kubeconfig (contexts spark-root, dev-lab, dev-llms) from sema01 to
# your MacBook, where every 02-Kubernetes command expects it (Chapter 04 §8.4):
#   01-Ansible/lab/.cache/kubeconfig-spark-lab.yaml
# Semaphore writes it to its state volume (/opt/spark-lab/cache on sema01)
# whenever playbook 19.1-kubernetes.yml or 20.2-vclusters.yml runs. Reading it
# through the container needs only your docker group membership on sema01.
#   tools/fetch-kubeconfig.sh [user@sema01]
set -euo pipefail
SEMA=${1:-${SEMA_HOST:-sema01}}
LAB="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$LAB/.cache/kubeconfig-spark-lab.yaml"
mkdir -p "$LAB/.cache" && chmod 0700 "$LAB/.cache"
umask 077
rm -f "$DEST.tmp" 2>/dev/null || true
if ! : > "$DEST.tmp" 2>/dev/null; then
  echo "cannot write $DEST.tmp" >&2
  echo "  check: ls -ldO \"$LAB/.cache\" \"$DEST\"*   (owner = you, flags -)" >&2
  echo "  macOS 'Operation not permitted': allow your terminal app the Documents folder" >&2
  echo "  (System Settings → Privacy & Security → Files and Folders), restart it, run again." >&2
  exit 1
fi
ssh "$SEMA" 'cd ~/semaphore && docker compose exec -T semaphore cat /var/lib/spark-lab/cache/kubeconfig-spark-lab.yaml' > "$DEST.tmp"
grep -q 'name: spark-root' "$DEST.tmp" || { rm -f "$DEST.tmp"; echo "no spark-root context in the file from $SEMA" >&2; exit 1; }
mv "$DEST.tmp" "$DEST"
echo "wrote $DEST"
if command -v kubectl >/dev/null; then
  KUBECONFIG="$DEST" kubectl config get-contexts -o name
else
  echo "kubectl is not installed on this machine; the kubeconfig is in place." >&2
  echo "macOS: brew install kubectl helm   (Chapter 04 §8.4)" >&2
  grep -oE 'name: (spark-root|dev-lab|dev-llms)$' "$DEST" | sort -u
fi
