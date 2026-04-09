# fpgas.online-infra

Ansible infrastructure-as-code for deploying and managing the [fpgas.online](https://fpgas.online) FPGA-as-a-Service platform.

## Overview

This repo contains the Ansible playbooks, inventory, and roles that configure:

- **Server (x86)**: Django web app, nginx, dnsmasq (DHCP/TFTP), NFS, PXE boot, camera streaming, web SSH
- **Raspberry Pis**: network-booted nodes, each connected to an FPGA board

## Architecture

### Pi Provisioning via nspawn+sshd

Pi configuration is **baked into the NFS root on the server**, not applied to running Pis.
The server uses `systemd-nspawn` with `qemu-user-static` ARM syscall emulation to create
a chroot environment from the Pi NFS root, runs `sshd` inside it, and Ansible connects
via SSH as if it were a real Pi. This ensures all packages, services, and configuration
are pre-installed before any Pi boots.

```
Server (x86)                              Pi (ARM)
┌─────────────────────┐                   ┌──────────────────┐
│ site.yml runs:      │                   │ PXE boots from   │
│  1. Server roles    │                   │ server TFTP      │
│     (SSH to server) │                   │                  │
│  2. nspawn+sshd on  │                   │ Mounts NFS root  │
│     NFS root        │ ──── PXE ────▶   │ read-only +      │
│  3. Pi roles via    │                   │ overlayfs tmpfs   │
│     SSH to :2200    │                   │                  │
│  4. Stop nspawn     │                   │ All config is    │
│                     │                   │ pre-baked         │
└─────────────────────┘                   └──────────────────┘
```

### PXE Boot Chain

1. Pi powers on, ROM requests DHCP from dnsmasq
2. dnsmasq provides IP, TFTP server address
3. Pi fetches `bootcode.bin`, `kernel8.img`, DTB, initramfs via TFTP
4. Kernel boots with `root=/dev/nfs nfsroot=<server>:<path> overlayroot=tmpfs`
5. NFS root mounted read-only, overlayfs provides tmpfs write layer
6. All packages and services are already installed in the NFS root

### Packages

The roles install packages from other fpgas-online repos rather than embedding source code:

| Package | Source repo | Installed via |
|---------|------------|---------------|
| `fpgas-online-site` | [fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) | pip (on server) |
| `fpgas-online-poe[cli]` | [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) | pip (on server) |
| `fpgas-online-setup-pi` | [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) | apt (in nspawn chroot) |
| `fpgas-online-cam` | [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) | apt (in nspawn chroot) |

## Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Ansible (installed via `uv sync`)
- SSH access to managed hosts
- The [fpgas.online apt repo](https://github.com/fpgas-online/apt) for Pi deb packages
- `qemu-user-static` and `systemd-container` on the server (for Pi NFS root provisioning)

## Usage

### Deploy

```bash
# Full deployment (server + Pi NFS root provisioning)
uv run ansible-playbook ansible/site.yml

# Server roles only
uv run ansible-playbook ansible/site.yml --limit nbp,uhubctl,pig

# Pi NFS root provisioning only (runs in nspawn chroot on server)
uv run ansible-playbook ansible/site.yml --limit nbp,pi
```

### Verify

Two verification playbooks check the deployment:

```bash
# Verify server setup (TFTP, NFS, dnsmasq, NFS root contents)
uv run ansible-playbook ansible/verify-server.yml

# Verify running Pi (NFS mount, overlayfs, services, packages)
# Run after Pis have booted
uv run ansible-playbook ansible/verify-pi.yml

# Skip hardware-dependent checks (camera, FPGA detection)
uv run ansible-playbook ansible/verify-pi.yml --skip-tags hw-camera,hw-fpga
```

### Test (QEMU VMs)

The test suite verifies the production setup without touching production systems. It boots
a Debian server VM, applies the same `site.yml`, then PXE-boots a virtual Pi using
[fpgas-online/rpi-qemu](https://github.com/fpgas-online/rpi-qemu) (patched QEMU with
BCM2838 GENET ethernet emulation).

```bash
# Install qemu-rpi packages
echo "deb [trusted=yes] https://fpgas-online.github.io/rpi-qemu trixie main" \
  | sudo tee /etc/apt/sources.list.d/qemu-rpi.list
sudo apt update && sudo apt install qemu-rpi-system-arm qemu-rpi-pxeboot

# Run full test (server + Pi PXE boot + verification)
uv run tests/vm/run_tests.py --phase all

# Run server phase only (faster iteration)
uv run tests/vm/run_tests.py --phase server --keep-vm

# Debug: SSH into server after setup
uv run tests/vm/run_tests.py --phase all --ssh-to-server --keep-vm
```

As rpi-qemu increases emulation fidelity (virtual camera, virtual USB hub, etc.),
test coverage grows correspondingly.

## Host Groups

| Group | Hosts | Purpose |
|-------|-------|---------|
| `nbp` | server | Netboot Pi server: firewall, NFS, image prep, Pi OS config, PXE/DHCP |
| `uhubctl` | server | USB hub power control (for FPGA board resets) |
| `pig` | server | Web server: Django site, web SSH, camera stream server |
| `pi` | nspawn chroot | Pi NFS root provisioning: apt repos, camera, environment setup |

## Roles

| Role | Target | Purpose |
|------|--------|---------|
| `firewall` | server (SSH) | nftables firewall rules |
| `nfs` | server (SSH) | NFS server for Pi netboot filesystems |
| `img` | server (SSH) | Download and extract Raspberry Pi OS images |
| `fixpi` | server (SSH) | Configure Pi OS in the NFS root (boot config, users, chroot installs) |
| `pxe` | server (SSH) | dnsmasq DHCP/DNS/TFTP for Pi network booting |
| `nspawn-pi` | server (SSH) | Start/stop nspawn+sshd for Pi NFS root provisioning |
| `site` | server (SSH) | Deploy Django web app (pip install, nginx, gunicorn, daphne) |
| `wssh` | server (SSH) | Web SSH terminal (webssh) |
| `cam/stream-server` | server (SSH) | nginx-rtmp HLS streaming server |
| `uhubctl` | server (SSH) | USB hub power control for FPGA board resets |
| `fpgas-apt` | NFS root (nspawn) | Add fpgas.online apt repository |
| `cam/pi` | NFS root (nspawn) | Install camera capture package |
| `onpi` | NFS root (nspawn) | Install Pi environment setup package |

## Linting

- **yamllint**: blocking (zero errors)
- **ansible-lint**: advisory (legacy issues tracked in [#4](https://github.com/fpgas-online/fpgas.online-infra/issues/4))

## Related Repos

- [fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) -- Django web app
- [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) -- PoE switch management
- [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) -- Camera capture scripts
- [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) -- Pi environment setup
- [fpgas.online-netboot-pi](https://github.com/fpgas-online/fpgas.online-netboot-pi) -- Netboot filesystem tools
- [fpgas.online-test-designs](https://github.com/fpgas-online/fpgas.online-test-designs) -- FPGA test designs
- [rpi-qemu](https://github.com/fpgas-online/rpi-qemu) -- Patched QEMU with RPi 4B GENET ethernet for testing

## License

Apache 2.0
