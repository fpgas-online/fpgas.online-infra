# QEMU VM Testing for Ansible Playbooks

**Date:** 2026-04-04
**Status:** Approved

## Goal

Automated end-to-end testing of the fpgas.online Ansible infrastructure using QEMU VMs.
The test harness boots a Debian server VM, applies the Ansible roles, then PXE-boots a
diskless aarch64 Pi VM from the server over a virtual LAN -- verifying the full netboot
pipeline from DHCP through TFTP to NFS root.

## Requirements

- Test server roles (nbp, pig, uhubctl) against a QEMU x86_64 VM
- Test Pi netboot: PXE-boot a diskless aarch64 VM from the server (DHCP + TFTP + NFS root)
- Verification playbooks paired with each role, usable against test VMs and production
- Works locally (with KVM) and in GitHub Actions CI (KVM or TCG fallback)
- Minimal dependencies: QEMU, cloud-image-utils, Python stdlib + paramiko
- No Molecule, Vagrant, Packer, or libvirt

## Architecture

### Two-VM Integration Test

```
Host machine
|
|  SSH (port 2222)
|
v
+----------------------------------+
|  Server VM (x86_64 Debian)       |
|                                  |
|  NIC 1: user-mode (SSH from host)|
|  NIC 2: socket-listen (VLAN)    |
|          10.21.0.1/24            |
|  Guest agent: virtio-serial      |
|                                  |
|  Roles: firewall, nfs, img,     |
|    fixpi, pxe, site, wssh,      |
|    uhubctl, cam/stream-server    |
|                                  |
|  Provides: dnsmasq, TFTP, NFS   |
+--------------+-------------------+
               |  QEMU socket (VLAN)
               |
+--------------+-------------------+
|  Pi VM (aarch64 QEMU)           |
|                                  |
|  NIC: socket-connect only       |
|       DHCP -> 10.21.0.x         |
|  Guest agent: virtio-serial      |
|                                  |
|  Diskless PXE boot:             |
|  DHCP -> TFTP kernel+initrd     |
|  -> NFS root mount              |
|  No local disk, no direct host  |
|  access                          |
+----------------------------------+

Access: host -> SSH server:2222 -> SSH pi:10.21.0.x (ProxyJump)
```

### Pi VM PXE Boot

The Pi VM is **diskless** -- it PXE-boots entirely from the server, testing the real
netboot chain end-to-end.

**Boot sequence:**

1. QEMU aarch64 VM starts with no disk, only a NIC on the socket VLAN
2. UEFI firmware (EDK2 `QEMU_EFI.fd`) or U-Boot attempts PXE boot
3. dnsmasq on the server responds to DHCP, provides boot filename via `dhcp-boot`
4. VM TFTPs the bootloader/kernel + initrd from the server
5. Kernel boots with NFS root mount parameters (from cmdline)
6. VM is now running from the server's NFS root -- same as a real Pi

**Real RPis vs QEMU:** Real Raspberry Pis use the VideoCore BootROM to fetch
`bootcode.bin` by serial number -- a Pi-specific protocol. QEMU uses standard
UEFI PXE instead. The pxe role is extended to serve both:

- Real RPis: existing `bootcode.bin` + serial-number TFTP paths
- QEMU test clients: architecture-tagged `dhcp-boot` serving an aarch64 kernel+initrd

This is controlled by a `pxe_test_clients` variable in the inventory. When defined,
the pxe role adds dnsmasq and TFTP configuration for QEMU UEFI PXE clients.

**UEFI PXE boot chain (three stages):**

1. **dnsmasq DHCP** responds to ARM64 UEFI client (architecture 0x0B) with
   `grubaa64.efi` as the boot filename
2. **GRUB EFI** (`grubaa64.efi`) is TFTP-fetched and executed as a UEFI application.
   It fetches `/grub/grub.cfg` over TFTP.
3. **GRUB loads kernel+initrd** specified in grub.cfg, boots into NFS root

```
# Added to dnsmasq config when pxe_test_clients is defined
dhcp-match=set:efi-arm64,option:client-arch,11
dhcp-boot=tag:efi-arm64,grubaa64.efi

# Existing RPi PXE service (arch 0) is left unchanged.
# The architecture tag ensures UEFI clients get grubaa64.efi
# while RPi clients continue to get bootcode.bin.
```

**GRUB config** (`/srv/tftp/grub/grub.cfg`), templated by the pxe role:

```
set default=0
set timeout=3
{% for client in pxe_test_clients %}
menuentry "NFS Boot ({{ client.name }})" {
    linux /{{ client.sn }}/kernel8.img root=/dev/nfs nfsroot=10.21.0.1:{{ client.nfs_root }},nfsvers=3 ro ip=dhcp rootwait console={{ client.console }} overlayroot=tmpfs
    initrd /{{ client.sn }}/initrd.img
}
{% endfor %}
```

Each test client entry has a `sn` field matching one of the `switch.nos` serial numbers,
so the kernel+initrd paths resolve to the existing TFTP serial-number directories
(symlinked by fixpi to the NFS root `/boot/`). `kernel8.img` is the RPi's aarch64
kernel -- a standard aarch64 Image with broad hardware support that boots on both
real RPi hardware and QEMU's `-machine virt`.

The cmdline uses `console=ttyAMA0,115200` for QEMU's PL011 UART (real RPis use
`serial0` which is a Pi-specific alias). Other parameters (nfsroot, overlayroot)
match production.

**TFTP tree additions** (when `pxe_test_clients` is defined):

```
/srv/tftp/
  grubaa64.efi                # Copied from /usr/lib/grub/arm64-efi/grubaa64.efi
  grub/                       #   (installed by grub-efi-arm64-bin on the server)
    grub.cfg                  # Templated by pxe role from pxe_test_clients
  <sn>/                       # Already exists -- fixpi symlinks RPi boot files here
    kernel8.img               # Real RPi aarch64 kernel (already in NFS root /boot/)
    initrd.img                # Real RPi initrd (already in NFS root /boot/)
    ...
```

Only `grubaa64.efi` and `grub/grub.cfg` are new. The pxe role extension adds tasks to:
1. Install `grub-efi-arm64-bin` package on the server
2. Copy `grubaa64.efi` from the package install path to `/srv/tftp/grubaa64.efi`
3. Template `grub.cfg` from `pxe_test_clients` entries

**`pxe_test_clients` variable schema:**

```yaml
pxe_test_clients:
  - name: test-pi
    sn: "deadbeef"                 # Must match an entry in switch.nos
    mac: "52:54:00:12:34:56"       # QEMU NIC MAC (matched in dnsmasq dhcp-host)
    ip: "10.21.0.128"              # Static DHCP assignment
    nfs_root: "/srv/nfs/rpi/bookworm/root"
    console: "ttyAMA0,115200"      # QEMU PL011 UART (not Pi's serial0)
```

**Architecture:** The NFS root contains armhf (32-bit) userspace binaries since
production uses Raspberry Pi OS armhf. The RPi `kernel8.img` is aarch64 and can
execute armhf binaries via the kernel's 32-bit compatibility layer, so the NFS root
is fully functional. This is the same kernel+userspace combination used in production.

**overlayroot requirement:** The NFS export is read-only (`ro`). Production Pis use
`overlayroot=tmpfs` to create a writable tmpfs overlay at boot. The `overlayroot`
package must be pre-installed in the NFS root before the Pi VM boots. The fixpi role
already uses qemu-user-static chroot to install packages into the NFS root -- the
`overlayroot` package must be included in this step (added to fixpi if not already
present).

**QEMU launch (no disk):**

```
qemu-system-aarch64 \
  -machine virt,accel=tcg \
  -cpu max \
  -m 1024 \
  -bios QEMU_EFI.fd \                    # UEFI firmware with PXE support
  -netdev socket,id=lan0,connect=:12345 \ # connect to server VLAN
  -device virtio-net-pci,netdev=lan0 \    # single NIC, PXE boots from this
  -nographic \                             # no display
  -device virtio-serial \                  # guest agent channel
  -device virtserialport,chardev=qga0 \
  -chardev socket,path=pi-qga.sock,server=on,wait=off,id=qga0
  # NO -drive flag -- diskless
```

### Networking

The Pi VM has a single NIC on the QEMU socket VLAN -- no user-mode NIC, no direct
host access. This mirrors production where Pis are only reachable via the server.

QEMU socket networking (`-netdev socket,listen/connect`) connects the two VMs on a
virtual LAN without requiring root, TAP interfaces, or bridges.

**Sequencing requirement:** The server VM's QEMU process must be started first and its
socket must be listening before the Pi VM's QEMU process is launched. The test harness
must verify the server's listen socket is open (e.g., via `ss -lnp` or probing the
port) before starting the Pi VM, otherwise the Pi's `-netdev socket,connect=...` will
fail with connection refused.

### Guest Agent

Both VMs run the QEMU guest agent over a virtio-serial channel. This provides a
back-channel for debugging that doesn't depend on networking being correctly configured:

- `guest-ping` -- is the agent running?
- `guest-network-get-interfaces` -- did it get an IP?
- `guest-exec` -- run commands directly

Useful when the thing being tested (dnsmasq, firewall, NFS) is the thing that
would enable SSH access.

### KVM/TCG Handling

- Server VM (x86_64): `accel=kvm:tcg` -- fast with KVM, workable with TCG
- Pi VM (aarch64): `accel=tcg` only -- cross-architecture, always software emulation

**CI performance note:** The aarch64 Pi VM under TCG is significantly slower than the
x86_64 server VM with KVM. Expect 2-5 minutes for Pi VM boot on a GitHub Actions
runner. The `--phase server` option allows running server-only tests quickly in CI,
with the full Pi phase reserved for local runs or KVM-enabled runners. GitHub Actions
hosted runners (ubuntu-22.04/24.04) support KVM for x86_64 but not for cross-arch
aarch64, so TCG is unavoidable for the Pi VM.

## Test Phases

### Phase: server

1. Download/cache Debian bookworm cloud image (x86_64)
2. Generate ephemeral SSH keypair (ed25519)
3. Create cloud-init seed ISO (SSH key, Python3, qemu-guest-agent)
4. Create qcow2 overlay on base image (keeps base clean)
5. Boot server VM (user NIC + socket-listen NIC + guest agent channel)
6. Wait for guest agent, then wait for SSH
7. Run: `ansible-playbook ansible/site.yml -i <inventory> --limit test-vm`
   (targets nbp + pig + uhubctl groups)
8. Run: `ansible-playbook ansible/verify.yml -i <inventory> --limit test-vm`
9. Report results

**Note:** The `img` role downloads a Raspberry Pi OS image (~500MB-1GB) during step 7.
The test harness caches this in `tests/vm/images/` alongside the cloud images, and
the CI workflow caches it between runs using GitHub Actions cache to avoid re-downloading
on every run.

### Phase: pi

Requires server VM running from the server phase (Ansible roles applied, dnsmasq +
TFTP + NFS all active).

1. Verify server VM's socket-listen port is open (sequencing gate)
2. Ensure UEFI firmware (`QEMU_EFI.fd`) is available (system package `qemu-efi-aarch64`)
3. Boot Pi VM -- diskless, single NIC (socket-connect), guest agent, no local disk
4. Pi VM UEFI does PXE: DHCP from dnsmasq → TFTP kernel+initrd → NFS root mount
5. Wait for guest agent -- confirm boot succeeded, DHCP address acquired, NFS root mounted
6. Wait for SSH via ProxyJump through server
7. Run: `ansible-playbook ansible/verify.yml -i <inventory> --limit test-pi`
   (verifies Pi booted correctly, packages present, SSH keys, config files)
8. Report results

If the Pi VM fails to PXE boot (no DHCP, TFTP failure, kernel panic), the guest agent
provides diagnostics. If the guest agent is also unreachable, QEMU serial console output
(captured to a log file) is used for post-mortem debugging.

### Phase: all

Run server then pi sequentially.

### Teardown

- SSH `shutdown -h now` on each VM (Pi first, then server), wait for process exit
- If SSH is unavailable, use guest agent `guest-shutdown` command
- If guest agent is unavailable, SIGTERM the QEMU process
- Remove overlays, seed ISOs, ephemeral keys
- Skip teardown if `--keep-vm` flag is set

## Verification Playbooks

### Design Principle

Each role gets a paired `verify/main.yml` that checks the role's expected state.
Verify tasks are assertion-only -- they check state but never modify it. All tasks
use `changed_when: false` with modules like `stat`, `command`, `uri`, `assert`.

A top-level `ansible/verify.yml` mirrors the structure of `ansible/site.yml`,
including role verify tasks for the same host groups.

### Usage

```bash
# Verify production (welland)
ansible-playbook ansible/verify.yml -i ansible/inventory/ --limit fpgas.online

# Verify production (ps1)
ansible-playbook ansible/verify.yml -i ansible/inventory/ --limit ps1.fpgas.online

# Verify test VM
ansible-playbook ansible/verify.yml -i tests/inventory/test-hosts

# Verify a single role
ansible-playbook ansible/verify.yml -i ansible/inventory/ --limit fpgas.online --tags firewall
```

### Server Role Verifications

| Role | Key Checks |
|------|------------|
| firewall | nftables service running, ruleset contains expected chains |
| nfs | nfs-kernel-server running, exports contain /srv/nfs/rpi, exportfs shows share |
| img | /srv/nfs/rpi/bookworm/root/bin/bash exists (extracted image) |
| fixpi | qemu-user-static registered, management scripts in place, cmdline.txt + fstab templated |
| pxe | dnsmasq running, dhcp-range configured, TFTP directory populated, bootcode.bin symlinked, serial-number directories exist, aarch64 PXE boot path configured (when pxe_test_clients defined) |
| site | nginx running, Django site returns 200, gunicorn/uvicorn socket active |
| wssh | wssh service running |
| cam/stream-server | nginx-rtmp configuration present |
| uhubctl | package installed, udev rules in place |

### Pi Role Verifications

Run on the PXE-booted Pi VM via ProxyJump. The Pi VM is running from the server's
NFS root, so these checks verify both the netboot chain and the NFS root contents:

| Check | How |
|-------|-----|
| PXE boot succeeded | VM is running (guest agent responds, SSH reachable) |
| DHCP from server | eth0 has 10.21.0.x address from dnsmasq |
| NFS root mounted | `mount` shows NFS root from server |
| Kernel booted | `uname -a` shows expected kernel |
| SSH keys | authorized_keys contains expected keys |
| fpgas-apt repo | apt sources configured, signing key present |
| onpi packages | overlayroot, openocd, openfpgaloader, python3 installed |
| cam packages | gstreamer plugins, rpicam-apps, fpgas-online-cam installed |
| Pi config | hostname, console, network config as expected |

## Test Inventory

### Phase 1: Minimal Test Inventory

Self-contained, no vault secrets, synthetic data:

```
tests/
  inventory/
    test-hosts              # Host definitions
    group_vars/
      all/                  # Stripped-down vars
    host_vars/
      test-vm.yml           # Server vars (see required variables below)
```

The test VM plays both nbp and pig groups (same as production where one machine
serves both). The test inventory includes minimal Pi entries to exercise
dnsmasq/NFS template generation.

**Required synthetic variables in `test-vm.yml`:**

These are derived from production `host_vars/fpgas.online.yml` but with fake values:

| Variable | Purpose | Test Value |
|----------|---------|------------|
| `eth_local` | Internal NIC name | `eth1` (QEMU socket NIC) |
| `eth_local_address` | Internal IP | `10.21.0.1` |
| `eth_local_mac_address` | Internal NIC MAC | Matches QEMU socket NIC MAC |
| `eth_local_netmask` | CIDR prefix length | `24` (not dotted-decimal) |
| `eth_uplink_static_address` | Uplink IP (for nftables) | `203.0.113.1` (TEST-NET-3) |
| `pib_network` | Pi network prefix | `10.21.0` |
| `firewall_internal_networks` | nftables allowed nets | `["10.21.0.0/24"]` |
| `switch.host` | PoE switch address | `10.21.0.200` (unreachable, OK) |
| `switch.mac` | PoE switch MAC | `00:00:00:00:00:00` (dummy) |
| `switch.nos` | Pi list with sn, mac, port | 1-2 entries; `sn` must match `pxe_test_clients.sn` |
| `pi_pw` | Pi user password hash | plaintext test value (not vault-encrypted) |
| `user_name` | Pi system user | `testuser` |
| `domain` | Server domain | `test.fpgas.online` |
| `domain_name` | Hostname for pistat etc. | `test.fpgas.online` |
| `django_dir` | Django install path | `/srv/www/pib` |
| `letsencrypt_email` | certbot email | `test@test.fpgas.online` |
| `pxe_test_clients` | QEMU PXE client config | See schema in Pi VM PXE Boot section |

**Cloud-init must configure the second NIC** (the socket-listen interface) with
static IP 10.21.0.1/24 before Ansible runs, so that roles referencing
`eth_local_address` find a working interface. The QEMU socket NIC's MAC address
must match `eth_local_mac_address` in the test inventory.

### Phase 2: Production Inventory

After confirming the harness works with minimal inventory:

- Run against real `ansible/inventory/` with `--limit test-vm`
- Requires `--vault-password-file`
- Overlays real host_vars onto the test VM

## CLI Interface

```
uv run tests/vm/run_tests.py [options]

Options:
  --distro bookworm|trixie       Target Debian version (default: bookworm)
  --phase server|pi|all          Which phases to run (default: all)
  --keep-vm                      Don't teardown on success (for debugging)
  --inventory minimal|production Which inventory to use (default: minimal)
  --vault-password-file PATH     Required for --inventory production
  --ssh-to-server                Drop into SSH shell on server VM after setup
  --ssh-to-pi                    Drop into SSH shell on Pi VM (via ProxyJump)
```

## File Structure

```
tests/
  vm/
    run_tests.py               CLI entry point
    vm_manager.py              VMManager class (lifecycle, guest agent)
    cloud_init.py              Seed ISO generation (user-data, meta-data)
    network.py                 Socket networking setup, ProxyJump config
    images/                    Downloaded cloud images + RaspiOS (gitignored)
      .gitkeep

  inventory/
    test-hosts                 Minimal test inventory
    group_vars/
      all/                     Synthetic vars (non-sensitive)
    host_vars/
      test-vm.yml              Minimal server vars (all required vars listed above)

ansible/
  verify.yml                   Main verify playbook (mirrors site.yml)
  roles/
    <each-role>/
      verify/main.yml          Paired verification for each role

.github/
  workflows/
    vm-test.yml                CI workflow
```

## Dependencies

### System Packages

- `qemu-system-x86` -- server VM
- `qemu-system-arm` -- Pi VM (aarch64)
- `qemu-efi-aarch64` -- UEFI firmware for aarch64 PXE boot (provides QEMU_EFI.fd)
- `qemu-utils` -- qemu-img for overlays
- `cloud-image-utils` -- cloud-localds for seed ISOs (server VM only)

### Python Packages

- `ansible-core` -- already required
- `paramiko` -- SSH wait/connection logic

### Inside Server VM (via cloud-init and Ansible)

- `python3` -- required by Ansible
- `qemu-guest-agent` -- back-channel debugging
- `grub-efi-arm64-bin` -- provides grubaa64.efi for Pi PXE boot (installed by pxe role)
- RPi kernel+initrd -- already in NFS root /boot/ (no additional package needed)

## CI Integration

```yaml
# .github/workflows/vm-test.yml
- name: Install QEMU
  run: |
    sudo apt-get update
    sudo apt-get install -y qemu-system-x86 qemu-system-arm qemu-efi-aarch64 qemu-utils cloud-image-utils
    sudo chmod 666 /dev/kvm  # if available

- name: Cache VM images
  uses: actions/cache@v4
  with:
    path: tests/vm/images
    key: vm-images-bookworm-${{ hashFiles('tests/vm/run_tests.py') }}

- name: Run VM integration tests (server only in CI)
  run: uv run tests/vm/run_tests.py --phase server

- name: Run full integration tests (if KVM available)
  if: env.KVM_AVAILABLE == 'true'
  run: uv run tests/vm/run_tests.py --phase pi
```

Same script locally and in CI. KVM auto-detected, TCG fallback transparent.
The Pi phase is gated in CI since aarch64 TCG is slow; it always runs locally.

Image caching covers both the Debian cloud images and the Raspberry Pi OS image
downloaded by the `img` role, avoiding ~1.5GB of downloads on each CI run.

## Known Pre-Existing Role Issues

These issues exist in the current roles and must be addressed before or during
implementation. They are not introduced by this spec.

| Role | Issue | Impact on Testing |
|------|-------|-------------------|
| fixpi | `manage.yml` copies from `files/scripts/` directory that doesn't exist in the repo (maintenance.sh, production.sh, chroot-mount-pi-fs.bash) | fixpi will fail; scripts must be created or task must be skipped |
| site | `tasks/main.yml` includes `switch.yml` which doesn't exist in `site/tasks/` | site role will fail; file must be created or include must be conditional |

These should be fixed in the roles before running the VM test, or handled with
`--skip-tags` during initial test development.

## Future Work

- Trixie (Debian 13) support -- image URL is parameterised, add when needed
- Production inventory testing (Phase 2) -- requires vault password handling
- pytest integration if test suite grows
