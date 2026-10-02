#!/usr/bin/env bash
# Qwen-specific fault drills (Volume 24). Same verbs as 03's breakfix.sh.
#   scripts/breakfix.sh list | inject Q0x | hint Q0x | answer Q0x | reset Q0x
here="$(cd "$(dirname "$0")/.." && pwd)"
export DS_DIR="$here"
# shellcheck source=/dev/null
source "$here/../../03 DeepSeek/lab/scripts/lib.sh"
declare -A TITLE HINT ANSWER
TITLE[Q01]="128K context requested, vLLM will not start"
HINT[Q01]="kubectl -n $NS logs deploy/vllm | grep -i max_model_len ; compare with k8s/models/qwen2.5-7b-128k"
ANSWER[Q01]="Qwen2.5 was trained to 32K. Beyond that you must enable YaRN (rope_scaling factor 4, original_max_position_embeddings 32768) — catalog entry qwen2.5-7b-128k. Overriding the limit with VLLM_ALLOW_LONG_MAX_MODEL_LEN without YaRN produces garbage past 32K (Vol 02)."
TITLE[Q02]="Code completions are nonsense in the editor"
HINT[Q02]="Which model and which endpoint does the plug-in call? Run fim_eval.py in both modes against both coder entries."
ANSWER[Q02]="FIM prompts (<|fim_prefix|>…) were sent to the INSTRUCT model through /v1/chat/completions, so the chat template wrapped the special tokens. Use the base coder model on /v1/completions with the raw FIM prompt (Vol 03)."
TITLE[Q03]="Math model returns HTTP 400 on longer problems"
HINT[Q03]="Read the 400 body; check max_model_len of qwen2.5-math-7b in models.yaml."
ANSWER[Q03]="Qwen2.5-Math-7B has a 4,096-token context. prompt + max_tokens must fit. Lower max_tokens (TIR rounds add up), or use qwen2.5-7b / QwQ for long reasoning (Vol 04)."
TITLE[Q04]="ms-swift job hangs at download, then fails"
HINT[Q04]="kubectl -n batch logs job/swift-sft | grep -iE 'modelscope|download'"
ANSWER[Q04]="ms-swift downloads from ModelScope by default. Without USE_HF=1 the job reaches modelscope.cn (blocked or slow from many networks). Set USE_HF=1 (and HF_TOKEN for gated models) (Vol 06)."
cmd=${1:-list}; id=${2:-}
case $cmd in
  list) for i in $(printf '%s\n' "${!TITLE[@]}" | sort); do printf '  %s  %s\n' "$i" "${TITLE[$i]}"; done ;;
  hint|answer) [[ -n ${TITLE[$id]:-} ]] || { bad "unknown $id"; exit 2; }
    [[ $cmd == hint ]] && echo "${HINT[$id]}" || echo "${ANSWER[$id]}" ;;
  inject)
    case $id in
      Q01) k apply -k "$here/breakfix/Q01-128k-without-yarn" ;;
      Q02) info "run: python3 tools/fim_eval.py --url http://localhost:8000 --model qwen2.5-coder-7b --mode fim   (instruct model, FIM prompt)" ;;
      Q03) info "run: python3 tools/tir_math.py --url http://localhost:8000 --model qwen2.5-math-7b --mode tir --max-tokens 4096 --limit 3" ;;
      Q04) yq 'del(.spec.template.spec.containers[0].env[] | select(.name == "USE_HF"))' "$here/k8s/jobs/swift-sft.yaml" \
             | k apply -f - && k -n batch patch job swift-sft -p '{"spec":{"suspend":false}}' ;;
      *) bad "unknown $id"; exit 2 ;;
    esac
    echo "Injected $id: ${TITLE[$id]}" ;;
  reset)
    case $id in
      Q01) k apply -k "$here/k8s/models/qwen2.5-7b" ;;
      Q04) k -n batch delete job swift-sft --ignore-not-found ;;
    esac
    true ;;
  *) echo "usage: $0 list|inject ID|hint ID|answer ID|reset ID"; exit 2 ;;
esac
