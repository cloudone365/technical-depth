#!/usr/bin/env bash
# Live triage of the DeepSeek serving stack (Volume 39). Read-only. Prints what is
# serving, how it is configured, what the engine is experiencing right now, and the
# usual suspects, each with a pointer to the drill/volume that explains the fix.
#   scripts/llm-triage.sh            # whole stack
#   scripts/llm-triage.sh serving    # one section: serving | gateway | secrets | day2 | host
# For an offline support bundle use 02's scripts/collect-diag.sh llm-serving batch.
source "$(dirname "$0")/lib.sh"
set +e
SECTIONS=("$@"); [[ ${#SECTIONS[@]} -eq 0 ]] && SECTIONS=(serving gateway secrets day2 host)
want() { [[ " ${SECTIONS[*]} " == *" $1 "* ]]; }
metric() {   # metric <promql-ish name filter> — sum of matching vLLM /metrics lines
  k -n "$NS" exec deploy/vllm -- curl -s localhost:8000/metrics 2>/dev/null \
    | awk -v m="$1" '$1 ~ "^"m && $1 !~ /_created/ {s += $2} END {printf "%.0f", s}'
}

if want serving; then
  echo "── serving (vLLM)"
  if ! k -n "$NS" get deploy vllm >/dev/null 2>&1; then bad "no vLLM Deployment (scripts/serve-model.sh <name>)"; else
    label=$(k -n "$NS" get deploy vllm -o jsonpath='{.metadata.labels.model}')
    ready=$(k -n "$NS" get deploy vllm -o jsonpath='{.status.readyReplicas}')
    args=$(k -n "$NS" get deploy vllm -o jsonpath='{.spec.template.spec.containers[0].args}')
    pod=$(k -n "$NS" get pod -l app=vllm -o name 2>/dev/null | head -1)
    restarts=$(k -n "$NS" get "$pod" -o jsonpath='{.status.containerStatuses[0].restartCount}' 2>/dev/null)
    last=$(k -n "$NS" get "$pod" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}' 2>/dev/null)
    info "model label: ${label:-?}   ready: ${ready:-0}   restarts: ${restarts:-0}   last termination: ${last:-none}"
    [[ ${ready:-0} -ge 1 ]] && ok "vLLM Ready" || bad "vLLM not Ready → kubectl -n $NS describe $pod; logs (Vol 15 §7)"
    [[ $last == OOMKilled ]] && bad "last restart was OOMKilled → util/limits vs UMA (Vol 12, drill D02)"
    util=$(grep -o 'gpu-memory-utilization=[0-9.]*' <<<"$args" | cut -d= -f2)
    mlen=$(grep -o 'max-model-len=[0-9]*' <<<"$args" | cut -d= -f2)
    info "util ${util:-?}   max-model-len ${mlen:-?}"
    if [[ $label == r1-* || $label == qwq-* ]]; then
      grep -q 'reasoning-parser' <<<"$args" && ok "reasoning parser on" || bad "R1-style model without --reasoning-parser → <think> in content (D01)"
    fi
    [[ $label == *tools* ]] && { grep -q 'enable-auto-tool-choice' <<<"$args" && ok "tool calling on" || bad "tool model without --enable-auto-tool-choice (D07)"; }
    if [[ ${ready:-0} -ge 1 ]]; then
      served=$(k -n "$NS" exec deploy/vllm -- curl -s localhost:8000/v1/models | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null)
      info "served names: ${served:-?}  (clients must use one of these, D08)"
      run=$(metric 'vllm:num_requests_running'); wait_=$(metric 'vllm:num_requests_waiting')
      pre=$(metric 'vllm:num_preemptions_total')
      len=$(metric 'vllm:request_success_total\\{.*finished_reason="length"'); all=$(metric 'vllm:request_success_total')
      info "running ${run:-0}   waiting ${wait_:-0}   preemptions(total) ${pre:-0}   finished: ${all:-0} (length ${len:-0})"
      [[ ${wait_:-0} -gt 16 ]] && warn "queue > 16 → capacity (Vol 22) or lower max_tokens"
      [[ ${pre:-0} -gt 0 ]] && warn "preemptions > 0 → KV cache too small for the load (Vol 12)"
      if [[ ${all:-0} -gt 20 ]] && (( len * 5 > all )); then warn "> 20 % truncated (finish_reason=length) → client max_tokens / temperature (D06, Vol 38)"; fi
    fi
    errs=$(k -n "$NS" logs deploy/vllm --tail=400 2>/dev/null | grep -E 'ERROR|Traceback|CUDA error|out of memory|OOM' | tail -5)
    [[ -n $errs ]] && { warn "recent errors in vLLM log:"; sed 's/^/        /' <<<"$errs"; }
  fi
fi

if want gateway; then
  echo "── gateway (LiteLLM, Gateway API)"
  if k -n "$NS" get deploy litellm >/dev/null 2>&1; then
    [[ $(k -n "$NS" get deploy litellm -o jsonpath='{.status.readyReplicas}') -ge 1 ]] && ok "LiteLLM Ready" || bad "LiteLLM not Ready (Vol 28 §7)"
    to=$(k -n "$NS" get cm litellm-config -o jsonpath='{.data.config\.yaml}' | grep -E '^\s+timeout:' | head -1 | awk '{print $2}')
    [[ ${to:-0} -ge 900 ]] && ok "router timeout ${to}s" || bad "router timeout ${to:-?}s < 900 → long reasoning cut off (D04)"
    k -n "$NS" get sts litellm-db >/dev/null 2>&1 && ok "LiteLLM DB present" || warn "no litellm-db → no virtual keys (Vol 28)"
  else warn "LiteLLM not deployed (kubectl apply -k k8s/apps)"; fi
  for r in litellm open-webui; do
    t=$(k -n "$NS" get httproute "$r" -o jsonpath='{.spec.rules[0].timeouts.request}' 2>/dev/null)
    [[ -n $t ]] && info "HTTPRoute $r request timeout: $t"
  done
fi

if want secrets; then
  echo "── secrets"
  chk() {   # chk <secret> <key> <placeholder> <hint>
    v=$(k -n "$NS" get secret "$1" -o jsonpath="{.data.$2}" 2>/dev/null | base64 -d 2>/dev/null)
    if [[ -z $v ]]; then warn "secret $1 missing"; elif [[ $v == "$3" ]]; then warn "secret $1 still holds the placeholder → $4"; else ok "secret $1 set"; fi
  }
  chk hf-token token replace-me "gated models fail (D05); sync from Vault (Vol 32)"
  chk litellm-master-key key sk-lab-change-me "rotate via Vault (Vol 32)"
  chk open-webui-secret secret-key change-me-32-random-bytes "set a random key (Vol 27)"
  m=$(k -n "$NS" get secret litellm-master-key -o jsonpath='{.metadata.labels.app\.kubernetes\.io/managed-by}' 2>/dev/null)
  [[ $m == vault-sync ]] && ok "litellm-master-key managed by vault-sync" || info "litellm-master-key not (yet) managed by vault-sync"
fi

if want day2; then
  echo "── day-2 jobs"
  failed=$(k -n "$NS" get jobs -o jsonpath='{range .items[?(@.status.failed)]}{.metadata.name}{" "}{end}' 2>/dev/null)
  [[ -z $failed ]] && ok "no failed Jobs in $NS" || warn "failed Jobs: $failed → logs + Vol 33 runbook"
  for cj in vault-sync weights-verify webui-backup litellm-db-backup catalog-drift; do
    t=$(k -n "$NS" get cronjob "$cj" -o jsonpath='{.status.lastSuccessfulTime}' 2>/dev/null)
    k -n "$NS" get cronjob "$cj" >/dev/null 2>&1 || { info "$cj not installed"; continue; }
    info "$cj last success: ${t:-never}"
  done
fi

if want host; then
  echo "── host (only when run on the Spark)"
  if [[ -r /proc/meminfo ]] && command -v nvidia-smi >/dev/null; then
    avail=$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo)
    cache=$(awk '/^Cached:/ {printf "%.1f", $2/1048576}' /proc/meminfo)
    info "UMA MemAvailable ${avail} GiB, page cache ${cache} GiB"
    awk -v a="$avail" 'BEGIN {exit !(a < 8)}' && bad "MemAvailable < 8 GiB → OOM risk (drop caches, lower util; Vol 20)"
    nvidia-smi --query-gpu=name,temperature.gpu,power.draw,clocks_event_reasons.active --format=csv,noheader 2>/dev/null | sed 's/^/        /'
    x=$(sudo -n dmesg 2>/dev/null | grep -ci 'xid')
    [[ ${x:-0} -gt 0 ]] && bad "$x Xid lines in dmesg → 02 Vol 19 Xid table"
  else info "not on the Spark (no /proc/meminfo or nvidia-smi) — skipped"; fi
fi
summary
