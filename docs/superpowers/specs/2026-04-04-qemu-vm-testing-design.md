# QEMU VM Testing for Ansible Playbooks

**Date:** 2026-04-04
**Status:** Approved

## Goal

Automated end-to-end testing of the fpgas.online Ansible infrastructure using QEMU VMs.
The test harness boots a Debian server VM, applies the Ansible roles, then boots an
aarch64 Pi VM on the same virtual LAN -- verifying both the server deployment and the
NFS root filesystem it prepares for Pis.

## Requirements

- Test server roles (nbp, pig, uhubctl) against a QEMU x86_64 VM
- Test Pi environment: boot an aarch64 VM with RPi hardware config, mount and verify
  the NFS root prepared by the server
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
|  Boots from cloud image,        |
|  configured with RPi hardware    |
|  vars, mounts + verifies NFS    |
|  root from server                |
|  No direct host access           |
+----------------------------------+

Access: host -> SSH server:2222 -> SSH pi:10.21.0.x (ProxyJump)
```

### Pi VM Boot Approach

Real Raspberry Pis use the VideoCore BootROM to PXE-boot -- a protocol that QEMU
does not emulate. Instead, the Pi VM boots from a Debian aarch64 cloud image but is
configured with the same Ansible variables as a real RPi (MAC, hostname, IP assignment).

After boot, the Pi VM:
1. Gets a DHCP address from the server's dnsmasq (verifying DHCP config works)
2. NFS-mounts `/srv/nfs/rpi/bookworm/root` from the server (verifying NFS exports work)
3. Runs verification against both its own state and the NFS root contents

This tests everything except the actual PXE/TFTP boot chain (bootcode.bin, serial-number
symlinks). Those are verified server-side via filesystem checks in the pxe role's
verify playbook.

**Note on architecture:** The NFS root contains armhf (32-bit) binaries since production
uses Raspberry Pi OS armhf. The Pi VM is aarch64 (64-bit), which can run armhf binaries
via the kernel's 32-bit compat layer. The Pi VM does not chroot into the NFS root --
it mounts and inspects the filesystem contents. For checks that require executing armhf
binaries, the server's qemu-user-static chroot is used instead.

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

Requires server VM running from the server phase.

1. Verify server VM's socket-listen port is open (sequencing gate)
2. Download/cache Debian bookworm cloud image (aarch64)
3. Boot Pi VM (socket-connect NIC + guest agent, no user NIC)
4. Wait for guest agent -- confirm DHCP address acquired from server's dnsmasq
5. NFS-mount server's `/srv/nfs/rpi/bookworm/root` on the Pi VM
6. Wait for SSH via ProxyJump through server
7. Run: `ansible-playbook ansible/verify.yml -i <inventory> --limit test-pi`
   (verifies NFS root contents and Pi configuration)
8. Report results

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
| pxe | dnsmasq running, dhcp-range configured, TFTP directory populated, bootcode.bin symlinked, serial-number directories exist |
| site | nginx running, Django site returns 200, gunicorn/uvicorn socket active |
| wssh | wssh service running |
| cam/stream-server | nginx-rtmp configuration present |
| uhubctl | package installed, udev rules in place |

### Pi Role Verifications

Run on the Pi VM via ProxyJump. Verifies the NFS root and Pi environment:

| Check | How |
|-------|-----|
| DHCP from server | eth0 has 10.21.0.x address from dnsmasq |
| NFS mount | server's /srv/nfs/rpi/bookworm/root is mounted |
| NFS root contents | key files exist (bin/bash, usr/bin/openocd, etc.) |
| fpgas-apt repo | apt sources configured in NFS root |
| onpi packages | package manifests present in NFS root |
| cam packages | gstreamer/rpicam-apps present in NFS root |
| SSH keys | authorized_keys in NFS root contains expected keys |
| Pi config files | cmdline.txt, config.txt, fstab correctly templated |

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
| `firewall_internal_networks` | nftables allowed nets | `["10.21.0.0/24"]` |
| `switch.host` | PoE switch address | `10.21.0.200` (unreachable, OK) |
| `switch.nos` | Pi list with sn, mac, port | 1-2 fake entries with dummy serials |
| `pi_pw` | Pi user password hash | plaintext test value (not vault-encrypted) |
| `domain` | Server domain | `test.fpgas.online` |
| `django_dir` | Django install path | `/srv/www/pib` |
| `letsencrypt_email` | certbot email | `test@test.fpgas.online` |

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
- `qemu-utils` -- qemu-img for overlays
- `cloud-image-utils` -- cloud-localds for seed ISOs

### Python Packages

- `ansible-core` -- already required
- `paramiko` -- SSH wait/connection logic

### Inside VMs (via cloud-init)

- `python3` -- required by Ansible
- `qemu-guest-agent` -- back-channel debugging

## CI Integration

```yaml
# .github/workflows/vm-test.yml
- name: Install QEMU
  run: |
    sudo apt-get update
    sudo apt-get install -y qemu-system-x86 qemu-system-arm qemu-utils cloud-image-utils
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

## Future Work

- Trixie (Debian 13) support -- image URL is parameterised, add when needed
- Production inventory testing (Phase 2) -- requires vault password handling
- pytest integration if test suite grows
