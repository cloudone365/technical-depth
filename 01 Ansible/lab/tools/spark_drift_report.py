#!/usr/bin/env python3
"""
spark_drift_report.py — turn an Ansible check-mode run into a drift report.

Usage:
  ANSIBLE_STDOUT_CALLBACK=ansible.posix.json \
    ansible-playbook playbooks/20-drift-check.yml > .cache/drift.json
  python3 tools/spark_drift_report.py .cache/drift.json [--markdown out.md] [--prom out.prom]

Exit codes (so cron / AWX / CI can act on it):
  0  no drift
  2  drift detected (tasks that WOULD change)
  3  failures or unreachable hosts (drift unknown — treat as an incident)
"""
import argparse
import json
import sys
from collections import defaultdict


def load(path):
    with open(path) as fh:
        text = fh.read()
    # The json callback prints one document; tolerate leading warnings and
    # trailing text from other callbacks (profile_tasks, timer).
    start = text.find("{")
    doc, _end = json.JSONDecoder().raw_decode(text[start:])
    return doc


def analyse(doc):
    drift = defaultdict(list)     # host -> [(play, task, diff summary)]
    failed = defaultdict(list)
    for play in doc.get("plays", []):
        pname = play["play"]["name"]
        for task in play.get("tasks", []):
            tname = task["task"]["name"]
            for host, res in task.get("hosts", {}).items():
                if res.get("unreachable"):
                    failed[host].append((pname, tname, "UNREACHABLE"))
                elif res.get("failed") and not res.get("ignore_errors"):
                    failed[host].append((pname, tname, res.get("msg", "failed")[:200]))
                elif res.get("changed"):
                    drift[host].append((pname, tname, summarise_diff(res)))
    stats = doc.get("stats", {})
    return drift, failed, stats


def summarise_diff(res):
    diffs = res.get("diff")
    if not diffs:
        return ""
    if isinstance(diffs, dict):
        diffs = [diffs]
    parts = []
    for d in diffs:
        if "before_header" in d or "after_header" in d:
            parts.append(d.get("after_header") or d.get("before_header"))
        elif "before" in d and "after" in d and isinstance(d["before"], dict):
            changed = [k for k in d["after"] if d["before"].get(k) != d["after"].get(k)]
            parts.append("keys: " + ",".join(changed))
    return "; ".join(p for p in parts if p)


def to_markdown(drift, failed, stats):
    lines = ["# DGX Spark drift report", ""]
    hosts = sorted(set(stats) | set(drift) | set(failed))
    lines += ["| Host | Drifted tasks | Failures |", "|---|---|---|"]
    for h in hosts:
        lines.append(f"| {h} | {len(drift.get(h, []))} | {len(failed.get(h, []))} |")
    for h in hosts:
        if drift.get(h) or failed.get(h):
            lines += ["", f"## {h}"]
            for p, t, d in drift.get(h, []):
                lines.append(f"- DRIFT `{t}` ({p}) {d}")
            for p, t, m in failed.get(h, []):
                lines.append(f"- FAIL `{t}` ({p}) {m}")
    return "\n".join(lines) + "\n"


def to_prom(drift, failed, stats):
    out = [
        "# HELP spark_config_drift_tasks Tasks that would change in check mode",
        "# TYPE spark_config_drift_tasks gauge",
    ]
    for h in sorted(set(stats) | set(drift)):
        out.append(f'spark_config_drift_tasks{{host="{h}"}} {len(drift.get(h, []))}')
    out += [
        "# HELP spark_config_drift_failures Failed/unreachable tasks during drift check",
        "# TYPE spark_config_drift_failures gauge",
    ]
    for h in sorted(set(stats) | set(failed)):
        out.append(f'spark_config_drift_failures{{host="{h}"}} {len(failed.get(h, []))}')
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file")
    ap.add_argument("--markdown")
    ap.add_argument("--prom", help="write node_exporter textfile metrics here")
    a = ap.parse_args()

    drift, failed, stats = analyse(load(a.json_file))
    md = to_markdown(drift, failed, stats)
    print(md)
    if a.markdown:
        open(a.markdown, "w").write(md)
    if a.prom:
        open(a.prom, "w").write(to_prom(drift, failed, stats))

    if any(failed.values()):
        sys.exit(3)
    if any(drift.values()):
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
