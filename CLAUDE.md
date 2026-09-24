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

**CI builds the Pi NFS root; the server pulls it.** GitHub Actions
(`.github/workflows/nfsroot-build.yml`, an arm64 runner with native AArch32)
runs `ansible/ci-nfsroot.yml` — the RasPiOS download/extract plus the
Pi-targeted roles over a `community.general.chroot` connection — and publishes
the provisioned root as a public OCI image at `ghcr.io/fpgas-online/nfsroot`
(rolling `bookworm-armhf` from main, dated pinnable tags always). The server
runs dnsmasq (DHCP/TFTP), NFS, and a Django web app; its `img` role pulls the
image (podman, digest-stamped) and extracts it to `/srv/nfs/rpi/<dist>`, and
`fixpi` applies the site layer (pi password, ssh host keys, controller
authorized_keys, TT catalogue, per-site config) on top. The old on-server
nspawn/chroot provisioning path is gone.

**Pis boot read-only from the network.** Each Pi PXE boots via:
dnsmasq DHCP → TFTP (bootcode.bin, kernel8.img, DTB, initramfs) → NFS root mounted
read-only with `overlayroot=tmpfs` (tmpfs overlay for ephemeral writes).

All Pi configuration (packages, services, config files) is baked into the NFS root on
the server. The Pi itself never has Ansible run against it directly — it boots from
the pre-provisioned NFS root.

The infra repo does NOT embed application source code. Instead, roles install packages
from other repos:
- `site` role: `pip install fpgas-online-site fpgas-online-poe[cli]`
- `onpi` role: `apt install fpgas-online-setup-pi` (baked into the CI image)
- `cam/pi` role: `apt install fpgas-online-cam` (baked into the CI image)
- `fpgas_apt` role: Adds the fpgas.online apt repository (baked into the CI image)

### Deployment Flow

1. CI publishes the provisioned NFS root image (every PR/merge via the VM
   test workflow's `nfsroot` job, plus a weekly cron)
2. `site.yml` runs `nbp`/`uhubctl`/`pig` plays against the server via SSH;
   the `img` role pulls+extracts the image and `fixpi` applies the site layer
3. `verify-server.yml` checks the x86 setup (TFTP, NFS, dnsmasq, NFS root packages/config)
4. Pis PXE boot from the fully-provisioned server
5. `verify-pi.yml` checks running Pis (NFS mount, overlayfs, services, packages)

### Key Files

- `ansible/site.yml` -- Main playbook with host groups: nbp (server), uhubctl, pig (web), pi
- `ansible/web.yml` -- Web tier play (site, wssh, cam/stream_server, ttsite); imported by site.yml, runnable alone
- `ansible/verify-server.yml` -- Server-side verification (TFTP, NFS, packages, config)
- `ansible/verify-pi.yml` -- Pi-side verification (boot, overlayfs, services) — same for test and production
- `ansible/inventory/` -- Hosts, group_vars, host_vars (contains sensitive switch config)
- `ansible/roles/` -- All deployment roles
- `ansible/ci-nfsroot.yml` + `ansible/inventory-ci-nfsroot/` -- CI build of the
  Pi NFS root image (chroot connection; publishes to GHCR)
- `tests/vm/` -- QEMU VM test harness


### Deployment Targets

- **`welland.fpgas.online`** (`tweed.welland.mithis.com`) -- Welland, South Australia.
  Two network interfaces: eth-local (10.21.0.1, FPGA network) and eth-uplink (upstream).
  PoE switch: Netgear S3300. FPGA boards: Arty, NeTV2, Fomu, TT FPGA, Acorn CLE-215+.
- **`ps1.fpgas.online`** (`val2`) -- Pumping Station: One hackerspace, Chicago, IL.
  Two network interfaces: eth-local (10.21.0.1/24, RPi network) and eth-uplink (76.227.131.147/25).
  PoE switch: Netgear FS728TPv2. FPGA boards: Arty A7, LiteFury.

### Testing

QEMU VM tests verify the production setup end-to-end without touching production
systems. The harness boots a Debian server VM, applies the same `site.yml` and
`verify-server.yml` used in production, PXE-boots a virtual Pi using patched QEMU
from [fpgas-online/rpi-qemu](https://github.com/fpgas-online/rpi-qemu) (BCM2838
GENET ethernet emulation on `raspi4b`), and runs `verify-pi.yml`. Only the inventory
differs between test and production.

**End-to-end coverage** (nothing is skipped: the harness passes no
`--skip-tags`, and the test inventory differs from production only in site
data -- addresses, names, the switch it cannot reach):

- `site.yml` converges a fresh Debian 13 server (tweed's OS), pulling and
  extracting the NFS root image built from the same checkout
- `verify-server.yml`: firewall, dnsmasq, TFTP layout, NFS exports, NFS root
  packages and the site layer (`fleet.toml`, `tt-boards.yaml`), web tier
- Virtual Pi PXE boots from the flat per-port-VLAN TFTP root, exactly as
  production Pis do: DHCP → TFTP → kernel → initramfs → NFS root read-only
  with overlayroot → systemd; SSH and the web terminal's password login work
- `verify-pi.yml`: mounts, per-port address, services, packages, JTAG tools,
  camera/TT services, nfsroot-watchdog armed -- and **fleet registration**:
  the server's Django app shows `/fleet/<serial>/` online with the Pi's
  current boot id

```bash
# Install qemu-rpi packages
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://fpgas.online/rpi-qemu/rpi-qemu.gpg | sudo tee /etc/apt/keyrings/rpi-qemu.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/rpi-qemu.gpg] https://fpgas.online/rpi-qemu/trixie/ ./" \
  | sudo tee /etc/apt/sources.list.d/qemu-rpi.list
sudo apt-get update && sudo apt-get install -y qemu-rpi-system-arm qemu-rpi-pxeboot

# Full run locally (server + Pi + both verify playbooks); --nfsroot-image
# names the prebuilt root the server pulls (rolling tag, or a CI run's
# dated tag to reproduce that run)
uv run tests/vm/run_tests.py --phase all \
  --nfsroot-image ghcr.io/fpgas-online/nfsroot:bookworm-armhf

# Server phase only (faster iteration)
uv run tests/vm/run_tests.py --phase server --keep-vm \
  --nfsroot-image ghcr.io/fpgas-online/nfsroot:bookworm-armhf
```

With KVM (used automatically when `/dev/kvm` is available) the whole run is
about 13-14 min in CI: server VM up in ~20 s, `site.yml` ~9 min (the Pi root
image is pulled in the background from right after `netif` and extracted in
the last play), then the Pi netboots (~2 min of aarch64 TCG to SSH) while
`verify-server` runs, and `verify-pi` (one collector call plus the fleet
lookup) takes about a minute.

The image build (`nfsroot-build.yml`, arm64 runner) is reused whenever no
image input changed (`tests/ci/nfsroot_inputs.py`), and otherwise converges
main's latest image (~3 min); the weekly scheduled build starts from
RasPiOS (~10 min).

**CI:** `.github/workflows/vm-test.yml` runs the full end-to-end test on every
push to `main` and on PRs. Serial logs are uploaded as an artifact on every run
(including failures) for post-mortem debugging.

As rpi-qemu increases emulation fidelity (virtual camera, virtual USB hub, etc.),
test coverage grows correspondingly.

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
- ansible-lint: blocking, and the tree is clean (`.ansible-lint`). Run it
  exactly as CI does: `uv sync && uv run ansible-galaxy collection install -r
  requirements.yml && (cd ansible && uv run ansible-lint)`. Both linters are
  pinned in the `dev` group of `pyproject.toml`. Fix violations rather than
  skipping them; a deliberate construct gets a scoped `# noqa: <rule>` with a
  comment explaining why.
