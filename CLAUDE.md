## Background

This repo is part of the [fpgas.online](https://fpgas.online) FPGA-as-a-Service platform.
The platform provides remote access to real FPGA boards (Arty A7, NeTV2, Fomu, TinyTapeout)
via PoE-powered Raspberry Pis that are network-booted.

This codebase was extracted from the original monorepo [`carlfk/pici`](https://github.com/CarlFK/pici)
in April 2026 using `git filter-repo` to preserve commit history. The monorepo was split into
purpose-specific repos under the `fpgas-online` GitHub organization, where each repo produces
installable artifacts (pip packages or deb packages) consumed by the infrastructure repo.

## Repository Overview

Ansible infrastructure for deploying the fpgas.online platform. Contains playbooks,
inventory (hosts, group_vars, host_vars), and roles.

### Architecture

The infra repo does NOT embed application source code. Instead, roles install packages
from other repos:
- `site` role: `pip install fpgas-online-site fpgas-online-poe[cli]`
- `onpi` role: `apt install fpgas-online-setup-pi`
- `cam/pi` role: `apt install fpgas-online-cam`
- `fpgas-apt` role: Adds the fpgas.online apt repository to Pi hosts

### Key Files

- `ansible/site.yml` -- Main playbook with host groups: nbp (server), uhubctl, pig (web), pi
- `ansible/inventory/` -- Hosts, group_vars, host_vars (contains sensitive switch config)
- `ansible/roles/` -- All deployment roles

### Deployment Target

The primary server is `tweed.welland.mithis.com` (also `welland.fpgas.online`).
Two network interfaces: eth-local (10.21.0.1, FPGA network) and eth-uplink (upstream).

## Conventions

- **Python**: Use `uv` for all Python commands (`uv run`, `uv pip`). Never use bare `python` or `pip`.
- **Dates**: Use ISO 8601 (YYYY-MM-DD) or day-first formats. Never American-style month-first dates.
- **Commits**: Make small, discrete commits. Each logical unit of work gets its own commit.
- **License**: Apache 2.0.
- **Linting**: All repos have CI lint workflows. Fix lint errors before pushing.
- **No force push**: Branch protection is enabled on main. Never force push.

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

## Linting

- yamllint: blocking (`.yamllint.yml`)
- ansible-lint: advisory, many legacy issues (`.ansible-lint` has extensive skip list)
