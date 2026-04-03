# Design: Split carlfk/pici into fpgas-online repos

**Date**: 2026-04-03
**Status**: Draft
**Author**: Tim Ansell + Claude

## Summary

Split the monolithic `carlfk/pici` repository into purpose-specific repositories
under the `fpgas-online` GitHub organization. Each source repo produces
installable artifacts (pip packages, deb packages), and the ansible infra repo
consumes them declaratively. No submodules.

## Background

`carlfk/pici` is the original monorepo for the fpgas.online FPGA-as-a-Service
platform. It contains ansible infrastructure, a Django web app, camera streaming,
PoE switch management, Pi configuration, netboot tooling, and misc scripts — all
in a single repo. The web app is embedded inside an ansible role at
`ansible/roles/site/files/pib/`.

The fpgas-online org already has 5 repos:
- `fpgas-online/.github` — org profile
- `fpgas-online/website` — fpgas.online website
- `fpgas-online/todo` — TODO tracking (23 open issues)
- `fpgas-online/fpgas.online-test-designs` — LiteX FPGA test designs (replaces `ansible/roles/ci/`)
- `fpgas-online/demo-repository` — demo repo

## Architecture

```
Source repos          Build artifacts              Infra consumption
============          ===============              =================

fpgas.online-site  →  pip package              →  site role: pip install
fpgas.online-poe   →  pip package              →  site role: pip install
fpgas.online-cam   →  deb package              →  cam roles: apt install
fpgas.online-setup-pi → deb package            →  fixpi/onpi roles: apt install
fpgas.online-netboot-pi → standalone scripts   →  img/pxe roles: clone/copy
fpgas.online-tools →  standalone scripts       →  ad-hoc / infra references
fpgas.online-infra →  (not an artifact)        →  runs ansible-playbook
```

All deb packages are hosted via a GitHub Pages-backed apt repository.
pip packages are installed directly from GitHub (or PyPI if warranted later).

## New Repositories

### 1. fpgas.online-infra

**Purpose**: Pure ansible infrastructure-as-code. Playbooks, inventory, roles,
group/host vars. Roles install packages from other repos rather than embedding
source code.

**Source content from pici**:
- `ansible/site.yml`
- `ansible/inventory/` (hosts, host_vars, group_vars)
- `ansible/roles/firewall/`
- `ansible/roles/nfs/`
- `ansible/roles/img/` (tasks stay; `img2files.sh` moves to netboot-pi)
- `ansible/roles/pxe/`
- `ansible/roles/site/` (deployment tasks + templates stay; `files/pib/` removed)
- `ansible/roles/wssh/` (all files stay — 4 static configs + 1 Jinja2 template)
- `ansible/roles/fixpi/` (tasks + templates stay; most `files/` move out)
- `ansible/roles/onpi/` (tasks stay; all `files/` move to setup-pi)
- `ansible/roles/cam/` (tasks stay; scripts/configs move to cam repo)
- `ansible/roles/uhubctl/`
- `ansible/roles/ci/` (remove — replaced by fpgas.online-test-designs)
- Root docs: `README.md`, `TECHDEBT.md`, `CONTRIBUTORS.txt`, `notes.txt`

**Jinja2 templates and deployment configs staying in infra**:

Site role:
- `templates/daphne.service.j2`, `daphne.socket.j2`
- `templates/gunicorn.service.j2`, `gunicorn.socket.j2`
- `templates/uvicorn.service.j2`
- `templates/nginx.conf.j2`
- `files/nginx/fpgas.online.conf` (redirect config)
- `files/nginx/certbot.sh` (cert renewal script)

Fixpi role:
- `templates/boot/cmdline.txt.j2`, `boot/config.txt.j2`
- `templates/etc/fstab.j2`
- `templates/resolve.conf.j2`

Cam roles:
- `cam/stream-server/templates/nginx-rtmp.conf.j2`
- `cam/stream-server/templates/live-hls.conf.j2`
- `cam/stream-server/templates/pib.conf.j2`

Wssh role (all stays in infra — no separate repo needed):
- `files/etc/nginx/includes/wssh.conf`
- `files/etc/systemd/system/gunicorn.service`
- `files/etc/systemd/system/gunicorn.socket`
- `files/etc/systemd/system/wssh.socket`
- `templates/wssh.service.j2`

**What changes in roles after the split**:
- `site` role: removes `files/pib/` entirely, does
  `pip install fpgas-online-site fpgas-online-poe[cli]` then
  `manage.py migrate`, `collectstatic`, service restarts. Per-app nginx confs
  are shipped inside the pip packages as data files; the role symlinks them
  into `/etc/nginx/includes/`.
- `fixpi` role: generic Pi configs move to setup-pi deb. Server-side scripts
  (`maintenance.sh`, `production.sh`, `mktftpln.sh`, `pipw.sh`,
  `chroot-mount-pi-fs.bash`) move to netboot-pi. Role becomes
  `apt install fpgas-online-setup-pi` + Jinja2-templated boot/network configs.
- `onpi` role: all `files/` content moves to setup-pi deb (zsh/tmux configs,
  FPGA board detection services, pistat services). Role becomes
  `apt install fpgas-online-setup-pi` + host-specific configuration.
- `cam` roles: scripts/service files move to cam deb, roles become
  `apt install fpgas-online-cam` + Jinja2-templated nginx configs.

### 2. fpgas.online-site

**Purpose**: The Django web application for fpgas.online.

**Source content from pici**:
- `ansible/roles/site/files/pib/manage.py`
- `ansible/roles/site/files/pib/pib/` (settings, urls, asgi)
- `ansible/roles/site/files/pib/pibfpgas/` (FPGA board management, demos)
- `ansible/roles/site/files/pib/pistat/` (real-time Pi status via WebSocket)
- `ansible/roles/site/files/pib/pibdemos/` (demo management)
- `ansible/roles/site/files/pib/pibup/` (file upload)
- `ansible/roles/site/files/pib/requirements.txt`
- `ansible/roles/site/files/pib/__init__.py`
- HTML templates from `ansible/roles/site/files/pib/*/templates/`
- Static files (JS, CSS) from `ansible/roles/site/files/pib/*/static/`
- Per-app nginx location confs from `ansible/roles/site/files/pib/*/nginx/*.conf`
  (pibdemos, pibfpgas, pistat, pibup — shipped as package data files)
- `ansible/roles/site/files/js/` (videojs etc.)

**Does NOT include**:
- `snmp_switch/` (moves to fpgas.online-poe, but its `nginx/snmp_switch.conf`
  ships with the poe package)
- `pistat/scripts/` (Pi-side status reporting scripts — move to
  fpgas.online-setup-pi, see below)
- `pistat/dnsmasq/send_stat.conf` (moves to fpgas.online-infra, deployed by
  pxe or site role to configure dnsmasq for stat collection)

**Packaging structure**: Each Django sub-app already has its own
`pyproject.toml` with `src/` layout. The site repo will use a single top-level
`pyproject.toml` that installs all sub-apps as a unified package (using
`packages = find:` or explicit package listing). The existing per-app
`pyproject.toml` files are removed — they were used for individual pip
installs from the monorepo, which is no longer needed. Dependencies include
`channels[daphne]`, `fpgas-online-poe` (as a dependency).

**Build**: GitHub Actions on push to main → build wheel → GitHub release.

**Django apps inside this package**:
- `pibfpgas` — FPGA board listing, demo execution, models (Board, etc.)
- `pistat` — Real-time status via WebSocket consumers, ping, serial/netconsole forwarding
- `pibdemos` — Demo management and utilities
- `pibup` — Bitstream/file upload to Pi boards

**Key technical notes**:
- Uses Django Channels with Daphne for WebSocket (pistat consumers)
- ASGI application with `ProtocolTypeRouter` for HTTP + WebSocket
- Currently deployed at `/srv/www/pib/` on tweed with gunicorn + daphne + uvicorn
- `pibfpgas/Demos/` contains binary FPGA bitstreams and Linux images
  (top.bit, emulator.bin, rootfs.cpio, etc.). These should NOT be shipped in
  the pip wheel — they should either be hosted as GitHub release assets and
  downloaded at deploy time, or moved to fpgas.online-test-designs. The infra
  role handles placing them at deploy time.
- `pibfpgas/fixtures/` contains Django fixtures for different deployments
  (`fpgas.online.json`, `ps1.fpgas.online.json`). These are
  environment-specific data and ship with the package. The infra role selects
  which fixture to load.

**Pi-side scripts** (move to fpgas.online-setup-pi):
- `pistat/scripts/send.py` — base/shared sender
- `pistat/scripts/send_serial.py` — serial data reporter
- `pistat/scripts/send_stat.py` — status reporter
- `pistat/scripts/send_ncc.py` — netconsole reporter
- `pistat/scripts/ncc.py` — netconsole client

These run on the Pi (not the server) and report status back to the pistat
WebSocket. They belong in fpgas.online-setup-pi as part of the deb package,
installed alongside the pistat systemd services that invoke them.

### 3. fpgas.online-poe

**Purpose**: SNMP PoE switch management — Python library and CLI tool.

**Source content from pici**:
- `ansible/roles/site/files/pib/snmp_switch/`

**Packaging**: pip package with `[cli]` extra (already structured this way).
`fpgas-online-poe` for the library, `fpgas-online-poe[cli]` for the CLI.

**Build**: GitHub Actions → wheel → GitHub release.

**Notes**: Currently also a Django app (views, urls) for the web UI showing
switch status. The Django views could either stay here (and the site repo
depends on this as a Django app) or move to the site repo (leaving this as
pure library + CLI). Recommend keeping the Django views here since
`snmp_switch` is already self-contained with its own `urls.py`, `views.py`,
and `utils.py`.

### 4. fpgas.online-cam

**Purpose**: Camera capture and streaming infrastructure.

**Source content from pici**:
- `ansible/roles/cam/pi/files/cam.sh` — camera capture script
- `ansible/roles/cam/pi/files/cam.service` — systemd unit
- `ansible/roles/cam/pi/files/gst-libcam.sh` — GStreamer libcamera pipeline
- `ansible/roles/cam/pi/files/gst-libcam-yt.sh` — GStreamer YouTube pipeline
- `ansible/roles/cam/stream-server/templates/nginx-rtmp.conf.j2` — **stays in infra** (Jinja2)
- `ansible/roles/cam/stream-server/templates/live-hls.conf.j2` — **stays in infra** (Jinja2)
- `ansible/roles/cam/stream-server/templates/pib.conf.j2` — **stays in infra** (Jinja2)

**Packaging**: deb package containing:
- `/usr/local/bin/fpgas-cam.sh` (or similar)
- `/etc/systemd/system/fpgas-cam.service`
- GStreamer pipeline scripts
- Package depends on `gstreamer1.0-tools`, `libcamera-tools`, etc.

**Build**: GitHub Actions → `nfpm` or `dpkg-deb` → `.deb` → apt repo via GitHub Pages.

**What stays in infra**: The nginx-rtmp and HLS Jinja2 templates (they use
ansible variables for hostnames, paths, etc.). The `cam/stream-server` role
tasks that install nginx-rtmp and configure it. The `cam/pi` role tasks that
install the deb and enable the service.

### 5. fpgas.online-setup-pi

**Purpose**: Everything that configures a Pi as an fpgas.online node —
user environment, shell setup, status reporting services, network scripts.

**Source content from pici**:

From `ansible/roles/onpi/files/`:
- `tmux/zshrc`, `tmux/zprofile`, `tmux/tmux.conf` — shell environment
- `pistat_ssh.service`, `pistat_info.service`, `pistat_cam.service`,
  `pistat_shutdown.service` — systemd units for status reporting
- `arty_blink/arty_blink.sh`, `arty_blink/arty_blink.service` — FPGA LED blink
- `is_arty/arty_here.sh`, `is_arty/arty_here.exp`, `is_arty/arty_here.service`
  — Arty board detection
- `is_wire/arty_wire.sh`, `is_wire/arty_wire.service` — Arty wiring detection

From `ansible/roles/fixpi/files/`:
- `etc/profile.d/show_info.sh`, `showkernel.sh`, `showrelease.sh`, `showcpu.sh`
- `etc/keyboard` — US keyboard config
- `etc/ssh/sshd_config.d/password.conf` — SSH password config
- `etc/issue` — console login banner
- `etc/systemd/network/11-eth-uplink.link`, `12-eth-fpga.link` — USB-path-based
  network interface naming (generic for all Pis with same USB topology, NOT
  MAC-specific)

From `ansible/roles/site/files/pib/pistat/scripts/` (Pi-side status reporters):
- `send.py`, `send_serial.py`, `send_stat.py`, `send_ncc.py`, `ncc.py`

**Does NOT include** (stays in infra):
- `ansible/roles/fixpi/files/etc/network/interfaces.d/eth1.conf` — static IP
  config, may need to become a Jinja2 template for per-host IPs
- `ansible/roles/fixpi/files/scripts/*` — server-side operator tools (move to
  fpgas.online-netboot-pi)
- `ansible/roles/fixpi/files/pipw.sh` — Pi user password generator (move to
  fpgas.online-netboot-pi)
- `ansible/roles/fixpi/files/boot/ncc.py` — netconsole client (move to
  fpgas.online-tools)

**Packaging**: deb package containing:
- `/etc/profile.d/fpgas-*.sh` scripts
- `/etc/skel/` dotfiles (zshrc, tmux.conf, zprofile)
- `/usr/lib/systemd/system/` service units (pistat_*, arty_blink, arty_here,
  arty_wire)
- `/usr/local/bin/` — FPGA detection scripts, pistat sender scripts
- `/etc/systemd/network/` — interface naming .link files
- Conffiles for SSH, keyboard, console issue
- Package depends on `zsh`, `tmux`, `vim`, `expect` (for arty_here.exp), etc.

**Build**: GitHub Actions → deb → apt repo.

### 6. fpgas.online-netboot-pi

**Purpose**: Tools for preparing Pi netboot filesystems — extracting SD images,
setting up NFS roots, QEMU/chroot for ARM-on-x86.

**Source content from pici**:
- `tools/pinet/` — netboot config files (config.txt, cmdline.txt, fstab,
  chroot.conf, userconf.txt, nbpi.sh, nbpi.tj.sh, user-data)
- `tools/mkpisd/` — SD card scripts (fixit.sh, ddsd.sh)
- `tools/qemu/` — chroot-mount-devices.bash.sh
- `setup-nfs.sh` — NFS debug/setup
- `update_pi4/` — Pi 4 update (config.txt, mksd.sh, bootconf.txt, notes.txt)
- `ansible/roles/img/files/img2files.sh` — image extraction script
- `ansible/roles/fixpi/files/scripts/chroot-mount-pi-fs.bash` — chroot helper
- `ansible/roles/fixpi/files/scripts/maintenance.sh` — switch Pi to
  maintenance mode (writable NFS)
- `ansible/roles/fixpi/files/scripts/production.sh` — switch Pi to production
  mode (read-only NFS)
- `ansible/roles/fixpi/files/scripts/mktftpln.sh` — create TFTP symlinks for
  Pi serial numbers (needed because Pi v3 ignores DHCP boot file option)
- `ansible/roles/fixpi/files/pipw.sh` — generate random Pi user password and
  create userconf.txt

**Packaging**: Standalone scripts repo. Optionally a deb package later, but
these are primarily operator tools run on the server, not deployed to Pis.

**Build**: Minimal — linting, shellcheck in CI. No artifact packaging initially.

### 7. fpgas.online-tools

**Purpose**: Miscellaneous utility scripts and tools.

**Source content from pici**:
- `tools/dhcp/dhcp-vc.py` — DHCP vendor class tool
- `tools/dhcp/log_lookie.py` — DHCP log analyzer
- `tools/dhcp/dhcp-logger.py` — DHCP event logger
- `tools/notes.txt`
- `ansible/roles/fixpi/files/boot/ncc.py` — netconsole client (runs on Pi)

**Also potentially from tweed** (not in pici repo):
- `/usr/local/bin/netconsole-recv.py` — netconsole UDP receiver
- sensors2mqtt dashboard (currently just `/srv/www/sensors/index.html`)

**Packaging**: Standalone scripts repo. Individual tools may get packaged as
needed.

## Content not migrated

The following content from pici is **not moved to any new repo**:

| Path | Reason |
|------|--------|
| `archive/` | Superseded scripts (old setup, PoE, PXE configs). Preserved in original `carlfk/pici` as history. |
| `temp/` | Experimental scripts (overlayroot, test scripts). Not production code. |
| `ansible/roles/ci/` | Replaced by `fpgas-online/fpgas.online-test-designs`. |
| `doc/img/` | Single image file, can go in infra README if needed. |
| `.gitignore` | Each new repo gets its own `.gitignore` appropriate to its content. |
| `ansible/roles/fixpi/notes.txt` | Developer notes, not production content. |
| `ansible/roles/onpi/notes.txt` | Developer notes, not production content. |
| `ansible/roles/wssh/notes.txt` | Developer notes, not production content. |

## Apt Repository Setup

### Overview

A GitHub Pages-backed apt repository at `apt.fpgas.online` (or
`fpgas-online.github.io/apt`) hosting deb packages for:
- `fpgas-online-setup-pi` (armhf, arm64)
- `fpgas-online-cam` (armhf, arm64)
- Future packages as needed

### Structure

```
apt repo (GitHub Pages)
  dists/
    bookworm/
      main/
        binary-armhf/
          Packages
          Packages.gz
        binary-arm64/
          Packages
          Packages.gz
      Release
      Release.gpg
      InRelease
  pool/
    main/
      f/
        fpgas-online-setup-pi/
          fpgas-online-setup-pi_0.1.0_armhf.deb
        fpgas-online-cam/
          fpgas-online-cam_0.1.0_armhf.deb
```

### Signing

GPG key for signing the apt repo. Private key stored as a GitHub Actions secret
shared across repos that publish debs. Public key distributed via:
- The infra ansible role that configures apt sources
- Published at `apt.fpgas.online/pubkey.gpg`

### Build pipeline

Each deb-producing repo has a GitHub Actions workflow:
1. On push to main (or tag): build `.deb` using `nfpm` or `dpkg-deb`
2. Upload `.deb` to the apt repo (separate `fpgas-online/apt` repo)
3. Trigger apt repo rebuild: `apt-ftparchive` or `reprepro` regenerates
   `Packages`, `Release`, signs with GPG
4. GitHub Pages serves the updated repo

### Pi-side configuration

The infra ansible role adds the apt source:
```
deb [signed-by=/usr/share/keyrings/fpgas-online.gpg] https://apt.fpgas.online bookworm main
```

## pip Package Distribution

### fpgas.online-site and fpgas.online-poe

Both are pip-installable Python packages with `pyproject.toml`.

**Distribution options** (in order of preference):
1. **Install from GitHub release**: `pip install https://github.com/fpgas-online/fpgas.online-site/releases/download/v0.1.0/fpgas_online_site-0.1.0-py3-none-any.whl`
2. **Install from GitHub repo**: `pip install git+https://github.com/fpgas-online/fpgas.online-site.git`
3. **PyPI**: if public distribution is desired later

The infra `site` role pins versions in a requirements file.

## Existing Repos — Impact

| Repo | Impact |
|------|--------|
| `fpgas-online/fpgas.online-test-designs` | No change. Already independent. Replaces `ansible/roles/ci/`. |
| `fpgas-online/website` | Update repos table to list new repos. |
| `fpgas-online/todo` | Issues may be migrated to the appropriate new repo. |
| `fpgas-online/.github` | Update org profile README with new repo list. |
| `carlfk/pici` | Archive or mark as deprecated with pointers to new repos. |

## Migration Strategy

### Phase 1: Create repos and extract content
1. Create all 7 new GitHub repos under fpgas-online
2. Extract content from pici into each repo (preserving git history where
   practical via `git filter-repo`)
3. Set up basic README, LICENSE (Apache 2.0), and CI for each

### Phase 2: Packaging pipelines
1. Add `pyproject.toml` to site and poe repos, verify pip install works
2. Set up `fpgas-online/apt` repo with GitHub Pages
3. Add deb build workflows to cam and setup-pi repos
4. Verify packages install correctly on a test Pi

### Phase 3: Refactor infra roles
1. Update `site` role to pip install from new repos
2. Update `fixpi`/`onpi` roles to apt install from new repos
3. Update `cam` roles similarly
4. Test full ansible playbook run against tweed
5. Test full ansible playbook run against a Pi

### Phase 4: Cleanup
1. Update `.github` org profile and `website` repo
2. Migrate relevant todo issues to appropriate repos
3. Archive `carlfk/pici` with deprecation notice pointing to new repos
4. Update any external references (wiki, docs)

## Resolved Questions

1. **pistat scripts on Pi**: Resolved — move to `fpgas.online-setup-pi`.
   `send.py`, `send_serial.py`, `send_stat.py`, `send_ncc.py`, and `ncc.py`
   all run on the Pi and are installed as part of the setup-pi deb alongside
   the pistat systemd services that invoke them.

2. **wssh**: Resolved — no separate repo needed. The wssh role has 4 static
   config files and 1 Jinja2 template, all small. Keep entirely in infra.

3. **Version numbering**: Resolved — start at 0.1.0. Reserve 0.1.0 for after
   the first successful end-to-end deployment from the split repos.

## Open Questions

1. **sensors2mqtt**: The sensors dashboard and mosquitto MQTT integration on
   tweed are not in the pici repo. Should this be tracked/added to one of
   the new repos?

2. **Git history preservation**: Should we use `git filter-repo` to preserve
   commit history for each extracted subtree, or start fresh repos?

3. **Demo bitstreams**: The `pibfpgas/Demos/` directory contains binary FPGA
   bitstreams and Linux images. Should these move to
   `fpgas.online-test-designs`, be hosted as GitHub release assets, or handled
   some other way?
