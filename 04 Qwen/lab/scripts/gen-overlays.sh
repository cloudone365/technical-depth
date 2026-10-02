#!/usr/bin/env bash
# Generate k8s/models/<name>/ from models.yaml with 03's generator (Volume 11, 21).
#   scripts/gen-overlays.sh [--check]
here="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$here/../../03 DeepSeek/lab/scripts/gen_overlays.py" --lab "$here" "$@"
