#!/usr/bin/env bash
# Get a short-lived SSH user certificate from Vault's SSH CA for the lab user.
# OpenSSH (and therefore Ansible) automatically presents <key>-cert.pub next to <key>.
#   tools/vault-ssh-cert.sh [~/.ssh/id_ed25519] [nvidia]
set -euo pipefail
KEY=${1:-$HOME/.ssh/id_ed25519}
PRINCIPAL=${2:-nvidia}
: "${VAULT_ADDR:?set VAULT_ADDR}"
vault write -field=signed_key ssh-client-signer/sign/ansible \
  public_key=@"${KEY}.pub" valid_principals="$PRINCIPAL" > "${KEY}-cert.pub"
chmod 0644 "${KEY}-cert.pub"
ssh-keygen -L -f "${KEY}-cert.pub" | sed -n '1,12p'
