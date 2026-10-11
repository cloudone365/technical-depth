#!/usr/bin/env bash
# Everything CI runs for the 04-Qwen lab without a GPU (Chapter 14).
#   tests/run-local-checks.sh     needs: python3 + pyyaml, yamllint, kubectl (kustomize), kubeconform, shellcheck
set -euo pipefail
cd "$(dirname "$0")/.."
KUBECTL=${KUBECTL:-kubectl}
kc() { kubeconform -strict -kubernetes-version 1.32.0 -summary "$@"; }
T=$(mktemp)
expect() {   # expect <pattern> <command…>: run it, fail loudly unless the output contains <pattern>
  local pat=$1; shift
  "$@" >"$T" 2>&1 || { cat "$T"; return 1; }
  grep -qF -- "$pat" "$T" || { cat "$T"; echo "expected output to contain: $pat"; return 1; }
}
refuse() {   # refuse <pattern> <command…>: the command must FAIL and print <pattern>
  local pat=$1; shift
  if "$@" >"$T" 2>&1; then cat "$T"; echo "expected a failure"; return 1; fi
  grep -qF -- "$pat" "$T" || { cat "$T"; echo "expected output to contain: $pat"; return 1; }
}
pids=(); cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; rm -f "$T"; }; trap cleanup EXIT

echo "== yamllint"
yamllint -s -d '{extends: default, rules: {line-length: {max: 200, level: warning}, document-start: disable,
  comments: {min-spaces-from-content: 1}, comments-indentation: disable, truthy: {check-keys: false},
  braces: {max-spaces-inside: 1}, brackets: {max-spaces-inside: 1},
  indentation: {spaces: consistent, indent-sequences: whatever}}}' .
echo "== catalog rules + overlays in sync"
python3 scripts/gen_overlays.py --check
echo "== kustomize + kubeconform (overlays of the 02 vLLM base, sandbox overlays, side render, Jobs)"
for d in k8s/models/*/ k8s/models/*/sandbox/; do "$KUBECTL" kustomize "$d" | kc -; done
for m in qwen3-4b-instruct qwen3-vl-8b; do KUBECTL=$KUBECTL scripts/serve-model.sh "$m" --side --render | kc -; done
kc k8s/jobs/*.yaml
echo "== pod rules: pinned image, request = limit memory, readiness on serving, Kueue + priority on Jobs"
KUBECTL=$KUBECTL python3 - <<'PY'
import glob, os, subprocess, yaml
def docs(cmd):
    return [d for d in yaml.safe_load_all(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout) if d]
objs = []
for d in sorted(glob.glob("k8s/models/*/")) + sorted(glob.glob("k8s/models/*/sandbox/")):
    objs += [(d, o) for o in docs([os.environ.get("KUBECTL", "kubectl"), "kustomize", d])]
for f in sorted(glob.glob("k8s/jobs/*.yaml")):
    objs += [(f, o) for o in yaml.safe_load_all(open(f)) if o]
bad = []
for src, o in objs:
    if o["kind"] not in ("Deployment", "Job"):
        continue
    spec = o["spec"]["template"]["spec"]
    for c in spec["containers"]:
        img, r = c["image"], c.get("resources", {})
        if img.endswith(":latest") or ":" not in img.split("/")[-1]:
            bad.append(f"{src}: unpinned image {img}")
        if r.get("requests", {}).get("memory") != r.get("limits", {}).get("memory"):
            bad.append(f"{src}: memory request != limit")
        if "nvidia.com/gpu" in r.get("limits", {}) and r["limits"]["nvidia.com/gpu"] != "1":
            bad.append(f"{src}: more than one GPU slice")
        if any(e["name"] in ("NVIDIA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES") for e in c.get("env", [])):
            bad.append(f"{src}: sets NVIDIA_/CUDA_VISIBLE_DEVICES")
        if o["kind"] == "Deployment" and "readinessProbe" not in c:
            bad.append(f"{src}: no readinessProbe")
        if o["kind"] == "Job":
            cpu, mem = r["limits"].get("cpu"), r["limits"]["memory"]
            if r["requests"].get("cpu") != cpu or float(cpu) > 8 or int(mem.rstrip("Gi")) > 80:
                bad.append(f"{src}: Job must be request = limit and fit the training window (8 CPU · 80 Gi)")
    if o["kind"] == "Job":
        if o["metadata"]["namespace"] != "batch" or o["metadata"]["labels"].get("kueue.x-k8s.io/queue-name") != "train":
            bad.append(f"{src}: Job must be in batch with kueue.x-k8s.io/queue-name: train")
        if spec.get("priorityClassName") != "spark-batch":
            bad.append(f"{src}: Job needs priorityClassName spark-batch")
    if o["kind"] == "Deployment" and spec.get("priorityClassName") != "spark-serving":
        bad.append(f"{src}: serving needs priorityClassName spark-serving")
names = {(src, o["kind"]) for src, o in objs}
assert not any(k == "Secret" for _, k in names), "an overlay still renders the placeholder hf-token Secret"
assert not bad, "\n".join(bad)
print(f"pod rules OK ({len(objs)} objects)")
PY
echo "== ms-swift command lines (flags of ms-swift 4.x)"
grep -q 'swift sft' k8s/jobs/swift-sft.yaml && grep -q -- '--rlhf_type "$RLHF_TYPE"' k8s/jobs/swift-dpo.yaml \
  && grep -q -- '--rlhf_type grpo' k8s/jobs/swift-grpo.yaml && ! grep -q -- '--train_type' k8s/jobs/*.yaml && echo "swift CLI OK"
echo "== python compile";  python3 -m py_compile tools/*.py tests/*.py scripts/*.py
echo "== shellcheck";      shellcheck -S warning -x scripts/*.sh tests/*.sh

echo "== serve-model.sh list / render (no cluster)"
expect "qwen3.6-35b-fp8" scripts/serve-model.sh list
expect "name: vllm-qwen3-4b-instruct" scripts/serve-model.sh qwen3-4b-instruct --side --render
refuse "unknown model" scripts/serve-model.sh no-such-model --render

echo "== catalog guard: an over-budget set and an undersized pod are refused"
bad_cat=$(mktemp -d); mkdir -p "$bad_cat/scripts" "$bad_cat/k8s"
cp scripts/gen_overlays.py "$bad_cat/scripts/"; cp versions.env "$bad_cat/"; cp -r k8s/models "$bad_cat/k8s/"
python3 - models.yaml "$bad_cat/models.yaml" <<'PY'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
c["sets"].append({"name": "too-much", "models": ["qwen3.6-35b-fp8", "qwen3-coder-30b-fp8"], "apps": False})
next(m for m in c["models"] if m["name"] == "qwen3-8b")["mem"] = "16Gi"
yaml.safe_dump(c, open(sys.argv[2], "w"), sort_keys=False)
PY
refuse "set too-much: Σ util 0.85 > 0.72" python3 "$bad_cat/scripts/gen_overlays.py" --check
refuse "qwen3-8b: mem 16Gi < util 0.2" python3 "$bad_cat/scripts/gen_overlays.py" --check
rm -rf "$bad_cat"; echo "catalog guard OK"

echo "== mock OpenAI endpoints (good / flaky)"
python3 tests/mock_openai.py 18780 & pids+=($!)
python3 tests/mock_openai.py 18781 flaky & pids+=($!)
for _ in $(seq 20); do curl -sf http://127.0.0.1:18780/health >/dev/null && curl -sf http://127.0.0.1:18781/health >/dev/null && break; sleep 0.3; done
M=http://127.0.0.1:18780; F=http://127.0.0.1:18781

echo "== fc_check: 6/6 against a correct model, gate fails on broken arguments"
expect "fc_check: 6/6 cases passed (100%)  tool calls: valid JSON 5/5, schema-valid 5/5" \
  python3 tools/fc_check.py run --url "$M" --model mock --min 1.0
refuse "GATE FAILED:" python3 tools/fc_check.py run --url "$F" --model mock --min 0.9
grep -q "arguments not JSON" "$T" && echo "flaky arguments detected"
schema=$(mktemp); python3 -c "import json,sys; sys.path.insert(0,'tools'); import fc_check; json.dump(fc_check.TOOLS[2], open(sys.argv[1],'w'))" "$schema"
expect "valid" python3 tools/fc_check.py validate --schema "$schema" --args '{"model": "qwen3-8b", "util": 0.2}'
refuse '$.util: 0.9 > maximum 0.72' python3 tools/fc_check.py validate --schema "$schema" --args '{"model": "qwen3-8b", "util": 0.9}'
refuse '$.extra: not allowed' python3 tools/fc_check.py validate --schema "$schema" --args '{"model": "m", "util": 0.2, "extra": 1}'
rm -f "$schema"

echo "== synth_data: generation, dedup and quality filters, judge, DPO pairs"
out=$(mktemp -d)
expect "generated 16 instructions → 11 new after filtering, 9 SFT rows kept, 9 DPO pairs" \
  python3 tools/synth_data.py --url "$M" --model mock --rounds 2 --per-round 8 --judge --out "$out/sft.jsonl" --dpo "$out/dpo.jsonl"
for why in "2  duplicate" "1  near-duplicate" "1  needs a non-text input" "1  refusal" "1  repetitive" "1  too short"; do
  grep -qF "dropped   $why" "$T" || { cat "$T"; echo "missing: dropped $why"; exit 1; }
done
python3 - "$out/sft.jsonl" "$out/dpo.jsonl" <<'PY'
import json, sys
sft = [json.loads(x) for x in open(sys.argv[1])]
dpo = [json.loads(x) for x in open(sys.argv[2])]
assert all([m["role"] for m in r["messages"]] == ["user", "assistant"] for r in sft + dpo)
assert all(r["rejected_response"] and r["rejected_response"] != r["messages"][1]["content"] for r in dpo)
print(f"ms-swift dataset format OK ({len(sft)} SFT rows, {len(dpo)} DPO pairs)")
PY
rm -rf "$out"
python3 -c "import sys; sys.path.insert(0,'tools'); import synth_data as s
assert s.rouge_l('a b c d', 'a b c d') == 1.0 and s.rouge_l('a b', 'c d') == 0.0
assert s.distinct3(' '.join(['x'] * 30)) < 0.5
print('rouge_l / distinct3 OK')"

echo "== qwen_agent_demo: parallel tool calls, local execution, error returned to the model"
audit=$(mktemp)
expect '"result": 24.34' python3 tools/qwen_agent_demo.py "Look up 'qwen3-vl-8b' in the model catalog and calculate 0.20 * 121.7" \
  --url "$M" --model mock --audit "$audit"
grep -qF "Qwen/Qwen3-VL-8B-Instruct" "$T"
python3 -c "import json,sys; r=[json.loads(x) for x in open(sys.argv[1])]; assert [x['tool'] for x in r]==['catalog_lookup','calculator'], r; print('agent audit OK')" "$audit"
expect '"gib": 54.77' python3 tools/qwen_agent_demo.py "How many GiB does a util of 0.45 reserve?" --url "$M" --model mock
expect "arguments are not valid JSON" python3 tools/qwen_agent_demo.py "Look up 'qwen3-8b' in the model catalog and calculate 2 * 3" --url "$F" --model mock
python3 -c "import sys; sys.path.insert(0,'tools'); import qwen_agent_demo as q
assert q.call_tool('calculator', '{\"expression\": \"__import__(1)\"}')['error']
assert q.catalog_lookup('nope')['error']
print('tool sandboxing OK')"
rm -f "$audit"
echo "ALL LOCAL CHECKS PASSED"
