#!/usr/bin/env bash
# Copy a trained LoRA adapter (or merged model) from the batch checkpoint PVC
# into the serving model cache (Volume 23). PVCs are namespaced, so two short-lived
# pods stream a tar from batch/deepseek-ckpt into llm-serving/model-cache — no local disk.
#   scripts/publish-adapter.sh sft-lora                   # /ckpt/sft-lora → /models/adapters/sft-lora
#   scripts/publish-adapter.sh grpo-tiny grpo-tiny-v1     # optional new name on the serving side
source "$(dirname "$0")/lib.sh"
src=${1:?usage: publish-adapter.sh <dir under /ckpt> [name under /models/adapters]}
dst=${2:-$src}
[[ $src =~ ^[A-Za-z0-9._-]+$ && $dst =~ ^[A-Za-z0-9._-]+$ ]] || { bad "names: letters, digits, . _ - only"; exit 2; }
pod() {   # pod <ns> <name> <pvc> <mount>
  k -n "$1" run "$2" --image=python:3.12-slim --restart=Never --overrides "{\"spec\":{
    \"securityContext\":{\"runAsUser\":1000,\"runAsNonRoot\":true,\"fsGroup\":1000,\"seccompProfile\":{\"type\":\"RuntimeDefault\"}},
    \"containers\":[{\"name\":\"c\",\"image\":\"python:3.12-slim\",\"command\":[\"sleep\",\"3600\"],
      \"securityContext\":{\"allowPrivilegeEscalation\":false,\"capabilities\":{\"drop\":[\"ALL\"]}},
      \"resources\":{\"requests\":{\"cpu\":\"100m\",\"memory\":\"128Mi\"},\"limits\":{\"memory\":\"512Mi\"}},
      \"volumeMounts\":[{\"name\":\"v\",\"mountPath\":\"$4\"}]}],
    \"volumes\":[{\"name\":\"v\",\"persistentVolumeClaim\":{\"claimName\":\"$3\"}}]}}" >/dev/null
}
trap 'k -n batch delete pod ckpt-reader --ignore-not-found --wait=false >/dev/null; k -n "$NS" delete pod cache-writer --ignore-not-found --wait=false >/dev/null' EXIT
pod batch ckpt-reader deepseek-ckpt /ckpt
pod "$NS" cache-writer model-cache /models
k -n batch wait --for=condition=Ready pod/ckpt-reader --timeout=3m >/dev/null
k -n "$NS" wait --for=condition=Ready pod/cache-writer --timeout=3m >/dev/null
k -n batch exec ckpt-reader -- test -f "/ckpt/$src/adapter_config.json" -o -f "/ckpt/$src/config.json" \
  || { bad "/ckpt/$src has no adapter_config.json or config.json — did the Job finish?"; exit 1; }
k -n "$NS" exec cache-writer -- mkdir -p /models/adapters
k -n batch exec ckpt-reader -- tar -C /ckpt -cf - "$src" \
  | k -n "$NS" exec -i cache-writer -- sh -c "rm -rf /models/adapters/$dst.tmp && mkdir /models/adapters/$dst.tmp && tar -C /models/adapters/$dst.tmp --strip-components=1 -xf - && rm -rf /models/adapters/$dst && mv /models/adapters/$dst.tmp /models/adapters/$dst"
k -n "$NS" exec cache-writer -- sh -c "ls -la /models/adapters/$dst; du -sh /models/adapters/$dst"
ok "published /ckpt/$src → model-cache:/models/adapters/$dst"
summary
