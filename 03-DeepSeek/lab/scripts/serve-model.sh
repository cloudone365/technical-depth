#!/usr/bin/env bash
# Switch the served chat model (Chapter 08): prefetch → drop page cache (on the Spark; DROP_CACHES=1 over ssh) → apply
# overlay → wait → smoke test, on the root (context spark-root). Replaces the model
# in Deployment vllm; k8s/multi/ adds more next to it within the 0.72 util budget.
#   scripts/serve-model.sh list
#   scripts/serve-model.sh r1-32b-fp8
source "$(dirname "$0")/lib.sh"
name=${1:?usage: serve-model.sh <name>|list}
if [[ $name == list ]]; then
  python3 - "$DS_DIR/models.yaml" <<'PY'
import sys, yaml
for m in yaml.safe_load(open(sys.argv[1]))["models"]:
    alone = "  (runs alone)" if m.get("alone") else ""
    print(f"  {m['name']:18} util {m['util']:.2f}  mem {m['mem']:>5}  {m['weights']:>6}  {m['hf']}{alone}")
PY
  exit 0
fi
hf=$(model_field "$name" hf); [[ -n $hf ]] || { bad "unknown model $name (scripts/serve-model.sh list)"; exit 2; }
# Wall 3: the prefetch pod needs 4Gi of serving-budget. After an `alone` model (r1-32b, 87Gi)
# the quota is full and the Job can never create its pod — park vLLM first in that case.
quota_free_mi() {
  k -n $NS get resourcequota serving-budget -o jsonpath='{.status.used.limits\.memory} {.status.hard.limits\.memory}' 2>/dev/null \
    | python3 -c 'import sys,re
u,h=sys.stdin.read().split()
def mi(x):
    n=float(re.sub("[^0-9.]","",x)); return n*1024 if x.endswith("Gi") else n if x.endswith("Mi") else n/1048576
print(int(mi(h)-mi(u)))'
}
free_mi=$(quota_free_mi || echo 999999)
if (( free_mi < 4096 )); then
  info "serving-budget has only ${free_mi}Mi free (an 'alone' model is up): parking vllm so the prefetch pod fits"
  k -n $NS scale deploy vllm --replicas=0 >/dev/null
  k -n $NS wait pod -l app=vllm --for=delete --timeout=5m >/dev/null || true
fi
info "prefetching $hf into model-cache"
k -n $NS delete job model-prefetch --ignore-not-found >/dev/null
rev=$(model_field "$name" revision); rev=${rev:-main}
sed -e "s|value: Qwen/Qwen2.5-0.5B-Instruct|value: $hf|" -e "s|{name: REVISION, value: main}|{name: REVISION, value: \"$rev\"}|" \
  "$K8S_LAB/manifests/root-llms/60-storage/model-prefetch-job.yaml" | k apply -f - >/dev/null
k -n $NS wait --for=condition=complete job/model-prefetch --timeout=60m >/dev/null && ok "weights cached" || { bad "prefetch failed: kubectl --context spark-root -n $NS logs job/model-prefetch"; exit 1; }
# The page cache lives on the Spark: dropping it only works there (or over ssh).
if [[ -e /proc/sys/vm/drop_caches ]] && { [[ -w /proc/sys/vm/drop_caches ]] || sudo -n true 2>/dev/null; }; then
  sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches' && info "page cache dropped (UMA headroom for the load)"
elif [[ ${DROP_CACHES:-0} == 1 ]]; then
  ssh dgx-spark-1 "sudo -n sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'" && info "page cache dropped on dgx-spark-1" \
    || warn "could not drop the page cache over ssh (needs passwordless sudo); on the Spark: sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'"
else
  info "page cache not dropped from this machine; for a cold load run on the Spark: sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'"
fi
k -n $NS scale deploy sglang trtllm --replicas=0 2>/dev/null || true
k apply -k "$DS_DIR/k8s/models/$name" >/dev/null
info "waiting for vLLM ($name) — first start compiles CUDA graphs"
k -n $NS rollout status deploy/vllm --timeout=45m && ok "vLLM ready with $name" || { bad "rollout failed: kubectl --context spark-root -n $NS logs deploy/vllm"; exit 1; }
out=$(k -n $NS exec deploy/vllm -- curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d "{\"model\":\"$name\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 12*12? Answer with the number only.\"}],\"max_tokens\":1024}")
# Reasoning models often ignore "number only" and explain; look for 144 anywhere in the answer.
content=$(python3 -c 'import json,sys; m=json.loads(sys.stdin.read())["choices"][0]["message"]; c=(m.get("content") or "").strip(); print("144" in c); print(c.replace("\n"," ")[:80]); print(len(m.get("reasoning_content") or ""))' <<<"$out" 2>/dev/null)
hit=$(sed -n 1p <<<"$content"); ans=$(sed -n 2p <<<"$content"); rlen=$(sed -n 3p <<<"$content")
[[ $hit == True ]] && ok "answer contains 144: ${ans}…  (reasoning chars: $rlen)" || bad "unexpected answer: ${out:0:300}"
summary
