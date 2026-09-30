#!/usr/bin/env bash
# etcd backup / restore drill for k3s with embedded etcd (Volume 03, Workbook ex. 20).
# Run ON the Spark. Requires k3s/20-k8s-lab.yaml (cluster-init: true).
#   scripts/etcd-drill.sh status     # members, DB size, alarms, snapshots
#   scripts/etcd-drill.sh snapshot   # on-demand snapshot
#   scripts/etcd-drill.sh restore <snapshot-name>   # DESTRUCTIVE: rolls cluster state back
source "$(dirname "$0")/lib.sh"
ETCDCTL=(sudo env ETCDCTL_API=3 etcdctl
  --cacert=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt
  --cert=/var/lib/rancher/k3s/server/tls/etcd/client.crt
  --key=/var/lib/rancher/k3s/server/tls/etcd/client.key
  --endpoints=https://127.0.0.1:2379)
case "${1:-status}" in
  status)
    command -v etcdctl >/dev/null || { warn "etcdctl missing: sudo apt-get install -y etcd-client"; exit 1; }
    "${ETCDCTL[@]}" member list -w table
    "${ETCDCTL[@]}" endpoint status -w table
    "${ETCDCTL[@]}" alarm list
    sudo k3s etcd-snapshot list
    ;;
  snapshot)
    sudo k3s etcd-snapshot save --name "drill-$(date +%Y%m%d-%H%M%S)"
    sudo k3s etcd-snapshot list | tail -3
    ;;
  restore)
    snap=${2:?usage: etcd-drill.sh restore <snapshot-name-from-list>}
    path=$(sudo k3s etcd-snapshot list 2>/dev/null | awk -v s="$snap" '$1==s {print $2}')
    [[ -n "$path" ]] || { bad "snapshot $snap not found"; exit 1; }
    read -r -p "Restore $path? This rolls ALL cluster objects back. Type 'restore' to continue: " a
    [[ "$a" == restore ]] || exit 1
    sudo systemctl stop k3s
    sudo k3s server --cluster-reset --cluster-reset-restore-path="${path#file://}"
    sudo systemctl start k3s
    until k get --raw /readyz >/dev/null 2>&1; do sleep 3; done
    ok "restored from $snap — verify with: kubectl get ns"
    ;;
  *) echo "usage: $0 status|snapshot|restore <name>"; exit 2 ;;
esac
