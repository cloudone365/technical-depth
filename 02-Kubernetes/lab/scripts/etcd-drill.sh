#!/usr/bin/env bash
# etcd backup / restore drill for the kubeadm ROOT cluster (Chapter 02, Workbook ex. 20).
# Run ON the Spark (dgx-spark-1). etcd is a static pod; 01-Ansible installed
# etcdctl/etcdutl matching its version and an etcd-snapshot timer (every 6 h).
#   scripts/etcd-drill.sh status            # members, DB size, alarms, snapshots
#   scripts/etcd-drill.sh snapshot          # on-demand snapshot (/usr/local/sbin/etcd-snapshot)
#   scripts/etcd-drill.sh restore <file>    # DESTRUCTIVE: rolls the root cluster state back
#
# What a root etcd restore does NOT roll back: the contents of the two vClusters.
# Each keeps its own SQLite database on its PVC (vc-dev-lab, vc-llms). Back
# those up separately (Chapter 04 §6.7: 'vcluster snapshot' or a copy of the PVC).
source "$(dirname "$0")/lib.sh"
SNAPDIR=/var/lib/etcd-snapshots
PKI=/etc/kubernetes/pki/etcd
ETCDCTL=(sudo env ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379
  "--cacert=$PKI/ca.crt" "--cert=$PKI/healthcheck-client.crt" "--key=$PKI/healthcheck-client.key")
case "${1:-status}" in
  status)
    command -v etcdctl >/dev/null || { warn "etcdctl missing: re-run 01-Ansible playbooks/19.1-kubernetes.yml"; exit 1; }
    "${ETCDCTL[@]}" member list -w table
    "${ETCDCTL[@]}" endpoint status -w table
    "${ETCDCTL[@]}" alarm list
    sudo ls -lh "$SNAPDIR" 2>/dev/null | tail -5
    systemctl list-timers etcd-snapshot.timer --no-pager 2>/dev/null | head -3
    ;;
  snapshot)
    sudo /usr/local/sbin/etcd-snapshot drill
    ;;
  restore)
    snap=${2:?usage: etcd-drill.sh restore <file from: sudo ls $SNAPDIR>}
    [[ "$snap" == /* ]] || snap="$SNAPDIR/$snap"
    sudo test -f "$snap" || { bad "snapshot $snap not found"; exit 1; }
    sudo etcdutl snapshot status "$snap" -w table
    read -r -p "Restore $snap? This rolls ALL root cluster objects back. Type 'restore' to continue: " a
    [[ "$a" == restore ]] || exit 1
    node=$(hostname -s)
    ip=$(sudo grep -oP -- '--advertise-client-urls=https://\K[0-9.]+' /etc/kubernetes/manifests/etcd.yaml)
    ts=$(date +%Y%m%d-%H%M%S)
    info "1/4 stop the control plane: move the static-pod manifests away"
    sudo mkdir -p /etc/kubernetes/manifests.stopped
    sudo mv /etc/kubernetes/manifests/*.yaml /etc/kubernetes/manifests.stopped/
    until ! sudo crictl ps --name '^etcd$' -q | grep -q .; do sleep 2; done
    info "2/4 keep the current data dir as /var/lib/etcd.before-restore-$ts"
    sudo mv /var/lib/etcd "/var/lib/etcd.before-restore-$ts"
    info "3/4 etcdutl snapshot restore → /var/lib/etcd (same path the manifest mounts)"
    sudo etcdutl snapshot restore "$snap" --data-dir /var/lib/etcd --name "$node" \
      --initial-cluster "$node=https://$ip:2380" --initial-advertise-peer-urls "https://$ip:2380"
    info "4/4 start the control plane again"
    sudo mv /etc/kubernetes/manifests.stopped/*.yaml /etc/kubernetes/manifests/
    until kr get --raw /readyz >/dev/null 2>&1; do sleep 3; done
    ok "restored from $(basename "$snap") — verify with: kubectl --context $ROOT_CTX get ns"
    warn "objects created after the snapshot are gone from the root; the vClusters' own data was NOT rolled back"
    ;;
  *) echo "usage: $0 status|snapshot|restore <file>"; exit 2 ;;
esac
