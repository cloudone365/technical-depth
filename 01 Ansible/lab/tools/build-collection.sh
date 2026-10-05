#!/usr/bin/env bash
# Package the lab roles as an Ansible collection: cloudone.spark
#   tools/build-collection.sh 1.2.0        → .cache/dist/cloudone-spark-1.2.0.tar.gz
set -euo pipefail
VERSION=${1:-0.1.0}
LAB=$(cd "$(dirname "$0")/.." && pwd)
OUT="$LAB/.cache/build/ansible_collections/cloudone/spark"
rm -rf "$OUT" && mkdir -p "$OUT"/{roles,plugins/inventory,playbooks,meta}
cp -r "$LAB"/roles/* "$OUT/roles/"
find "$OUT/roles" -type d -name molecule -prune -exec rm -rf {} +
cp "$LAB"/inventory_plugins/spark_mdns.py "$OUT/plugins/inventory/"
cp "$LAB"/playbooks/{01-baseline,02-fabric,03-containers,20-drift-check,21-emergency-drain,30-validate}.yml "$OUT/playbooks/"
# roles referenced by short name inside playbooks resolve inside the collection namespace
sed -i 's/- role: \([a-z_]*\)/- role: cloudone.spark.\1/' "$OUT"/playbooks/*.yml
cat > "$OUT/galaxy.yml" <<YML
namespace: cloudone
name: spark
version: $VERSION
readme: README.md
authors: [cloudone365]
description: DGX Spark automation — baseline, CX-7 fabric, containers, telemetry, kubeadm + Cilium + vCluster, Slurm, Vault, drain, validation
license: [MIT]
tags: [nvidia, dgx, gpu, rdma, infrastructure]
dependencies:
  ansible.posix: ">=1.5.4"
  community.general: ">=9.0.0"
  community.docker: ">=3.10.0"
  community.crypto: ">=2.20.0"
  kubernetes.core: ">=5.0.0"
repository: https://github.com/cloudone365/technical-depth
build_ignore: ['*.retry', '.cache']
YML
printf 'requires_ansible: ">=2.17.0"\n' > "$OUT/meta/runtime.yml"
cp "$LAB/README.md" "$OUT/README.md"
mkdir -p "$LAB/.cache/dist"
ansible-galaxy collection build "$OUT" --output-path "$LAB/.cache/dist" --force
