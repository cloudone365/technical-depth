#!/usr/bin/env bash
# Walk a pod's network path (Step 08): netns → eth0 → lxc* veth peer → Cilium → cilium_vxlan
#   scripts/pod-netns.sh <namespace> <pod> [context]     (run ON the Spark, needs sudo)
# context: spark-root (default), dev-lab or llms — a vCluster pod is looked up
# on the root through vCluster's annotations, because that is where it runs.
source "$(dirname "$0")/lib.sh"
ns=${1:?namespace}; pod=${2:?pod}; ctx=${3:-$ROOT_CTX}
read -r hns hpod < <(host_pod "$ctx" "$ns" "$pod")
[[ "$ctx" != "$ROOT_CTX" ]] && info "$ctx $ns/$pod runs on the root as $hns/$hpod"
cid=$(kr -n "$hns" get pod "$hpod" -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|.*://||')
pid=$(sudo crictl inspect -o go-template --template '{{.info.pid}}' "$cid")
ip=$(kr -n "$hns" get pod "$hpod" -o jsonpath='{.status.podIP}')
echo "pod $hns/$hpod  ip=$ip  container-pid=$pid"
echo "── inside the pod netns"
sudo nsenter -t "$pid" -n ip -br addr
sudo nsenter -t "$pid" -n ip route
peer=$(sudo nsenter -t "$pid" -n cat /sys/class/net/eth0/iflink)
host_if=$(ip -o link | awk -F': ' -v i="$peer" '$1==i {print $2}' | cut -d@ -f1)
echo "── host side"
echo "eth0 (in pod) ⇄ $host_if (on host, a Cilium lxc* veth), ifindex $peer"
ip -br link show "$host_if"
ip -br addr show cilium_host 2>/dev/null                 # the node's gateway address in the pod CIDR
ip -d link show cilium_vxlan 2>/dev/null | grep -E 'vxlan|mtu' | sed 's/^ *//'
echo "── route the host uses to reach the pod"; ip route get "$ip"
echo "── Cilium's view of this endpoint (identity, policy)"
kr -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg endpoint list 2>/dev/null | grep -E "ENDPOINT|$ip" || true
