#!/usr/bin/env bash
# Drift cycle: detect → report → publish metrics → (optionally) heal SAFE drift → re-check.
#
#   tools/drift-cycle.sh                 # detect + report only (exit 0 clean, 2 drift, 3 failures)
#   AUTO_HEAL=1 tools/drift-cycle.sh     # also re-apply safe tags on drifted hosts only
#
# Run from cron/systemd on the control node, or as an AWX job (Volume 20).
set -uo pipefail
cd "$(dirname "$0")/.."
export ANSIBLE_CONFIG=$PWD/ansible.cfg
OUT="${SPARK_LAB_CACHE:-.cache}/drift"; mkdir -p "$OUT"
TS=$(date -u +%Y%m%dT%H%M%SZ)
SAFE_TAGS=${SAFE_TAGS:-baseline,telemetry_node}      # never auto-heal fabric/driver/kubernetes
BECOME_ARGS=${BECOME_ARGS:-}                          # e.g. "--become-password-file /path" for unattended runs

run_check() {
  ANSIBLE_STDOUT_CALLBACK=ansible.posix.json ANSIBLE_CALLBACKS_ENABLED= \
    ansible-playbook playbooks/20-drift-check.yml $BECOME_ARGS > "$OUT/$1.json" 2> "$OUT/$1.stderr"
  python3 tools/spark_drift_report.py "$OUT/$1.json" \
    --markdown "$OUT/$1.md" --prom "$OUT/spark_config_drift.prom" --drifted-hosts "$OUT/$1.hosts"
}

run_check "check-$TS"; rc=$?
echo "drift check exit=$rc  report=$OUT/check-$TS.md"

# Publish metrics to the monitoring host's textfile collector (Volume 09 dashboard shows it)
ansible monitoring -b -m ansible.builtin.copy \
  -a "src=$OUT/spark_config_drift.prom dest=/var/lib/prometheus/node-exporter/spark_config_drift.prom mode=0644" \
  $BECOME_ARGS >/dev/null || echo "WARN: could not publish drift metrics"

if [[ $rc -eq 2 && "${AUTO_HEAL:-0}" == "1" ]]; then
  HOSTS=$(cat "$OUT/check-$TS.hosts")
  echo "auto-heal: tags=$SAFE_TAGS hosts=$HOSTS"
  ansible-playbook playbooks/01-baseline.yml playbooks/04-telemetry.yml \
    --limit "$HOSTS" --tags "$SAFE_TAGS" $BECOME_ARGS > "$OUT/heal-$TS.log" 2>&1
  run_check "recheck-$TS"; rc=$?
  echo "post-heal exit=$rc  report=$OUT/recheck-$TS.md"
fi
exit $rc
