#!/usr/bin/env bash
# Everything CI runs that doesn't need a Spark. Run from lab/:  tests/run-local-checks.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export ANSIBLE_CONFIG=$PWD/ansible.cfg
echo "== yamllint";      yamllint -s .
echo "== ansible-lint";  ansible-lint
echo "== syntax-check";  for p in playbooks/*.yml; do ansible-playbook --syntax-check "$p" >/dev/null; done
echo "== jinja katas";   kata=$(ansible-playbook playbooks/15-jinja-lab.yml); grep -q '7/7 Jinja katas passed' <<<"$kata"
echo "== mdns plugin";   out=$(ANSIBLE_INVENTORY_ENABLED=spark_mdns ansible-inventory -i tests/fixtures/spark.mdns.yml --list)
                         echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert sorted(d["spark"]["hosts"])==["dgx-spark-01","dgx-spark-02"], d'
echo "== drift report";  set +e; python3 tools/spark_drift_report.py tests/fixtures/drift-sample.json >/dev/null; rc=$?; set -e
                         [ "$rc" -eq 2 ] || { echo "expected exit 2 (drift), got $rc"; exit 1; }
echo "== python tools";  python3 -m py_compile tools/*.py roles/spark_facts/files/spark.fact inventory_plugins/*.py
echo "== shell";         bash -n roles/gpu_telemetry/files/*.sh roles/slurm_cluster/files/*.sh tools/*.sh
echo "ALL LOCAL CHECKS PASSED"
