#!/usr/bin/env bash
# Show the kube-proxy iptables chain for a Service and its endpoints (Volume 07).
#   scripts/svc-trace.sh <namespace> <service>     (run ON the Spark, needs sudo)
source "$(dirname "$0")/lib.sh"
ns=${1:?namespace}; svc=${2:?service}
cip=$(k -n "$ns" get svc "$svc" -o jsonpath='{.spec.clusterIP}')
echo "Service $ns/$svc ClusterIP=$cip"
k -n "$ns" get endpointslices -l kubernetes.io/service-name="$svc" -o wide
chain=$(sudo iptables-save -t nat | awk -v ip="$cip/32" '$0 ~ ip && /-j KUBE-SVC-/ {for(i=1;i<=NF;i++) if($i=="-j") print $(i+1)}' | head -1)
[[ -n "$chain" ]] || { bad "no KUBE-SVC chain for $cip (IPVS mode? try: sudo ipvsadm -Ln)"; exit 1; }
echo "── $chain (random-probability load balancing, top to bottom)"
sudo iptables-save -t nat | grep -E "^-A $chain " | sed 's/^/  /'
for sep in $(sudo iptables-save -t nat | grep -E "^-A $chain " | grep -oE 'KUBE-SEP-[A-Z0-9]+' | sort -u); do
  echo "── $sep"; sudo iptables-save -t nat | grep -E "^-A $sep " | sed 's/^/  /'
done
echo "── conntrack entries to $cip"; sudo conntrack -L -d "$cip" 2>/dev/null | head -5
