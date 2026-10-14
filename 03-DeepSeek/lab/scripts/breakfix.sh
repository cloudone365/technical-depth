#!/usr/bin/env bash
# DeepSeek serving drills (Chapters 17, 19). Same workflow as 02's breakfix.sh.
#   scripts/breakfix.sh list | inject D01 | hint D01 | answer D01 | reset D01
if (( BASH_VERSINFO[0] < 4 )); then
  echo "$0 needs bash >= 4 (this is bash $BASH_VERSION). macOS ships 3.2: brew install bash, then open a new terminal." >&2
  exit 1
fi
source "$(dirname "$0")/lib.sh"
declare -A TITLE BASE HINT ANSWER
TITLE[D01]="Chain of thought leaks into the answer";   BASE[D01]=r1-7b
HINT[D01]="Look at the response JSON: which field holds the <think> text?"
ANSWER[D01]="vLLM started without --reasoning-parser=deepseek_r1, so everything lands in message.content and reasoning_content is empty. Open WebUI shows raw thinking. Fix: add the parser (models.yaml → gen_overlays.py)."
TITLE[D02]="vLLM will not start: memory utilisation";   BASE[D02]=r1-7b
HINT[D02]="kubectl --context spark-root logs deploy/vllm | grep -i memory ; free -g on the Spark"
ANSWER[D02]="--gpu-memory-utilization=0.95 asks for 95 % of the ~121.7 GiB the GB10 reports — memory the OS, Kubernetes, page cache and every other pod also use. vLLM fails its free-memory check (or the node hits MemoryPressure), and the pod (28 Gi request = limit) could not hold it anyway. Fix: keep Σ utilisation of all engines running together ≲ 0.72 (the ~90 GiB LLM pool); drop page cache before loading."
TITLE[D03]="vLLM will not start: context too long";     BASE[D03]=r1-32b
HINT[D03]="Compare max-model-len with the KV-cache token count vLLM logs at startup; tools/model_math.py r1-32b"
ANSWER[D03]="One sequence of 131,072 tokens needs 131072 × 256 KiB = 32 GiB of KV, but util 0.70 (≈85 GiB) leaves only ≈21 GiB after the ≈61 GiB of BF16 weights and the CUDA graphs. More util is not an option: 0.70 is already the most one model may take. Fix: --max-model-len 16384, or FP8 weights (r1-32b-fp8) / FP8 KV cache (--kv-cache-dtype fp8)."
TITLE[D04]="Long reasoning answers cut off at the gateway"; BASE[D04]=""
HINT[D04]="Time a long request through LiteLLM vs directly to vLLM. Which hop gives up first?"
ANSWER[D04]="LiteLLM router timeout was lowered to 30 s; R1 chains of thought routinely run minutes. Fix: timeout/request_timeout ≥ 900 in litellm-config AND in every hop (HTTPRoute timeouts.request, client)."
TITLE[D05]="Gated model never becomes Ready";          BASE[D05]=llama-3.1-8b
HINT[D05]="kubectl --context spark-root logs job/model-prefetch ; kubectl --context spark-root logs deploy/vllm | grep -i -E '401|gated'"
ANSWER[D05]="meta-llama models are gated: there is no hf-token Secret (the 02 base ships none) or it still holds a placeholder. Fix: accept the licence on Hugging Face, store the token in Vault on vault01 (vault kv put kv/spark-lab/hf token=hf_…) and let vault-sync update the Secret hf-token (Chapter 15)."
TITLE[D06]="R1 loops forever at temperature 0";        BASE[D06]=""
HINT[D06]="Compare completion_tokens and finish_reason at temperature 0 vs 0.6."
ANSWER[D06]="Greedy decoding makes R1-style models repeat inside <think> until max_tokens (finish_reason=length). DeepSeek recommends temperature 0.5–0.7 (0.6), top_p 0.95, no system prompt tricks. Fix the client default."
TITLE[D07]="Agent gets 400 / no tool_calls";            BASE[D07]=qwen2.5-7b-tools
HINT[D07]="python3 tools/agent_tools.py … and read the HTTP error body"
ANSWER[D07]="tool_choice=auto needs --enable-auto-tool-choice plus the model's parser (--tool-call-parser=hermes for Qwen2.5, llama3_json for Llama 3.1). Without them vLLM rejects the request or returns prose."
TITLE[D08]="404 model not found";                       BASE[D08]=""
HINT[D08]="curl /v1/models — what name does the server actually serve?"
ANSWER[D08]="Clients must use the --served-model-name (e.g. r1-32b-fp8), not the Hugging Face repo id, unless both are served. LiteLLM aliases (reasoning, reasoning-fast) decouple clients from this."
cmd=${1:-list}; id=${2:-}
case $cmd in
  list) for i in $(printf '%s\n' "${!TITLE[@]}" | sort); do printf '  %s  %s\n' "$i" "${TITLE[$i]}"; done ;;
  hint) echo "${HINT[$id]}" ;;
  answer) echo "${ANSWER[$id]}" ;;
  inject)
    case $id in
      D05) k -n $NS delete secret hf-token --ignore-not-found   # no token: the gated download must fail
           k apply -k "$DS_DIR/breakfix/$id"-* ;;
      D01|D02|D03|D07) k apply -k "$DS_DIR/breakfix/$id"-* ;;
      D04) k -n $NS patch cm litellm-config --type merge -p "$(k -n $NS get cm litellm-config -o json | python3 -c 'import json,sys; c=json.load(sys.stdin)["data"]["config.yaml"]; print(json.dumps({"data":{"config.yaml":c.replace("timeout: 900","timeout: 30").replace("request_timeout: 900","request_timeout: 30")}}))')" && k -n $NS rollout restart deploy/litellm ;;
      D06) info "run: python3 tools/eval_harness.py --url http://localhost:8000 --model r1-7b --suites math --limit 5 --temperature 0 --max-tokens 8192" ;;
      D08) info "run: python3 tools/eval_harness.py --url http://localhost:8000 --model deepseek-ai/DeepSeek-R1-Distill-Qwen-7B --suites json --limit 2" ;;
      *) bad "unknown $id"; exit 2 ;;
    esac
    echo "Injected $id: ${TITLE[$id]}" ;;
  reset)
    if [[ -n ${BASE[$id]} ]]; then k apply -k "$DS_DIR/k8s/models/${BASE[$id]}"; fi
    [[ $id == D04 ]] && k apply -f "$DS_DIR/k8s/apps/litellm.yaml" && k -n $NS rollout restart deploy/litellm
    true ;;
  *) echo "usage: $0 list|inject ID|hint ID|answer ID|reset ID"; exit 2 ;;
esac
