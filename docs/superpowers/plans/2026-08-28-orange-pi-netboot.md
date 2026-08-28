# Orange Pi H3 Netboot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the four Orange Pi PC (Allwinner H3) boards on s3300-1 ports 20/21/23/24 netboot the shared Raspberry Pi NFS root automatically, recovering from a PoE cycle with no operator action.

**Architecture:** The shared Raspbian armhf NFS root gains one extra kernel (Debian `linux-image-armmp`, pinned from deb.debian.org) whose kernel/initrd/DTBs are synced to a `sunxi/` directory in the existing TFTP root next to a U-Boot distro-boot `pxelinux.cfg/default-arm-sunxi`. The hub host (pi-sw2-p30) gets a udev-triggered `sunxi-fel` service (shipped in `fpgas-online-setup-pi`, so any Pi with FEL devices attached becomes a boot host) that loads U-Boot into every board that enumerates in FEL mode; U-Boot then DHCPs through the untouched per-port VLAN scheme and PXE-boots.

**Tech Stack:** Ansible (fpgas.online-infra roles `fixpi`, `onpi`, `verify-pi.yml`), Raspbian bookworm armhf NFS root provisioned through `chroot-mount-pi-fs.bash` under qemu-user-static, dnsmasq TFTP, U-Boot 2025.01 distro-boot, `sunxi-tools`, nfpm-built deb in `fpgas.online-setup-pi`, GitHub Actions CI (`tests/vm/run_tests.py`).

**Spec:** `docs/superpowers/specs/2026-08-28-orange-pi-netboot-design.md` (board mapping in `docs/hardware/2026-08-28-orange-pi-h3-boards.md`).

## Global Constraints

- Every new switch/board-specific render is guarded `when: sunxi_boards is defined` — ps1.fpgas.online, slf.sytes.net and the CI VM must be unaffected unless they define it (spec §3, multi-host guard rule).
- Raspbian stays the source of every package except `linux-image-*-armmp` and `linux-base`: the Debian source is pinned to priority −1 for everything else (spec §3.1).
- Kernel: Debian bookworm `linux-image-armmp` (currently 6.1.0-50-armmp, 6.1.176-1) — matches the root's bookworm userland (spec §1).
- U-Boot image: `orangepi_pc_plus` `u-boot-sunxi-with-spl.bin` from Debian `u-boot-sunxi_2025.01-3+deb13u1` (spec §3.4); default DTB `sun8i-h3-orangepi-pc.dtb` (spec §3.2).
- PXE append line = the Pi `cmdline.txt.j2` line with `console=ttyS0,115200` and **without** `netconsole=` (spec §3.2).
- Work only in the worktrees `fpgas.online-infra/.worktrees/orange-pi-netboot` (branch `orange-pi-netboot`) and `fpgas.online-setup-pi/.worktrees/felboot` (branch `felboot`); land via PRs with CI green; small commits.
- Python via `uv run`; multi-command shell logic goes in Python scripts, never long bash one-liners.
- Real-hardware steps go through tweed: `ssh -F tmp/tweed-ssh.cfg ansible@10.99.21.2` (sudo), boards via `-J ansible@10.99.21.2 pi@10.21.2.<port>`; switch PoE via `ngsw --config ~/.config/ngsw/inventory.toml --switch s3300-1 --write-community public poe <port> off|on -y --force`.

---

### Task 1: Inventory — `sunxi_boards` for tweed and the CI VM

**Files:**
- Modify: `ansible/inventory/host_vars/fpgas.online.yml` (append after the `tt_boards:` block)
- Modify: `tests/inventory/host_vars/test-vm.yml` (append at end)

**Interfaces:**
- Produces: `sunxi_boards` (list of `{switch, port, host, usb, model}`), `sunxi_default_dtb` (string), `sunxi_kernel_package` (string) — consumed by Tasks 2–4, 6.

- [ ] **Step 1: Add the production vars**

Append to `ansible/inventory/host_vars/fpgas.online.yml`:

```yaml

# --- Orange Pi H3 boards (spec: docs/superpowers/specs/2026-08-28-orange-pi-netboot-design.md) --
# They have no SD/eMMC, so the BROM sits in USB FEL mode until the hub host
# (`host`, USB path `usb`) FEL-boots U-Boot into them; U-Boot then PXE-boots
# the SHARED NFS root with the Debian armmp kernel that fixpi/sunxi.yml bakes
# in. Network identity is the ordinary per-port one (pi-sw2-p<port>).
# Mapping verified 2026-08-28: docs/hardware/2026-08-28-orange-pi-h3-boards.md
sunxi_kernel_package: linux-image-armmp   # Debian bookworm metapackage (6.1.0-x-armmp)
sunxi_default_dtb: sun8i-h3-orangepi-pc.dtb
sunxi_boards:
  - {switch: 2, port: 20, host: pi-sw2-p30, usb: "1-1.2.2", model: orangepi-pc, mac: "02:81:bf:f6:b7:99"}
  - {switch: 2, port: 21, host: pi-sw2-p30, usb: "1-1.3.1", model: orangepi-pc, mac: "02:81:31:f4:6e:48"}
  - {switch: 2, port: 23, host: pi-sw2-p30, usb: "1-1.2.3", model: orangepi-pc, mac: "02:81:1f:e1:45:1d"}
  - {switch: 2, port: 24, host: pi-sw2-p30, usb: "1-1.2.4", model: orangepi-pc, mac: "02:81:f5:c0:a6:10"}
```

- [ ] **Step 2: Add the CI VM vars (PXE file + verify get exercised; the kernel install is skipped by tag, Task 3)**

Append to `tests/inventory/host_vars/test-vm.yml`:

```yaml
# Orange Pi netboot rendering (fixpi/sunxi.yml). The kernel install itself is
# tagged sunxi-kernel and skipped in CI (tests/vm/run_tests.py --skip-tags):
# a 30-minute qemu-emulated update-initramfs is not worth a CI run.
sunxi_kernel_package: linux-image-armmp
sunxi_default_dtb: sun8i-h3-orangepi-pc.dtb
sunxi_boards:
  - {switch: 1, port: 1, host: pi-sw1-p1, usb: "1-1.2.2", model: orangepi-pc, mac: "02:81:00:00:00:01"}
```

- [ ] **Step 3: Lint and commit**

Run: `cd .worktrees/orange-pi-netboot && uv run ansible-lint ansible/inventory tests/inventory`
Expected: no new findings.

```bash
git add ansible/inventory/host_vars/fpgas.online.yml tests/inventory/host_vars/test-vm.yml
git commit -m "inventory: declare the Orange Pi H3 boards (sunxi_boards) for tweed and the CI VM"
```

---

### Task 2: fixpi — bake the armmp kernel into the NFS root and publish it to TFTP

**Files:**
- Create: `ansible/roles/fixpi/tasks/sunxi.yml`
- Create: `ansible/roles/fixpi/defaults/main.yml` (`sunxi_debian_keyring_deb`, `sunxi_debian_keyring_sha256` — Raspbian ships no `debian-archive-keyring`, found at the first hardware converge)
- Create: `ansible/roles/fixpi/templates/apt/debian-armmp.sources.j2`
- Create: `ansible/roles/fixpi/templates/apt/debian-armmp.pref.j2`
- Create: `ansible/roles/fixpi/templates/boot/default-arm-sunxi.j2`
- Modify: `ansible/roles/fixpi/tasks/main.yml` (add one include line)

**Interfaces:**
- Consumes: `sunxi_boards`, `sunxi_default_dtb`, `sunxi_kernel_package` (Task 1); `nfs_root`, `tftp_root`, `eth_local_address` (existing group/host vars); `chroot-mount-pi-fs.bash` (existing helper, mounts proc/sys/dev in a private namespace and chroots).
- Produces: `{{ tftp_root }}/sunxi/{vmlinuz,initrd.img,dtbs/*.dtb}` and `{{ tftp_root }}/pxelinux.cfg/default-arm-sunxi` (consumed by U-Boot and by Task 3's verify).

- [ ] **Step 1: Write the apt source + pin templates**

`ansible/roles/fixpi/templates/apt/debian-armmp.sources.j2`:

```
# {{ ansible_managed }}
# Debian (not Raspbian) armhf source, used ONLY for the sunxi kernel: Raspbian
# has no armmp kernel. Everything else from here is pinned to -1 (see
# preferences.d/debian-armmp.pref) so Raspbian stays the source of every other
# package on the shared NFS root.
Types: deb
URIs: http://deb.debian.org/debian
Suites: bookworm
Components: main
Architectures: armhf
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
```

`ansible/roles/fixpi/templates/apt/debian-armmp.pref.j2`:

```
# {{ ansible_managed }}
# Allow only the armmp kernel (+ its linux-base dependency) from deb.debian.org.
Package: linux-image-*-armmp {{ sunxi_kernel_package }} linux-base
Pin: origin deb.debian.org
Pin-Priority: 500

Package: *
Pin: origin deb.debian.org
Pin-Priority: -1
```

- [ ] **Step 2: Write the PXE template**

`ansible/roles/fixpi/templates/boot/default-arm-sunxi.j2` (the append line mirrors `cmdline.txt.j2`; `netconsole=` dropped because dwmac-sun8i is an initramfs module and built-in netconsole cannot bind; `ttyS0` = H3 UART0):

```
# {{ ansible_managed }}
# U-Boot distro-boot PXE config for the Allwinner sunxi (Orange Pi H3) boards.
# U-Boot asks the DHCP server's TFTP for pxelinux.cfg/01-<mac>, then the IP-hex
# names, then default-arm-sunxi (this file), default-arm, default. Kernel,
# initrd and DTBs live in sunxi/ (fixpi/sunxi.yml syncs them from the NFS root).
default sunxi
timeout 10

label sunxi
  kernel sunxi/vmlinuz
  initrd sunxi/initrd.img
  fdt sunxi/dtbs/{{ sunxi_default_dtb }}
  append root=/dev/nfs nfsroot={{ eth_local_address }}:{{ nfs_root }}/root,nfsvers=3,tcp ro ip=dhcp rootwait consoleblank=0 overlayroot=tmpfs console=ttyS0,115200 systemd.log_level=debug systemd.log_target=kmsg log_buf_len=1M printk.devkmsg=on
```

- [ ] **Step 3: Write `sunxi.yml`**

`ansible/roles/fixpi/tasks/sunxi.yml`:

```yaml
---
# Orange Pi H3 (sunxi) support for the SHARED Pi NFS root: a Debian armmp
# kernel baked into the root + its boot payload published under
# <tftp_root>/sunxi/ for U-Boot's distro-boot PXE. Only runs on hosts that
# declare sunxi_boards (multi-host guard rule). Spec:
# docs/superpowers/specs/2026-08-28-orange-pi-netboot-design.md
#
# Safe for the Pi fleet: Raspbian's z50-raspi-firmware kernel hook prints
# "Unsupported kernel version (6.1.0-50-armmp) - skipping setup" and leaves
# /boot/firmware alone (verified 2026-08-28 in the spike).

# Raspbian does not carry debian-archive-keyring, so fetch Debian's own
# bookworm build of it (sha256 from the bookworm Packages index) and dpkg it
# into the root; it provides the Signed-By keyring the source below names.
- name: fetch debian-archive-keyring for the armmp kernel source
  get_url:
    url: "http://deb.debian.org/debian/pool/main/d/debian-archive-keyring/{{ sunxi_debian_keyring_deb }}"
    dest: "{{ nfs_root }}/root/root/{{ sunxi_debian_keyring_deb }}"
    checksum: "sha256:{{ sunxi_debian_keyring_sha256 }}"
    mode: "0644"
  tags: [sunxi, sunxi-kernel]

- name: install debian-archive-keyring into the NFS root
  command: >-
    chroot-mount-pi-fs.bash "{{ nfs_root }}" "/tmp/pi"
    "dpkg -i /root/{{ sunxi_debian_keyring_deb }}"
  args:
    creates: "{{ nfs_root }}/root/usr/share/keyrings/debian-archive-keyring.gpg"
  tags: [sunxi, sunxi-kernel]

- name: debian armhf apt source (kernel only) in NFS root
  template:
    src: templates/apt/debian-armmp.sources.j2
    dest: "{{ nfs_root }}/root/etc/apt/sources.list.d/debian-armmp.sources"
    mode: "0644"
  tags: [sunxi]

- name: pin the debian source to the armmp kernel only
  template:
    src: templates/apt/debian-armmp.pref.j2
    dest: "{{ nfs_root }}/root/etc/apt/preferences.d/debian-armmp.pref"
    mode: "0644"
  tags: [sunxi]

# ~30 min under qemu-user-static (update-initramfs with MODULES=most), hence
# the creates guard and the sunxi-kernel tag CI skips. Kernel upgrades arrive
# through onpi's apt upgrade in the pi play like every other package.
- name: install the armmp kernel into the NFS root
  command: >-
    chroot-mount-pi-fs.bash "{{ nfs_root }}" "/tmp/pi"
    "apt-get update && apt-get install -y {{ sunxi_kernel_package }}"
  args:
    creates: "{{ nfs_root }}/root/boot/vmlinuz-*-armmp"
  tags: [sunxi, sunxi-kernel]

- name: find the installed armmp kernel
  find:
    paths: "{{ nfs_root }}/root/boot"
    patterns: "vmlinuz-*-armmp"
  register: fixpi_sunxi_vmlinuz
  tags: [sunxi]

- name: derive the armmp kernel version
  set_fact:
    fixpi_sunxi_kver: >-
      {{ (fixpi_sunxi_vmlinuz.files | map(attribute='path') | sort | last | basename)
         | regex_replace('^vmlinuz-', '') }}
  when: fixpi_sunxi_vmlinuz.matched > 0
  tags: [sunxi]

- name: create tftp sunxi directories
  file:
    path: "{{ tftp_root }}/{{ item }}"
    state: directory
    mode: "0755"
  loop: [sunxi, sunxi/dtbs, pxelinux.cfg]
  tags: [sunxi]

- name: publish kernel + initrd to tftp sunxi/
  copy:
    remote_src: true
    src: "{{ nfs_root }}/root/boot/{{ item.src }}-{{ fixpi_sunxi_kver }}"
    dest: "{{ tftp_root }}/sunxi/{{ item.dest }}"
    mode: "0644"
  loop:
    - {src: vmlinuz, dest: vmlinuz}
    - {src: initrd.img, dest: initrd.img}
  when: fixpi_sunxi_vmlinuz.matched > 0
  tags: [sunxi]

- name: publish sunxi DTBs to tftp sunxi/dtbs/
  copy:
    remote_src: true
    src: "{{ nfs_root }}/root/usr/lib/linux-image-{{ fixpi_sunxi_kver }}/{{ item }}"
    dest: "{{ tftp_root }}/sunxi/dtbs/{{ item }}"
    mode: "0644"
  loop:
    - sun8i-h3-orangepi-pc.dtb
    - sun8i-h3-orangepi-pc-plus.dtb
    - sun8i-h3-orangepi-one.dtb
  when: fixpi_sunxi_vmlinuz.matched > 0
  tags: [sunxi]

- name: U-Boot PXE config for the sunxi boards
  template:
    src: templates/boot/default-arm-sunxi.j2
    dest: "{{ tftp_root }}/pxelinux.cfg/default-arm-sunxi"
    mode: "0644"
  tags: [sunxi]
```

- [ ] **Step 4: Include it from `main.yml`**

Modify `ansible/roles/fixpi/tasks/main.yml` — after the `netboot.yml` line add:

```yaml
- {include_tasks: sunxi.yml, tags: [sunxi, sunxi-kernel], when: sunxi_boards is defined}
```

- [ ] **Step 5: Syntax-check, lint, render-check**

Run:
```bash
cd .worktrees/orange-pi-netboot
uv run ansible-playbook -i tests/inventory/test-hosts ansible/site.yml --syntax-check
uv run ansible-lint ansible/roles/fixpi
```
Expected: syntax OK, no new lint findings.

Render check (template only, local):
```bash
uv run ansible localhost -m template -a "src=ansible/roles/fixpi/templates/boot/default-arm-sunxi.j2 dest=tmp/default-arm-sunxi" \
  -e sunxi_default_dtb=sun8i-h3-orangepi-pc.dtb -e eth_local_address=10.21.0.1 -e nfs_root=/srv/nfs/rpi/bookworm
grep -c "fdt sunxi/dtbs/sun8i-h3-orangepi-pc.dtb" tmp/default-arm-sunxi   # expect 1
grep -c netconsole tmp/default-arm-sunxi                                    # expect 0
rm tmp/default-arm-sunxi
```

- [ ] **Step 6: Commit**

```bash
git add ansible/roles/fixpi
git commit -m "fixpi: bake a Debian armmp kernel into the NFS root and publish sunxi PXE files"
```

---

### Task 3: CI skip-tag and server-side verification

**Files:**
- Modify: `tests/vm/run_tests.py:385` (default `--skip-tags`)
- Modify: `ansible/roles/fixpi/tasks/verify/main.yml` (append)

**Interfaces:**
- Consumes: files produced by Task 2 under `{{ tftp_root }}`.

- [ ] **Step 1: Skip the kernel install in CI**

In `tests/vm/run_tests.py` change the default:

```python
    parser.add_argument("--skip-tags", type=str, default="cam,fpgas-apt,hw-camera,hw-fpga,sunxi-kernel",
```

and update the help text/comment on that argument to mention `sunxi-kernel` ("30-minute qemu-emulated initramfs build; the PXE render is still verified").

- [ ] **Step 2: Add server-side asserts**

Append to `ansible/roles/fixpi/tasks/verify/main.yml`:

```yaml

- name: sunxi PXE config present
  stat:
    path: "{{ tftp_root }}/pxelinux.cfg/default-arm-sunxi"
  register: fixpi_verify_sunxi_pxe
  when: sunxi_boards is defined

- name: assert sunxi PXE config present and names the default DTB
  assert:
    that:
      - fixpi_verify_sunxi_pxe.stat.exists
      - lookup('file', tftp_root ~ '/pxelinux.cfg/default-arm-sunxi') is search('fdt sunxi/dtbs/' ~ sunxi_default_dtb)
    fail_msg: "{{ tftp_root }}/pxelinux.cfg/default-arm-sunxi missing or not pointing at {{ sunxi_default_dtb }}"
  when: sunxi_boards is defined

# Only meaningful when the kernel was actually installed (tag sunxi-kernel).
- name: sunxi kernel payload present in tftp
  stat:
    path: "{{ tftp_root }}/sunxi/{{ item }}"
  register: fixpi_verify_sunxi_payload
  loop: [vmlinuz, initrd.img, "dtbs/{{ sunxi_default_dtb }}"]
  when: sunxi_boards is defined
  tags: [sunxi-kernel]

- name: assert sunxi kernel payload present
  assert:
    that: fixpi_verify_sunxi_payload.results | map(attribute='stat.exists') | list == [true, true, true]
    fail_msg: "sunxi/vmlinuz, sunxi/initrd.img or the default DTB missing from {{ tftp_root }}"
  when: sunxi_boards is defined
  tags: [sunxi-kernel]
```

Note: `lookup('file', …)` runs on the controller. `verify-server.yml` runs against tweed remotely, so use a `slurp` instead if the assert fails in CI:

```yaml
- name: read sunxi PXE config
  slurp:
    src: "{{ tftp_root }}/pxelinux.cfg/default-arm-sunxi"
  register: fixpi_verify_sunxi_pxe_body
  when: sunxi_boards is defined
```
and assert on `fixpi_verify_sunxi_pxe_body.content | b64decode is search(...)`. Use the slurp form from the start (it is the one that works remotely) — the lookup variant is shown only to explain why.

- [ ] **Step 3: Lint, commit, push, watch CI**

```bash
uv run ansible-lint ansible/roles/fixpi tests
uv run ruff check tests/vm/run_tests.py
git add tests/vm/run_tests.py ansible/roles/fixpi/tasks/verify/main.yml
git commit -m "ci: skip the sunxi kernel build in the VM run; verify the sunxi PXE files"
git push -u origin orange-pi-netboot
gh pr checks --watch   # (PR #28 already exists for this branch)
```
Expected: Lint green; "Server + Pi PXE Boot" green (VM defines `sunxi_boards`, renders the PXE file, skips the kernel).

---

### Task 4: fpgas.online-setup-pi — the FEL-boot service

**Files (repo `fpgas.online-setup-pi`, worktree `.worktrees/felboot`, branch `felboot`):**
- Create: `felboot/fpgas-felboot.sh`
- Create: `felboot/fpgas-felboot@.service`
- Create: `felboot/60-fpgas-felboot.rules`
- Create: `felboot/u-boot/orangepi_pc_plus/u-boot-sunxi-with-spl.bin` (binary, 559488 bytes)
- Create: `felboot/u-boot/README.md`
- Create: `felboot/test_felboot.sh`
- Modify: `nfpm.yaml` (depends + contents)
- Modify: `README.md`, `CLAUDE.md` (one bullet each)

**Interfaces:**
- Produces: package `fpgas-online-setup-pi` ≥ next `v0.0.postN` with `/usr/local/bin/fpgas-felboot.sh`, `fpgas-felboot@.service`, udev rule, `/usr/lib/fpgas-online/u-boot/orangepi_pc_plus/u-boot-sunxi-with-spl.bin`; depends on `sunxi-tools`.

- [ ] **Step 1: Create the worktree**

```bash
cd ~/github/fpgas-online/fpgas.online-setup-pi
git fetch origin && printf '.worktrees/\n' >> .git/info/exclude
git worktree add .worktrees/felboot -b felboot origin/main
cd .worktrees/felboot
```

- [ ] **Step 2: Vendor the U-Boot image with provenance**

```bash
mkdir -p felboot/u-boot/orangepi_pc_plus tmp && cd tmp
curl -sSLO https://deb.debian.org/debian/pool/main/u/u-boot/u-boot-sunxi_2025.01-3+deb13u1_armhf.deb
dpkg-deb -x u-boot-sunxi_2025.01-3+deb13u1_armhf.deb x
cp x/usr/lib/u-boot/orangepi_pc_plus/u-boot-sunxi-with-spl.bin ../felboot/u-boot/orangepi_pc_plus/
sha256sum ../felboot/u-boot/orangepi_pc_plus/u-boot-sunxi-with-spl.bin u-boot-sunxi_2025.01-3+deb13u1_armhf.deb
cd .. && rm -rf tmp
```

`felboot/u-boot/README.md`:

```markdown
# Vendored U-Boot images for FEL boot

`orangepi_pc_plus/u-boot-sunxi-with-spl.bin` is copied unmodified from Debian's
`u-boot-sunxi_2025.01-3+deb13u1_armhf.deb` (`usr/lib/u-boot/orangepi_pc_plus/`),
GPL-2.0-or-later, https://deb.debian.org/debian/pool/main/u/u-boot/ .
Raspbian does not ship u-boot-sunxi, so the image is vendored here.
sha256 of the .bin: <paste from the sha256sum above>
sha256 of the .deb: <paste>

It runs on the Orange Pi PC boards (same H3 + 1 GB DRAM as the PC Plus; the extra
eMMC/wifi nodes are harmless). Refresh: download the newer deb, repeat, update the hashes.
```

- [ ] **Step 3: Write the boot script (test first)**

`felboot/test_felboot.sh` — runs the script against a stub `sunxi-fel` and checks argument handling:

```bash
#!/bin/bash
# Unit test for fpgas-felboot.sh: a stub sunxi-fel records its argv.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d "$here/../tmp/felboot-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/sunxi-fel" <<'EOF'
#!/bin/bash
echo "$@" >> "$FELBOOT_TEST_LOG"
[ "${FELBOOT_TEST_FAIL:-0}" = "1" ] && exit 1
exit 0
EOF
chmod +x "$tmp/bin/sunxi-fel"
export FELBOOT_TEST_LOG="$tmp/log" PATH="$tmp/bin:$PATH" FPGAS_FELBOOT_IMAGE="$tmp/uboot.bin" FPGAS_FELBOOT_RETRY_SLEEP=0
: > "$tmp/uboot.bin"

# 1. bus/dev from a kernel device name like 1-1.2.2 -> sunxi-fel --dev 001:012
printf '12\n' > "$tmp/devnum"; printf '1\n' > "$tmp/busnum"
FPGAS_FELBOOT_SYSFS="$tmp" "$here/fpgas-felboot.sh" 1-1.2.2
grep -qx -- "--dev 001:012 uboot $tmp/uboot.bin" "$tmp/log" || { echo "FAIL: argv was: $(cat "$tmp/log")"; exit 1; }

# 2. retries three times then fails
: > "$tmp/log"
if FELBOOT_TEST_FAIL=1 FPGAS_FELBOOT_SYSFS="$tmp" "$here/fpgas-felboot.sh" 1-1.2.2; then echo "FAIL: expected non-zero exit"; exit 1; fi
[ "$(wc -l < "$tmp/log")" = 3 ] || { echo "FAIL: expected 3 attempts, got $(wc -l < "$tmp/log")"; exit 1; }
echo "PASS"
```

Run: `bash felboot/test_felboot.sh` → expected `FAIL` (script missing: "No such file").

- [ ] **Step 4: Write the script**

`felboot/fpgas-felboot.sh`:

```bash
#!/bin/bash
# FEL-boot U-Boot into an Allwinner board that enumerated in BROM FEL mode.
# Called by fpgas-felboot@<kernel-device>.service (udev rule 60-fpgas-felboot),
# e.g. fpgas-felboot@1-1.2.2.service. The board's bus/devnum come from sysfs;
# U-Boot then DHCP+PXE-boots on its own (fpgas.online-infra fixpi/sunxi.yml).
set -euo pipefail

dev="${1:?usage: fpgas-felboot.sh <usb kernel device, e.g. 1-1.2.2>}"
image="${FPGAS_FELBOOT_IMAGE:-/usr/lib/fpgas-online/u-boot/orangepi_pc_plus/u-boot-sunxi-with-spl.bin}"
sysfs="${FPGAS_FELBOOT_SYSFS:-/sys/bus/usb/devices/$dev}"
retry_sleep="${FPGAS_FELBOOT_RETRY_SLEEP:-2}"

busnum=$(<"$sysfs/busnum")
devnum=$(<"$sysfs/devnum")
target=$(printf '%03d:%03d' "$busnum" "$devnum")

for attempt in 1 2 3; do
    if sunxi-fel --dev "$target" uboot "$image"; then
        echo "fpgas-felboot: $dev ($target): U-Boot loaded (attempt $attempt)"
        exit 0
    fi
    echo "fpgas-felboot: $dev ($target): sunxi-fel failed (attempt $attempt)" >&2
    sleep "$retry_sleep"
done
exit 1
```

Run: `bash felboot/test_felboot.sh` → expected `PASS`. Run `shellcheck --severity=warning felboot/*.sh` → clean.

- [ ] **Step 5: udev rule + service unit**

`felboot/60-fpgas-felboot.rules` (`%k` = kernel name, e.g. `1-1.2.2`; `systemd-run`-free: udev's `TAG+="systemd"` + `ENV{SYSTEMD_WANTS}` starts the instance):

```
# Allwinner BROM in USB FEL mode (no SD/eMMC): load U-Boot into it, which then
# PXE-boots. Shipped by fpgas-online-setup-pi; see fpgas-felboot.sh.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="1f3a", ATTR{idProduct}=="efe8", TAG+="systemd", ENV{SYSTEMD_WANTS}+="fpgas-felboot@%k.service"
```

`felboot/fpgas-felboot@.service`:

```ini
[Unit]
Description=FEL-boot U-Boot into Allwinner board %I
# One shot per enumeration; udev restarts it when the board re-enumerates.
StopWhenUnneeded=yes

[Service]
Type=oneshot
# %i (escaped), not %I: systemd unescapes %I and turns 1-1.2.2 into 1/1.2.2
# (found on hardware 2026-08-28).
ExecStart=/usr/local/bin/fpgas-felboot.sh %i
```

- [ ] **Step 6: Package it**

In `nfpm.yaml` add `sunxi-tools` to `depends:` and append to `contents:`:

```yaml
  # --- felboot: FEL-boot Allwinner (Orange Pi H3) boards that hang off this Pi's USB ---
  - src: felboot/fpgas-felboot.sh
    dst: /usr/local/bin/fpgas-felboot.sh
    file_info:
      mode: 0755
  - src: felboot/fpgas-felboot@.service
    dst: /usr/lib/systemd/system/fpgas-felboot@.service
  - src: felboot/60-fpgas-felboot.rules
    dst: /usr/lib/udev/rules.d/60-fpgas-felboot.rules
  - src: felboot/u-boot/orangepi_pc_plus/u-boot-sunxi-with-spl.bin
    dst: /usr/lib/fpgas-online/u-boot/orangepi_pc_plus/u-boot-sunxi-with-spl.bin
```

Local build check (nfpm is a single Go binary; the build workflow installs it the same way):
```bash
mkdir -p tmp && VERSION=$(uv run packaging/deb-version.py) nfpm package -p deb -f nfpm.yaml -t tmp/ && dpkg-deb -c tmp/*.deb | grep felboot
```
Expected: the four felboot paths listed. If `nfpm` is not installed locally, install it per `.github/workflows/build-deb.yml` (`gh release download` from goreleaser/nfpm) into `~/.local/bin` first.

- [ ] **Step 7: Docs, lint, commit, PR**

Add to `README.md` "What It Provides": `- **FEL boot host** -- udev-triggered \`fpgas-felboot@.service\` loads U-Boot (vendored, \`felboot/u-boot/\`) into Allwinner boards that enumerate in FEL mode, so PoE-cycled Orange Pis netboot without an operator.` and the `felboot/` line to the directory structure; mirror in `CLAUDE.md`.

```bash
shellcheck --severity=warning felboot/*.sh && bash felboot/test_felboot.sh
git add felboot nfpm.yaml README.md CLAUDE.md
git commit -m "felboot: FEL-boot U-Boot into Allwinner boards that enumerate on this Pi's USB"
git push -u origin felboot
gh pr create --title "felboot: FEL-boot Orange Pi H3 boards from the hub Pi" --body "..."
gh pr checks --watch
```
Expected: Lint (ruff + shellcheck) green. After merge, the Build workflow publishes the new `.deb` to the rolling release; the apt repo pulls it within 15 min (check `https://fpgas-online.github.io/apt/pool/main/` for the new `fpgas-online-setup-pi_*.deb`).

---

### Task 5: Mask ifupdown in the NFS root (2-minute boot stall)

**Files:**
- Modify: `ansible/roles/fixpi/tasks/tweeks.yml` (append)

**Interfaces:** none.

Background: `ifupdown-pre.service` (`udevadm settle`) waits its full 2 min on the H3 and `networking.service` fails on both the H3 and the Pi 5 — the shared root is configured by the kernel's `ip=dhcp` + NetworkManager, not ifupdown. Masking both removes the stall and the spurious failed unit.

- [ ] **Step 1: Add the mask tasks**

Append to `ansible/roles/fixpi/tasks/tweeks.yml`:

```yaml

# ifupdown is unused on the netbooted root (kernel ip=dhcp + NetworkManager):
# networking.service fails on every Pi and ifupdown-pre.service (udevadm
# settle) waits its full 2 min on the Orange Pi H3 before anything else starts
# (spike 2026-08-28: userspace 2 m 16 s, of which ifupdown-pre 2 m 02 s).
- name: mask ifupdown units in NFS root
  file:
    src: /dev/null
    dest: "{{ nfs_root }}/root/etc/systemd/system/{{ item }}"
    state: link
    force: true
  loop:
    - networking.service
    - ifupdown-pre.service
  tags:
    - fixpi
```

- [ ] **Step 2: Add the Pi-side check**

Append to `ansible/verify-pi.yml` tasks:

```yaml
    - name: Check no failed units
      command: systemctl --failed --no-legend --plain
      changed_when: false
      register: verify_pi_failed
      tags: [verify, services]

    - name: Assert ifupdown is masked and not failed
      assert:
        that:
          - "'networking.service' not in verify_pi_failed.stdout"
          - "'ifupdown-pre.service' not in verify_pi_failed.stdout"
        fail_msg: "ifupdown units still failing on the Pi: {{ verify_pi_failed.stdout }}"
      tags: [verify, services]
```

- [ ] **Step 3: Lint, commit, push; CI proves a Pi still gets its network**

```bash
uv run ansible-lint ansible
git add ansible/roles/fixpi/tasks/tweeks.yml ansible/verify-pi.yml
git commit -m "fixpi: mask the unused ifupdown units (2-minute udevadm settle stall on the H3)"
git push && gh pr checks --watch
```
Expected: VM run green, verify-pi's per-port-address check still passes (network came from `ip=dhcp`/NM, not ifupdown).

---

### Task 6: verify-pi — hardware checks for the sunxi boards and the FEL host

**Files:**
- Modify: `ansible/verify-pi.yml` (append; tag `hw-sunxi`)

**Interfaces:**
- Consumes: `sunxi_boards` from the server host (`hostvars[groups['nbp'][0]].sunxi_boards`, same pattern as `tt_boards` in `onpi/tasks/tt.yml`).

- [ ] **Step 1: Append the checks**

```yaml
    # --- Orange Pi H3 boards + their FEL-boot host (tag hw-sunxi) ---
    - name: read the device-tree model
      slurp:
        src: /proc/device-tree/model
      register: verify_pi_dt_model
      failed_when: false
      tags: [verify, hw-sunxi]

    - name: Assert a sunxi board runs the armmp kernel from the shared root
      assert:
        that:
          - ansible_kernel is search('armmp')
          - ansible_architecture == 'armv7l'
        fail_msg: "Orange Pi {{ ansible_hostname }} runs {{ ansible_kernel }} on {{ ansible_architecture }}, expected an armmp kernel"
      when: verify_pi_dt_model.content is defined and (verify_pi_dt_model.content | b64decode) is search('Orange Pi')
      tags: [verify, hw-sunxi]

    - name: sunxi boards declared for this site
      set_fact:
        verify_pi_sunxi: "{{ hostvars[groups['nbp'][0]].sunxi_boards | default([]) }}"
      tags: [verify, hw-sunxi]

    # Finished oneshot instances are unloaded, so `systemctl list-units` shows
    # nothing; the journal of this boot is the evidence that each board's
    # instance ran and sunxi-fel reported "U-Boot loaded".
    - name: Check FEL-boot journal on the hub host
      command: journalctl -b -u 'fpgas-felboot@*' -o cat --no-pager
      changed_when: false
      register: verify_pi_felboot
      when: verify_pi_sunxi | selectattr('host', 'equalto', ansible_hostname) | list | length > 0
      tags: [verify, hw-sunxi]

    - name: Assert every attached board was FEL-booted from this host
      assert:
        that: >-
          verify_pi_sunxi | selectattr('host', 'equalto', ansible_hostname)
          | map(attribute='usb') | map('regex_replace', '^(.*)$', 'fpgas-felboot: \\1 (') | list
          | reject('in', verify_pi_felboot.stdout) | list | length == 0
        fail_msg: "No 'U-Boot loaded' journal line for every board on {{ ansible_hostname }}: {{ verify_pi_felboot.stdout }}"
      when: verify_pi_felboot is not skipped
      tags: [verify, hw-sunxi]
```

- [ ] **Step 2: CI skips it (hardware only), lint, commit**

In `tests/vm/run_tests.py` extend the same default: `"cam,fpgas-apt,hw-camera,hw-fpga,sunxi-kernel,hw-sunxi"`.

```bash
uv run ansible-lint ansible/verify-pi.yml && uv run ruff check tests/vm/run_tests.py
git add ansible/verify-pi.yml tests/vm/run_tests.py
git commit -m "verify-pi: hardware checks for Orange Pi boards and their FEL-boot host (hw-sunxi)"
git push && gh pr checks --watch
```

---

### Task 7: Deploy to tweed and prove all four boards boot unattended

**Files:**
- Create: `docs/superpowers/runbooks/2026-08-28-orange-pi-netboot.md`

**Interfaces:**
- Consumes: merged Tasks 1–6 on `main` of fpgas.online-infra (PR #28) and the new `fpgas-online-setup-pi` deb in the apt repo (Task 4).

- [ ] **Step 1: Converge tweed + the NFS root (kernel install included; ~35 min for the kernel, plus the usual pi play)**

```bash
cd ~/github/fpgas-online/fpgas.online-infra/.worktrees/orange-pi-netboot   # or main after merge
uv run ansible-playbook -i ansible/inventory ansible/site.yml --limit fpgas.online,pi \
  --tags fixpi,netboot,sunxi,sunxi-kernel,onpi,fpgas-apt --vault-password-file <as usual>
```
Expected recap: `fpgas.online` and `pi` lines, `failed=0`; `install the armmp kernel into the NFS root` changed once; `sunxi/vmlinuz`, `sunxi/initrd.img`, `sunxi/dtbs/*.dtb`, `pxelinux.cfg/default-arm-sunxi` present under `/srv/nfs/rpi/bookworm/boot`; `dpkg -l fpgas-online-setup-pi` in the root shows the new version with `sunxi-tools` pulled in.

- [ ] **Step 2: Reboot the hub host so it picks up the new root**

`ngsw … poe 30 off -y --force`, 5 s, `poe 30 on`; wait for `pi@10.21.2.30` (via `-J ansible@10.99.21.2`). Then on it:
```bash
systemctl list-units --all 'fpgas-felboot@*'      # four instances, all "inactive (dead)" = exited 0 after boot
journalctl -u 'fpgas-felboot@*' --no-pager | tail  # "U-Boot loaded (attempt 1)" ×4
```
Expected: the four boards were FEL-booted as soon as the hub host's udev enumerated them.

- [ ] **Step 3: Prove each board is up (per-port identity, armmp kernel, shared root)**

```bash
for p in 20 21 23 24; do ssh -F tmp/tweed-ssh.cfg -J ansible@10.99.21.2 pi@10.21.2.$p \
  'hostname; uname -r; findmnt / -o SOURCE; findmnt /media/root-ro -o SOURCE; systemd-analyze | head -1'; done
```
Expected: `pi-sw2-p2x`, `6.1.0-50-armmp`, `overlayroot`, `10.21.0.1:/srv/nfs/rpi/bookworm/root`, userspace well under 30 s (Task 5).

- [ ] **Step 4: Unattended recovery test**

PoE-cycle one board (`poe 23 off` / `on`), no other action. Within ~60 s `pi@10.21.2.23` must be back with a fresh uptime. Then run the hardware verify:
```bash
uv run ansible-playbook -i 10.21.2.20,10.21.2.21,10.21.2.23,10.21.2.24,10.21.2.30, ansible/verify-pi.yml \
  -u pi -e verify_pi_hosts=all --skip-tags hw-camera,hw-fpga
```
Expected: `failed=0` on all five (hw-sunxi asserts included).

- [ ] **Step 5: Runbook + commit**

`docs/superpowers/runbooks/2026-08-28-orange-pi-netboot.md` records: the converge command, how to add a board (PoE it → `sunxi-fel --list` on the hub host gives the SID → FEL-boot it once → read the MAC from `ngsw macs` → add to `sunxi_boards` and to the `welland-ansible-rpi` sheet tool's `FPGAS_PORT_MAC`/`KNOWN_BOARDS`), recovery = PoE cycle, and the known limits (no early console until UART0 is wired; `netconsole=` inert on sunxi).

```bash
git add docs/superpowers/runbooks/2026-08-28-orange-pi-netboot.md
git commit -m "docs: Orange Pi netboot runbook (deploy, add a board, recovery)"
git push && gh pr checks --watch   # then merge PR #28 after green (no --auto: this repo has no required checks)
```

---

## Self-review

- **Spec coverage:** §3.1 kernel + pin → Task 2; §3.2 PXE file → Task 2; §3.3 inventory → Task 1; §3.4 felboot package → Task 4; §3.5 verify → Tasks 3 (server) + 6 (Pi/hub); §3.6 boot-time fix → Task 5; §3.7 docs → Task 7; hardware proof → Task 7. Sheet: already done (spec "out of scope").
- **Placeholders:** the U-Boot README hashes are filled from the `sha256sum` run in Task 4 Step 2 (values are produced by that step, not known in advance); the PR body in Task 4 Step 7 is the executor's summary of that task.
- **Type consistency:** `sunxi_boards` items always carry `switch, port, host, usb, model, mac`; `sunxi_default_dtb` and `sunxi_kernel_package` names match across Tasks 1/2/3; the service instance name is always the USB kernel name (`fpgas-felboot@1-1.2.2.service`) in the udev rule (`%k`), the script's `$1`, and Task 6's assert.
