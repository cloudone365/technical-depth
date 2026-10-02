#!/usr/bin/env bash
# Head-to-head of Qwen catalog models with 03's harness (Volumes 01, 13). Results in results/.
#   SUITES="math json" scripts/compare-models.sh qwen2.5-7b qwen2.5-14b qwen2.5-32b-awq
here="$(cd "$(dirname "$0")/.." && pwd)"
DS_DIR="$here" exec "$here/../../03 DeepSeek/lab/scripts/compare-models.sh" "$@"
