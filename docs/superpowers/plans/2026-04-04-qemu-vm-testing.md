# QEMU VM Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automated end-to-end testing of Ansible infrastructure using QEMU VMs — a server VM runs all server roles, then a diskless aarch64 Pi VM PXE-boots from it.

**Architecture:** Python test harness manages QEMU VM lifecycle (boot, SSH wait, ansible-playbook, verify, teardown). Two VMs connected via QEMU socket networking. Verification playbooks paired with each role, usable against both test VMs and production.

**Tech Stack:** Python 3, QEMU, cloud-init, Ansible, paramiko, GRUB EFI

**Spec:** `docs/superpowers/specs/2026-04-04-qemu-vm-testing-design.md`

---

## File Map

### New files — Test harness

| File | Responsibility |
|------|---------------|
| `tests/vm/run_tests.py` | CLI entry point, argument parsing, phase orchestration |
| `tests/vm/vm_manager.py` | `VMManager` class: QEMU process lifecycle, guest agent, SSH wait |
| `tests/vm/cloud_init.py` | Generate cloud-init seed ISOs (user-data, meta-data) |
| `tests/vm/network.py` | Socket networking helpers, ProxyJump SSH config generation |
| `tests/vm/images/.gitkeep` | Placeholder for cached cloud images (gitignored) |

### New files — Test inventory

| File | Responsibility |
|------|---------------|
| `tests/inventory/test-hosts` | Host definitions for test-vm and test-pi |
| `tests/inventory/group_vars/all/all.yml` | Minimal group vars (Python interpreter, domain) |
| `tests/inventory/group_vars/all/srv.yml` | NFS root paths, image download config |
| `tests/inventory/group_vars/all/site.yml` | Django paths |
| `tests/inventory/group_vars/all/streaming.yml` | Streaming config |
| `tests/inventory/group_vars/all/firewall.yml` | Firewall rules |
| `tests/inventory/group_vars/all/ssh_keys.yml` | SSH key imports |
| `tests/inventory/group_vars/all/ci.yml` | TFTP port |
| `tests/inventory/host_vars/test-vm.yml` | All host-specific vars (see spec for full list) |

### New files — Verification playbooks

| File | Responsibility |
|------|---------------|
| `ansible/verify.yml` | Top-level verify playbook mirroring `site.yml` |
| `ansible/roles/firewall/verify/main.yml` | Assert nftables running + rules loaded |
| `ansible/roles/nfs/verify/main.yml` | Assert NFS exports + services running |
| `ansible/roles/img/verify/main.yml` | Assert NFS root populated |
| `ansible/roles/fixpi/verify/main.yml` | Assert qemu-user-static, TFTP symlinks, templates |
| `ansible/roles/pxe/verify/main.yml` | Assert dnsmasq running + TFTP populated |
| `ansible/roles/site/verify/main.yml` | Assert nginx + Django responding |
| `ansible/roles/wssh/verify/main.yml` | Assert wssh service running |
| `ansible/roles/cam/stream-server/verify/main.yml` | Assert nginx-rtmp config |
| `ansible/roles/uhubctl/verify/main.yml` | Assert package + udev rules |

### New files — PXE role extension

| File | Responsibility |
|------|---------------|
| `ansible/roles/pxe/tasks/test_clients.yml` | Install grub-efi, copy grubaa64.efi, template grub.cfg |
| `ansible/roles/pxe/templates/grub.cfg.j2` | GRUB config for aarch64 PXE test clients |

### Modified files

| File | Change |
|------|--------|
| `ansible/roles/pxe/tasks/main.yml` | Add conditional include of `test_clients.yml` |
| `ansible/roles/site/tasks/main.yml` | Make `switch.yml` include conditional |
| `.gitignore` | Add `tests/vm/images/` |
| `.github/workflows/vm-test.yml` | New CI workflow |

### Pre-existing gaps to fix

| File | Issue |
|------|-------|
| `ansible/roles/fixpi/files/scripts/maintenance.sh` | Missing — create stub |
| `ansible/roles/fixpi/files/scripts/production.sh` | Missing — create stub |
| `ansible/roles/fixpi/files/scripts/chroot-mount-pi-fs.bash` | Missing — create minimal working version |
| `ansible/roles/img/files/img2files.sh` | Missing — create minimal working version |
| `ansible/roles/site/tasks/switch.yml` | Missing — create empty task file |

---

## Task 1: Fix pre-existing role gaps

These files were lost during the `git filter-repo` extraction from the monorepo.
They must exist before any role can run successfully.

**Files:**
- Create: `ansible/roles/fixpi/files/scripts/maintenance.sh`
- Create: `ansible/roles/fixpi/files/scripts/production.sh`
- Create: `ansible/roles/fixpi/files/scripts/chroot-mount-pi-fs.bash`
- Create: `ansible/roles/img/files/img2files.sh`
- Create: `ansible/roles/site/tasks/switch.yml`
- Modify: `ansible/roles/site/tasks/main.yml`

- [ ] **Step 1: Create fixpi scripts directory and stub scripts**

The `fixpi/tasks/manage.yml` copies three scripts. `maintenance.sh` and `production.sh` control Pi boot mode (maintenance = writable NFS, production = overlayroot). `chroot-mount-pi-fs.bash` mounts proc/sys/dev inside the NFS root for chroot operations.

```bash
mkdir -p ansible/roles/fixpi/files/scripts
```

Create `ansible/roles/fixpi/files/scripts/maintenance.sh`:
```bash
#!/bin/bash
# Switch Pi NFS root to maintenance mode (writable, no overlayroot)
# Usage: maintenance.sh
set -euo pipefail
echo "Switching to maintenance mode..."
# TODO: Implement based on original monorepo logic
# This typically modifies cmdline.txt to remove overlayroot=tmpfs
```

Create `ansible/roles/fixpi/files/scripts/production.sh`:
```bash
#!/bin/bash
# Switch Pi NFS root to production mode (read-only with overlayroot)
# Usage: production.sh
set -euo pipefail
echo "Switching to production mode..."
# TODO: Implement based on original monorepo logic
# This typically adds overlayroot=tmpfs to cmdline.txt
```

Create `ansible/roles/fixpi/files/scripts/chroot-mount-pi-fs.bash`:
```bash
#!/bin/bash
# Mount proc/sys/dev for chroot operations on Pi NFS root, run a command, unmount.
# Usage: chroot-mount-pi-fs.bash <nfs_root> <mount_point> "<command>"
# Example: chroot-mount-pi-fs.bash /srv/nfs/rpi/bookworm /tmp/pi "apt install -y nfs-common"
set -euo pipefail

NFS_ROOT="$1"
MOUNT_POINT="$2"
COMMAND="$3"

ROOT_DIR="${NFS_ROOT}/root"

# Mount necessary filesystems for chroot
mount --bind /proc "${ROOT_DIR}/proc"
mount --bind /sys "${ROOT_DIR}/sys"
mount --bind /dev "${ROOT_DIR}/dev"
mount --bind /dev/pts "${ROOT_DIR}/dev/pts"

# Run command in chroot
chroot "${ROOT_DIR}" /bin/bash -c "${COMMAND}" || RETVAL=$?

# Unmount in reverse order
umount "${ROOT_DIR}/dev/pts"
umount "${ROOT_DIR}/dev"
umount "${ROOT_DIR}/sys"
umount "${ROOT_DIR}/proc"

exit ${RETVAL:-0}
```

Make all scripts executable:
```bash
chmod +x ansible/roles/fixpi/files/scripts/*.sh
chmod +x ansible/roles/fixpi/files/scripts/*.bash
```

- [ ] **Step 2: Create img2files.sh script**

The `img/tasks/main.yml` calls `files/img2files.sh` to extract the RPi OS image.
It receives `zip_name`, `img_name`, and `dist` as arguments. It must extract the
.img.xz, mount both partitions, and rsync to the NFS root.

Create `ansible/roles/img/files/img2files.sh`:
```bash
#!/bin/bash
# Extract Raspberry Pi OS image to NFS root directories.
# Usage: img2files.sh <zip_name> <img_name> <dist>
# Run from the cache directory containing the downloaded .img.xz
set -euo pipefail

ZIP_NAME="$1"
IMG_NAME="$2"
DIST="$3"

NFS_ROOT="/srv/nfs/rpi/${DIST}"

# Extract if not already done
if [ ! -f "${IMG_NAME}" ]; then
    echo "Extracting ${ZIP_NAME}..."
    xz -dk "${ZIP_NAME}"
fi

# Set up loop device
LOOP=$(losetup --find --show --partscan "${IMG_NAME}")
echo "Loop device: ${LOOP}"

# Wait for partition devices
sleep 1

# Mount boot partition (partition 1)
mkdir -p /tmp/rpi-boot
mount "${LOOP}p1" /tmp/rpi-boot

# Mount root partition (partition 2)
mkdir -p /tmp/rpi-root
mount "${LOOP}p2" /tmp/rpi-root

# Create NFS directories
mkdir -p "${NFS_ROOT}/boot"
mkdir -p "${NFS_ROOT}/root"

# Rsync contents
echo "Syncing boot partition..."
rsync -a /tmp/rpi-boot/ "${NFS_ROOT}/boot/"

echo "Syncing root partition..."
rsync -a /tmp/rpi-root/ "${NFS_ROOT}/root/"

# Cleanup
umount /tmp/rpi-boot
umount /tmp/rpi-root
losetup -d "${LOOP}"
rmdir /tmp/rpi-boot /tmp/rpi-root

echo "Done. NFS root at ${NFS_ROOT}"
```

```bash
chmod +x ansible/roles/img/files/img2files.sh
```

- [ ] **Step 3: Create empty site/tasks/switch.yml and make include conditional**

The `site/tasks/main.yml` includes `switch.yml` which doesn't exist. Create an
empty task file, and make the include conditional so it's skippable.

Create `ansible/roles/site/tasks/switch.yml`:
```yaml
---
# Switch management tasks
# TODO: Implement PoE switch configuration
```

- [ ] **Step 4: Run yamllint to verify new YAML files**

```bash
uv run yamllint -c .yamllint.yml ansible/roles/site/tasks/switch.yml
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/fixpi/files/scripts/ ansible/roles/img/files/img2files.sh ansible/roles/site/tasks/switch.yml
git commit -m "Add missing role scripts lost during monorepo extraction

fixpi: maintenance.sh, production.sh, chroot-mount-pi-fs.bash
img: img2files.sh (extract RPi OS image to NFS root)
site: switch.yml (empty placeholder)"
```

---

## Task 2: Test inventory

Create the minimal test inventory with synthetic data. This must include every
variable referenced by the roles, with values that exercise the template logic
without requiring vault secrets or real hardware.

**Files:**
- Create: `tests/inventory/test-hosts`
- Create: `tests/inventory/group_vars/all/all.yml`
- Create: `tests/inventory/group_vars/all/srv.yml`
- Create: `tests/inventory/group_vars/all/site.yml`
- Create: `tests/inventory/group_vars/all/streaming.yml`
- Create: `tests/inventory/group_vars/all/firewall.yml`
- Create: `tests/inventory/group_vars/all/ssh_keys.yml`
- Create: `tests/inventory/group_vars/all/ci.yml`
- Create: `tests/inventory/host_vars/test-vm.yml`

- [ ] **Step 1: Create inventory directory structure**

```bash
mkdir -p tests/inventory/group_vars/all tests/inventory/host_vars
```

- [ ] **Step 2: Create test-hosts inventory file**

The test VM plays both `nbp` and `pig` groups (server roles) plus `uhubctl`.
The `pi` group uses the `test-pi` host, accessible via ProxyJump through test-vm.

Create `tests/inventory/test-hosts`:
```ini
[nbp]
test-vm ansible_host=127.0.0.1 ansible_port=2222 ansible_user=debian

[uhubctl]
test-vm

[pig]
test-vm

[pi]
test-pi ansible_host=10.21.0.128 ansible_user=testuser

[all:vars]
ansible_ssh_common_args=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null

[pi:vars]
ansible_ssh_common_args=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ProxyJump=debian@127.0.0.1:2222
```

- [ ] **Step 3: Create group_vars matching production structure**

Each file mirrors its production counterpart with the same variable names.

Create `tests/inventory/group_vars/all/all.yml`:
```yaml
---
ansible_python_interpreter: /usr/bin/python3

domain: test.fpgas.online

ssh_password_auth: false
```

Create `tests/inventory/group_vars/all/srv.yml`:
```yaml
---

img_host: http://downloads.raspberrypi.org
dir_date: 2024-07-04
release_date: "{{ dir_date }}"
dist: bookworm

img_path: "raspios_lite_armhf/images/raspios_lite_armhf-{{ dir_date }}"
base_name: "{{ release_date }}-raspios-{{ dist }}-armhf-lite"
img_name: "{{ base_name }}.img"
zip_name: "{{ img_name }}.xz"

user: pi

nfs_root: "/srv/nfs/rpi/{{ dist }}"
```

Create `tests/inventory/group_vars/all/site.yml`:
```yaml
django_dir: /srv/www/pib
static_dir: /srv/www/static
django_project_name: pib

letsencrypt_account_email: test@test.fpgas.online
```

Create `tests/inventory/group_vars/all/streaming.yml`:
```yaml
streaming:
  data_root: /srv/streams
  method: none
```

Create `tests/inventory/group_vars/all/firewall.yml`:
```yaml
---
firewall_rules:
  - proto: tcp
    dports: [80, 443, 1935]
```

Create `tests/inventory/group_vars/all/ssh_keys.yml`:
```yaml
---
ssh_imports: []

ssh_public_keys: []
```

Create `tests/inventory/group_vars/all/ci.yml`:
```yaml
tftpd_port: 6069
```

- [ ] **Step 4: Create host_vars/test-vm.yml**

This is the most critical file — every variable referenced by templates must be
present. Cross-reference with production `host_vars/fpgas.online.yml`.

Create `tests/inventory/host_vars/test-vm.yml`:
```yaml
---
# Network interfaces — must match QEMU NIC configuration
eth_uplink_mac_address: "52:54:00:aa:bb:01"
eth_uplink_static: false
eth_uplink_static_address: 203.0.113.1  # TEST-NET-3 (RFC 5737)

eth_local_mac_address: "52:54:00:aa:bb:02"
eth_local_address: 10.21.0.1
eth_local_netmask: 24
dhcp_range: 10.21.0.128,10.21.0.254,6h

pib_network: 10.21.0

firewall_internal_networks: [10.21.0.0/24]

user_name: testuser

conference_name: test
room_name: test

time_zone: UTC

common_name: test.fpgas.online
subject_alt_names: []

streaming_frontend_aliases: []
streaming_frontend_hostname: test.fpgas.online
domain_name: "{{ streaming_frontend_hostname }}"

fixture_path: test.json

switch_base: base

switch:
  mac: "00:00:00:00:00:00"
  oid: iso.3.6.1.2.1.105.1.1.1.3.1
  host: "10.21.0.200"

  mpi_port: 2
  mpi_ip: "{{ pib_network }}.102"

  nos:
    - {port: 2, mac: "52:54:00:12:34:56", sn: "deadbeef", loc: "", cable_color: ""}

  SNMP_SWITCH_SECURITY_LEVEL: authNoPriv
  SNMP_SWITCH_AUTH_PROTOCOL: SHA512
  SNMP_SWITCH_PRIV_PROTOCOL: AES128
  SNMP_SWITCH_USERNAME: test_snmp_user
  SNMP_SWITCH_AUTHKEY: test_auth_key
  SNMP_SWITCH_PRIVKEY: test_priv_key

pi_pw: "$6$test$testpasswordhash"

# QEMU PXE test clients — triggers pxe role extension
pxe_test_clients:
  - name: test-pi
    sn: "deadbeef"
    mac: "52:54:00:12:34:56"
    ip: "10.21.0.128"
    nfs_root: "/srv/nfs/rpi/bookworm/root"
    console: "ttyAMA0,115200"
```

- [ ] **Step 5: Validate inventory with ansible-inventory**

```bash
ansible-inventory -i tests/inventory/test-hosts --list
```

Expected: JSON output showing test-vm in nbp, pig, uhubctl groups and test-pi in pi group, with all variables resolved.

- [ ] **Step 6: Run yamllint on all inventory files**

```bash
uv run yamllint -c .yamllint.yml tests/inventory/
```

Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add tests/inventory/
git commit -m "Add minimal test inventory for QEMU VM testing

Synthetic data for all role variables. No vault secrets.
test-vm plays nbp+pig+uhubctl, test-pi plays pi via ProxyJump."
```

---

## Task 3: PXE role extension for QEMU test clients

Add support for aarch64 UEFI PXE boot alongside existing RPi boot. Only activated
when `pxe_test_clients` is defined in inventory.

**Files:**
- Create: `ansible/roles/pxe/tasks/test_clients.yml`
- Create: `ansible/roles/pxe/templates/grub.cfg.j2`
- Modify: `ansible/roles/pxe/tasks/main.yml`

- [ ] **Step 1: Create GRUB config template**

Create `ansible/roles/pxe/templates/grub.cfg.j2`:
```
# {{ ansible_managed }}
# GRUB config for QEMU aarch64 PXE test clients

set default=0
set timeout=3

{% for client in pxe_test_clients %}
menuentry "NFS Boot ({{ client.name }})" {
    linux /{{ client.sn }}/kernel8.img root=/dev/nfs nfsroot={{ eth_local_address }}:{{ client.nfs_root }},nfsvers=3 ro ip=dhcp rootwait console={{ client.console }} overlayroot=tmpfs
    initrd /{{ client.sn }}/initrd.img
}
{% endfor %}
```

- [ ] **Step 2: Create test_clients.yml tasks**

Create `ansible/roles/pxe/tasks/test_clients.yml`:
```yaml
---
# UEFI PXE boot support for QEMU aarch64 test clients.
# Only included when pxe_test_clients is defined.

- name: Install GRUB EFI arm64 binary
  apt:
    name:
      - grub-efi-arm64-bin
  tags:
    - pxe-test

- name: Create GRUB TFTP directory
  file:
    path: /srv/tftp/grub
    state: directory
  tags:
    - pxe-test

- name: Copy grubaa64.efi to TFTP root
  copy:
    src: /usr/lib/grub/arm64-efi/monolithic/grubnetaa64.efi
    dest: /srv/tftp/grubaa64.efi
    remote_src: true
  tags:
    - pxe-test

- name: Template GRUB config for test clients
  template:
    src: templates/grub.cfg.j2
    dest: /srv/tftp/grub/grub.cfg
  notify: restart dnsmasq
  tags:
    - pxe-test

- name: Add EFI arm64 architecture match to dnsmasq
  copy:
    content: |
      # QEMU aarch64 UEFI PXE boot support
      dhcp-match=set:efi-arm64,option:client-arch,11
      dhcp-boot=tag:efi-arm64,grubaa64.efi
    dest: /etc/dnsmasq.d/efi-arm64.conf
  notify: restart dnsmasq
  tags:
    - pxe-test

- name: Add DHCP host entries for test clients
  lineinfile:
    path: /etc/dnsmasq.d/pibs.conf
    line: "dhcp-host={{ item.mac }},{{ item.name }},{{ item.ip }}"
    create: false
  with_items: "{{ pxe_test_clients }}"
  notify: restart dnsmasq
  tags:
    - pxe-test
```

- [ ] **Step 3: Add conditional include to pxe/tasks/main.yml**

Add to end of `ansible/roles/pxe/tasks/main.yml`:

```yaml

- name: configure UEFI PXE test clients
  include_tasks: test_clients.yml
  when: pxe_test_clients is defined
  tags:
    - pxe-test
```

- [ ] **Step 4: Run yamllint on new files**

```bash
uv run yamllint -c .yamllint.yml ansible/roles/pxe/tasks/test_clients.yml ansible/roles/pxe/templates/grub.cfg.j2
```

Expected: No errors.

- [ ] **Step 5: Dry-run the pxe role against test inventory**

```bash
ansible-playbook ansible/site.yml -i tests/inventory/test-hosts --limit test-vm --tags pxe-test --check --diff
```

Expected: Shows the tasks that would run (will fail on actual execution since no VM,
but validates templates render without undefined variable errors).

- [ ] **Step 6: Commit**

```bash
git add ansible/roles/pxe/tasks/test_clients.yml ansible/roles/pxe/templates/grub.cfg.j2 ansible/roles/pxe/tasks/main.yml
git commit -m "Add UEFI PXE boot support for QEMU aarch64 test clients

New dnsmasq config serves grubaa64.efi to ARM64 UEFI clients (arch 0x0B).
GRUB config templates kernel+initrd paths from pxe_test_clients variable.
Only activated when pxe_test_clients is defined in inventory."
```

---

## Task 4: Verification playbooks — server roles

Create verify playbooks for all server roles (nbp + pig + uhubctl groups) and the
top-level `verify.yml` orchestrator.

**Files:**
- Create: `ansible/verify.yml`
- Create: `ansible/roles/firewall/verify/main.yml`
- Create: `ansible/roles/nfs/verify/main.yml`
- Create: `ansible/roles/img/verify/main.yml`
- Create: `ansible/roles/fixpi/verify/main.yml`
- Create: `ansible/roles/pxe/verify/main.yml`
- Create: `ansible/roles/site/verify/main.yml`
- Create: `ansible/roles/wssh/verify/main.yml`
- Create: `ansible/roles/cam/stream-server/verify/main.yml`
- Create: `ansible/roles/uhubctl/verify/main.yml`

- [ ] **Step 1: Create top-level verify.yml**

This mirrors `ansible/site.yml` but includes verify tasks instead of setup tasks.

Create `ansible/verify.yml`:
```yaml
---
- name: Verify server roles (nbp)
  hosts: nbp
  become: true
  tasks:
    - name: verify firewall
      include_role:
        name: firewall
        tasks_from: verify/main.yml
      tags: [verify, firewall]

    - name: verify nfs
      include_role:
        name: nfs
        tasks_from: verify/main.yml
      tags: [verify, nfs]

    - name: verify img
      include_role:
        name: img
        tasks_from: verify/main.yml
      tags: [verify, img]

    - name: verify fixpi
      include_role:
        name: fixpi
        tasks_from: verify/main.yml
      tags: [verify, fixpi]

    - name: verify pxe
      include_role:
        name: pxe
        tasks_from: verify/main.yml
      tags: [verify, pxe]

- name: Verify uhubctl
  hosts: uhubctl
  become: true
  tasks:
    - name: verify uhubctl
      include_role:
        name: uhubctl
        tasks_from: verify/main.yml
      tags: [verify, uhubctl]

- name: Verify web roles (pig)
  hosts: pig
  become: true
  tasks:
    - name: verify site
      include_role:
        name: site
        tasks_from: verify/main.yml
      tags: [verify, site]

    - name: verify wssh
      include_role:
        name: wssh
        tasks_from: verify/main.yml
      tags: [verify, wssh]

    - name: verify cam/stream-server
      include_role:
        name: cam/stream-server
        tasks_from: verify/main.yml
      tags: [verify, cam-stream]

- name: Verify Pi roles
  hosts: pi
  become: true
  tasks:
    - name: verify Pi netboot
      include_role:
        name: fixpi
        tasks_from: verify/pi.yml
      tags: [verify, pi-netboot]
```

- [ ] **Step 2: Create firewall verify playbook**

Create `ansible/roles/firewall/verify/main.yml`:
```yaml
---
- name: Check nftables package installed
  command: dpkg -s nftables
  changed_when: false
  register: nftables_pkg

- name: Assert nftables is installed
  assert:
    that: nftables_pkg.rc == 0
    fail_msg: "nftables package is not installed"

- name: Check nftables service is running
  command: systemctl is-active nftables.service
  changed_when: false
  register: nftables_svc

- name: Assert nftables service is active
  assert:
    that: nftables_svc.stdout == "active"
    fail_msg: "nftables service is not running"

- name: Get nftables ruleset
  command: nft list ruleset
  changed_when: false
  register: nft_rules

- name: Assert nftables has input chain
  assert:
    that: "'chain input' in nft_rules.stdout"
    fail_msg: "nftables input chain not found"

- name: Assert nftables has internal_networks chain
  assert:
    that: "'chain internal_networks' in nft_rules.stdout"
    fail_msg: "nftables internal_networks chain not found"

- name: Assert nftables has NAT masquerade
  assert:
    that: "'masquerade' in nft_rules.stdout"
    fail_msg: "nftables NAT masquerade rule not found"
```

- [ ] **Step 3: Create nfs verify playbook**

Create `ansible/roles/nfs/verify/main.yml`:
```yaml
---
- name: Check nfs-kernel-server installed
  command: dpkg -s nfs-kernel-server
  changed_when: false
  register: nfs_pkg

- name: Assert nfs-kernel-server is installed
  assert:
    that: nfs_pkg.rc == 0
    fail_msg: "nfs-kernel-server package is not installed"

- name: Check nfs-kernel-server service is running
  command: systemctl is-active nfs-kernel-server.service
  changed_when: false
  register: nfs_svc

- name: Assert nfs-kernel-server is active
  assert:
    that: nfs_svc.stdout == "active"
    fail_msg: "nfs-kernel-server service is not running"

- name: Check rpcbind service is running
  command: systemctl is-active rpcbind.service
  changed_when: false
  register: rpcbind_svc

- name: Assert rpcbind is active
  assert:
    that: rpcbind_svc.stdout == "active"
    fail_msg: "rpcbind service is not running"

- name: Read /etc/exports
  command: cat /etc/exports
  changed_when: false
  register: exports_content

- name: Assert exports contains NFS root
  assert:
    that: "nfs_root in exports_content.stdout"
    fail_msg: "/etc/exports does not contain {{ nfs_root }}"

- name: Check NFS root directory exists
  stat:
    path: "{{ nfs_root }}"
  register: nfs_dir

- name: Assert NFS root directory exists
  assert:
    that: nfs_dir.stat.exists
    fail_msg: "NFS root directory {{ nfs_root }} does not exist"
```

- [ ] **Step 4: Create img verify playbook**

Create `ansible/roles/img/verify/main.yml`:
```yaml
---
- name: Check NFS root has extracted filesystem
  stat:
    path: "{{ nfs_root }}/root/bin/bash"
  register: root_bash

- name: Assert NFS root filesystem is populated
  assert:
    that: root_bash.stat.exists
    fail_msg: "{{ nfs_root }}/root/bin/bash not found — img role did not extract filesystem"

- name: Check NFS boot directory exists
  stat:
    path: "{{ nfs_root }}/boot/kernel8.img"
  register: boot_kernel

- name: Assert boot kernel exists
  assert:
    that: boot_kernel.stat.exists
    fail_msg: "{{ nfs_root }}/boot/kernel8.img not found — boot partition not extracted"
```

- [ ] **Step 5: Create fixpi verify playbook**

Create `ansible/roles/fixpi/verify/main.yml`:
```yaml
---
- name: Check qemu-user-static installed
  command: dpkg -s qemu-user-static
  changed_when: false
  register: qemu_pkg

- name: Assert qemu-user-static is installed
  assert:
    that: qemu_pkg.rc == 0
    fail_msg: "qemu-user-static package is not installed"

- name: Check management scripts exist
  stat:
    path: "/usr/local/sbin/{{ item }}"
  register: mgmt_scripts
  with_items:
    - maintenance.sh
    - production.sh
    - chroot-mount-pi-fs.bash

- name: Assert management scripts exist
  assert:
    that: item.stat.exists
    fail_msg: "Management script {{ item.item }} not found"
  with_items: "{{ mgmt_scripts.results }}"

- name: Check bootcode.bin symlink in TFTP root
  stat:
    path: /srv/tftp/bootcode.bin
  register: bootcode

- name: Assert bootcode.bin symlink exists
  assert:
    that: bootcode.stat.exists and bootcode.stat.islnk
    fail_msg: "/srv/tftp/bootcode.bin symlink not found"

- name: Check TFTP serial number directories
  stat:
    path: "/srv/tftp/{{ item.sn }}"
  register: sn_dirs
  with_items: "{{ switch.nos }}"

- name: Assert serial number TFTP directories exist
  assert:
    that: item.stat.exists and item.stat.islnk
    fail_msg: "TFTP serial directory /srv/tftp/{{ item.item.sn }} not found"
  with_items: "{{ sn_dirs.results }}"

- name: Check cmdline.txt was templated
  stat:
    path: "{{ nfs_root }}/boot/cmdline.txt"
  register: cmdline

- name: Assert cmdline.txt exists
  assert:
    that: cmdline.stat.exists
    fail_msg: "{{ nfs_root }}/boot/cmdline.txt not found"

- name: Read cmdline.txt contents
  command: cat "{{ nfs_root }}/boot/cmdline.txt"
  changed_when: false
  register: cmdline_content

- name: Assert cmdline.txt contains NFS root
  assert:
    that: "'nfsroot=' in cmdline_content.stdout"
    fail_msg: "cmdline.txt does not contain nfsroot= parameter"
```

- [ ] **Step 6: Create pxe verify playbook**

Create `ansible/roles/pxe/verify/main.yml`:
```yaml
---
- name: Check dnsmasq service is running
  command: systemctl is-active dnsmasq.service
  changed_when: false
  register: dnsmasq_svc

- name: Assert dnsmasq is active
  assert:
    that: dnsmasq_svc.stdout == "active"
    fail_msg: "dnsmasq service is not running"

- name: Check rpi.conf exists
  stat:
    path: /etc/dnsmasq.d/rpi.conf
  register: rpi_conf

- name: Assert rpi.conf exists
  assert:
    that: rpi_conf.stat.exists
    fail_msg: "/etc/dnsmasq.d/rpi.conf not found"

- name: Check pibs.conf exists
  stat:
    path: /etc/dnsmasq.d/pibs.conf
  register: pibs_conf

- name: Assert pibs.conf exists
  assert:
    that: pibs_conf.stat.exists
    fail_msg: "/etc/dnsmasq.d/pibs.conf not found"

- name: Read pibs.conf contents
  command: cat /etc/dnsmasq.d/pibs.conf
  changed_when: false
  register: pibs_content

- name: Assert pibs.conf contains DHCP host entries
  assert:
    that: "'dhcp-host=' in pibs_content.stdout"
    fail_msg: "pibs.conf does not contain dhcp-host entries"

- name: Check TFTP root has bootcode.bin
  stat:
    path: /srv/tftp/bootcode.bin
  register: tftp_bootcode

- name: Assert TFTP bootcode.bin exists
  assert:
    that: tftp_bootcode.stat.exists
    fail_msg: "/srv/tftp/bootcode.bin not found"

# Test client checks (only when pxe_test_clients defined)
- name: Check GRUB EFI binary in TFTP root
  stat:
    path: /srv/tftp/grubaa64.efi
  register: grub_efi
  when: pxe_test_clients is defined

- name: Assert GRUB EFI exists
  assert:
    that: grub_efi.stat.exists
    fail_msg: "/srv/tftp/grubaa64.efi not found"
  when: pxe_test_clients is defined

- name: Check GRUB config exists
  stat:
    path: /srv/tftp/grub/grub.cfg
  register: grub_cfg
  when: pxe_test_clients is defined

- name: Assert GRUB config exists
  assert:
    that: grub_cfg.stat.exists
    fail_msg: "/srv/tftp/grub/grub.cfg not found"
  when: pxe_test_clients is defined

- name: Check EFI arm64 dnsmasq config
  stat:
    path: /etc/dnsmasq.d/efi-arm64.conf
  register: efi_conf
  when: pxe_test_clients is defined

- name: Assert EFI arm64 dnsmasq config exists
  assert:
    that: efi_conf.stat.exists
    fail_msg: "/etc/dnsmasq.d/efi-arm64.conf not found"
  when: pxe_test_clients is defined
```

- [ ] **Step 7: Create site verify playbook**

Create `ansible/roles/site/verify/main.yml`:
```yaml
---
- name: Check nginx package installed
  command: dpkg -s nginx
  changed_when: false
  register: nginx_pkg

- name: Assert nginx is installed
  assert:
    that: nginx_pkg.rc == 0
    fail_msg: "nginx package is not installed"

- name: Check nginx service is running
  command: systemctl is-active nginx.service
  changed_when: false
  register: nginx_svc

- name: Assert nginx is active
  assert:
    that: nginx_svc.stdout == "active"
    fail_msg: "nginx service is not running"

- name: Check Django virtualenv exists
  stat:
    path: "{{ django_dir }}/venv/bin/python"
  register: django_venv

- name: Assert Django virtualenv exists
  assert:
    that: django_venv.stat.exists
    fail_msg: "{{ django_dir }}/venv/bin/python not found"

- name: Check gunicorn socket is active
  command: systemctl is-active gunicorn.socket
  changed_when: false
  register: gunicorn_socket
  failed_when: false

- name: Report gunicorn socket status
  debug:
    msg: "gunicorn.socket is {{ gunicorn_socket.stdout }}"
```

- [ ] **Step 8: Create wssh verify playbook**

Create `ansible/roles/wssh/verify/main.yml`:
```yaml
---
- name: Check wssh socket exists
  stat:
    path: /etc/systemd/system/wssh.socket
  register: wssh_socket_file

- name: Assert wssh socket unit exists
  assert:
    that: wssh_socket_file.stat.exists
    fail_msg: "wssh.socket unit file not found"

- name: Check wssh service unit exists
  command: systemctl list-unit-files wssh.service
  changed_when: false
  register: wssh_svc_unit

- name: Assert wssh service unit is present
  assert:
    that: "'wssh.service' in wssh_svc_unit.stdout"
    fail_msg: "wssh.service unit not found"
```

- [ ] **Step 9: Create cam/stream-server verify playbook**

Create `ansible/roles/cam/stream-server/verify/main.yml`:
```yaml
---
- name: Check nginx-rtmp config exists
  stat:
    path: /etc/nginx/modules-enabled
  register: nginx_modules

- name: Assert nginx modules directory exists
  assert:
    that: nginx_modules.stat.exists
    fail_msg: "nginx modules-enabled directory not found"
```

- [ ] **Step 10: Create uhubctl verify playbook**

Create `ansible/roles/uhubctl/verify/main.yml`:
```yaml
---
- name: Check uhubctl package installed
  command: dpkg -s uhubctl
  changed_when: false
  register: uhubctl_pkg
  failed_when: false

- name: Assert uhubctl is installed
  assert:
    that: uhubctl_pkg.rc == 0
    fail_msg: "uhubctl package is not installed"

- name: Check udev rules exist
  stat:
    path: /etc/udev/rules.d/52-uhubctl.rules
  register: udev_rules

- name: Assert uhubctl udev rules exist
  assert:
    that: udev_rules.stat.exists
    fail_msg: "uhubctl udev rules not found at /etc/udev/rules.d/52-uhubctl.rules"
```

- [ ] **Step 11: Create Pi netboot verify playbook**

This runs ON the PXE-booted Pi VM. It verifies the netboot chain worked.

Create `ansible/roles/fixpi/verify/pi.yml`:
```yaml
---
- name: Check NFS root is mounted
  command: mount
  changed_when: false
  register: mount_output

- name: Assert NFS root is mounted
  assert:
    that: "'nfs' in mount_output.stdout"
    fail_msg: "No NFS mount found — Pi may not have netbooted correctly"

- name: Check network interface has DHCP address
  command: ip -4 addr show
  changed_when: false
  register: ip_output

- name: Assert Pi has 10.21.0.x address
  assert:
    that: "'10.21.0.' in ip_output.stdout"
    fail_msg: "Pi does not have a 10.21.0.x address from DHCP"

- name: Check Python3 is available
  command: python3 --version
  changed_when: false
  register: python_version

- name: Assert Python3 works
  assert:
    that: python_version.rc == 0
    fail_msg: "python3 is not available on the Pi"
```

- [ ] **Step 12: Run yamllint on all verify playbooks**

```bash
uv run yamllint -c .yamllint.yml ansible/verify.yml ansible/roles/*/verify/ ansible/roles/cam/*/verify/
```

Expected: No errors.

- [ ] **Step 13: Commit**

```bash
git add ansible/verify.yml ansible/roles/*/verify/ ansible/roles/cam/*/verify/
git commit -m "Add verification playbooks for all roles

Each role gets verify/main.yml with assertion-only checks.
Top-level verify.yml mirrors site.yml structure.
Usable against test VMs and production hosts."
```

---

## Task 5: Cloud-init module

Generate seed ISOs for the server VM's cloud-init configuration.

**Files:**
- Create: `tests/vm/cloud_init.py`

- [ ] **Step 1: Create cloud_init.py**

Create `tests/vm/cloud_init.py`:
```python
"""Generate cloud-init seed ISOs for QEMU VMs."""

import subprocess
import tempfile
from pathlib import Path


def create_seed_iso(
    output_path: Path,
    ssh_pubkey: str,
    hostname: str = "test-vm",
    eth_local_mac: str = "52:54:00:aa:bb:02",
    eth_local_ip: str = "10.21.0.1/24",
) -> Path:
    """Create a cloud-init NoCloud seed ISO.

    Configures the VM with SSH key, Python3, qemu-guest-agent,
    and a static IP on the second NIC (for the socket VLAN).
    """
    user_data = f"""#cloud-config
hostname: {hostname}
manage_etc_hosts: true

users:
  - name: debian
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - {ssh_pubkey}

packages:
  - python3
  - qemu-guest-agent

# Start guest agent
runcmd:
  - systemctl enable --now qemu-guest-agent

# Configure second NIC with static IP for internal VLAN
write_files:
  - path: /etc/network/interfaces.d/eth1.cfg
    content: |
      auto eth1
      iface eth1 inet static
        address {eth_local_ip}

# Disable slow cloud-init modules for faster boot
package_update: false
package_upgrade: false
"""

    meta_data = f"""instance-id: {hostname}
local-hostname: {hostname}
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        ud_path = Path(tmpdir) / "user-data"
        md_path = Path(tmpdir) / "meta-data"
        ud_path.write_text(user_data)
        md_path.write_text(meta_data)

        subprocess.run(
            ["cloud-localds", str(output_path), str(ud_path), str(md_path)],
            check=True,
            capture_output=True,
        )

    return output_path
```

- [ ] **Step 2: Test seed ISO creation manually**

```bash
uv run python -c "
from tests.vm.cloud_init import create_seed_iso
from pathlib import Path
p = create_seed_iso(Path('tmp/test-seed.iso'), 'ssh-ed25519 AAAA_test_key test@test')
print(f'Created: {p} ({p.stat().st_size} bytes)')
"
```

Expected: Creates an ISO file in `tmp/`. Clean up afterwards.

```bash
rm -f tmp/test-seed.iso && rmdir tmp 2>/dev/null || true
```

- [ ] **Step 3: Commit**

```bash
git add tests/vm/cloud_init.py
git commit -m "Add cloud-init seed ISO generator for server VM

Configures SSH key, Python3, guest agent, and static IP on
internal NIC for QEMU socket VLAN."
```

---

## Task 6: VM manager module

Core QEMU VM lifecycle management: boot, wait for SSH/guest agent, shutdown.

**Files:**
- Create: `tests/vm/vm_manager.py`

- [ ] **Step 1: Create vm_manager.py**

Create `tests/vm/vm_manager.py`:
```python
"""QEMU VM lifecycle management for Ansible testing."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import paramiko


IMAGES_DIR = Path(__file__).parent / "images"
DEBIAN_CLOUD_URL = "https://cloud.debian.org/images/cloud/{dist}/latest/debian-12-genericcloud-amd64.qcow2"
DEBIAN_CLOUD_URL_ARM64 = "https://cloud.debian.org/images/cloud/{dist}/latest/debian-12-genericcloud-arm64.qcow2"


def kvm_available() -> bool:
    """Check if KVM acceleration is available."""
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


def download_image(url: str, dest: Path) -> Path:
    """Download a cloud image if not already cached."""
    if dest.exists():
        print(f"Using cached image: {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} -> {dest}")
    subprocess.run(
        ["wget", "-q", "--show-progress", "-O", str(dest), url],
        check=True,
    )
    return dest


def create_overlay(base_image: Path, overlay_path: Path) -> Path:
    """Create a qcow2 overlay so the base image stays clean."""
    subprocess.run(
        [
            "qemu-img", "create", "-f", "qcow2",
            "-b", str(base_image.resolve()), "-F", "qcow2",
            str(overlay_path),
        ],
        check=True,
        capture_output=True,
    )
    return overlay_path


def generate_ssh_keypair(key_path: Path) -> tuple[Path, str]:
    """Generate an ephemeral ed25519 SSH keypair. Returns (private_key_path, public_key_string)."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        key_path.unlink()
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
        check=True,
    )
    pubkey = key_path.with_suffix(".pub").read_text().strip()
    return key_path, pubkey


class QemuGuestAgent:
    """Communicate with QEMU guest agent over Unix socket."""

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path

    def _send_command(self, command: str, arguments: dict | None = None, timeout: float = 10) -> dict:
        """Send a QGA command and return the response."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(self.socket_path))
        try:
            msg = {"execute": command}
            if arguments:
                msg["arguments"] = arguments
            sock.sendall(json.dumps(msg).encode() + b"\n")
            # Read response (may need multiple reads)
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            return json.loads(data.decode().strip())
        finally:
            sock.close()

    def ping(self, timeout: float = 5) -> bool:
        """Check if guest agent is responsive."""
        try:
            self._send_command("guest-ping", timeout=timeout)
            return True
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
            return False

    def get_interfaces(self) -> list[dict]:
        """Get network interface information from guest."""
        result = self._send_command("guest-network-get-interfaces")
        return result.get("return", [])

    def exec_command(self, command: str) -> dict:
        """Execute a command in the guest."""
        result = self._send_command(
            "guest-exec",
            {"path": "/bin/bash", "arg": ["-c", command], "capture-output": True},
        )
        pid = result.get("return", {}).get("pid")
        if pid is None:
            return result
        # Wait for completion
        time.sleep(1)
        status = self._send_command("guest-exec-status", {"pid": pid})
        return status.get("return", {})


class VMManager:
    """Manage a QEMU VM lifecycle."""

    def __init__(self, name: str, workdir: Path):
        self.name = name
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.process: subprocess.Popen | None = None
        self.qga_socket = self.workdir / f"{name}-qga.sock"
        self.serial_log = self.workdir / f"{name}-serial.log"
        self.guest_agent = QemuGuestAgent(self.qga_socket)

    def boot_server(
        self,
        overlay: Path,
        seed_iso: Path,
        ssh_port: int = 2222,
        vlan_port: int = 12345,
        memory: int = 2048,
    ) -> None:
        """Boot the x86_64 server VM with two NICs + guest agent."""
        accel = "kvm" if kvm_available() else "tcg"
        cpu = "host" if accel == "kvm" else "max"
        cmd = [
            "qemu-system-x86_64",
            "-machine", f"q35,accel={accel}",
            "-cpu", cpu,
            "-m", str(memory),
            "-smp", "2",
            # Boot disk (overlay on cloud image)
            "-drive", f"file={overlay},format=qcow2,if=virtio",
            # Cloud-init seed ISO
            "-drive", f"file={seed_iso},format=raw,if=virtio",
            # NIC 1: user-mode for SSH from host
            "-netdev", f"user,id=net0,hostfwd=tcp::{ssh_port}-:22",
            "-device", "virtio-net-pci,netdev=net0,mac=52:54:00:aa:bb:01",
            # NIC 2: socket-listen for internal VLAN
            "-netdev", f"socket,id=net1,listen=:{vlan_port}",
            "-device", "virtio-net-pci,netdev=net1,mac=52:54:00:aa:bb:02",
            # Guest agent
            "-device", "virtio-serial",
            "-device", "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
            "-chardev", f"socket,path={self.qga_socket},server=on,wait=off,id=qga0",
            # Headless
            "-nographic",
            "-serial", f"file:{self.serial_log}",
        ]
        print(f"[{self.name}] Booting server VM (accel={accel})...")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def boot_pi(
        self,
        vlan_port: int = 12345,
        memory: int = 1024,
        efi_firmware: str = "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd",
    ) -> None:
        """Boot the aarch64 Pi VM — diskless, PXE boot from server."""
        cmd = [
            "qemu-system-aarch64",
            "-machine", "virt,accel=tcg",
            "-cpu", "max",
            "-m", str(memory),
            # UEFI firmware for PXE
            "-bios", efi_firmware,
            # Single NIC: socket-connect to server VLAN
            "-netdev", f"socket,id=lan0,connect=:{vlan_port}",
            "-device", "virtio-net-pci,netdev=lan0,mac=52:54:00:12:34:56",
            # Guest agent
            "-device", "virtio-serial",
            "-device", "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
            "-chardev", f"socket,path={self.qga_socket},server=on,wait=off,id=qga0",
            # Headless, serial to log
            "-nographic",
            "-serial", f"file:{self.serial_log}",
            # NO disk — PXE boot only
        ]
        print(f"[{self.name}] Booting Pi VM (diskless PXE, aarch64 TCG)...")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def wait_for_guest_agent(self, timeout: int = 180) -> bool:
        """Wait for guest agent to become responsive."""
        print(f"[{self.name}] Waiting for guest agent...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.guest_agent.ping():
                print(f"[{self.name}] Guest agent ready.")
                return True
            time.sleep(2)
        print(f"[{self.name}] Guest agent timeout after {timeout}s")
        return False

    def wait_for_ssh(
        self,
        host: str = "127.0.0.1",
        port: int = 2222,
        username: str = "debian",
        key_path: Path | None = None,
        timeout: int = 120,
        proxy_jump: str | None = None,
    ) -> paramiko.SSHClient:
        """Wait for SSH to become available and return a connected client."""
        print(f"[{self.name}] Waiting for SSH on {host}:{port}...")
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                sock = None
                if proxy_jump:
                    # Parse proxy_jump as "user@host:port"
                    proxy_user, proxy_rest = proxy_jump.split("@")
                    proxy_host, proxy_port = proxy_rest.split(":")
                    proxy = paramiko.SSHClient()
                    proxy.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    proxy.connect(
                        proxy_host, port=int(proxy_port),
                        username=proxy_user, key_filename=str(key_path),
                        timeout=5,
                    )
                    transport = proxy.get_transport()
                    sock = transport.open_channel(
                        "direct-tcpip", (host, port), ("127.0.0.1", 0)
                    )

                client.connect(
                    host, port=port, username=username,
                    key_filename=str(key_path) if key_path else None,
                    timeout=5, auth_timeout=10,
                    sock=sock,
                )
                print(f"[{self.name}] SSH ready.")
                return client
            except (
                paramiko.ssh_exception.NoValidConnectionsError,
                paramiko.ssh_exception.SSHException,
                socket.timeout,
                ConnectionRefusedError,
                OSError,
            ):
                time.sleep(2)

        raise TimeoutError(f"[{self.name}] SSH not ready after {timeout}s")

    def shutdown(self, ssh_client: paramiko.SSHClient | None = None) -> None:
        """Graceful shutdown: SSH -> guest agent -> SIGTERM."""
        if ssh_client:
            try:
                print(f"[{self.name}] Shutting down via SSH...")
                ssh_client.exec_command("sudo shutdown -h now")
                ssh_client.close()
                if self.process:
                    self.process.wait(timeout=30)
                return
            except Exception:
                pass

        if self.guest_agent.ping():
            try:
                print(f"[{self.name}] Shutting down via guest agent...")
                self.guest_agent._send_command("guest-shutdown")
                if self.process:
                    self.process.wait(timeout=30)
                return
            except Exception:
                pass

        if self.process:
            print(f"[{self.name}] Sending SIGTERM...")
            self.process.terminate()
            self.process.wait(timeout=10)

    def cleanup(self) -> None:
        """Remove temporary files (overlays, seed ISOs, keys, sockets)."""
        for pattern in ["*.qcow2", "*.iso", "*.sock", "*-serial.log"]:
            for f in self.workdir.glob(pattern):
                f.unlink(missing_ok=True)

    def is_alive(self) -> bool:
        """Check if the QEMU process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None
```

- [ ] **Step 2: Test VMManager can be imported**

```bash
uv run python -c "from tests.vm.vm_manager import VMManager, kvm_available; print(f'KVM available: {kvm_available()}')"
```

Expected: Prints `KVM available: True` or `KVM available: False` without errors.

- [ ] **Step 3: Commit**

```bash
git add tests/vm/vm_manager.py
git commit -m "Add QEMU VM lifecycle manager

VMManager class: boot server/Pi VMs, wait for SSH/guest agent,
graceful shutdown with fallback chain, overlay/cleanup management."
```

---

## Task 7: Network helpers module

Socket networking readiness checks and ProxyJump configuration.

**Files:**
- Create: `tests/vm/network.py`

- [ ] **Step 1: Create network.py**

Create `tests/vm/network.py`:
```python
"""Network helpers for QEMU VM testing."""

import socket
import time


def wait_for_socket_listen(port: int, host: str = "127.0.0.1", timeout: int = 30) -> bool:
    """Wait for a TCP port to be listening (for QEMU socket networking sequencing)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def proxy_jump_string(user: str, host: str, port: int) -> str:
    """Generate a ProxyJump connection string."""
    return f"{user}@{host}:{port}"
```

- [ ] **Step 2: Commit**

```bash
git add tests/vm/network.py
git commit -m "Add network helpers for socket readiness and ProxyJump"
```

---

## Task 8: CLI entry point

Main test orchestrator with argument parsing and phase execution.

**Files:**
- Create: `tests/vm/run_tests.py`
- Create: `tests/vm/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/vm/images/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Create package structure and .gitignore**

```bash
touch tests/__init__.py tests/vm/__init__.py tests/vm/images/.gitkeep
```

Add to `.gitignore`:
```
# QEMU VM test images
tests/vm/images/*.qcow2
tests/vm/images/*.img*
```

- [ ] **Step 2: Create run_tests.py**

Create `tests/vm/run_tests.py`:
```python
#!/usr/bin/env python3
"""QEMU VM integration test harness for fpgas.online Ansible infrastructure.

Usage:
    uv run tests/vm/run_tests.py [options]

Boots a Debian server VM, applies Ansible roles, optionally PXE-boots
a diskless aarch64 Pi VM from the server, and verifies everything works.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from tests.vm.cloud_init import create_seed_iso
from tests.vm.network import proxy_jump_string, wait_for_socket_listen
from tests.vm.vm_manager import (
    DEBIAN_CLOUD_URL,
    IMAGES_DIR,
    VMManager,
    create_overlay,
    download_image,
    generate_ssh_keypair,
)

REPO_ROOT = Path(__file__).parent.parent.parent
ANSIBLE_DIR = REPO_ROOT / "ansible"
TEST_INVENTORY = REPO_ROOT / "tests" / "inventory" / "test-hosts"

SSH_PORT = 2222
VLAN_PORT = 12345


def run_ansible(playbook: str, inventory: Path, limit: str, extra_args: list[str] | None = None) -> int:
    """Run an ansible-playbook command and return exit code."""
    cmd = [
        "ansible-playbook",
        str(ANSIBLE_DIR / playbook),
        "-i", str(inventory),
        "--limit", limit,
        "--ssh-extra-args", "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
    ]
    if extra_args:
        cmd.extend(extra_args)
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}\n")
    result = subprocess.run(cmd)
    return result.returncode


def phase_server(args, workdir: Path) -> VMManager | None:
    """Run the server phase: boot VM, apply roles, verify."""
    dist = args.distro
    image_url = DEBIAN_CLOUD_URL.format(dist=dist)
    image_path = IMAGES_DIR / f"debian-12-genericcloud-amd64.qcow2"

    # Download and prepare
    download_image(image_url, image_path)
    key_path, pubkey = generate_ssh_keypair(workdir / "test_key")
    seed_iso = create_seed_iso(workdir / "seed.iso", pubkey)
    overlay = create_overlay(image_path, workdir / "server-overlay.qcow2")

    # Boot server
    server = VMManager("server", workdir)
    server.boot_server(overlay, seed_iso, ssh_port=SSH_PORT, vlan_port=VLAN_PORT)

    if not server.wait_for_guest_agent(timeout=180):
        print("ERROR: Server VM guest agent did not respond.")
        print(f"Serial log: {server.serial_log}")
        server.shutdown()
        return None

    try:
        ssh = server.wait_for_ssh(port=SSH_PORT, key_path=key_path)
        ssh.close()
    except TimeoutError:
        print("ERROR: Server VM SSH did not become available.")
        server.shutdown()
        return None

    # Select inventory
    if args.inventory == "production":
        inventory = REPO_ROOT / "ansible" / "inventory"
        extra = ["--vault-password-file", args.vault_password_file] if args.vault_password_file else []
    else:
        inventory = TEST_INVENTORY
        extra = []

    # Set SSH key for ansible
    extra.extend([
        "-e", f"ansible_ssh_private_key_file={key_path}",
    ])

    # Run site.yml
    rc = run_ansible("site.yml", inventory, "test-vm", extra)
    if rc != 0:
        print(f"ERROR: site.yml failed with exit code {rc}")
        if not args.keep_vm:
            server.shutdown()
            server.cleanup()
        return server if args.keep_vm else None

    # Run verify.yml
    rc = run_ansible("verify.yml", inventory, "test-vm", extra)
    if rc != 0:
        print(f"WARNING: verify.yml failed with exit code {rc}")

    if args.ssh_to_server:
        print(f"\nSSH into server: ssh -i {key_path} -p {SSH_PORT} -o StrictHostKeyChecking=no debian@127.0.0.1")
        print("Press Ctrl+C to exit and continue teardown.")
        try:
            subprocess.run([
                "ssh", "-i", str(key_path), "-p", str(SSH_PORT),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "debian@127.0.0.1",
            ])
        except KeyboardInterrupt:
            pass

    return server


def phase_pi(args, workdir: Path, server: VMManager) -> bool:
    """Run the Pi phase: PXE-boot diskless Pi VM from server."""
    # Verify server socket is listening
    if not wait_for_socket_listen(VLAN_PORT):
        print("ERROR: Server VLAN socket not listening.")
        return False

    # Check UEFI firmware exists
    efi_path = "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd"
    if not Path(efi_path).exists():
        print(f"ERROR: UEFI firmware not found at {efi_path}")
        print("Install: sudo apt install qemu-efi-aarch64")
        return False

    # Boot Pi (diskless)
    pi = VMManager("pi", workdir)
    pi.boot_pi(vlan_port=VLAN_PORT, efi_firmware=efi_path)

    if not pi.wait_for_guest_agent(timeout=300):
        print("WARNING: Pi VM guest agent did not respond.")
        print(f"Serial log: {pi.serial_log}")
        # Continue — the Pi may still be booting

    # Wait for SSH via ProxyJump through server
    key_path = workdir / "test_key"
    proxy = proxy_jump_string("debian", "127.0.0.1", SSH_PORT)

    try:
        ssh = pi.wait_for_ssh(
            host="10.21.0.128", port=22, username="testuser",
            key_path=key_path, proxy_jump=proxy, timeout=300,
        )
        ssh.close()
    except TimeoutError:
        print("ERROR: Pi VM SSH not reachable via ProxyJump.")
        pi.shutdown()
        return False

    # Select inventory
    inventory = TEST_INVENTORY
    extra = ["-e", f"ansible_ssh_private_key_file={key_path}"]

    # Run verify.yml for Pi
    rc = run_ansible("verify.yml", inventory, "test-pi", extra)
    if rc != 0:
        print(f"WARNING: Pi verify.yml failed with exit code {rc}")

    if args.ssh_to_pi:
        print(f"\nSSH into Pi via ProxyJump:")
        print(f"  ssh -i {key_path} -o StrictHostKeyChecking=no -o ProxyJump=debian@127.0.0.1:{SSH_PORT} testuser@10.21.0.128")
        print("Press Ctrl+C to exit.")
        try:
            subprocess.run([
                "ssh", "-i", str(key_path),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", f"ProxyJump=debian@127.0.0.1:{SSH_PORT}",
                "testuser@10.21.0.128",
            ])
        except KeyboardInterrupt:
            pass

    if not args.keep_vm:
        pi.shutdown()

    return rc == 0


def main():
    parser = argparse.ArgumentParser(description="QEMU VM integration tests for fpgas.online")
    parser.add_argument("--distro", choices=["bookworm", "trixie"], default="bookworm")
    parser.add_argument("--phase", choices=["server", "pi", "all"], default="all")
    parser.add_argument("--keep-vm", action="store_true", help="Don't teardown on success")
    parser.add_argument("--inventory", choices=["minimal", "production"], default="minimal")
    parser.add_argument("--vault-password-file", type=str, help="Vault password file for production inventory")
    parser.add_argument("--ssh-to-server", action="store_true", help="Drop into SSH on server after setup")
    parser.add_argument("--ssh-to-pi", action="store_true", help="Drop into SSH on Pi via ProxyJump")
    args = parser.parse_args()

    workdir = Path(__file__).parent / "workdir"
    workdir.mkdir(exist_ok=True)

    server = None
    try:
        if args.phase in ("server", "all"):
            server = phase_server(args, workdir)
            if server is None:
                print("\nSERVER PHASE FAILED")
                sys.exit(1)
            print("\nSERVER PHASE PASSED")

        if args.phase in ("pi", "all"):
            if server is None:
                print("ERROR: Pi phase requires a running server VM (use --phase all)")
                sys.exit(1)
            success = phase_pi(args, workdir, server)
            if not success:
                print("\nPI PHASE FAILED")
                sys.exit(1)
            print("\nPI PHASE PASSED")

    finally:
        if server and not args.keep_vm:
            server.shutdown()
            server.cleanup()

    print("\nALL PHASES PASSED")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Test CLI help works**

```bash
uv run python -m tests.vm.run_tests --help
```

Expected: Shows argument parser help with all options.

- [ ] **Step 4: Commit**

```bash
git add tests/ .gitignore
git commit -m "Add QEMU VM test harness CLI

Entry point: uv run tests/vm/run_tests.py
Phases: server (boot + Ansible + verify), pi (PXE boot + verify).
Supports --keep-vm, --ssh-to-server, --ssh-to-pi for debugging."
```

---

## Task 9: GitHub Actions CI workflow

**Files:**
- Create: `.github/workflows/vm-test.yml`

- [ ] **Step 1: Create CI workflow**

Create `.github/workflows/vm-test.yml`:
```yaml
---
name: VM Integration Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  vm-test:
    runs-on: ubuntu-latest
    timeout-minutes: 60

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Install system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y \
            qemu-system-x86 \
            qemu-system-arm \
            qemu-efi-aarch64 \
            qemu-utils \
            cloud-image-utils \
            ansible

      - name: Enable KVM (if available)
        run: |
          if [ -e /dev/kvm ]; then
            echo "KVM_AVAILABLE=true" >> "$GITHUB_ENV"
            sudo chmod 666 /dev/kvm
            echo "KVM acceleration enabled"
          else
            echo "KVM_AVAILABLE=false" >> "$GITHUB_ENV"
            echo "KVM not available, using TCG (slower)"
          fi

      - name: Install Python dependencies
        run: uv pip install paramiko --system

      - name: Cache VM images
        uses: actions/cache@v4
        with:
          path: tests/vm/images
          key: vm-images-bookworm-v1

      - name: Run server VM tests
        run: uv run tests/vm/run_tests.py --phase server
```

- [ ] **Step 2: Run yamllint on workflow**

```bash
uv run yamllint -c .yamllint.yml .github/workflows/vm-test.yml
```

Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/vm-test.yml
git commit -m "Add GitHub Actions workflow for VM integration tests

Runs server phase on every push/PR. Caches VM images between runs.
KVM auto-detected for acceleration."
```

---

## Task 10: End-to-end local test

Run the full test locally to validate everything works together.

- [ ] **Step 1: Verify system dependencies are installed**

```bash
which qemu-system-x86_64 qemu-system-aarch64 qemu-img cloud-localds
dpkg -s qemu-efi-aarch64 2>/dev/null | head -3
```

If any are missing, install:
```bash
sudo apt-get install -y qemu-system-x86 qemu-system-arm qemu-efi-aarch64 qemu-utils cloud-image-utils
```

- [ ] **Step 2: Install Python dependency**

```bash
uv pip install paramiko
```

- [ ] **Step 3: Run server phase only**

```bash
uv run tests/vm/run_tests.py --phase server --keep-vm
```

Expected: Downloads cloud image, boots VM, runs Ansible, runs verify.
This is the first real test — expect issues. Debug using:
- Serial log: `tests/vm/workdir/server-serial.log`
- SSH into VM: `ssh -i tests/vm/workdir/test_key -p 2222 -o StrictHostKeyChecking=no debian@127.0.0.1`

- [ ] **Step 4: If server phase passes, run Pi phase**

```bash
uv run tests/vm/run_tests.py --phase all --keep-vm
```

Expected: Server phase completes, then Pi VM PXE boots from server.
Pi phase is slower (aarch64 TCG). Debug using:
- Serial log: `tests/vm/workdir/pi-serial.log`
- SSH via ProxyJump: see CLI output for exact command

- [ ] **Step 5: Clean up**

```bash
# Kill any remaining QEMU processes
pkill -f "qemu-system" || true
# Remove workdir
rm -rf tests/vm/workdir/
```

- [ ] **Step 6: Run full test without --keep-vm**

```bash
uv run tests/vm/run_tests.py --phase server
```

Expected: Completes and cleans up automatically.

- [ ] **Step 7: Final commit with any fixes**

If fixes were needed during testing, commit them:
```bash
git add -u
git commit -m "Fix issues found during end-to-end VM testing"
```

---

## Task Summary

| Task | Description | Dependencies |
|------|-------------|--------------|
| 1 | Fix pre-existing role gaps | None |
| 2 | Create test inventory | None |
| 3 | PXE role extension | None |
| 4 | Verification playbooks | None |
| 5 | Cloud-init module | None |
| 6 | VM manager module | None |
| 7 | Network helpers | None |
| 8 | CLI entry point | Tasks 5, 6, 7 |
| 9 | CI workflow | Task 8 |
| 10 | End-to-end test | All above |

Tasks 1-7 are independent and can be worked on in parallel.
Task 8 depends on 5, 6, 7 (Python imports).
Task 9 depends on 8.
Task 10 is the integration test that validates everything.
