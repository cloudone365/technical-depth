#!/usr/bin/env bash
# Switch the served chat model (Volume 19): prefetch → drop page cache → apply
# overlay → wait → smoke test. One chat model at a time on one Spark.
#   scripts/serve-model.sh list
#   scripts/serve-model.sh r1-32b-fp8
source "$(dirname "$0")/lib.sh"
name=${1:?usage: serve-model.sh <name>|list}
if [[ $name == list ]]; then
  python3 - "$DS_DIR/models.yaml" <<'PY'
import sys, yaml
for m in yaml.safe_load(open(sys.argv[1]))["models"]:
    print(f"  {m['name']:18} util {m['util']:<5} {m['weights']:>6}  {m['hf']}")
PY
  exit 0
fi
hf=$(model_field "$name" hf); [[ -n $hf ]] || { bad "unknown model $name (scripts/serve-model.sh list)"; exit 2; }
info "prefetching $hf into model-cache"
k -n $NS delete job model-prefetch --ignore-not-found >/dev/null
rev=$(model_field "$name" revision); rev=${rev:-main}
sed -e "s|value: Qwen/Qwen2.5-0.5B-Instruct|value: $hf|" -e "s|{name: REVISION, value: main}|{name: REVISION, value: \"$rev\"}|" \
  "$K8S_LAB/manifests/60-storage/model-prefetch-job.yaml" | k apply -f - >/dev/null
k -n $NS wait --for=condition=complete job/model-prefetch --timeout=60m >/dev/null && ok "weights cached" || { bad "prefetch failed: kubectl -n $NS logs job/model-prefetch"; exit 1; }
if [[ -w /proc/sys/vm/drop_caches ]] || sudo -n true 2>/dev/null; then
  sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches' && info "page cache dropped (UMA headroom for the load)"
fi
k -n $NS scale deploy sglang trtllm --replicas=0 2>/dev/null || true
k apply -k "$DS_DIR/k8s/models/$name" >/dev/null
info "waiting for vLLM ($name) — first start compiles CUDA graphs"
k -n $NS rollout status deploy/vllm --timeout=45m && ok "vLLM ready with $name" || { bad "rollout failed: kubectl -n $NS logs deploy/vllm"; exit 1; }
out=$(k -n $NS exec deploy/vllm -- curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d "{\"model\":\"$name\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 12*12? Answer with the number only.\"}],\"max_tokens\":1024}")
content=$(python3 -c 'import json,sys; m=json.loads(sys.stdin.read())["choices"][0]["message"]; print((m.get("content") or "").strip()[:80]); print(len(m.get("reasoning_content") or ""))' <<<"$out" 2>/dev/null)
ans=$(head -1 <<<"$content"); rlen=$(tail -1 <<<"$content")
[[ $ans == *144* ]] && ok "answer: $ans  (reasoning chars: $rlen)" || bad "unexpected answer: ${out:0:300}"
summary
