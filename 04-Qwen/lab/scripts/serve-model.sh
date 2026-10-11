#!/usr/bin/env bash
# Serve a Qwen catalog model with vLLM on spark-root (Chapter 06): budget check →
# prefetch into model-cache → apply the overlay → wait → smoke test.
#   scripts/serve-model.sh list
#   scripts/serve-model.sh qwen3-8b                       main model behind svc/vllm (replaces the current one)
#   scripts/serve-model.sh qwen3-4b-instruct --side       next to it, as deploy/svc vllm-qwen3-4b-instruct
#   scripts/serve-model.sh qwen3-4b-instruct --side --render   print the manifests, apply nothing
#   scripts/serve-model.sh qwen3-0.6b --sandbox           dev-llms vCluster, namespace llm-sandbox
#   scripts/serve-model.sh --stop-side qwen3-4b-instruct
# FORCE=1 skips the Σ util ≤ 0.72 check; DROP_CACHES=1 drops the Spark's page cache first (ssh dgx-spark-1).
source "$(dirname "$0")/lib.sh"

name=${1:?usage: serve-model.sh <name> [--side|--sandbox] [--render] | list | --stop-side <name>}
mode=main; render=0
for a in "${@:2}"; do
  case $a in
    --side) mode=side ;;
    --sandbox) mode=sandbox ;;
    --render) render=1 ;;
    *) bad "unknown option $a"; exit 2 ;;
  esac
done

if [[ $name == list ]]; then
  python3 - "$QW_DIR/models.yaml" <<'PY'
import sys, yaml
cat = yaml.safe_load(open(sys.argv[1]))
for m in cat["models"]:
    print(f"  {m['name']:20} util {m['util']:<5} {m['mem']:>5}  {m['weights']:>7}  {m['hf']}")
print("\n  sets (main + --side):")
for s in cat["sets"]:
    print(f"  {s['name']:20} {' + '.join(s['models'])}{'  (with the 03 apps)' if s['apps'] else ''}")
PY
  exit 0
fi
if [[ $name == --stop-side ]]; then
  side=vllm-${2:?usage: serve-model.sh --stop-side <name>}
  k -n "$NS" delete deploy,svc "$side" --ignore-not-found && ok "$side removed"
  exit 0
fi

hf=$(model_field "$name" hf); [[ -n $hf ]] || { bad "unknown model $name (scripts/serve-model.sh list)"; exit 2; }
util=$(model_field "$name" util)
deploy=vllm; [[ $mode == side ]] && deploy=vllm-$name

if [[ $render == 1 ]]; then
  case $mode in
    main) "$KUBECTL" kustomize "$QW_DIR/k8s/models/$name" ;;
    side) side_render "$name" ;;
    sandbox) "$KUBECTL" kustomize "$QW_DIR/k8s/models/$name/sandbox" ;;
  esac
  exit 0
fi

smoke() {   # smoke <kubectl-wrapper> <namespace> <deployment>
  local out
  out=$("$1" -n "$2" exec "deploy/$3" -c vllm -- python3 -c '
import json, sys, urllib.request
body = {"model": sys.argv[1], "max_tokens": 2048,
        "messages": [{"role": "user", "content": "What is 12*12? Answer with the number only."}]}
req = urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions", json.dumps(body).encode(),
                             {"Content-Type": "application/json"})
m = json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]
print((m.get("content") or "").strip().replace("\n", " ")[:80])
print(len(m.get("reasoning_content") or m.get("reasoning") or ""))
' "$name" 2>&1) || { bad "smoke request failed: ${out:0:300}"; return; }
  local ans rlen
  ans=$(head -1 <<<"$out"); rlen=$(tail -1 <<<"$out")
  [[ $ans == *144* ]] && ok "answer: $ans  (reasoning chars: $rlen)" || bad "unexpected answer: ${out:0:300}"
}

if [[ $mode == sandbox ]]; then
  [[ -d $QW_DIR/k8s/models/$name/sandbox ]] || { bad "$name has no sandbox overlay (models.yaml: sandbox:)"; exit 2; }
  kdl apply -k "$QW_DIR/k8s/models/$name/sandbox" >/dev/null
  info "waiting for vLLM ($name) in dev-llms/llm-sandbox — downloads the weights into an emptyDir"
  kdl -n llm-sandbox rollout status deploy/vllm --timeout=30m && ok "sandbox vLLM ready with $name" \
    || { bad "rollout failed: kubectl --context dev-llms -n llm-sandbox logs deploy/vllm"; exit 1; }
  smoke kdl llm-sandbox vllm
  summary; exit $?
fi

# Σ util of everything else that runs in llm-serving + this model must stay ≲ 0.72 (02-Kubernetes Chapter 14)
others=$(running_util "$deploy")
if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) + float(sys.argv[2]) <= float(sys.argv[3]) + 1e-9 else 1)" "$others" "$util" "$UTIL_BUDGET"; then
  ok "budget: running $others + $name $util ≤ $UTIL_BUDGET"
elif [[ ${FORCE:-0} == 1 ]]; then
  warn "budget: running $others + $name $util > $UTIL_BUDGET (FORCE=1)"
else
  bad "budget: running $others + $name $util > $UTIL_BUDGET — park a model first (--stop-side, or scale a Deployment to 0)"; exit 1
fi
if [[ $mode == main ]] && k -n "$NS" get scaledobject vllm -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}' 2>/dev/null | grep -q '^0$'; then
  warn "KEDA keeps deploy/vllm at 0 (training window?): kubectl --context spark-root -n $NS annotate scaledobject vllm autoscaling.keda.sh/paused-replicas-"
fi

info "prefetching $hf into model-cache"
k -n "$NS" delete job model-prefetch --ignore-not-found >/dev/null
rev=$(model_field "$name" revision); rev=${rev:-main}
sed -e "s|value: Qwen/Qwen2.5-0.5B-Instruct|value: $hf|" -e "s|{name: REVISION, value: main}|{name: REVISION, value: \"$rev\"}|" \
  "$K8S_LAB/manifests/root-llms/60-storage/model-prefetch-job.yaml" | k apply -f - >/dev/null
k -n "$NS" wait --for=condition=complete job/model-prefetch --timeout=60m >/dev/null && ok "weights cached" \
  || { bad "prefetch failed: kubectl --context spark-root -n $NS logs job/model-prefetch"; exit 1; }
if [[ ${DROP_CACHES:-0} == 1 ]]; then
  ssh dgx-spark-1 "sudo -n sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'" && info "page cache dropped (UMA headroom for the load)"
fi

if [[ $mode == side ]]; then
  side_render "$name" | k apply -f - >/dev/null
else
  k apply -k "$QW_DIR/k8s/models/$name" >/dev/null
fi
info "waiting for $deploy ($name) — the first start compiles CUDA graphs"
k -n "$NS" rollout status "deploy/$deploy" --timeout=45m && ok "$deploy ready with $name" \
  || { bad "rollout failed: kubectl --context spark-root -n $NS logs deploy/$deploy"; exit 1; }
smoke k "$NS" "$deploy"
if model_field "$name" args | grep -q tool-call-parser; then
  info "tool calling: kubectl --context spark-root -n $NS port-forward svc/$deploy 8000:8000, then tools/fc_check.py --model $name"
fi
summary
