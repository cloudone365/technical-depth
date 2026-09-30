#!/usr/bin/env bash
# End-to-end verification of the 02 Kubernetes lab (Volume 15 §6, Volume 20).
# Prints PASS/WARN/FAIL per check; exit 0 only if nothing FAILed.
#   scripts/verify.sh              # everything
#   scripts/verify.sh tenancy gpu  # selected sections: platform tenancy admission gpu ingress storage serving observability
source "$(dirname "$0")/lib.sh"
set +e
want() { [[ $# -eq 0 || " ${SECTIONS[*]} " == *" $1 "* ]]; }
SECTIONS=("$@"); [[ ${#SECTIONS[@]} -eq 0 ]] && SECTIONS=(platform tenancy admission gpu ingress storage serving observability)

if want platform; then
  echo "── platform"
  k get --raw /readyz >/dev/null && ok "apiserver ready" || bad "apiserver not ready"
  nr=$(k get nodes --no-headers | awk '$2!="Ready"' | wc -l); [[ $nr -eq 0 ]] && ok "all nodes Ready" || bad "$nr node(s) not Ready"
  k -n kube-system get deploy coredns -o jsonpath='{.status.readyReplicas}' | grep -q '^[1-9]' && ok "CoreDNS ready" || bad "CoreDNS not ready"
  miss=0; for pc in spark-platform spark-serving spark-interactive spark-batch spark-preemptible; do
    k get priorityclass $pc >/dev/null 2>&1 || { bad "PriorityClass $pc missing"; miss=1; }; done
  [[ $miss -eq 0 ]] && ok "5 PriorityClasses"
fi

if want tenancy; then
  echo "── tenancy"
  for ns in tenant-alpha tenant-beta; do
    cpu=$(k -n $ns get resourcequota budget-5pct -o jsonpath='{.spec.hard.limits\.cpu}' 2>/dev/null)
    [[ "$cpu" == 1 ]] && ok "$ns quota limits.cpu=1 (5 % of 20 cores)" || bad "$ns quota missing/wrong ($cpu)"
    k -n $ns get limitrange defaults >/dev/null 2>&1 && ok "$ns LimitRange" || bad "$ns LimitRange missing"
    k -n $ns get networkpolicy default-deny-ingress >/dev/null 2>&1 && ok "$ns default-deny NetworkPolicy" || bad "$ns no default-deny"
  done
  [[ $(k auth can-i create pods -n tenant-alpha --as=u --as-group=team-alpha) == yes ]] && ok "team-alpha can create pods in tenant-alpha" || bad "team-alpha cannot create pods"
  [[ $(k auth can-i create pods -n tenant-beta --as=u --as-group=team-alpha) == no ]] && ok "team-alpha cannot touch tenant-beta" || bad "cross-tenant access allowed!"
  [[ $(k auth can-i update resourcequota -n tenant-alpha --as=u --as-group=team-alpha) == no ]] && ok "tenants cannot edit their quota" || bad "tenant can edit quota!"
fi

if want admission; then
  echo "── admission (server-side dry-run, nothing is created)"
  for t in "$LAB_DIR"/tests/policy/*.yaml; do
    expect=$(awk -F': ' '/# expect:/ {print $2; exit}' "$t")
    out=$(k create --dry-run=server -f "$t" 2>&1); rc=$?
    name=$(basename "$t" .yaml)
    if [[ $expect == deny && $rc -ne 0 ]]; then ok "denied  $name"
    elif [[ $expect == allow && $rc -eq 0 ]]; then ok "allowed $name"
    else bad "$name expected $expect, got rc=$rc: ${out//$'\n'/ }"; fi
  done
fi

if want gpu; then
  echo "── gpu"
  node=$(k get nodes -l nvidia.com/gpu.product=GB10 -o jsonpath='{.items[0].metadata.name}')
  [[ -n "$node" ]] && ok "GB10 node: $node" || bad "no node labelled nvidia.com/gpu.product=GB10"
  alloc=$(k get node "$node" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}')
  [[ ${alloc:-0} -ge 1 ]] && ok "allocatable nvidia.com/gpu=$alloc" || bad "no GPU allocatable"
  k -n gpu-operator get pods -l app=nvidia-operator-validator --no-headers 2>/dev/null | grep -q Running && ok "operator-validator Running" || warn "operator-validator not Running"
  k -n tenant-beta delete pod gpu-smoke --ignore-not-found >/dev/null
  k apply -f "$LAB_DIR/manifests/70-gpu/gpu-smoke.yaml" >/dev/null
  if k -n tenant-beta wait --for=jsonpath='{.status.phase}'=Succeeded pod/gpu-smoke --timeout=180s >/dev/null; then
    ok "gpu-smoke: $(k -n tenant-beta logs gpu-smoke | head -1)"
  else bad "gpu-smoke did not succeed: $(k -n tenant-beta get pod gpu-smoke -o jsonpath='{.status.phase} {.status.containerStatuses[0].state}')"; fi
  k -n tenant-beta delete pod gpu-smoke --wait=false >/dev/null
fi

if want ingress; then
  echo "── ingress"
  if k -n ingress get deploy traefik-lab >/dev/null 2>&1; then
    lb=$(k -n ingress get svc traefik-lab -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
    [[ -n "$lb" ]] && ok "Traefik LoadBalancer IP $lb" || bad "Traefik has no LB IP (servicelb disabled?)"
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: llm.lab.local' "http://$lb/v1/models")
    [[ $code == 200 ]] && ok "Ingress llm.lab.local/v1/models → 200" || bad "Ingress returned $code"
    n=$(curl -sN -H 'Host: llm.lab.local' "http://$lb/v1/chat/completions" -d '{"stream":true,"max_tokens":10}' | grep -c '^data:')
    [[ $n -ge 10 ]] && ok "SSE streaming through ingress ($n events)" || bad "streaming broken ($n events)"
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: gw.lab.local' "http://$lb/v1/models")
    [[ $code == 200 ]] && ok "Gateway API HTTPRoute gw.lab.local → 200" || warn "HTTPRoute returned $code"
  else warn "Traefik not installed — skipped"; fi
fi

if want storage; then
  echo "── storage"
  for sc in local-path local-nvme local-nvme-retain; do k get sc $sc >/dev/null 2>&1 && ok "StorageClass $sc" || bad "StorageClass $sc missing"; done
  ph=$(k -n llm-serving get pvc model-cache -o jsonpath='{.status.phase}' 2>/dev/null)
  [[ $ph == Bound ]] && ok "model-cache Bound" || info "model-cache is '${ph:-absent}' (WaitForFirstConsumer binds when a pod uses it)"
fi

if want serving; then
  echo "── serving"
  k -n llm-serving get deploy mock-llm -o jsonpath='{.status.readyReplicas}' 2>/dev/null | grep -q '^[1-9]' && ok "mock-llm ready" || bad "mock-llm not ready"
  if k -n llm-serving get deploy vllm >/dev/null 2>&1; then
    r=$(k -n llm-serving get deploy vllm -o jsonpath='{.status.readyReplicas}')
    if [[ ${r:-0} -ge 1 ]]; then
      out=$(k -n llm-serving exec deploy/vllm -- curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
        -d '{"model":"qwen2.5-0.5b","messages":[{"role":"user","content":"Say OK"}],"max_tokens":5}')
      grep -q '"choices"' <<<"$out" && ok "vLLM answered a chat completion" || bad "vLLM response: $out"
    else warn "vLLM deployed but not ready"; fi
  else info "vLLM not deployed (Volume 21)"; fi
fi

if want observability; then
  echo "── observability"
  if k -n observability get prometheus >/dev/null 2>&1; then
    up=$(k -n observability exec sts/prometheus-kps-prometheus -c prometheus -- \
         wget -qO- 'http://localhost:9090/api/v1/query?query=up' 2>/dev/null | grep -o '"value":\[[^]]*"1"\]' | wc -l)
    [[ $up -ge 5 ]] && ok "Prometheus: $up targets up" || warn "Prometheus: only $up targets up"
    k -n observability get cm spark-k8s-dashboard >/dev/null 2>&1 && ok "Grafana dashboard ConfigMap" || warn "dashboard ConfigMap missing"
  else warn "kube-prometheus-stack not installed — skipped"; fi
fi
summary
