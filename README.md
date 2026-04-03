# fpgas.online-infra

Ansible infrastructure-as-code for deploying and managing the [fpgas.online](https://fpgas.online) FPGA-as-a-Service platform.

## Overview

This repo contains the Ansible playbooks, inventory, and roles that configure:

- **tweed** (the server): Django web app, nginx, dnsmasq, NFS, PXE boot, camera streaming, web SSH
- **Raspberry Pis**: network-booted nodes, each connected to an FPGA board

The roles install packages from other fpgas-online repos rather than embedding source code:

| Package | Source repo | Installed via |
|---------|------------|---------------|
| `fpgas-online-site` | [fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) | pip |
| `fpgas-online-poe[cli]` | [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) | pip |
| `fpgas-online-setup-pi` | [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) | apt |
| `fpgas-online-cam` | [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) | apt |

## Prerequisites

- Ansible
- SSH access to all managed hosts
- The [fpgas.online apt repo](https://github.com/fpgas-online/apt) for Pi-side deb packages

## Usage

```bash
cd ansible
ansible-playbook site.yml
```

Target specific host groups:

```bash
ansible-playbook site.yml --limit pig     # server only
ansible-playbook site.yml --limit pi      # Pis only
ansible-playbook site.yml --limit nbp     # netboot Pi server
```

## Host Groups

| Group | Hosts | Purpose |
|-------|-------|---------|
| `nbp` | tweed | Netboot Pi server: firewall, NFS, image prep, Pi OS config, PXE/DHCP |
| `uhubctl` | tweed | USB hub power control (for FPGA board resets) |
| `pig` | tweed | Web server: Django site, web SSH, camera stream server |
| `pi` | Pi boards | Raspberry Pi nodes: camera capture, environment setup |

## Roles

| Role | Runs on | Purpose |
|------|---------|---------|
| `firewall` | server | nftables firewall rules |
| `nfs` | server | NFS server for Pi netboot filesystems |
| `img` | server | Download and extract Raspberry Pi OS images |
| `fixpi` | server | Configure Pi OS in the NFS root (boot config, network, hostname) |
| `pxe` | server | dnsmasq DHCP/DNS/TFTP for Pi network booting |
| `site` | server | Deploy Django web app (pip install, nginx, gunicorn, daphne) |
| `wssh` | server | Web SSH terminal (webssh) |
| `cam/stream-server` | server | nginx-rtmp HLS streaming server |
| `uhubctl` | server | USB hub power control for FPGA board resets |
| `fpgas-apt` | Pis | Add fpgas.online apt repository |
| `cam/pi` | Pis | Install camera capture package |
| `onpi` | Pis | Install Pi environment setup package |

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

## License

Apache 2.0
