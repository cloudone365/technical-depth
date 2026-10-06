# shellcheck shell=bash
# shellcheck disable=SC2034  # variables are used by the scripts that source this file
# 03-DeepSeek helpers: reuse the 02-Kubernetes lab helpers (KUBECONFIG discovery,
# PASS/FAIL printing), then point DS_DIR at this lab.
DS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$DS_DIR/../../02-Kubernetes/lab/scripts/lib.sh"
K8S_LAB="$LAB_DIR"
# shellcheck source=../versions.env
source "$DS_DIR/versions.env"
NS=llm-serving
model_field() {   # model_field <name> <field>  (from models.yaml)
  python3 - "$DS_DIR/models.yaml" "$1" "$2" <<'PY'
import sys, yaml
m = {x["name"]: x for x in yaml.safe_load(open(sys.argv[1]))["models"]}
print(m[sys.argv[2]].get(sys.argv[3], "") if sys.argv[2] in m else "", end="")
PY
}
