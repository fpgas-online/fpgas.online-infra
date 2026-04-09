## Background

This repo is part of the [fpgas.online](https://fpgas.online) FPGA-as-a-Service platform.
The platform provides remote access to real FPGA boards (Arty A7, NeTV2, Fomu, TinyTapeout)
via PoE-powered Raspberry Pis that are network-booted from an x86 server.

This codebase was extracted from the original monorepo [`carlfk/pici`](https://github.com/CarlFK/pici)
in April 2026 using `git filter-repo` to preserve commit history. The monorepo was split into
purpose-specific repos under the `fpgas-online` GitHub organization, where each repo produces
installable artifacts (pip packages or deb packages) consumed by the infrastructure repo.

## Repository Overview

Ansible infrastructure for deploying the fpgas.online platform. Contains playbooks,
inventory (hosts, group_vars, host_vars), and roles.

### Architecture

**Server (x86) provisions everything.** The server runs dnsmasq (DHCP/TFTP), NFS, and
a Django web app. It also builds the Pi NFS root filesystem — downloading the RPi OS image,
extracting it, then running Pi-targeted Ansible roles against the NFS root via
`systemd-nspawn` + `sshd` with `qemu-user-static` ARM syscall emulation. This makes the
NFS root chroot appear as a normal SSH host to Ansible.

**Pis boot read-only from the network.** Each Pi PXE boots via:
dnsmasq DHCP → TFTP (bootcode.bin, kernel8.img, DTB, initramfs) → NFS root mounted
read-only with `overlayroot=tmpfs` (tmpfs overlay for ephemeral writes).

All Pi configuration (packages, services, config files) is baked into the NFS root on
the server. The Pi itself never has Ansible run against it directly — it boots from
the pre-provisioned NFS root.

The infra repo does NOT embed application source code. Instead, roles install packages
from other repos:
- `site` role: `pip install fpgas-online-site fpgas-online-poe[cli]`
- `onpi` role: `apt install fpgas-online-setup-pi` (via nspawn chroot)
- `cam/pi` role: `apt install fpgas-online-cam` (via nspawn chroot)
- `fpgas-apt` role: Adds the fpgas.online apt repository (via nspawn chroot)

### Deployment Flow

1. `site.yml` runs `nbp`/`uhubctl`/`pig` plays against the server via SSH
2. `site.yml` `pi` play: server starts nspawn+sshd on the NFS root, Pi roles
   (`fpgas-apt`, `cam/pi`, `onpi`) run against localhost:2200, server stops nspawn
3. `verify-server.yml` checks the x86 setup (TFTP, NFS, dnsmasq, NFS root packages/config)
4. Pis PXE boot from the fully-provisioned server
5. `verify-pi.yml` checks running Pis (NFS mount, overlayfs, services, packages)

### Key Files

- `ansible/site.yml` -- Main playbook with host groups: nbp (server), uhubctl, pig (web), pi
- `ansible/verify-server.yml` -- Server-side verification (TFTP, NFS, packages, config)
- `ansible/verify-pi.yml` -- Pi-side verification (boot, overlayfs, services) — same for test and production
- `ansible/inventory/` -- Hosts, group_vars, host_vars (contains sensitive switch config)
- `ansible/roles/` -- All deployment roles
- `ansible/roles/nspawn-pi/` -- Manages nspawn+sshd lifecycle for Pi NFS root provisioning
- `tests/vm/` -- QEMU VM test harness

Note: `nspawn-pi` role, `verify-server.yml`, and `verify-pi.yml` are being implemented
(replacing the current `verify.yml` and plain chroot approach in `fixpi/netboot.yml`).

### Deployment Targets

- **`welland.fpgas.online`** (`tweed.welland.mithis.com`) -- Welland, South Australia.
  Two network interfaces: eth-local (10.21.0.1, FPGA network) and eth-uplink (upstream).
  PoE switch: Netgear S3300. FPGA boards: Arty, NeTV2, Fomu, TT FPGA, Acorn CLE-215+.
- **`ps1.fpgas.online`** (`val2`) -- Pumping Station: One hackerspace, Chicago, IL.
  Two network interfaces: eth-local (10.21.0.1/24, RPi network) and eth-uplink (76.227.131.147/25).
  PoE switch: Netgear FS728TPv2. FPGA boards: Arty A7, LiteFury.

### Testing

QEMU VM tests verify the production setup without touching production systems. The test
harness boots a Debian server VM, applies the exact same `site.yml` and `verify-server.yml`
used in production, then PXE-boots a virtual Pi using patched QEMU from
[fpgas-online/rpi-qemu](https://github.com/fpgas-online/rpi-qemu) (BCM2838 GENET ethernet
emulation on raspi4b) and runs `verify-pi.yml`. Only the inventory differs between test
and production.

As rpi-qemu increases emulation fidelity (virtual camera, virtual USB hub, etc.), test
coverage grows correspondingly.

```bash
# Run full test locally
uv run tests/vm/run_tests.py --phase all

# Run server phase only (faster iteration)
uv run tests/vm/run_tests.py --phase server --keep-vm
```

## Conventions

- **Python**: Use `uv` for all Python commands (`uv run`, `uv pip`). Never use bare `python` or `pip`.
- **Dates**: Use ISO 8601 (YYYY-MM-DD) or day-first formats. Never American-style month-first dates.
- **Commits**: Make small, discrete commits. Each logical unit of work gets its own commit.
- **License**: Apache 2.0.
- **Linting**: All repos have CI lint workflows. Fix lint errors before pushing.
- **No force push**: Branch protection is enabled on main. Never force push.
- **No QEMU-specific workarounds**: Test infrastructure uses the identical Ansible setup as production. If something doesn't work in QEMU, fix it in rpi-qemu, not in Ansible roles.

## Related Repos

| Repo | Purpose |
|------|---------|
| [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) | Ansible infrastructure (playbooks, roles, inventory) |
| [fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) | Django web application |
| [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) | SNMP PoE switch management |
| [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) | Camera capture and streaming |
| [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) | Raspberry Pi environment setup |
| [fpgas.online-netboot-pi](https://github.com/fpgas-online/fpgas.online-netboot-pi) | Netboot filesystem tools |
| [fpgas.online-tools](https://github.com/fpgas-online/fpgas.online-tools) | Utility scripts |
| [fpgas.online-test-designs](https://github.com/fpgas-online/fpgas.online-test-designs) | FPGA test designs |
| [apt](https://github.com/fpgas-online/apt) | APT package repository (GitHub Pages) |
| [rpi-qemu](https://github.com/fpgas-online/rpi-qemu) | Patched QEMU with RPi 4B GENET ethernet for testing |

## Linting

- yamllint: blocking (`.yamllint.yml`)
- ansible-lint: advisory, many legacy issues (`.ansible-lint` has extensive skip list)
