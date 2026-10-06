#!/usr/bin/env bash
# ansible-vault password source backed by HashiCorp Vault.
#   ansible.cfg:  vault_password_file = ./tools/vault-pass.sh
# Needs VAULT_ADDR, VAULT_CACERT and a token (VAULT_TOKEN or ~/.vault-token) that can
# read kv/data/spark-lab/ansible-vault. Nothing is ever written to disk.
set -euo pipefail
: "${VAULT_ADDR:?set VAULT_ADDR}"
exec vault kv get -field=password kv/spark-lab/ansible-vault
