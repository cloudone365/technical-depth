#!/usr/bin/env bash
# 03 DeepSeek lab verification (Volume 39, 40). Sections: platform serving apps rag
source "$(dirname "$0")/lib.sh"
set +e
SECTIONS=("$@"); [[ ${#SECTIONS[@]} -eq 0 ]] && SECTIONS=(platform serving apps rag)
want() { [[ " ${SECTIONS[*]} " == *" $1 "* ]]; }
if want platform; then
  echo "── platform (from 02 Kubernetes)"
  k get ns llm-serving batch >/dev/null 2>&1 && ok "namespaces llm-serving + batch" || bad "02 platform not applied (02 scripts/apply-lab.sh)"
  k -n $NS get pvc model-cache >/dev/null 2>&1 && ok "model-cache PVC" || bad "model-cache PVC missing (02 manifests/60-storage)"
  k -n $NS get cm deepseek-tools deepseek-evaldata >/dev/null 2>&1 && ok "tool/eval ConfigMaps" || bad "kubectl apply -k \"03 DeepSeek/lab\""
fi
if want serving; then
  echo "── serving"
  served=$(k -n $NS get deploy vllm -o jsonpath='{.metadata.labels.model}' 2>/dev/null)
  ready=$(k -n $NS get deploy vllm -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
  if [[ ${ready:-0} -ge 1 ]]; then
    ok "vLLM serving '$served'"
    m=$(k -n $NS exec deploy/vllm -- curl -s localhost:8000/v1/models | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null)
    [[ -n $m ]] && ok "/v1/models → $m" || bad "/v1/models failed"
    k -n $NS exec deploy/vllm -- curl -s localhost:8000/metrics | grep -q '^vllm:num_requests_running' && ok "vLLM metrics exposed" || warn "no vllm metrics"
  else warn "vLLM not ready (scripts/serve-model.sh <name>)"; fi
fi
if want apps; then
  echo "── apps"
  for d in bge-m3 litellm; do
    [[ $(k -n $NS get deploy $d -o jsonpath='{.status.readyReplicas}' 2>/dev/null) -ge 1 ]] && ok "$d ready" || warn "$d not ready"
  done
  [[ $(k -n $NS get sts open-webui -o jsonpath='{.status.readyReplicas}' 2>/dev/null) -ge 1 ]] && ok "open-webui ready" || warn "open-webui not ready"
  key=$(k -n $NS get secret litellm-master-key -o jsonpath='{.data.key}' 2>/dev/null | base64 -d)
  if [[ -n $key ]] && k -n $NS get deploy litellm >/dev/null 2>&1; then
    n=$(k -n $NS exec deploy/litellm -- python3 -c "import urllib.request,json;r=urllib.request.Request('http://localhost:4000/v1/models',headers={'Authorization':'Bearer $key'});print(len(json.load(urllib.request.urlopen(r))['data']))" 2>/dev/null)
    [[ ${n:-0} -ge 1 ]] && ok "LiteLLM lists $n model aliases" || warn "LiteLLM /v1/models failed"
  fi
fi
if want rag; then
  echo "── rag"
  k -n $NS port-forward svc/qdrant 16333:6333 >/dev/null 2>&1 & pf=$!; sleep 2
  cnt=$(curl -s localhost:16333/collections/technical-depth | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])' 2>/dev/null)
  kill $pf 2>/dev/null
  [[ ${cnt:-0} -gt 100 ]] && ok "Qdrant collection technical-depth: $cnt points" || warn "RAG index missing (k8s/jobs/rag-ingest.yaml)"
fi
summary
