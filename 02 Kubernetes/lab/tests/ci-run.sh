#!/usr/bin/env bash
# Run a command; on failure, surface its last 40 output lines as one GitHub
# annotation (readable from the PR checks page and the API, no log download needed).
# PodSecurity "would violate" warnings are left out so they cannot hide the error.
set -uo pipefail
log=$(mktemp)
"$@" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
if [[ $rc -ne 0 ]]; then
  body=$(grep -v "^Warning: would violate PodSecurity" "$log" | tail -40 | sed 's/%/%25/g' | sed ':a;N;$!ba;s/\n/%0A/g')
  echo "::error title=$(basename "$1") failed (rc=$rc)::${body}"
fi
exit "$rc"
