#!/usr/bin/env bash
# Head-to-head on ONE Spark (Volumes 34-37, 41): serve each catalog model in turn,
# run the same eval suites through a port-forward, save results/<model>.json,
# then print one comparison table. One chat model at a time (unified memory).
#   scripts/compare-models.sh r1-7b r1-llama-8b llama-3.1-8b
#   SUITES="math json" LIMIT=20 POWER_W=180 API_PRICE_OUT=2.19 scripts/compare-models.sh r1-32b-fp8 qwen2.5-32b-awq
source "$(dirname "$0")/lib.sh"
[[ $# -ge 1 ]] || { echo "usage: compare-models.sh <catalog-name>..."; exit 2; }
SUITES=${SUITES:-"math json"}   # the code suite runs model-written code: use k8s/jobs/eval.yaml (sandboxed pod)
LIMIT=${LIMIT:-0}; PORT=${PORT:-18000}
mkdir -p "$DS_DIR/results"
outs=()
for name in "$@"; do
  "$DS_DIR/scripts/serve-model.sh" "$name" || { bad "$name did not come up — skipped"; continue; }
  k -n "$NS" port-forward svc/vllm "$PORT:8000" >/dev/null 2>&1 & pf=$!; sleep 3
  extra=(); [[ -n ${API_PRICE_OUT:-} ]] && extra+=(--api-price-out "$API_PRICE_OUT")
  # shellcheck disable=SC2086  # SUITES is a word list on purpose
  python3 "$DS_DIR/tools/eval_harness.py" --url "http://127.0.0.1:$PORT" --model "$name" --suites $SUITES \
    --limit "$LIMIT" --concurrency "${CONCURRENCY:-8}" --max-tokens "${MAX_TOKENS:-8192}" \
    --power-w "${POWER_W:-200}" "${extra[@]}" --out "$DS_DIR/results/$name.json" | tail -4
  kill "$pf" 2>/dev/null; wait "$pf" 2>/dev/null
  outs+=("$DS_DIR/results/$name.json")
done
echo
python3 "$DS_DIR/tools/eval_harness.py" --report "${outs[@]}"
