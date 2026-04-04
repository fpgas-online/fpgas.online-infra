# QEMU VM Testing for Ansible Playbooks

**Date:** 2026-04-04
**Status:** Approved

## Goal

Automated end-to-end testing of the fpgas.online Ansible infrastructure using QEMU VMs.
The test harness boots a Debian server VM, applies the Ansible roles, then boots an
aarch64 Pi VM that netboots from the server -- verifying the full deployment pipeline
without real hardware.

## Requirements

- Test server roles (nbp, pig, uhubctl) against a QEMU x86_64 VM
- Test Pi netboot chain: server prepares NFS root + TFTP + DHCP, Pi VM boots from it
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
|  PXE/netboot from server        |
|  NFS root from server           |
|  No direct host access           |
+----------------------------------+

Access: host -> SSH server:2222 -> SSH pi:10.21.0.x (ProxyJump)
```

### Networking

The Pi VM has a single NIC on the QEMU socket VLAN -- no user-mode NIC, no direct
host access. This mirrors production where Pis are only reachable via the server.

QEMU socket networking (`-netdev socket,listen/connect`) connects the two VMs on a
virtual LAN without requiring root, TAP interfaces, or bridges.

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

### Phase: pi

Requires server VM running from the server phase.

1. Download/cache Debian bookworm cloud image (aarch64)
2. Boot Pi VM (socket-connect NIC + guest agent, no user NIC)
3. Wait for guest agent -- confirm DHCP acquired, NFS mounted
4. Wait for SSH via ProxyJump through server
5. Run: `ansible-playbook ansible/verify.yml -i <inventory> --limit test-pi`
   (Pi roles already applied via fixpi chroot on server)
6. Report results

### Phase: all

Run server then pi sequentially.

### Teardown

- SSH `shutdown -h now` on each VM, wait for process exit
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
| pxe | dnsmasq running, dhcp-range configured, TFTP directory populated, bootcode.bin symlinked |
| site | nginx running, Django site returns 200, gunicorn/uvicorn socket active |
| wssh | wssh service running |
| cam/stream-server | nginx-rtmp configuration present |
| uhubctl | package installed, udev rules in place |

### Pi Role Verifications

| Role | Key Checks |
|------|------------|
| (netboot) | NFS root mounted, DHCP address on 10.21.0.x, hostname set |
| fpgas-apt | fpgas.online apt repo configured, signing key present |
| onpi | Package suite installed (overlayroot, openocd, openfpgaloader, python3) |
| cam/pi | gstreamer plugins, rpicam-apps, fpgas-online-cam installed |

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
      test-vm.yml           # Dummy interfaces, IPs, 1-2 fake Pi entries
```

The test VM plays both nbp and pig groups (same as production where one machine
serves both). The test inventory includes minimal Pi entries (MAC, port, serial)
to exercise dnsmasq/NFS template generation.

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
    images/                    Downloaded cloud images (gitignored)
      .gitkeep

  inventory/
    test-hosts                 Minimal test inventory
    group_vars/
      all/                     Synthetic vars (non-sensitive)
    host_vars/
      test-vm.yml              Minimal server vars

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

- name: Run VM integration tests
  run: uv run tests/vm/run_tests.py --phase all
```

Same script locally and in CI. KVM auto-detected, TCG fallback transparent.

## Future Work

- Trixie (Debian 13) support -- image URL is parameterised, add when needed
- Production inventory testing (Phase 2) -- requires vault password handling
- pytest integration if test suite grows
- Caching cloud images in CI to speed up repeated runs
