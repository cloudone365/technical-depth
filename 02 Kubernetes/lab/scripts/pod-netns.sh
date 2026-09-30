#!/usr/bin/env bash
# Walk a pod's network path (Volume 06): netns → eth0 → veth peer → cni0 → flannel.1
#   scripts/pod-netns.sh <namespace> <pod>     (run ON the Spark, needs sudo)
source "$(dirname "$0")/lib.sh"
ns=${1:?namespace}; pod=${2:?pod}
cid=$(k -n "$ns" get pod "$pod" -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|.*://||')
pid=$(sudo k3s crictl inspect -o go-template --template '{{.info.pid}}' "$cid")
ip=$(k -n "$ns" get pod "$pod" -o jsonpath='{.status.podIP}')
echo "pod $ns/$pod  ip=$ip  container-pid=$pid"
echo "── inside the pod netns"
sudo nsenter -t "$pid" -n ip -br addr
sudo nsenter -t "$pid" -n ip route
peer=$(sudo nsenter -t "$pid" -n cat /sys/class/net/eth0/iflink)
host_if=$(ip -o link | awk -F': ' -v i="$peer" '$1==i {print $2}' | cut -d@ -f1)
echo "── host side"
echo "eth0 (in pod) ⇄ $host_if (on host), ifindex $peer"
ip -br link show "$host_if"
bridge link show dev "$host_if" 2>/dev/null
ip -d link show flannel.1 2>/dev/null | grep -E 'vxlan|mtu' | sed 's/^ *//'
echo "── route the host uses to reach the pod"; ip route get "$ip"
