# Chapter 03 · Bare-Metal Provisioning & Bootstrap: Day-0/Day-1 Without a BMC, Safe Network Cut-over, Redfish & PXE Practice

> **01-Ansible · Part I — Management plane & Ansible foundations · Chapter 03 of 30** · ← [Chapter 02 · Control node & Ansible core](02-control-node-and-ansible-core.md) · [All chapters](00-ansible-step-by-step-guide.md) · [Chapter 04 · DGX Spark as a Semaphore target](04-dgx-spark-as-semaphore-target.md) →

| | |
|---|---|
| **You will build** | A repeatable path from *fresh out of the box* (or *just re-imaged*) to *reachable by Ansible*: first-boot wizard → key trust → hostname → static mgmt IP with an automatic rollback. For a Spark that is already installed, SSH trust only. Either way it ends with your MacBook logging in to `dgxadmin@192.168.0.100` by key. Then a Redfish practice target and a PXE design for when you graduate to DGX/HGX |
| **Hardware** | 1× DGX Spark (recovery USB stick optional) and your MacBook |
| **Time** | 2 h for a fresh DGX OS (+30 min if you do a full re-image drill); 10 min for SSH trust only |
| **Risk** | **Medium.** Changing the management IP on a headless box can lock you out, which is exactly what the dead-man switch below prevents |

---

## 1. What's different about a Spark

| Data-centre DGX/HGX | DGX Spark | Consequence for automation |
|---|---|---|
| BMC with Redfish/IPMI (power, virtual media, boot order, sensors) | **No BMC** | No remote power cycling or console. A hung box needs a human (or a smart plug) |
| PXE + autoinstall, fully unattended | **First-boot wizard** (local display, or headless via a Wi-Fi hotspot whose SSID, password and URL are on the Quick Start Guide sticker) | Day 0 is manual. Ansible takes over at Day 1 |
| Re-image over the network | **USB recovery media** from NVIDIA/OEM; a re-install takes roughly 25–30 min | Your playbooks must rebuild everything after a re-image, and that's the point of this module |
| OS images you build | **DGX OS 7** (Ubuntu 24.04 based) with NVIDIA's kernel, driver, CUDA, Docker and toolkit preinstalled | Converge the vendor image; don't fight it |

```mermaid
stateDiagram-v2
  direction LR
  [*] --> Unboxed
  Unboxed --> OOBE: power on
  OOBE --> DHCP_Ready: wizard (user, Wi-Fi/Ethernet, updates)
  DHCP_Ready --> Bootstrapped: 03.1-bootstrap.yml (-k -K, MacBook)
  Bootstrapped --> Target: 04.1-semaphore-target.yml (MacBook)
  Target --> Baselined: Semaphore 04.3 Baseline
  Baselined --> Operational: templates 11.1 … 22.1 (runtime, fabric, kubeadm + vClusters, slurm…)
  Operational --> Operational: drift check / day-2
  Operational --> Recovery: disk failure · bad update · "start clean"
  Recovery --> OOBE: USB recovery image (25–30 min)
```

> **Do not power off during the first-boot update.** NVIDIA warns that interrupting the initial software download and install can damage the system. Let it finish before running any playbook.

---

## 2. Architecture

### 2.1 HLD: control-plane responsibilities by phase

```mermaid
flowchart LR
  subgraph D0["Day 0 (human)"]
    W["First-boot wizard<br/>user · locale · network · updates"]
  end
  subgraph D1["Day 1 (03.1-bootstrap.yml)"]
    K["SSH key trust"] --> H["hostname + /etc/hosts"] --> N["static mgmt IP<br/>with dead-man rollback"]
  end
  subgraph D1b["Day 1 (04.1-semaphore-target.yml)"]
    SA["svc-ansible + NOPASSWD sudo"] --> CA["trust vault01's SSH CA"]
  end
  subgraph D2["Day 1½ (Semaphore: 04.3 Baseline)"]
    F["spark.fact"] --> B["packages · NVIDIA holds ·<br/>sysctl · sshd · chrony · journald"]
  end
  D0 --> D1 --> D1b --> D2 --> Rest["Chapters 10–30"]
```

### 2.2 LLD: the dead-man switch for network changes

```mermaid
sequenceDiagram
  participant A as Ansible (MacBook, as dgxadmin)
  participant S as Spark (old address)
  participant T as systemd timer
  A->>S: ip -4 addr show enP7s7 — already static on the target IP?
  Note over A,S: yes → skip the whole step (nothing to change)
  A->>S: nmcli connection add mgmt-static (static, not active yet)
  A->>T: systemd-run --on-active=180 /root/mgmt-ip-rollback.sh
  A->>S: async: sleep 2 && nmcli connection up mgmt-static   (SSH drops)
  Note over S: now on the static IP
  A->>A: set_fact ansible_host = new IP
  A->>S: wait_for_connection (≤ 90 s)
  alt reconnected
    A->>T: stop mgmt-ip-rollback.timer  ✅
    A->>S: old profile: autoconnect no (kept for recovery)
  else never reconnected
    T->>S: delete mgmt-static, bring the old profile up  ↩️ (back on the old address)
  end
```

DGX OS is based on Ubuntu Desktop, so **NetworkManager** owns the network: the static address is a NetworkManager profile, set with `nmcli`, not a file in `/etc/netplan`. (Writing a netplan file next to NetworkManager's would leave two managers claiming the same port.) On a host without NetworkManager, such as Ubuntu Server, the playbook falls back to a netplan file `/etc/netplan/30-mgmt.yaml`, with the same timer.


The same pattern protects **any** change that can cut your own access: sshd config, firewall rules, bonding, VLANs.

---

## 3. Hands-on

Two paths, one end state:

- **Fresh DGX OS** (new or just re-imaged): §3.1 wizard, then §3.2 bootstrap. The bootstrap installs your MacBook key.
- **Already installed** (named `dgx-spark-1`, on 192.168.0.100, user `dgxadmin`): §3.3, SSH trust only. No bootstrap.

**What the bootstrap does, and whether your Spark needs it.** Every step checks first and leaves alone what is already right, so running it on a set-up Spark is harmless; you just don't need to.

| `03.1-bootstrap.yml` step | Already in place if… (check from the MacBook) | If it is |
|---|---|---|
| Hostname = inventory name | `ssh dgxadmin@192.168.0.100 hostname` prints `dgx-spark-1` | unchanged |
| `127.0.1.1 dgx-spark-1` in `/etc/hosts` (slow `sudo` without it) | `ssh dgxadmin@192.168.0.100 grep dgx-spark-1 /etc/hosts` finds a line | unchanged |
| Your MacBook key for `dgxadmin` | the `ssh` commands above don't ask for a password | unchanged |
| SSH server enabled | you can SSH in at all | unchanged |
| Static management IP (only with `-e bootstrap_static_ip=true`) | `ssh dgxadmin@192.168.0.100 ip -4 addr show enP7s7` shows `192.168.0.100` **without** `dynamic` | **skipped**, and the run says so |

Two cases for the last row:

- **Already static on 192.168.0.100** (set in NetworkManager or Settings): nothing to do. You won't find it in `/etc/netplan` on DGX OS; it lives in NetworkManager (`nmcli -g ipv4.method,ipv4.addresses connection show "<profile>"` shows `manual`).
- **DHCP that always gives 192.168.0.100** (a reservation on your router; `ip addr` on the Spark shows `dynamic`): fine for the lab, as long as the reservation exists. The bootstrap leaves it alone unless you add `-e bootstrap_force_static=true`.

So with an installed Spark whose name and address already match, use §3.3 and skip §3.2. If only the hostname is wrong, run just the first bootstrap command of §3.2 (without `bootstrap_static_ip`) with `-e bootstrap_current_ip=192.168.0.100`: it fixes the name and doesn't touch the network.

Either way you need an SSH key on the MacBook first; `03.1-bootstrap.yml` installs `~/.ssh/id_ed25519.pub` (`spark_admin_pubkeys` in [`group_vars/all.yml`](lab/inventory/group_vars/all.yml)):

```bash
# ▶ MacBook · any folder
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -C "$(whoami)@$(hostname -s)"   # create one if you don't have a key; -C is only a label
```

Both paths end when `ssh dgxadmin@192.168.0.100 hostname` prints `dgx-spark-1` without asking for a password (§7).

### 3.1 Day 0: the first-boot wizard

1. Connect the 10GbE port to your management LAN (recommended over Wi-Fi for everything that follows).
2. Power on. Either use a monitor and keyboard, or join the setup hotspot from a laptop and open the URL on the Quick Start Guide sticker.
3. Create the user. Use **the same username on every Spark** (`dgxadmin` in this lab); NVIDIA's multi-node playbooks and MPI depend on it.
4. Let the updates finish, including the reboot.
5. Find its DHCP address from your router, or with `avahi-browse -rt _ssh._tcp` from a Linux machine on the same LAN (Chapter 06).

### 3.2 Day 1: bootstrap with Ansible

This is one of the few playbooks that runs **from your MacBook**, not from Semaphore: the Spark has no `svc-ansible` account and doesn't trust vault01's CA yet, so Semaphore can't log in. You connect as `dgxadmin` with a password (`-k`), and the play installs your key.

```yaml
# lab/playbooks/03.1-bootstrap.yml
---
# Day-1 bootstrap for a Spark (Chapter 03 §3.2). Safe on a fresh DGX OS and on a
# Spark that is already set up: every step checks first and changes only what differs.
#
#   ansible-playbook playbooks/03.1-bootstrap.yml -l dgx-spark-1 -k -K \
#     -e bootstrap_current_ip=<current IP> [-e bootstrap_static_ip=true]
#
# What it does                         Already in place on your Spark?
#   hostname = inventory name          `hostname` prints dgx-spark-1        → unchanged
#   127.0.1.1 line in /etc/hosts       `grep dgx-spark-1 /etc/hosts`        → unchanged
#   your MacBook key for dgxadmin      key login works                      → unchanged
#   SSH server enabled                 you can SSH in                       → unchanged
#   static management IP (opt-in)      already static on the inventory IP   → skipped, says so
#
# NOTE: don't pass -e ansible_host=… — extra vars outrank set_fact, so Ansible
# could never switch to the new address and the rollback would always fire.
#
# Static IP: DGX OS (Ubuntu Desktop based) manages the network with
# NetworkManager, so the address is set with nmcli as a new profile
# "mgmt-static". Hosts without NetworkManager get a netplan file instead.
# Either way a DEAD-MAN SWITCH (systemd timer) restores the previous setup in
# 180 s unless Ansible reconnects on the new IP and cancels it.
#   Already static on the inventory IP     → step skipped
#   DHCP lease that already gives that IP  → skipped (router reservation);
#                                            -e bootstrap_force_static=true converts it
- name: Bootstrap a freshly installed DGX Spark
  hosts: spark
  become: true
  gather_facts: false          # we must redirect the connection before the first SSH
  vars:
    bootstrap_static_ip: false
    bootstrap_force_static: false
    bootstrap_prefix: "{{ mgmt_cidr | default('192.168.0.0/24') | regex_replace('^.*/', '') }}"
    bootstrap_rollback_seconds: 180
    bootstrap_netplan: /etc/netplan/30-mgmt.yaml
    bootstrap_nm_profile: mgmt-static
  tasks:
    - name: Remember the inventory (target) IP, connect via the current one
      ansible.builtin.set_fact:
        bootstrap_target_ip: "{{ ansible_host }}"
        ansible_host: "{{ bootstrap_current_ip | default(ansible_host) }}"

    - name: Gather facts on the current address
      ansible.builtin.setup:

    - name: Set hostname to the inventory name
      ansible.builtin.hostname:
        name: "{{ inventory_hostname }}"

    - name: Make the hostname resolvable locally (sudo is slow without it)
      ansible.builtin.lineinfile:
        path: /etc/hosts
        regexp: '^127\.0\.1\.1\s'
        line: "127.0.1.1 {{ inventory_hostname }}.{{ lab_domain | default('lab.local') }} {{ inventory_hostname }}"
        mode: "0644"

    - name: Install control-node SSH key(s)
      ansible.posix.authorized_key:
        user: "{{ spark_admin_user }}"
        key: "{{ item }}"
      loop: "{{ spark_admin_pubkeys | select | list }}"

    - name: Ensure OpenSSH server is enabled
      ansible.builtin.systemd_service:
        name: ssh
        enabled: true
        state: started

    # ------------------------------------------------------------ static mgmt IP with rollback
    - name: Static management IP (opt-in) — check first
      when: bootstrap_static_ip | bool
      block:
        - name: The management interface must exist
          ansible.builtin.assert:
            that: mgmt_interface in ansible_interfaces
            fail_msg: >-
              {{ mgmt_interface }} (mgmt_interface in group_vars/all.yml) is not on this host.
              Interfaces found: {{ ansible_interfaces | sort | join(', ') }}
            quiet: true

        - name: Current IPv4 addresses on the management interface
          ansible.builtin.command: ip -4 -o addr show dev {{ mgmt_interface }}
          register: bootstrap_addr
          changed_when: false

        - name: Which services run here (is NetworkManager active?)
          ansible.builtin.service_facts:

        - name: Does NetworkManager manage the interface?
          ansible.builtin.command: nmcli -g GENERAL.STATE device show {{ mgmt_interface }}
          register: bootstrap_nm_state
          changed_when: false
          failed_when: false
          when: ansible_facts.services['NetworkManager.service'].state | default('') == 'running'

        - name: Decide what to do
          ansible.builtin.set_fact:
            bootstrap_has_target: "{{ (' ' ~ bootstrap_target_ip ~ '/') in bootstrap_addr.stdout }}"
            bootstrap_is_dhcp: "{{ 'dynamic' in (bootstrap_addr.stdout_lines | select('search', ' ' ~ bootstrap_target_ip ~ '/') | join(' ')) }}"
            bootstrap_use_nm: >-
              {{ (bootstrap_nm_state.rc | default(1)) == 0 and
                 bootstrap_nm_state.stdout | default('') | length > 0 and
                 'unmanaged' not in bootstrap_nm_state.stdout }}

        - name: Report the decision
          ansible.builtin.debug:
            msg: >-
              {{ mgmt_interface }}:
              {{ 'already static on ' ~ bootstrap_target_ip ~ ' — nothing to change'
                 if (bootstrap_has_target and not bootstrap_is_dhcp) else
                 ('DHCP lease that already gives ' ~ bootstrap_target_ip ~ ' (router reservation) — left as is; '
                  ~ '-e bootstrap_force_static=true converts it to a static address'
                  if (bootstrap_has_target and not bootstrap_force_static | bool) else
                 ('setting ' ~ bootstrap_target_ip ~ '/' ~ bootstrap_prefix ~ ' via '
                  ~ ('NetworkManager (nmcli)' if bootstrap_use_nm else 'netplan')
                  ~ ', with a ' ~ bootstrap_rollback_seconds ~ ' s dead-man switch')) }}

        - name: Change needed?
          ansible.builtin.set_fact:
            bootstrap_change_ip: "{{ not bootstrap_has_target or (bootstrap_is_dhcp and bootstrap_force_static | bool) }}"

    # NetworkManager (DGX OS): a new profile "mgmt-static"; the old profile stays,
    # with autoconnect off, so you can switch back by hand at any time.
    - name: Static management IP via NetworkManager (dead-man switch)
      when:
        - bootstrap_static_ip | bool
        - bootstrap_change_ip | default(false) | bool
        - bootstrap_use_nm | bool
      block:
        - name: Which profile is active on the interface now
          ansible.builtin.command: nmcli -g GENERAL.CONNECTION device show {{ mgmt_interface }}
          register: bootstrap_nm_old
          changed_when: false

        - name: Write rollback script (back to the previous profile)
          ansible.builtin.copy:
            dest: /root/mgmt-ip-rollback.sh
            mode: "0700"
            content: |
              #!/bin/sh
              nmcli connection down {{ bootstrap_nm_profile }} 2>/dev/null
              nmcli connection delete {{ bootstrap_nm_profile }} 2>/dev/null
              {% if bootstrap_nm_old.stdout and bootstrap_nm_old.stdout != bootstrap_nm_profile %}
              nmcli connection modify "{{ bootstrap_nm_old.stdout }}" connection.autoconnect yes
              nmcli connection up "{{ bootstrap_nm_old.stdout }}"
              {% endif %}
              logger -t bootstrap "mgmt IP rolled back — Ansible did not confirm the new address"

        - name: Create the static profile (not active yet)
          ansible.builtin.shell: |
            nmcli connection delete {{ bootstrap_nm_profile }} >/dev/null 2>&1 || true
            nmcli connection add type ethernet ifname {{ mgmt_interface }} con-name {{ bootstrap_nm_profile }} \
              ipv4.method manual ipv4.addresses {{ bootstrap_target_ip }}/{{ bootstrap_prefix }} \
              ipv4.gateway {{ mgmt_gateway }} ipv4.dns "{{ dns_servers | join(',') }}" \
              connection.autoconnect yes connection.autoconnect-priority 100
          changed_when: true

        - name: Arm the dead-man switch
          ansible.builtin.command: >-
            systemd-run --unit=mgmt-ip-rollback --on-active={{ bootstrap_rollback_seconds }}
            /root/mgmt-ip-rollback.sh
          changed_when: true

        - name: Activate it in the background (our SSH session may drop)
          ansible.builtin.shell: sleep 2 && nmcli connection up {{ bootstrap_nm_profile }}
          async: 60
          poll: 0
          changed_when: true

        - name: Switch Ansible to the new address
          ansible.builtin.set_fact:
            ansible_host: "{{ bootstrap_target_ip }}"

        - name: Reconnect on the new IP
          ansible.builtin.wait_for_connection:
            delay: 5
            timeout: 90

        - name: Disarm the dead-man switch
          ansible.builtin.systemd_service:
            name: mgmt-ip-rollback.timer
            state: stopped

        - name: Keep the old profile, but stop it from taking the port back at boot
          ansible.builtin.command: nmcli connection modify "{{ bootstrap_nm_old.stdout }}" connection.autoconnect no
          when: bootstrap_nm_old.stdout not in ['', bootstrap_nm_profile]
          changed_when: true

    # No NetworkManager (plain Ubuntu Server): a netplan file.
    - name: Static management IP via netplan (dead-man switch)
      when:
        - bootstrap_static_ip | bool
        - bootstrap_change_ip | default(false) | bool
        - not (bootstrap_use_nm | bool)
      block:
        - name: Back up current netplan directory
          ansible.builtin.command: cp -a /etc/netplan /root/netplan.pre-bootstrap
          args:
            creates: /root/netplan.pre-bootstrap

        - name: Write rollback script
          ansible.builtin.copy:
            dest: /root/mgmt-ip-rollback.sh
            mode: "0700"
            content: |
              #!/bin/sh
              rm -f {{ bootstrap_netplan }}
              cp -a /root/netplan.pre-bootstrap/. /etc/netplan/
              netplan apply
              logger -t bootstrap "mgmt IP rolled back — Ansible did not confirm the new address"

        - name: Render static netplan for the mgmt NIC
          ansible.builtin.copy:
            dest: "{{ bootstrap_netplan }}"
            mode: "0600"
            content: |
              # {{ ansible_managed }}
              network:
                version: 2
                ethernets:
                  {{ mgmt_interface }}:
                    dhcp4: false
                    addresses: [{{ bootstrap_target_ip }}/{{ bootstrap_prefix }}]
                    routes: [{to: default, via: {{ mgmt_gateway }}}]
                    nameservers: {addresses: {{ dns_servers | to_json }}}

        - name: Arm the dead-man switch
          ansible.builtin.command: >-
            systemd-run --unit=mgmt-ip-rollback --on-active={{ bootstrap_rollback_seconds }}
            /root/mgmt-ip-rollback.sh
          changed_when: true

        - name: Apply netplan in the background (our SSH session will drop)
          ansible.builtin.shell: sleep 2 && netplan apply
          async: 60
          poll: 0
          changed_when: true

        - name: Switch Ansible to the new address
          ansible.builtin.set_fact:
            ansible_host: "{{ bootstrap_target_ip }}"

        - name: Reconnect on the new IP
          ansible.builtin.wait_for_connection:
            delay: 5
            timeout: 90

        - name: Disarm the dead-man switch
          ansible.builtin.systemd_service:
            name: mgmt-ip-rollback.timer
            state: stopped

        - name: Other netplan files (review by hand if one still configures the mgmt NIC)
          ansible.builtin.find:
            paths: /etc/netplan
            patterns: ["*.yaml"]
            excludes: ["30-mgmt.yaml", "40-cx7.yaml"]
          register: bootstrap_old_netplan

        - name: Show them
          ansible.builtin.debug:
            msg: "Review: {{ bootstrap_old_netplan.files | map(attribute='path') | list }}"

    - name: Confirm the management address
      when: bootstrap_static_ip | bool
      block:
        - name: Read it back
          ansible.builtin.command: ip -4 -o addr show dev {{ mgmt_interface }}
          register: bootstrap_addr_after
          changed_when: false

        - name: The inventory IP is on the interface
          ansible.builtin.assert:
            that: (' ' ~ bootstrap_target_ip ~ '/') in bootstrap_addr_after.stdout
            fail_msg: "{{ bootstrap_target_ip }} is not on {{ mgmt_interface }}: {{ bootstrap_addr_after.stdout }}"
            success_msg: "{{ mgmt_interface }} has {{ bootstrap_target_ip }}"
```

**Your first Spark, `dgx-spark-1`.** On a Spark that has just finished the first-boot wizard (password login, DHCP address), `03.1-bootstrap.yml` sets the hostname, installs your key for `dgxadmin` (task *Install control-node SSH key(s)*) and, on the second run, moves it to the static IP through NetworkManager. A dead-man timer rolls the network back if Ansible can't reconnect. Each step checks first, so a re-run changes nothing that is already right.

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
```

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/03.1-bootstrap.yml -l dgx-spark-1 -k -K -e bootstrap_current_ip=<its DHCP IP>                              # -k: SSH password, still on
ansible-playbook playbooks/03.1-bootstrap.yml -l dgx-spark-1 -K -e bootstrap_current_ip=<its DHCP IP> -e bootstrap_static_ip=true     # move to 192.168.0.100
ssh dgxadmin@192.168.0.100 hostname                                   # dgx-spark-1, no password: your key is in
```

That is this chapter's end state for dgx-spark-1 (§7); Chapter 04 then makes it a Semaphore target. The rest of §3.2 explains the second run and its safety net.

**A later Spark, `dgx-spark-2`** (once Chapter 04 is done for the first one), shows the full sequence including the Semaphore step:

```bash
# ▶ MacBook · technical-depth (repo root)
cd "01-Ansible/lab"
```

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
# First run: password SSH (-k) and sudo (-K), on the DHCP address, no IP change yet
ansible-playbook playbooks/03.1-bootstrap.yml -l dgx-spark-2 -k -K -e bootstrap_current_ip=192.168.0.137

# Second run: move it to its inventory IP (192.168.0.101) with the dead-man switch
ansible-playbook playbooks/03.1-bootstrap.yml -l dgx-spark-2 -K \
  -e bootstrap_current_ip=192.168.0.137 -e bootstrap_static_ip=true

# Make it a Semaphore target (Chapter 04 §3): svc-ansible, NOPASSWD sudo, trust vault01's CA
ansible-playbook playbooks/04.1-semaphore-target.yml -l dgx-spark-2,localhost -K
```

From now on, plain inventory addressing works and the node belongs to Semaphore: run the templates `04.2 Ping` and `04.3 Baseline` with CLI args `--limit dgx-spark-2,localhost`. (When dgx-spark-2 is permanent, drop the `--limit dgx-spark-1,localhost` from all templates, Chapter 04 §5.5.)

**Test the rollback on purpose, once.** Point the node at an address your MacBook can't reach, e.g. `-e bootstrap_target_ip=10.99.99.99`. (`-e` beats the playbook's own `set_fact`, so this is a handy way to force a bad target.) The reconnect times out, and 180 s later the Spark is back on its old address. `journalctl -t bootstrap` on the Spark shows the rollback.

> **Precedence trap (the reason for `bootstrap_current_ip`):** extra vars (`-e`) outrank everything, including `set_fact`. If you passed the DHCP address as `-e ansible_host=…`, the play could never switch its connection to the new IP, and the dead-man switch would roll back every time. Always carry "where it is now" in a separate variable.

### 3.3 Spark already installed? SSH trust only

Skip this on a fresh DGX OS: `03.1-bootstrap.yml` in §3.2 already installed your MacBook key (task *Install control-node SSH key(s)*). For a Spark that is already installed, named `dgx-spark-1`, on 192.168.0.100, with the user `dgxadmin`, copy the key yourself (it asks for `dgxadmin`'s password once):

```bash
# ▶ MacBook · any folder
ssh-copy-id dgxadmin@192.168.0.100                        # your ~/.ssh/id_ed25519.pub (§3)
ssh-copy-id dgxadmin@192.168.0.101                        # second Spark, if any
ssh dgxadmin@192.168.0.100 'hostname; grep -c "$(hostname)" /etc/hosts; ip -4 addr show enP7s7 | grep inet; uname -m; head -3 /etc/dgx-release'
```

Expected: `dgx-spark-1`, a count of 1 or more, `inet 192.168.0.100/24 …` (with or without `dynamic`, see the two cases in §3), `aarch64` and a `DGX_*` release line. If the interface isn't `enP7s7`, `ip -br -4 addr` shows which one holds the address: set `mgmt_interface` in `group_vars/all.yml` to it, because later chapters use it. If `/etc/dgx-release` is missing, you're not on DGX OS. The lab still runs, but the version checks in `spark_validate` will warn.

This key is **your** key, for the `dgxadmin` admin user. It is what the MacBook uses for the bootstrap and break-glass paths. Semaphore never sees it: it logs in as `svc-ansible` with a certificate, after `04.1-semaphore-target.yml` has made the Spark trust vault01's CA.

> Installed, but a different hostname or address? Run `03.1-bootstrap.yml` (§3.2) with `-e bootstrap_current_ip=<its address>`: it works on any DGX OS that accepts the password, fixes the name, and (with `bootstrap_static_ip=true`) moves it to its inventory IP.

### 3.4 Managing kernel arguments (when you have a reason)

DGX OS ships tuned kernel parameters, so don't change them casually. When you must (a vendor-advised setting, or a debugging flag), use a GRUB drop-in plus a controlled reboot, never a `sed` on `/etc/default/grub`:

```yaml
- name: Kernel arguments via GRUB drop-in
  hosts: spark
  become: true
  serial: 1                               # one node at a time
  vars:
    spark_kernel_args: []                 # e.g. ["nvidia.NVreg_EnableStreamMemOPs=1"] — only if advised
  tasks:
    - name: Drop-in
      ansible.builtin.copy:
        dest: /etc/default/grub.d/90-spark-lab.cfg
        content: |
          # {{ ansible_managed }}
          GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT {{ spark_kernel_args | join(' ') }}"
        mode: "0644"
      register: grub_dropin
    - name: Regenerate GRUB
      ansible.builtin.command: update-grub
      when: grub_dropin is changed
      changed_when: true
    - name: Reboot and wait for the GPU to come back
      ansible.builtin.reboot:
        reboot_timeout: 900
        test_command: nvidia-smi -L
      when: grub_dropin is changed
    - name: Verify args are live
      ansible.builtin.command: cat /proc/cmdline
      register: cmdline
      changed_when: false
      failed_when: spark_kernel_args | reject('in', cmdline.stdout) | list | length > 0
```

### 3.5 Recovery drill: prove you can rebuild

The real test of provisioning automation is to wipe a node and rebuild it:

1. Record the state. **Semaphore UI:** run the template `30.1 Validate` with `--limit dgx-spark-2,localhost` (keep `validation/dgx-spark-2.json` from sema01's state volume, `/opt/spark-lab/cache`).
2. Re-image dgx-spark-2 from the USB recovery media (the OEM/NVIDIA guide covers creating it with `dd`; verify the checksum first).
3. Complete the wizard (§3.1).
4. From the MacBook: `03.1-bootstrap.yml` (both runs), then `04.1-semaphore-target.yml -l dgx-spark-2,localhost -K`. The re-image gave the node a new SSH host key, so on sema01 remove the old one first (`docker compose exec semaphore ssh-keygen -R 192.168.0.101`, Chapter 04 §13). Then, **Semaphore UI:** run the template `site` with `--limit dgx-spark-2,localhost`.
5. Validate again and `diff` the two JSON reports. **Anything that differs is something you did by hand and never automated.**

Semaphore, vault01 and the task history of the first build are untouched by the re-image: that's why the controller lives outside the Spark.

Time the whole thing. Under an hour from USB boot to validated is a good target.

---

## 4. Practising Redfish (for DGX/HGX) on the Spark

The Spark has no BMC, but the Redfish automation you'll use on DGX B200/GB200 systems (power control, boot override, firmware inventory, sensors) can be practised against DMTF's **Redfish Mockup Server**, which serves a realistic BMC tree from static JSON:

```yaml
# lab/playbooks/03.2-redfish-practice.yml
---
# DGX Spark has no BMC. To practise the Redfish automation you'll need on
# DGX/HGX servers, run DMTF's Redfish Mockup Server on the Spark and point
# community.general redfish modules at it (Chapter 03).
- name: Short-lived SSH certificate from vault01 (Semaphore runs only)
  ansible.builtin.import_playbook: 00-vault-cert.yml

- name: Start a Redfish mockup server
  hosts: spark[0]
  become: true
  vars:
    redfish_mock_dir: /opt/redfish-mockup
    redfish_mock_port: 8000
  tasks:
    - name: Clone DMTF Redfish-Mockup-Server
      ansible.builtin.git:
        repo: https://github.com/DMTF/Redfish-Mockup-Server.git
        dest: "{{ redfish_mock_dir }}"
        version: main
        depth: 1
    - name: Install its Python requirements in a venv
      ansible.builtin.pip:
        requirements: "{{ redfish_mock_dir }}/requirements.txt"
        virtualenv: "{{ redfish_mock_dir }}/.venv"
        virtualenv_command: python3 -m venv
    - name: Run it as a transient systemd unit
      ansible.builtin.command: >-
        systemd-run --unit=redfish-mock --collect
        {{ redfish_mock_dir }}/.venv/bin/python {{ redfish_mock_dir }}/redfishMockupServer.py
        -H 0.0.0.0 -p {{ redfish_mock_port }} -D {{ redfish_mock_dir }}/public-rackmount1
      register: redfish_mock_run
      changed_when: redfish_mock_run.rc == 0
      failed_when: redfish_mock_run.rc != 0 and 'already' not in redfish_mock_run.stderr

- name: Query it like a real BMC
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    bmc: "{{ hostvars[groups['spark'][0]].ansible_host }}:8000"
  tasks:
    - name: Inventory via Redfish
      community.general.redfish_info:
        category: Systems,Chassis,Manager
        command: GetSystemInventory,GetPsuInventory,GetFirmwareInventory,GetBootOverride
        baseuri: "{{ bmc }}"
        username: root
        password: unused-by-mockup
      register: redfish
    - name: Show system inventory
      ansible.builtin.debug:
        var: redfish.redfish_facts.system
```

**Semaphore UI:** run the template `03.2 Redfish practice`, or break-glass from the MacBook:

```bash
# ▶ MacBook · 01-Ansible/lab (venv active)
ansible-playbook playbooks/03.2-redfish-practice.yml -l dgx-spark-1,localhost -K
curl -s http://192.168.0.100:8000/redfish/v1/Systems | jq '.Members'
```

Real-BMC equivalents of what you just ran:

| Task | Module / command | On a real DGX/HGX BMC |
|---|---|---|
| Inventory | `community.general.redfish_info` `GetSystemInventory` | CPU/GPU/DIMM/PSU inventory |
| One-time PXE boot | `redfish_command` `SetOneTimeBoot` `bootdevice: Pxe` | Next boot from the network |
| Power | `redfish_command` `PowerGracefulRestart` / `PowerForceOff` | Remote power-cycle a hung node (you **can't** do this on a Spark; a smart plug is the home-lab stand-in) |
| Virtual media | `redfish_command` `VirtualMediaInsert` | Boot an ISO without USB |
| Firmware | `redfish_info` `GetFirmwareInventory` + `redfish_command` `MultipartHTTPPushUpdate` | BMC/BIOS/GPU firmware |

## 5. PXE / autoinstall design (reference for data-centre nodes)

Not runnable on a Spark (no network install path documented for it), but here's the blueprint the rest of this module plugs into:

```mermaid
flowchart LR
  BMC["BMC (Redfish)<br/>SetOneTimeBoot=Pxe + reboot"] --> NIC["Node UEFI PXE"]
  NIC -->|DHCP option 67| DHCP["dnsmasq / Kea<br/>per-MAC reservations from NetBox"]
  NIC -->|HTTP| IPXE["iPXE script"] --> K["kernel + initrd"]
  K -->|autoinstall ds=nocloud-net| AI["user-data (Ansible-templated)<br/>storage · users · SSH key · late-commands"]
  AI --> OS["OS installed → reboot"]
  OS --> CB["cloud-init phone-home → AWX webhook"]
  CB --> AWX["AWX: site.yml -l <new node>"]
```

Ansible owns every box in that diagram: it templates the dnsmasq reservations from NetBox (Chapter 06), renders the per-node `user-data`, flips the BMC boot order through Redfish, and receives the phone-home webhook in AWX.

---

## 6. Troubleshooting & diagnostics

| Symptom | Diagnose | Fix |
|---|---|---|
| `UNREACHABLE! … Permission denied (publickey)` / password prompt for `dgxadmin` | Key not on the Spark, or the wrong user: `ssh -v dgxadmin@192.168.0.100` | Installed Spark: `ssh-copy-id` (§3.3). Fresh: re-run the bootstrap with `-k` (§3.2), with `~/.ssh/id_ed25519.pub` present. Check `remote_user` in `ansible.cfg` |
| Can't find the Spark after the wizard | Router DHCP leases; `avahi-browse -rt _ssh._tcp`; `arp -a` | Use a wired connection; the Wi-Fi hotspot is setup-only |
| `-k` fails: `to use the 'ssh' connection type with passwords, you must install the sshpass program` | — | `brew install hudochenkov/sshpass/sshpass` on the MacBook (`apt install sshpass` on Linux) |
| Semaphore after a re-image: `Host key verification failed` | the node's host key changed | on sema01: `docker compose exec semaphore ssh-keygen -R <ip>`, only once you know why it changed (Chapter 04 §13) |
| Bootstrap: `wait_for_connection` times out and then the old IP answers again | The dead-man switch worked (`/root/mgmt-ip-rollback.sh`) | Check the new IP/prefix/gateway; `journalctl -t bootstrap`; make sure you used `bootstrap_current_ip`, **not** `-e ansible_host`; fix and re-run |
| Both the old DHCP and the new static address are present | NetworkManager: `nmcli connection show --active` lists two profiles on the port. Netplan host: `netplan get` | NetworkManager: `nmcli connection modify "<old profile>" connection.autoconnect no` and `nmcli connection down "<old profile>"`. Netplan: remove the duplicate file the last task lists; `netplan apply` |
| Bootstrap stops at *The management interface must exist* | `ip -br -4 addr` on the Spark | Set `mgmt_interface` in `group_vars/all.yml` to the port that holds the management address |
| You want the old network setup back | `nmcli connection show` lists `mgmt-static` and your old profile | `nmcli connection modify "<old profile>" connection.autoconnect yes; nmcli connection up "<old profile>"; nmcli connection delete mgmt-static` (that is what `/root/mgmt-ip-rollback.sh` does) |
| `netplan apply` warns `Permissions for /etc/netplan/*.yaml are too open` | `ls -l /etc/netplan` | `mode: "0600"` (the roles already do this) |
| After `update-grub` + reboot, the GPU is missing | `nvidia-smi`; `dmesg \| grep -i nvrm` | Remove the drop-in, `update-grub`, reboot; bisect the argument you added |
| Redfish mockup: `Connection refused` | `systemctl status redfish-mock` on the Spark | `systemctl reset-failed redfish-mock`; re-run; check the port with `ss -ltnp \| grep 8000` |

## 7. Validation

- [ ] (Fresh DGX OS) A Spark goes from wizard-complete to its static IP and key login using only playbooks.
- [ ] (Fresh DGX OS) You triggered the dead-man rollback deliberately and watched it recover.
- [ ] (Drill, once the lab is built) Re-image → rebuild → `diff` of the validation JSON is empty.
- [ ] (Once Chapter 04 is done) `redfish_info` returns system inventory from the mockup.
- [ ] Both paths: `ssh dgxadmin@192.168.0.100 hostname` prints `dgx-spark-1` without asking for a password.
