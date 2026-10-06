#!/usr/bin/env bash
# Show the kube-proxy iptables chain for a Service and its endpoints (Chapter 09).
#   scripts/svc-trace.sh <namespace> <service> [context]     (run ON the Spark, needs sudo)
# A Service in a vCluster is synced to the root and keeps the SAME ClusterIP,
# so the root's kube-proxy rules are the ones that balance its traffic.
source "$(dirname "$0")/lib.sh"
ns=${1:?namespace}; svc=${2:?service}; ctx=${3:-$ROOT_CTX}
cip=$(kc "$ctx" -n "$ns" get svc "$svc" -o jsonpath='{.spec.clusterIP}')
echo "Service $ctx $ns/$svc ClusterIP=$cip"
kc "$ctx" -n "$ns" get endpointslices -l kubernetes.io/service-name="$svc" -o wide
chain=$(sudo iptables-save -t nat | awk -v ip="$cip/32" '$0 ~ ip && /-j KUBE-SVC-/ {for(i=1;i<=NF;i++) if($i=="-j") print $(i+1)}' | head -1)
[[ -n "$chain" ]] || { bad "no KUBE-SVC chain for $cip (kube-proxy in IPVS or replaced by Cilium? try: sudo ipvsadm -Ln)"; exit 1; }
comment=$(sudo iptables-save -t nat | grep -m1 -- "-A KUBE-SERVICES .*$cip/32" | grep -oP -- '--comment "\K[^"]+')
echo "── root sees it as: $comment"
echo "── $chain (random-probability load balancing, top to bottom)"
sudo iptables-save -t nat | grep -E "^-A $chain " | sed 's/^/  /'
for sep in $(sudo iptables-save -t nat | grep -E "^-A $chain " | grep -oE 'KUBE-SEP-[A-Z0-9]+' | sort -u); do
  echo "── $sep"; sudo iptables-save -t nat | grep -E "^-A $sep " | sed 's/^/  /'
done
echo "── conntrack entries to $cip"; sudo conntrack -L -d "$cip" 2>/dev/null | head -5
