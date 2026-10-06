#!/usr/bin/env bash
# Manual test of vault01's SSH CA (Step 01 §6, Step 04 §3.3): sign a public
# key for the automation account and show the certificate. Run it ON vault01
# (logged in with `vault login`); Semaphore does the same thing in play 1.
#   tools/vault-ssh-cert.sh [~/semaphore_lab] [svc-ansible]
#   ssh -i ~/semaphore_lab -o CertificateFile=~/semaphore_lab-cert.pub svc-ansible@192.168.0.100 'sudo -n whoami'
set -euo pipefail
KEY=${1:-$HOME/semaphore_lab}
PRINCIPAL=${2:-svc-ansible}
: "${VAULT_ADDR:=https://192.168.0.211:8200}"
export VAULT_ADDR
[[ -f "$KEY" ]] || ssh-keygen -t ed25519 -N "" -f "$KEY" >/dev/null
vault write -field=signed_key ssh-client-signer/sign/ansible \
  public_key=@"${KEY}.pub" valid_principals="$PRINCIPAL" > "${KEY}-cert.pub"
chmod 0644 "${KEY}-cert.pub"
ssh-keygen -L -f "${KEY}-cert.pub" | sed -n '1,12p'
