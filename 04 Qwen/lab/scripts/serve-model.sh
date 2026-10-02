#!/usr/bin/env bash
# Serve a Qwen catalog model with 03's serve-model.sh (prefetch → drop cache → overlay → smoke test).
#   scripts/serve-model.sh list | <name>
here="$(cd "$(dirname "$0")/.." && pwd)"
DS_DIR="$here" exec "$here/../../03 DeepSeek/lab/scripts/serve-model.sh" "$@"
