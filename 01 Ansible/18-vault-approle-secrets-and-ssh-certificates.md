# Step 18 · Vault ↔ Ansible in Production Style: AppRole for Semaphore, Short-Lived Tokens, KV Secrets, SSH Certificates, ansible-vault Keys

> **01 Ansible · Part IV — Secrets & platforms · Step 18 of 30** · ← [Step 17 · Vault server deep dive](17-vault-server-deep-dive.md) · [All steps](00-ansible-step-by-step-guide.md) · [Step 19 · Kubernetes with kubeadm](19-kubernetes-kubeadm-root-cluster-and-vclusters.md) →
>
> Builds on: [Step 17](17-vault-server-deep-dive.md) (the Vault server) · [Step 01 §4, §8](01-management-plane-semaphore-and-vault.md) · [Step 04 §5–6](04-dgx-spark-as-semaphore-target.md)

| | |
|---|---|
| **You will build** | A Semaphore task that holds **no long-lived credential except the AppRole pair in Semaphore's encrypted variable group**: AppRole login → 10-minute token (`semaphore-ssh` + `spark-lab-read`) → 15-minute SSH certificate for `svc-ansible` → NGC key read from `kv/spark-lab/ngc` → `docker login nvcr.io` on dgx-spark-1. Then you rotate that AppRole secret_id with response wrapping and a CIDR binding, and source an ansible-vault password from Vault |
| **Hardware** | `vault01` and `sema01` ([Step 01](01-management-plane-semaphore-and-vault.md)), `dgx-spark-1` prepared with [Step 04](04-dgx-spark-as-semaphore-target.md) (§3 and §6 done); MacBook |
| **Time** | 75 min |
| **Risk** | Low. The step to rehearse is the secret_id rotation (§3.5): destroy the old secret_id only after **every** variable group that uses it has the new one and a task has passed |

---

## 1. Threat model → design

| Risk in a typical lab repo | Control in this lab |
|---|---|
| API keys in `group_vars` / git history | Secrets live in vault01's KV (`kv/spark-lab/*`). The repo only holds *paths* (`vault_kv_mount`, `vault_kv_prefix` in `group_vars/all.yml`) |
| One SSH key that works forever, everywhere | SSH **certificates** from vault01's CA, valid 15 min, principal `svc-ansible` only; `svc-ansible` has no `authorized_keys` at all |
| Automation credentials that never expire | Token TTL 10 min (max 30); certificate 15 min (max 1 h); the secret_id is the one long-lived item, and §3.5 binds it to sema01's IP and rotates it |
| A human holds the automation credential | The secret_id is pasted once into a Semaphore *secret* and never shown again; humans run templates, not keys |
| Secrets in logs (task log, `ansible.log`, AWX, ARA) | `no_log: true` on every task that touches values; only booleans and key *names* are printed |
| "Who read what?" | vault01's audit log records every login, signature and read with the AppRole identity (Step 17 §3.4) |

## 2. Architecture

### 2.1 Flow of a Semaphore task

```mermaid
sequenceDiagram
  autonumber
  participant ADM as Admin (vault01 CLI, once)
  participant SEM as Semaphore task on sema01 (play 1 + host plays)
  participant V as vault01 :8200
  participant SP as dgx-spark-1 (sshd trusts vault01 CA)
  ADM->>V: read role-id, write secret-id (wrapped, CIDR 192.168.0.210/32)
  ADM-->>SEM: role_id + secret_id into variable group vault-approle
  SEM->>V: POST auth/approle/login {role_id, secret_id}
  V-->>SEM: token (TTL 10m, policies semaphore-ssh + spark-lab-read)
  SEM->>V: POST ssh-client-signer/sign/ansible {public_key, valid_principals=svc-ansible}
  V-->>SEM: signed cert (15m) → /tmp/lab_ssh/id_ed25519-cert.pub
  SEM->>V: GET kv/data/spark-lab/ngc (when vault_lab_secrets_enabled)
  V-->>SEM: api_key → hostvars['localhost'].vault_lab_secrets (no_log)
  SEM->>SP: SSH as svc-ansible with key + cert, sudo NOPASSWD
  SEM->>SP: docker login nvcr.io with api_key (no_log)
```

### 2.2 Secret zero and AppRole

Every machine identity starts with one secret it has to be *given*: **secret zero**. AppRole splits it in two:

| Half | Like | Where it lives in the lab | Sensitivity |
|---|---|---|---|
| `role_id` | a username | variable group `vault-approle`, *extra variables* (visible to project members) | identifies the role; useless alone |
| `secret_id` | a password | variable group `vault-approle`, *secret* (encrypted in Semaphore's database, hidden in the UI and the log) | the credential |

The pattern is the **trusted orchestrator**: something already trusted (here Semaphore, which you configured as an admin) receives the secret_id and uses it on behalf of jobs. The jobs themselves (playbooks) never see it in a form they could print, and every token they get from it is short-lived and policy-limited.

The knobs that make a secret_id less dangerous, and the lab's values:

| Knob | Set on | Lab (Step 01 §4) | Hardened (§3.5) |
|---|---|---|---|
| `token_ttl` / `token_max_ttl` | role | 10m / 30m | same |
| `token_policies` | role | `semaphore-ssh` + `spark-lab-read` (after 08) | one role per template class |
| `secret_id_ttl` | role | none (never expires) | e.g. 30 days, with a rotation runbook |
| `secret_id_num_uses` | role | 0 (unlimited) | 0 for Semaphore (it logs in every task); 1 for a bootstrap secret |
| `cidr_list` | secret_id (or `secret_id_bound_cidrs` on the role) | none | `192.168.0.210/32`: works from sema01 only |
| `token_bound_cidrs` | role | none | `192.168.0.210/32`: a stolen token is useless elsewhere |
| response wrapping | the delivery of the secret_id | not used | `-wrap-ttl=5m`: single-use, tamper-evident |

Changing role-level settings affects **both** Semaphore projects (`lab` from Step 01 and `spark-lab`), because they share AppRole `semaphore`. The per-secret_id settings in §3.5 are the safe way to practise.

### 2.3 LLD: the Vault objects

| Object | Path | Created by | Purpose |
|---|---|---|---|
| SSH engine | `ssh-client-signer/` | Step 01 §4 | CA key generated inside Vault; public half at `/v1/ssh-client-signer/public_key` |
| Signing role | `ssh-client-signer/roles/ansible` | Step 01 §4 | `allowed_users: svc-ansible`, `ttl: 15m`, `max_ttl: 1h`, `permit-pty` |
| Policy | `semaphore-ssh` | Step 01 §4 | `create, update` on `ssh-client-signer/sign/ansible` only |
| AppRole | `auth/approle/role/semaphore` | Step 01 §4 | machine identity of Semaphore |
| KV v2 engine | `kv/` | `08-vault.yml` | lab secrets under `kv/spark-lab/*` |
| Policy | `spark-lab-read` | `08-vault.yml` | `read` on `kv/data/spark-lab/*`, `list, read` on `kv/metadata/spark-lab/*` |
| Attachment | AppRole `semaphore` → `spark-lab-read` | `08-vault.yml` | appended, `semaphore-ssh` kept (Step 17 §2.2) |
| Secrets | `kv/spark-lab/ngc`, `kv/spark-lab/ansible-vault` | placeholder by 08; values by you | NGC login; ansible-vault password |
| Audit | `file` → `/var/log/vault_audit.log` | Step 01 §3.5 | every request logged, values HMAC'd |

What the lab reads these from, in [`group_vars/all.yml`](lab/inventory/group_vars/all.yml):

```yaml
vault_addr: https://192.168.0.211:8200
vault_cacert: "{{ '/etc/semaphore/vault-ca.crt' if vault_role_id is defined else lab_cache_dir ~ '/vault-ca.crt' }}"
vault_ssh_mount: ssh-client-signer       # SSH secrets engine path on vault01
vault_ssh_role: ansible                  # signing role: principal svc-ansible, 15-minute certificates
vault_ssh_principal: svc-ansible         # the automation account on every Spark
vault_ssh_key_dir: /tmp/lab_ssh          # where play 1 keeps the key + certificate (inside the Semaphore container)
vault_ssh_cert_ttl: 15m                  # long tasks with reboots (17, 21, site): 1h as a template extra variable
vault_kv_mount: kv
vault_kv_prefix: spark-lab
vault_lab_secrets_enabled: false         # set true (Semaphore variable group) once 08-vault.yml has run
```

and [`group_vars/spark.yml`](lab/inventory/group_vars/spark.yml) switches the login on `vault_role_id`: `svc-ansible` + `/tmp/lab_ssh/id_ed25519` (+ cert) from Semaphore, the admin user `nvidia` with your own key from the MacBook (Step 04 §1).

> **KV v2 path gotcha:** the API path for *reading* is `kv/data/<path>`, for *listing* `kv/metadata/<path>`. Policies and `uri` calls must use those exact prefixes; the CLI (`vault kv get kv/spark-lab/ngc`) hides the `data/` segment from you.

### 2.4 Static vs dynamic secrets, and leases

| Kind | Example in the lab | Lifetime | Revocable? |
|---|---|---|---|
| **Static** secret | `kv/spark-lab/ngc` | until you change it (KV v2 keeps versions) | no: you rotate it at the source (NGC) |
| **Leased** credential | the AppRole token | 10 min, renewable to 30 | yes: `vault token revoke`, or revoke all tokens of the role via `vault token revoke -mode=path auth/approle/login` |
| **Dynamic, unleased** credential | the SSH certificate | 15 min | **no**: sshd checks it offline against the CA key; only expiry, a KRL (`RevokedKeys`) or rotating the CA stop it |
| **Dynamic, leased** secret | (not in the lab) database, AWS, PKI engines | lease TTL | yes: `vault lease revoke`, and Vault deletes the credential at the source |

Dynamic secrets are created on demand, unique per consumer and short-lived, so a leak has a small blast radius and a clear audit trail. The SSH CA is the dynamic secret this lab is built around. The lab has no database, but the shape of a leased dynamic secret is worth knowing:

```bash
# ILLUSTRATIVE — not in this lab: a database engine issuing a per-run PostgreSQL user
vault secrets enable database
vault write database/config/slurmdb plugin_name=postgresql-database-plugin \
  connection_url="postgresql://{{username}}:{{password}}@db01:5432/slurm" username=vault password=…
vault write database/roles/ansible-ro db_name=slurmdb default_ttl=15m max_ttl=1h \
  creation_statements="CREATE ROLE \"{{name}}\" LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}'; GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"{{name}}\";"
vault read database/creds/ansible-ro        # new user + password + lease_id, every call
```

Why 15 minutes for the SSH certificate when tasks like `05 Kubernetes` can run longer: the certificate is only checked when a connection is **authenticated**. Ansible's `ControlPersist=600s` (in [`ansible.cfg`](lab/ansible.cfg)) keeps the authenticated connection open and reuses it, so a busy task keeps working past 15 minutes. A connection that sits idle for over 10 minutes after the certificate expired, or one opened after a reboot, has to re-authenticate and is refused (§5). For those tasks play 1 asks for a longer certificate: set the template extra variable `vault_ssh_cert_ttl: 1h`, the role's `max_ttl` (`17 DGX OS upgrade`, `21 Emergency drain` with a reboot, `site`).

---

## 3. Hands-on

### 3.1 Play 1: how a Semaphore task gets its credentials

Every playbook that SSHes to the Sparks starts with `import_playbook: 00-vault-cert.yml`. The core of [`playbooks/00-vault-cert.yml`](lab/playbooks/00-vault-cert.yml):

```yaml
- name: Get an SSH certificate from Vault
  hosts: localhost
  connection: local
  gather_facts: false
  check_mode: false                       # must really run, even for a --check dry run
  tasks:
    - name: Semaphore run (vault01 AppRole attached)
      when: vault_role_id is defined      # MacBook runs skip the whole block (break-glass as nvidia)
      block:
        - name: Generate the SSH key pair once
          ansible.builtin.command: ssh-keygen -t ed25519 -N "" -f {{ vault_ssh_key_dir }}/id_ed25519
          args:
            creates: "{{ vault_ssh_key_dir }}/id_ed25519"

        - name: Log in to Vault with AppRole
          ansible.builtin.uri:
            url: "{{ vault_addr }}/v1/auth/approle/login"
            method: POST
            body_format: json
            body:
              role_id: "{{ vault_role_id }}"
              secret_id: "{{ vault_secret_id }}"
            ca_path: "{{ vault_cacert }}"
          register: vault_cert_login
          no_log: true

        - name: Ask Vault to sign the public key
          ansible.builtin.uri:
            url: "{{ vault_addr }}/v1/{{ vault_ssh_mount }}/sign/{{ vault_ssh_role }}"
            method: POST
            headers:
              X-Vault-Token: "{{ vault_cert_login.json.auth.client_token }}"
            body_format: json
            body:
              public_key: "{{ vault_cert_pubkey.content | b64decode }}"
              valid_principals: "{{ vault_ssh_principal }}"
              ttl: "{{ vault_ssh_cert_ttl }}"
            ca_path: "{{ vault_cacert }}"
          register: vault_cert_signed
          no_log: true

        - name: Read the lab secrets (kv/spark-lab/*), when enabled
          ansible.builtin.uri:
            url: "{{ vault_addr }}/v1/{{ vault_kv_mount }}/data/{{ vault_kv_prefix }}/{{ item }}"
            headers:
              X-Vault-Token: "{{ vault_cert_login.json.auth.client_token }}"
            ca_path: "{{ vault_cacert }}"
            status_code: [200, 404]
          loop: [ngc]
          register: vault_cert_kv
          no_log: true
          when: vault_lab_secrets_enabled | bool
        # … then: save the cert as id_ed25519-cert.pub, and set_fact vault_lab_secrets (no_log)
```

Design choices worth copying:

- **`uri`, not `community.hashi_vault`.** Plain HTTP calls need no Python library, show exactly which endpoint is hit (it matches the audit log line for line), and behave identically in the stock and the lab Semaphore image.
- **One login per task.** The token is used three times (sign, read) and then simply expires. No renew, no revoke, nothing to clean up.
- **The certificate sits next to the key** (`id_ed25519-cert.pub`), so OpenSSH presents it automatically; `spark.yml` only sets the key path.
- **Secrets are read up front,** inside the token's 10 minutes, into a fact on `localhost`; host plays take them from `hostvars['localhost'].vault_lab_secrets`.
- **The paths read are a list (`loop: [ngc]`).** A new lab secret means a `vault kv put kv/spark-lab/<name>` plus its name in that loop; the policy already covers `kv/spark-lab/*`.

### 3.2 The integration playbook: run it from Semaphore

[`playbooks/19-vault-integration.yml`](lab/playbooks/19-vault-integration.yml) makes the whole chain visible:

```yaml
- name: Short-lived SSH certificate from vault01 (Semaphore runs only)
  ansible.builtin.import_playbook: 00-vault-cert.yml

- name: Check what Vault handed out
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: This playbook demonstrates the Semaphore path
      ansible.builtin.assert:
        that:
          - vault_role_id is defined
          - vault_lab_secrets_enabled | bool
        fail_msg: >-
          Run it from Semaphore with the vault-approle variable group and
          vault_lab_secrets_enabled=true (Step 04 §6). 08-vault.yml must have run once.
        quiet: true

    - name: What we got (no secret values)
      ansible.builtin.debug:
        msg:
          - "SSH certificate: {{ vault_ssh_key_dir }}/id_ed25519-cert.pub (inspect: ssh-keygen -L -f …)"
          - "Lab secrets read from {{ vault_kv_mount }}/{{ vault_kv_prefix }}/: {{ (vault_lab_secrets | default({})).keys() | list }}"
          - "NGC key present: {{ (vault_lab_secrets.ngc.api_key | default('')) not in ['', 'REPLACE_ME'] }}"

- name: Use the secrets on the Sparks
  hosts: spark
  become: true
  gather_facts: false
  vars:
    container_runtime_ngc_api_key: "{{ hostvars['localhost'].vault_lab_secrets.ngc.api_key | default('') }}"
    container_runtime_ngc_login: "{{ container_runtime_ngc_api_key not in ['', 'REPLACE_ME'] }}"
    container_runtime_smoke_test: false
  roles:
    - role: container_runtime
```

**Prerequisites:** Step 17 §3.2 / Step 04 §6 done (KV, policy, your real NGC key in `kv/spark-lab/ngc`), and `vault_lab_secrets_enabled` set to `true` in the variable group `vault-approle`.

**Run:** in Semaphore, project `spark-lab`, create (Step 04 §6) and run the template **19 Vault integration**: playbook `01 Ansible/lab/playbooks/19-vault-integration.yml`, inventory `spark-lab`, variable group `vault-approle`, CLI args `["--limit", "dgx-spark-1,localhost"]`.

Expected in the task log:

```
TASK [Log in to Vault with AppRole]       ok: [localhost]   (output hidden: no_log)
TASK [Ask Vault to sign the public key]   ok: [localhost]
TASK [What we got (no secret values)]
  "SSH certificate: /tmp/lab_ssh/id_ed25519-cert.pub (inspect: ssh-keygen -L -f …)"
  "Lab secrets read from kv/spark-lab/: ['ngc']"
  "NGC key present: True"
TASK [container_runtime : Log in to nvcr.io]   ok/changed: [dgx-spark-1]
PLAY RECAP  dgx-spark-1 : … failed=0   localhost : … failed=0
```

**Verify on all three systems:**

```bash
# vault01: the AppRole read the NGC key (path and role in clear, values HMAC'd)
sudo jq -c 'select(.type=="response" and .request.path=="kv/data/spark-lab/ngc") | {time, role: .auth.metadata.role_name, policies: .auth.policies}' \
  /var/log/vault_audit.log | tail -1                     # role "semaphore", policies [default semaphore-ssh spark-lab-read]

# dgx-spark-1: logged in by certificate, and Docker holds an nvcr.io login
sudo journalctl -u ssh --since "15 min ago" | grep 'svc-ansible.*CERT'   # Accepted publickey for svc-ansible … ED25519-CERT
sudo jq '.auths | keys' /root/.docker/config.json                        # ["nvcr.io"]

# sema01 (in ~/semaphore): the certificate play 1 wrote
docker compose exec semaphore ssh-keygen -L -f /tmp/lab_ssh/id_ed25519-cert.pub | grep -E 'Key ID|Valid|Principals' -A1
```

**Now try it from the MacBook** (break-glass path) to see the boundary:

```bash
cd ~/technical-depth/"01 Ansible/lab"
ansible-playbook playbooks/19-vault-integration.yml -l dgx-spark-1,localhost -K
# play 1 is skipped (no vault_role_id), then the assert stops with "Run it from Semaphore …"
```

The MacBook has no AppRole and should never get one: the human path is your own admin key and an admin Vault login, the automation path is Semaphore.

### 3.3 Everyday lookup patterns

Play 1's login result is registered on `localhost`, so any later play in the same task can reuse the token (valid 10 minutes) with the `community.hashi_vault` collection (installed in the lab Semaphore image, with `hvac`):

```yaml
# Pattern A (what the lab does): read in play 1, use the fact
container_runtime_ngc_api_key: "{{ hostvars['localhost'].vault_lab_secrets.ngc.api_key | default('') }}"

# Pattern B: an ad-hoc read with play 1's token, e.g. in a role of your own
#   (needs a 'vault kv put kv/spark-lab/huggingface token=…' first; spark-lab-read already allows it)
hf_token: >-
  {{ lookup('community.hashi_vault.vault_kv2_get', 'spark-lab/huggingface', engine_mount_point=vault_kv_mount,
            url=vault_addr, ca_cert=vault_cacert, auth_method='token',
            token=hostvars['localhost'].vault_cert_login.json.auth.client_token).secret.token }}
```

> Lookups run **on the controller**, once per host that templates them, and a lookup with `auth_method='approle'` logs in every time. With 50 hosts, that's 50 logins and 50 audit lines per variable. Log in once (play 1), pass the token, or read once into a `localhost` fact (pattern A). And don't build anything that must run after the token's 10 minutes on the token: read early.

### 3.4 ansible-vault keys from HashiCorp Vault

Some values have to exist **without** Vault: the break-glass path runs when vault01 is down or unreachable. ansible-vault encrypts them in a file; HashiCorp Vault holds the *password* to that file. [`tools/vault-pass.sh`](lab/tools/vault-pass.sh) is the bridge:

```bash
#!/usr/bin/env bash
# ansible-vault password source backed by HashiCorp Vault.
#   ansible.cfg:  vault_password_file = ./tools/vault-pass.sh
# Needs VAULT_ADDR, VAULT_CACERT and a token (VAULT_TOKEN or ~/.vault-token) that can
# read kv/data/spark-lab/ansible-vault. Nothing is ever written to disk.
set -euo pipefail
: "${VAULT_ADDR:?set VAULT_ADDR}"
exec vault kv get -field=password kv/spark-lab/ansible-vault
```

It runs **on the MacBook**, with your own admin login. Store the password once, then encrypt the NGC key as a fallback variable (`03-containers.yml` takes `ngc_api_key` first, then the vault01 value):

```bash
cd ~/technical-depth/"01 Ansible/lab"
export VAULT_ADDR=https://192.168.0.211:8200 VAULT_CACERT=$PWD/.cache/vault-ca.crt
vault login                                                                   # your admin login; the token lands in ~/.vault-token
vault kv put kv/spark-lab/ansible-vault password="$(openssl rand -base64 32)" # the ansible-vault password lives in vault01
ansible-vault encrypt_string --vault-password-file tools/vault-pass.sh \
  "$(vault kv get -field=api_key kv/spark-lab/ngc)" --name ngc_api_key > .cache/ngc.vault.yml   # encrypted, git-ignored
ansible localhost -m ansible.builtin.debug -a 'msg={{ ngc_api_key[:6] }}' \
  -e @.cache/ngc.vault.yml --vault-password-file tools/vault-pass.sh          # the first characters of your key = decryption works
ansible-playbook playbooks/03-containers.yml -l dgx-spark-1,localhost -K \
  -e @.cache/ngc.vault.yml --vault-password-file tools/vault-pass.sh          # break-glass run as nvidia, NGC key from ansible-vault
```

Two limits to understand:

- **Semaphore can't use `vault-pass.sh`.** Ansible needs the vault password when it *loads* variables, before play 1 has a token, and the lab image has no `vault` CLI. In Semaphore you put the ansible-vault password into the **Key Store** (type *Login with password*, password field) and select it as the template's vault password; menu names differ between Semaphore versions. `kv/spark-lab/ansible-vault` stays the source of truth you copy it from when you rotate.
- **The password is only as available as vault01.** For a true vault01-down emergency, keep a sealed offline copy of the ansible-vault password next to the unseal keys (Step 17 §5).

Uncommenting `vault_password_file = ./tools/vault-pass.sh` in [`ansible.cfg`](lab/ansible.cfg) saves the flag on the MacBook, but then *every* MacBook run needs a Vault login, including ones that don't use encrypted files.

### 3.5 Secret zero, hardened: rotate the secret_id with response wrapping (vault01 + Semaphore)

**Why:** the static secret_id from Step 01 §4 never expires and works from anywhere on the LAN. Replace it with one that only works from sema01, and deliver it in a single-use wrapper.

On vault01 (admin login):

```bash
vault list auth/approle/role/semaphore/secret-id                    # accessors of the secret_ids in use (note the current one)
WRAP=$(vault write -wrap-ttl=5m -field=wrapping_token auth/approle/role/semaphore/secret-id \
  cidr_list=192.168.0.210/32 metadata='{"issued_for":"sema01","by":"vol19"}')   # a NEW secret_id, sealed in a wrapper
vault write sys/wrapping/lookup token=$WRAP                         # creation_path auth/approle/role/semaphore/secret-id, ttl 5m; contents not revealed
vault unwrap $WRAP                                                  # once: secret_id + secret_id_accessor
vault unwrap $WRAP                                                  # again: error: the wrapper is single-use
```

What wrapping buys you: the wrapper is **single-use** and **short-lived**, so if the delivery channel was read by someone else, either they used it (your unwrap fails, which is an incident you now know about) or it expired. In a full trusted-orchestrator setup, a pipeline hands only the wrapping token to the runner and the runner unwraps it. In this lab you unwrap and paste by hand, which still protects transit and gives you the tamper check.

Prove the CIDR binding (still on vault01, i.e. *not* from 192.168.0.210):

```bash
vault write auth/approle/login role_id=$(vault read -field=role_id auth/approle/role/semaphore/role-id) \
  secret_id=<the new secret_id>                                     # fails: source address not allowed by the secret_id's CIDR list
vault write auth/approle/role/semaphore/secret-id-accessor/lookup \
  secret_id_accessor=<the new accessor>                             # cidr_list [192.168.0.210/32], metadata, creation_time
```

In Semaphore, paste the new secret_id into the `vault_secret_id` secret of **both** variable groups that use AppRole `semaphore` (project `lab` from Step 01 §8.6 and project `spark-lab`), then run **00 Ping** in each: `failed=0`. Only then destroy the old one:

```bash
vault write auth/approle/role/semaphore/secret-id-accessor/destroy secret_id_accessor=<the OLD accessor>
vault list auth/approle/role/semaphore/secret-id                    # only the new accessor is left
```

Run **00 Ping** once more. Next steps toward production (role-level, so they affect both projects; plan them): `secret_id_ttl` with a calendar reminder to rotate, `token_bound_cidrs=192.168.0.210/32`, and one AppRole per environment or template class (Step 01 §11, Step 04 §11).

### 3.6 SSH certificates: what's signed, what's refused, what can't be revoked

Inspect a certificate play 1 issued (on sema01, in `~/semaphore`):

```bash
docker compose exec semaphore ssh-keygen -L -f /tmp/lab_ssh/id_ed25519-cert.pub
#   Type: ssh-ed25519-cert-v01@openssh.com user certificate
#   Key ID: "vault-approle-…"           Vault's default key_id_format (token display name + key hash)
#   Valid: from … to …                  15 minutes
#   Principals: svc-ansible
#   Extensions: permit-pty
```

Test the CA by hand on vault01 with [`tools/vault-ssh-cert.sh`](lab/tools/vault-ssh-cert.sh) (needs a `vault login` there and the repository cloned on vault01), or with the commands of Step 04 §3.3:

```bash
tools/vault-ssh-cert.sh ~/semaphore_lab svc-ansible                 # signs ~/semaphore_lab.pub, prints the certificate
ssh -i ~/semaphore_lab -o CertificateFile=~/semaphore_lab-cert.pub svc-ansible@192.168.0.100 'hostname; sudo -n whoami'   # dgx-spark-1, root
```

Now try what the role must refuse:

```bash
vault write ssh-client-signer/sign/ansible public_key=@$HOME/semaphore_lab.pub valid_principals=root
# error: root is not a valid value for valid_principals   (allowed_users: svc-ansible)
vault write ssh-client-signer/sign/ansible public_key=@$HOME/semaphore_lab.pub valid_principals=nvidia
# error as well: the admin account can't be reached with a Vault certificate, by design
```

And the expiry test of Step 04 §8: 16 minutes after a task, `docker compose exec semaphore ssh -i /tmp/lab_ssh/id_ed25519 svc-ansible@192.168.0.100 true` is refused; the next task run works again. No cleanup, no revocation list.

**Revocation.** A signed certificate can't be recalled by Vault. The controls are the short TTL, a `RevokedKeys` KRL in sshd for an emergency, and CA rotation (re-run `00b-semaphore-target.yml` after `vault write ssh-client-signer/config/ca generate_signing_key=true`, which invalidates every outstanding certificate at once).

**Static keys.** `svc-ansible` never had an `authorized_keys` file: certificate or nothing. The `nvidia` admin account keeps your own key; that's the break-glass path (Step 04 §10), so protect it (passphrase, MacBook only) rather than removing it. Keep console access too: if vault01 is sealed, certificate logins stop as soon as the current ones expire.

---

## 4. Integrations

| Consumer | Pattern |
|---|---|
| Semaphore (Step 01, Step 04) | Variable group with AppRole `semaphore` → play 1 → token + certificate per task; ansible-vault password as a Key Store key |
| `03 Containers` (Step 11) | NGC key from `hostvars['localhost'].vault_lab_secrets` (play 1), else `ngc_api_key` from ansible-vault |
| AWX (Step 24) | Credential types **HashiCorp Vault Secret Lookup** (AppRole) + **HashiCorp Vault Signed SSH**, so AWX never stores the NGC key or a private SSH key |
| Kubernetes pods (Step 20) | Vault Agent Injector or External Secrets; a Kubernetes auth method replaces AppRole for pods. Use one auth mount per cluster (`spark-root`, `dev-lab`, `llms`): each vCluster has its own API server and ServiceAccount token issuer |
| Grafana / Prometheus (Step 12) | a `kv/spark-lab/grafana` secret, added to play 1's loop, feeding `gpu_telemetry_grafana_admin_password` |
| CI (Step 25) | no Vault access at all for lint/test, or a separate AppRole with a lint-only policy; never AppRole `semaphore` |

## 5. Troubleshooting & diagnostics

| Symptom | Diagnose | Fix |
|---|---|---|
| Play 1: *Log in to Vault with AppRole* fails (details hidden) | on vault01: `sudo jq -c 'select(.request.path=="auth/approle/login") \| .error' /var/log/vault_audit.log \| tail -3` | `invalid role or secret ID`: the secret_id was destroyed, expired, or is bound to another CIDR (§3.5); 503: vault01 is sealed (Step 17 §3.3) |
| Play 1 skipped, then `Permission denied` for **nvidia** | the template has no variable group | attach `vault-approle` (Step 04 §12) |
| `Lab secrets read from kv/spark-lab/: []` | `vault read -field=token_policies auth/approle/role/semaphore` | `spark-lab-read` missing: run `08-vault.yml`; or `vault_lab_secrets_enabled` still false |
| 403 / `permission denied` reading a KV path | `vault token capabilities <token> kv/data/spark-lab/ngc`; audit log | policy written as `kv/spark-lab/*` instead of `kv/data/spark-lab/*` |
| `NGC key present: False` | `vault kv get -field=api_key kv/spark-lab/ngc \| cut -c1-6` | still `REPLACE_ME`: `vault kv put kv/spark-lab/ngc api_key=…` |
| `19 Vault integration` from the MacBook stops at the assert | by design | run it in Semaphore (§3.2) |
| `certificate verify failed` | inside Semaphore: `/etc/semaphore/vault-ca.crt` (Step 01 §7.3); MacBook: `.cache/vault-ca.crt` | re-copy `~/vault-ca.crt` from vault01 (Step 04 §2) |
| `The 'hvac' python library is required` (pattern B) | `docker compose exec semaphore ansible-galaxy collection list community.hashi_vault` | the stock image is running: rebuild the lab image (Step 04 §4) |
| SSH: `Certificate invalid: name is not a listed principal` | `ssh-keygen -L` → Principals | `valid_principals` must equal the login user (`svc-ansible`) |
| SSH: `Permission denied (publickey)` with a fresh certificate | on the Spark: `sudo sshd -T \| grep trustedusercakeys`; compare fingerprints (Step 04 §3.2) | re-run `00b-semaphore-target.yml` (the CA was regenerated, or the drop-in is missing) |
| `Permission denied` late in a long task | the cert (15 min) expired and the SSH control connection was closed after 10 idle minutes | re-run the task; split very long, idle-heavy work into separate templates |
| `vault-pass.sh`: `set VAULT_ADDR` or `permission denied` | `vault token lookup` | export `VAULT_ADDR`/`VAULT_CACERT` and `vault login` with a token that can read `kv/data/spark-lab/ansible-vault` |
| A secret appears in a task log or `ansible.log` | `grep -n nvapi "$SPARK_LAB_CACHE/ansible.log"` on sema01 | a task without `no_log`: fix it, then rotate the secret at the source (treat it as leaked) |

## 6. Validation

- [ ] `git grep -n "nvapi-"` in the repository finds nothing; `.cache/` is git-ignored.
- [ ] Template **19 Vault integration** ends with `NGC key present: True` and `failed=0`, and vault01's audit log shows role `semaphore` reading `kv/data/spark-lab/ngc`.
- [ ] The certificate in the Semaphore container shows principal `svc-ansible`, 15-minute validity; a manual request for `root` is refused.
- [ ] The secret_id in use is bound to `192.168.0.210/32`, the old accessor is destroyed, and both Semaphore projects still pass **00 Ping**.
- [ ] `tools/vault-pass.sh` decrypts `.cache/ngc.vault.yml` on the MacBook; the Semaphore templates that need ansible-vault use a Key Store key, not the script.
- [ ] `python3 tools/capstone_scorecard.py` check **17/18** passes: no AppRole, token or Vault init file in the cache.
