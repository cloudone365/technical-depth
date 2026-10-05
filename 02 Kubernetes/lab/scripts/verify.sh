#!/usr/bin/env bash
# End-to-end verification of the 02 Kubernetes lab (Volume 15 §6, Volume 20, 27).
# Prints PASS/WARN/FAIL per check; exit 0 only if nothing FAILed.
#   scripts/verify.sh              # everything
#   scripts/verify.sh tenancy gpu  # selected sections:
#     platform vclusters tenancy admission gpu ingress storage serving observability
source "$(dirname "$0")/lib.sh"
set +e
want() { [[ " ${SECTIONS[*]} " == *" $1 "* ]]; }
SECTIONS=("$@"); [[ ${#SECTIONS[@]} -eq 0 ]] && SECTIONS=(platform vclusters tenancy admission gpu ingress storage serving observability)
gpu_node() { kr get nodes -l spark.lab/gpu=gb10 -o jsonpath='{.items[0].metadata.name}'; }

if want platform; then
  echo "── platform (root, context $ROOT_CTX)"
  kr get --raw /readyz >/dev/null && ok "root apiserver ready" || bad "root apiserver not ready"
  nr=$(kr get nodes --no-headers | awk '$2!="Ready"' | wc -l); [[ $nr -eq 0 ]] && ok "all nodes Ready" || bad "$nr node(s) not Ready"
  kr -n kube-system get deploy coredns -o jsonpath='{.status.readyReplicas}' | grep -q '^[1-9]' && ok "CoreDNS ready" || bad "CoreDNS not ready"
  kr -n kube-system get ds cilium -o jsonpath='{.status.numberReady}' | grep -q '^[1-9]' && ok "Cilium ready" || bad "Cilium not ready"
  kr -n kube-system get ds kube-proxy >/dev/null 2>&1 && ok "kube-proxy present (iptables mode, Volume 07)" || warn "kube-proxy missing"
  miss=0; for pc in spark-platform spark-serving spark-interactive spark-batch spark-preemptible; do
    kr get priorityclass $pc >/dev/null 2>&1 || { bad "PriorityClass $pc missing"; miss=1; }; done
  [[ $miss -eq 0 ]] && ok "5 PriorityClasses"
  ls /var/lib/etcd-snapshots/*.db >/dev/null 2>&1 && ok "etcd snapshots present ($(ls /var/lib/etcd-snapshots/*.db | wc -l))" \
    || info "no etcd snapshot on this machine yet (sudo etcd-snapshot, or wait for etcd-snapshot.timer)"
fi

if want vclusters; then
  echo "── vclusters"
  declare -A WANT_CPU=([dev-lab]=2 [llms]=4) WANT_MEM=([dev-lab]=8Gi [llms]=48Gi) WANT_GPU=([dev-lab]=2 [llms]=8)
  for v in "$DEV_CTX" "$LLM_CTX"; do
    kr -n "vc-$v" get statefulset "$v" -o jsonpath='{.status.readyReplicas}' 2>/dev/null | grep -q '^1' \
      && ok "$v control plane Running in vc-$v" || bad "$v control plane not ready (kubectl --context $ROOT_CTX -n vc-$v get pods)"
    kc "$v" get --raw /readyz >/dev/null 2>&1 && ok "$v API answers ($(kc "$v" config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null))" \
      || bad "context $v unreachable"
    q=$(kr -n "vc-$v" get resourcequota vcluster-budget -o jsonpath='{.spec.hard.requests\.cpu} {.spec.hard.limits\.memory} {.spec.hard.requests\.nvidia\.com/gpu}' 2>/dev/null)
    [[ "$q" == "${WANT_CPU[$v]} ${WANT_MEM[$v]} ${WANT_GPU[$v]}" ]] && ok "$v root budget: $q (CPU · memory · GPU slices)" || bad "$v root budget is '$q'"
    n=$(kc "$v" get nodes --no-headers 2>/dev/null | wc -l)
    [[ $n -ge 1 ]] && ok "$v sees $n real node(s) (sync.fromHost.nodes)" || bad "$v sees no nodes"
    kc "$v" get runtimeclass nvidia >/dev/null 2>&1 && ok "$v RuntimeClass nvidia" || bad "$v RuntimeClass nvidia missing (apply $v/00-platform)"
  done
  kr -n vc-dev-lab get ciliumnetworkpolicy vcluster-boundary >/dev/null 2>&1 && ok "Cilium vCluster boundary policies" || warn "root/05-vclusters cilium-policies not applied"
fi

if want tenancy; then
  echo "── tenancy (inside $DEV_CTX)"
  for ns in tenant-alpha tenant-beta; do
    cpu=$(kd -n $ns get resourcequota tenant-budget -o jsonpath='{.spec.hard.limits\.cpu}' 2>/dev/null)
    [[ "$cpu" == 500m ]] && ok "$ns quota limits.cpu=500m" || bad "$ns quota missing/wrong ($cpu)"
    kd -n $ns get limitrange defaults >/dev/null 2>&1 && ok "$ns LimitRange" || bad "$ns LimitRange missing"
    kd -n $ns get networkpolicy default-deny-ingress >/dev/null 2>&1 && ok "$ns default-deny NetworkPolicy" || bad "$ns no default-deny"
  done
  [[ $(kd auth can-i create pods -n tenant-alpha --as=u --as-group=team-alpha) == yes ]] && ok "team-alpha can create pods in tenant-alpha" || bad "team-alpha cannot create pods"
  [[ $(kd auth can-i create pods -n tenant-beta --as=u --as-group=team-alpha) == no ]] && ok "team-alpha cannot touch tenant-beta" || bad "cross-tenant access allowed!"
  [[ $(kd auth can-i update resourcequota -n tenant-alpha --as=u --as-group=team-alpha) == no ]] && ok "tenants cannot edit their quota" || bad "tenant can edit quota!"
  [[ $(kr auth can-i list pods -n vc-dev-lab --as=u --as-group=team-alpha) == no ]] && ok "team-alpha has no rights on the root" || bad "team-alpha can read the root!"
fi

if want admission; then
  echo "── admission (server-side dry-run inside each vCluster, nothing is created)"
  for t in "$LAB_DIR"/tests/policy/*.yaml; do
    expect=$(awk -F': ' '/# expect:/ {print $2; exit}' "$t")
    ctx=$(awk -F': ' '/# cluster:/ {print $2; exit}' "$t")
    out=$(kc "$ctx" create --dry-run=server -f "$t" 2>&1); rc=$?
    name=$(basename "$t" .yaml)
    if [[ $expect == deny && $rc -ne 0 ]]; then ok "denied  $name ($ctx)"
    elif [[ $expect == allow && $rc -eq 0 ]]; then ok "allowed $name ($ctx)"
    else bad "$name ($ctx) expected $expect, got rc=$rc: ${out//$'\n'/ }"; fi
  done
fi

if want gpu; then
  echo "── gpu"
  node=$(gpu_node)
  [[ -n "$node" ]] && ok "GB10 node: $node" || bad "no node labelled spark.lab/gpu=gb10"
  alloc=$(kr get node "$node" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}')
  [[ ${alloc:-0} -eq 15 ]] && ok "allocatable nvidia.com/gpu=$alloc (root 5 · dev-lab 2 · llms 8)" \
    || { [[ ${alloc:-0} -ge 1 ]] && warn "allocatable nvidia.com/gpu=$alloc (budgets assume 15)" || bad "no GPU allocatable"; }
  kr -n gpu-operator get pods -l app=nvidia-operator-validator --no-headers 2>/dev/null | grep -q Running && ok "operator-validator Running" || warn "operator-validator not Running"
  info "gpu-smoke from INSIDE dev-lab (tenant-beta → syncer → root scheduler → nvidia runtime)"
  kd -n tenant-beta delete pod gpu-smoke --ignore-not-found >/dev/null
  kd apply -f "$LAB_DIR/manifests/dev-lab/70-gpu/gpu-smoke.yaml" >/dev/null
  if kd -n tenant-beta wait --for=jsonpath='{.status.phase}'=Succeeded pod/gpu-smoke --timeout=180s >/dev/null; then
    ok "gpu-smoke: $(kd -n tenant-beta logs gpu-smoke | head -1)"
  else bad "gpu-smoke did not succeed: $(kd -n tenant-beta get pod gpu-smoke -o jsonpath='{.status.phase} {.status.containerStatuses[0].state}')"; fi
  kd -n tenant-beta delete pod gpu-smoke --wait=false >/dev/null
fi

if want ingress; then
  echo "── ingress (Traefik inside $LLM_CTX)"
  if kl -n ingress get deploy traefik-lab >/dev/null 2>&1; then
    lb=$(kl -n ingress get svc traefik-lab -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
    [[ "$lb" == 192.168.0.115 ]] && ok "Traefik LoadBalancer IP $lb (MetalLB on the root)" || bad "Traefik LB IP is '$lb' (expected 192.168.0.115 — MetalLB, root quota services.loadbalancers?)"
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: llm.lab.local' "http://$lb/v1/models")
    [[ $code == 200 ]] && ok "Ingress llm.lab.local/v1/models → 200" || bad "Ingress returned $code"
    n=$(curl -sN -H 'Host: llm.lab.local' "http://$lb/v1/chat/completions" -d '{"stream":true,"max_tokens":10}' | grep -c '^data:')
    [[ $n -ge 10 ]] && ok "SSE streaming through ingress ($n events)" || bad "streaming broken ($n events)"
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: gw.lab.local' "http://$lb/v1/models")
    [[ $code == 200 ]] && ok "Gateway API HTTPRoute gw.lab.local → 200" || warn "HTTPRoute returned $code"
  else warn "Traefik not installed in $LLM_CTX — skipped (scripts/install-addons.sh traefik)"; fi
fi

if want storage; then
  echo "── storage"
  for sc in local-path local-nvme local-nvme-retain; do kr get sc $sc >/dev/null 2>&1 && ok "StorageClass $sc (root)" || bad "StorageClass $sc missing"; done
  kl get sc local-nvme-retain >/dev/null 2>&1 && ok "llms sees the root StorageClasses (sync.fromHost.storageClasses)" || warn "llms does not see local-nvme-retain"
  ph=$(kl -n llm-serving get pvc model-cache -o jsonpath='{.status.phase}' 2>/dev/null)
  [[ $ph == Bound ]] && ok "model-cache Bound (llms)" || info "model-cache is '${ph:-absent}' (WaitForFirstConsumer binds when a pod uses it)"
fi

if want serving; then
  echo "── serving (inside $LLM_CTX)"
  kl -n llm-serving get deploy mock-llm -o jsonpath='{.status.readyReplicas}' 2>/dev/null | grep -q '^[1-9]' && ok "mock-llm ready" || bad "mock-llm not ready"
  if kl -n llm-serving get deploy vllm >/dev/null 2>&1; then
    r=$(kl -n llm-serving get deploy vllm -o jsonpath='{.status.readyReplicas}')
    if [[ ${r:-0} -ge 1 ]]; then
      out=$(kl -n llm-serving exec deploy/vllm -- curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
        -d '{"model":"qwen2.5-0.5b","messages":[{"role":"user","content":"Say OK"}],"max_tokens":5}')
      grep -q '"choices"' <<<"$out" && ok "vLLM answered a chat completion" || bad "vLLM response: $out"
    else warn "vLLM deployed but not ready"; fi
  else info "vLLM not deployed (Volume 21)"; fi
fi

if want observability; then
  echo "── observability (root)"
  if kr -n observability get prometheus >/dev/null 2>&1; then
    up=$(kr -n observability exec sts/prometheus-kps-prometheus -c prometheus -- \
         wget -qO- 'http://localhost:9090/api/v1/query?query=up' 2>/dev/null | grep -o '"value":\[[^]]*"1"\]' | wc -l)
    [[ $up -ge 5 ]] && ok "Prometheus: $up targets up" || warn "Prometheus: only $up targets up"
    vc=$(kr -n observability exec sts/prometheus-kps-prometheus -c prometheus -- \
         wget -qO- 'http://localhost:9090/api/v1/query?query=count(up%7Bvcluster%21%3D%22%22%7D)' 2>/dev/null | grep -o '"value":\[[^]]*"[0-9]*"\]' | grep -o '"[0-9]*"\]' | tr -dc 0-9)
    [[ ${vc:-0} -ge 1 ]] && ok "Prometheus scrapes ${vc} target(s) inside vClusters" || info "no vCluster targets yet (deploy Traefik/vLLM in llms)"
    kr -n observability get cm spark-k8s-dashboard >/dev/null 2>&1 && ok "Grafana dashboard ConfigMap" || warn "dashboard ConfigMap missing"
  else warn "kube-prometheus-stack not installed — skipped"; fi
fi
summary
