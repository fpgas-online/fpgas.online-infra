# pici Repo Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `carlfk/pici` into 7 purpose-specific repos under `fpgas-online`, with artifact-based packaging (pip/deb) and a GitHub Pages-backed apt repository.

**Architecture:** Each source repo produces installable artifacts. The infra repo consumes them declaratively via `pip install` and `apt install`. No submodules. Git history preserved via `git filter-repo`.

**Tech Stack:** Git, `git-filter-repo`, GitHub CLI (`gh`), Python packaging (`pyproject.toml`, `build`), Debian packaging (`nfpm`), GitHub Actions, GitHub Pages, GPG, Ansible.

**Spec:** `docs/specs/2026-04-03-pici-repo-split-design.md`

---

## Phase 0: Prerequisites

### Task 0: Install tools and verify access

**Files:** None (environment setup)

- [ ] **Step 1: Install git-filter-repo**

```bash
uv pip install git-filter-repo
```

- [ ] **Step 2: Install nfpm (for deb packaging)**

```bash
# Check if nfpm is available
which nfpm || echo "Install from https://nfpm.goreleaser.com/install/"
```

If not installed:
```bash
go install github.com/goreleaser/nfpm/v2/cmd/nfpm@latest
```

Or via the GitHub release binary for Linux amd64.

- [ ] **Step 3: Verify GitHub org access**

```bash
gh repo list fpgas-online --limit 20
gh api orgs/fpgas-online/memberships/$( gh api user --jq .login ) --jq .role
```

Expected: lists existing repos, shows your role (admin or member).

- [ ] **Step 4: Generate GPG key for apt repo signing**

```bash
gpg --batch --gen-key <<GPGEOF
  Key-Type: RSA
  Key-Length: 4096
  Name-Real: fpgas-online apt repo
  Name-Email: apt@fpgas.online
  Expire-Date: 0
  %no-protection
GPGEOF
```

Export the public key:
```bash
gpg --armor --export apt@fpgas.online > /tmp/fpgas-online-apt.gpg.asc
```

Note the key ID for later:
```bash
gpg --list-keys apt@fpgas.online --keyid-format long
```

- [ ] **Step 5: Commit**

No files to commit — this is environment setup.

---

## Phase 1: Create Repos and Extract Content

Each task creates one repo using `git filter-repo` to preserve history, then
restructures the content for its new purpose. All repos use Apache 2.0 license.

**Important**: Every `git filter-repo` operation must be done on a **fresh
clone** of pici. `git filter-repo` is destructive — it rewrites the entire
repo. Never run it on your working copy.

**Critical**: `git filter-repo` can only be run **once** per clone (subsequent
calls further filter what remains, losing data). Use it once to extract the
relevant paths, then use regular `git mv` + `git commit` for restructuring.

### Task 1: Create fpgas.online-infra

This is the largest extraction — it keeps the ansible directory structure but
removes content that moves to other repos.

**Files:**
- Create: `fpgas-online/fpgas.online-infra` (new GitHub repo)

- [ ] **Step 1: Create the GitHub repo**

```bash
gh repo create fpgas-online/fpgas.online-infra \
  --public \
  --description "Ansible infrastructure for fpgas.online" \
  --license apache-2.0
```

- [ ] **Step 2: Clone a fresh copy of pici for filtering**

```bash
git clone ~/github/pici /tmp/pici-for-infra
cd /tmp/pici-for-infra
```

- [ ] **Step 3: Filter to keep only ansible/ and root docs**

```bash
git filter-repo \
  --path ansible/ \
  --path README.md \
  --path TECHDEBT.md \
  --path CONTRIBUTORS.txt \
  --path notes.txt
```

- [ ] **Step 4: Remove content that belongs in other repos**

`--invert-paths` is a global flag (inverts all --path args), so we cannot
combine include and exclude in one call. Use `git rm` instead:

```bash
git rm -rf \
  ansible/roles/site/files/pib/ \
  ansible/roles/site/files/js/ \
  ansible/roles/ci/ \
  ansible/roles/fixpi/files/scripts/ \
  ansible/roles/fixpi/files/pipw.sh \
  ansible/roles/fixpi/files/boot/ncc.py \
  ansible/roles/fixpi/files/etc/profile.d/ \
  ansible/roles/fixpi/files/etc/keyboard \
  ansible/roles/fixpi/files/etc/ssh/ \
  ansible/roles/fixpi/files/etc/issue \
  ansible/roles/fixpi/files/etc/systemd/network/ \
  ansible/roles/onpi/files/ \
  ansible/roles/cam/pi/files/ \
  ansible/roles/img/files/img2files.sh
git commit -m "Remove content moving to other repos"
```

Note: `ansible/roles/fixpi/files/etc/network/interfaces.d/eth1.conf` stays in
infra (it contains a host-specific IP that needs templating).

Note: `pistat/dnsmasq/send_stat.conf` was inside `files/pib/` and was removed
above. It needs to be manually added back — see Step 5.

- [ ] **Step 5: Add pistat dnsmasq config back to infra**

This file was excluded with `files/pib/` but belongs in infra per the spec:

```bash
cd /tmp/pici-for-infra
# Copy from the original pici repo
cp ~/github/pici/ansible/roles/site/files/pib/pistat/dnsmasq/send_stat.conf \
   ansible/roles/pxe/files/send_stat.conf
git add ansible/roles/pxe/files/send_stat.conf
git commit -m "Add pistat dnsmasq send_stat.conf (from pici site role)"
```

- [ ] **Step 6: Push to the new repo**

```bash
git remote add origin git@github.com:fpgas-online/fpgas.online-infra.git
git push -u origin main
```

- [ ] **Step 7: Verify the result**

```bash
cd /tmp/pici-for-infra
# Should exist:
ls ansible/site.yml
ls ansible/inventory/hosts
ls ansible/roles/site/tasks/main.yml
ls ansible/roles/site/templates/daphne.service.j2
ls ansible/roles/fixpi/tasks/main.yml
ls ansible/roles/fixpi/templates/boot/cmdline.txt.j2
ls ansible/roles/cam/stream-server/templates/nginx-rtmp.conf.j2
ls ansible/roles/wssh/
ls ansible/roles/site/files/nginx/certbot.sh

# Should NOT exist:
test ! -d ansible/roles/site/files/pib && echo "OK: pib removed"
test ! -d ansible/roles/ci && echo "OK: ci removed"
test ! -d ansible/roles/onpi/files && echo "OK: onpi files removed"
```

- [ ] **Step 8: Clean up**

```bash
rm -rf /tmp/pici-for-infra
```

### Task 2: Create fpgas.online-site

Extract the Django web app from `ansible/roles/site/files/pib/` and
restructure it as a standalone pip-installable project.

**Files:**
- Create: `fpgas-online/fpgas.online-site` (new GitHub repo)

- [ ] **Step 1: Create the GitHub repo**

```bash
gh repo create fpgas-online/fpgas.online-site \
  --public \
  --description "Django web application for fpgas.online" \
  --license apache-2.0
```

- [ ] **Step 2: Clone and filter (single git filter-repo call)**

```bash
git clone ~/github/pici /tmp/pici-for-site
cd /tmp/pici-for-site
git filter-repo \
  --path ansible/roles/site/files/pib/ \
  --path ansible/roles/site/files/js/ \
  --path-rename ansible/roles/site/files/pib/: \
  --path-rename ansible/roles/site/files/js/:static/js/
```

This extracts the pib/ and js/ subtrees AND renames them to the repo root
in a single operation.

- [ ] **Step 3: Remove content that belongs in other repos**

Use regular git operations (not filter-repo) to remove snmp_switch (goes to
poe repo), pistat Pi-side scripts (go to setup-pi), and pistat dnsmasq
config (goes to infra):

```bash
git rm -rf snmp_switch/
git rm -rf pistat/scripts/
git rm -rf pistat/dnsmasq/
git commit -m "Remove content moving to other repos (poe, setup-pi, infra)"
```

- [ ] **Step 4: Remove per-app pyproject.toml files (replaced by top-level one)**

```bash
git rm -f pibdemos/pyproject.toml pibfpgas/pyproject.toml \
         pistat/pyproject.toml pibup/pyproject.toml 2>/dev/null
git commit -m "Remove per-app pyproject.toml files (replaced by top-level)"
```

- [ ] **Step 5: Create top-level pyproject.toml**

Each Django sub-app uses a `src/` layout (e.g., `pibfpgas/src/pibfpgas/`).
The pyproject.toml must account for this:

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "setuptools-scm"]
build-backend = "setuptools.build_meta"

[project]
name = "fpgas-online-site"
version = "0.1.0"
description = "Django web application for fpgas.online"
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.11"
dependencies = [
    "django>=4.2",
    "channels[daphne]>=4.0",
    "fpgas-online-poe",
]

[tool.setuptools.packages.find]
where = [".", "pibfpgas/src", "pistat/src", "pibdemos/src", "pibup/src"]
include = ["pib*", "pistat*"]

[tool.setuptools.package-data]
"*" = ["templates/**/*", "static/**/*", "nginx/*.conf", "fixtures/*.json"]
```

- [ ] **Step 7: Create .gitignore**

```gitignore
__pycache__/
*.pyc
*.egg-info/
dist/
build/
*.sqlite3
.venv/
```

- [ ] **Step 8: Verify pip install works**

```bash
cd /tmp/pici-for-site
uv venv .venv
source .venv/bin/activate
uv pip install -e .
python -c "import pibfpgas; print('pibfpgas OK')"
python -c "import pistat; print('pistat OK')"
python -c "import pibdemos; print('pibdemos OK')"
python -c "import pibup; print('pibup OK')"
deactivate
```

- [ ] **Step 9: Push**

```bash
git add pyproject.toml .gitignore
git commit -m "Add top-level pyproject.toml for unified pip package"
git remote add origin git@github.com:fpgas-online/fpgas.online-site.git
git push -u origin main
```

- [ ] **Step 10: Clean up**

```bash
rm -rf /tmp/pici-for-site
```

### Task 3: Create fpgas.online-poe

Extract the SNMP switch management from the Django app.

**Files:**
- Create: `fpgas-online/fpgas.online-poe` (new GitHub repo)

- [ ] **Step 1: Create the GitHub repo**

```bash
gh repo create fpgas-online/fpgas.online-poe \
  --public \
  --description "SNMP PoE switch management for fpgas.online" \
  --license apache-2.0
```

- [ ] **Step 2: Clone and filter (single call with path-rename)**

```bash
git clone ~/github/pici /tmp/pici-for-poe
cd /tmp/pici-for-poe
git filter-repo \
  --path ansible/roles/site/files/pib/snmp_switch/ \
  --path-rename ansible/roles/site/files/pib/snmp_switch/:
```

- [ ] **Step 3: Verify existing pyproject.toml**

The `snmp_switch` already has a `pyproject.toml`. Check it and update the
package name:

```bash
cat pyproject.toml
```

Update the `name` field to `fpgas-online-poe` and ensure version is `0.1.0`.

- [ ] **Step 5: Create .gitignore**

```gitignore
__pycache__/
*.pyc
*.egg-info/
dist/
build/
.venv/
```

- [ ] **Step 6: Verify pip install works**

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[cli]"
python -c "import snmp_switch; print('OK')"
deactivate
```

- [ ] **Step 7: Push**

```bash
git add -A
git commit -m "Rename package to fpgas-online-poe, set version 0.1.0"
git remote add origin git@github.com:fpgas-online/fpgas.online-poe.git
git push -u origin main
```

- [ ] **Step 8: Clean up**

```bash
rm -rf /tmp/pici-for-poe
```

### Task 4: Create fpgas.online-cam

Extract camera capture scripts and GStreamer pipeline configs.

**Files:**
- Create: `fpgas-online/fpgas.online-cam` (new GitHub repo)

- [ ] **Step 1: Create the GitHub repo**

```bash
gh repo create fpgas-online/fpgas.online-cam \
  --public \
  --description "Camera capture and streaming for fpgas.online Raspberry Pi boards" \
  --license apache-2.0
```

- [ ] **Step 2: Clone and filter (single call with path-rename)**

```bash
git clone ~/github/pici /tmp/pici-for-cam
cd /tmp/pici-for-cam
git filter-repo \
  --path ansible/roles/cam/pi/files/ \
  --path-rename ansible/roles/cam/pi/files/:
```

- [ ] **Step 3: Create nfpm.yaml for deb packaging**

```yaml
name: fpgas-online-cam
arch: armhf
platform: linux
version: 0.1.0
maintainer: fpgas.online <apt@fpgas.online>
description: Camera capture and GStreamer streaming for fpgas.online Pi boards
license: Apache-2.0

depends:
  - gstreamer1.0-tools
  - gstreamer1.0-plugins-base
  - gstreamer1.0-plugins-good
  - libcamera-tools

contents:
  - src: cam.sh
    dst: /usr/local/bin/fpgas-cam.sh
    file_info:
      mode: 0755
  - src: gst-libcam.sh
    dst: /usr/local/bin/fpgas-gst-libcam.sh
    file_info:
      mode: 0755
  - src: gst-libcam-yt.sh
    dst: /usr/local/bin/fpgas-gst-libcam-yt.sh
    file_info:
      mode: 0755
  - src: cam.service
    dst: /usr/lib/systemd/system/fpgas-cam.service

scripts:
  postinstall: |
    systemctl daemon-reload
```

- [ ] **Step 5: Create .gitignore and README**

```gitignore
*.deb
dist/
```

- [ ] **Step 6: Test deb build (on an appropriate system)**

```bash
nfpm package --packager deb --target .
ls *.deb
dpkg-deb --info fpgas-online-cam_0.1.0_armhf.deb
```

- [ ] **Step 7: Push**

```bash
git add nfpm.yaml .gitignore
git commit -m "Add nfpm config for deb packaging"
git remote add origin git@github.com:fpgas-online/fpgas.online-cam.git
git push -u origin main
```

- [ ] **Step 8: Clean up**

```bash
rm -rf /tmp/pici-for-cam
```

### Task 5: Create fpgas.online-setup-pi

Extract Pi environment configuration from onpi + fixpi + pistat scripts.

**Files:**
- Create: `fpgas-online/fpgas.online-setup-pi` (new GitHub repo)

- [ ] **Step 1: Create the GitHub repo**

```bash
gh repo create fpgas-online/fpgas.online-setup-pi \
  --public \
  --description "Raspberry Pi environment setup for fpgas.online nodes" \
  --license apache-2.0
```

- [ ] **Step 2: Clone and filter (single call with path-rename)**

This repo draws from three locations in pici. Extract and rename in one call:

```bash
git clone ~/github/pici /tmp/pici-for-setup-pi
cd /tmp/pici-for-setup-pi
git filter-repo \
  --path ansible/roles/onpi/files/ \
  --path ansible/roles/fixpi/files/etc/profile.d/ \
  --path ansible/roles/fixpi/files/etc/keyboard \
  --path ansible/roles/fixpi/files/etc/ssh/ \
  --path ansible/roles/fixpi/files/etc/issue \
  --path ansible/roles/fixpi/files/etc/systemd/network/ \
  --path ansible/roles/site/files/pib/pistat/scripts/ \
  --path-rename ansible/roles/onpi/files/:onpi/ \
  --path-rename ansible/roles/fixpi/files/:fixpi/ \
  --path-rename ansible/roles/site/files/pib/pistat/scripts/:pistat-scripts/
```

- [ ] **Step 3: Create nfpm.yaml**

```yaml
name: fpgas-online-setup-pi
arch: armhf
platform: linux
version: 0.1.0
maintainer: fpgas.online <apt@fpgas.online>
description: Pi environment setup for fpgas.online FPGA test nodes
license: Apache-2.0

depends:
  - zsh
  - tmux
  - vim
  - expect
  - python3

contents:
  # Shell environment
  - src: onpi/tmux/zshrc
    dst: /etc/skel/.zshrc
  - src: onpi/tmux/zprofile
    dst: /etc/skel/.zprofile
  - src: onpi/tmux/tmux.conf
    dst: /etc/skel/.tmux.conf

  # Profile scripts
  - src: fixpi/etc/profile.d/show_info.sh
    dst: /etc/profile.d/fpgas-show-info.sh
    file_info:
      mode: 0755
  - src: fixpi/etc/profile.d/showkernel.sh
    dst: /etc/profile.d/fpgas-showkernel.sh
    file_info:
      mode: 0755
  - src: fixpi/etc/profile.d/showrelease.sh
    dst: /etc/profile.d/fpgas-showrelease.sh
    file_info:
      mode: 0755
  - src: fixpi/etc/profile.d/showcpu.sh
    dst: /etc/profile.d/fpgas-showcpu.sh
    file_info:
      mode: 0755

  # SSH config
  - src: fixpi/etc/ssh/sshd_config.d/password.conf
    dst: /etc/ssh/sshd_config.d/fpgas-password.conf

  # Keyboard
  - src: fixpi/etc/keyboard
    dst: /etc/default/keyboard
    type: config

  # Console banner
  - src: fixpi/etc/issue
    dst: /etc/issue
    type: config

  # Network interface naming (USB-path-based, generic for all fpgas Pis)
  - src: fixpi/etc/systemd/network/11-eth-uplink.link
    dst: /etc/systemd/network/11-eth-uplink.link
  - src: fixpi/etc/systemd/network/12-eth-fpga.link
    dst: /etc/systemd/network/12-eth-fpga.link

  # Systemd services — FPGA board detection
  - src: onpi/arty_blink/arty_blink.service
    dst: /usr/lib/systemd/system/fpgas-arty-blink.service
  - src: onpi/arty_blink/arty_blink.sh
    dst: /usr/local/bin/fpgas-arty-blink.sh
    file_info:
      mode: 0755
  - src: onpi/is_arty/arty_here.service
    dst: /usr/lib/systemd/system/fpgas-arty-here.service
  - src: onpi/is_arty/arty_here.sh
    dst: /usr/local/bin/fpgas-arty-here.sh
    file_info:
      mode: 0755
  - src: onpi/is_arty/arty_here.exp
    dst: /usr/local/bin/fpgas-arty-here.exp
    file_info:
      mode: 0755
  - src: onpi/is_wire/arty_wire.service
    dst: /usr/lib/systemd/system/fpgas-arty-wire.service
  - src: onpi/is_wire/arty_wire.sh
    dst: /usr/local/bin/fpgas-arty-wire.sh
    file_info:
      mode: 0755

  # Systemd services — pistat reporting
  - src: onpi/pistat_ssh.service
    dst: /usr/lib/systemd/system/fpgas-pistat-ssh.service
  - src: onpi/pistat_info.service
    dst: /usr/lib/systemd/system/fpgas-pistat-info.service
  - src: onpi/pistat_cam.service
    dst: /usr/lib/systemd/system/fpgas-pistat-cam.service
  - src: onpi/pistat_shutdown.service
    dst: /usr/lib/systemd/system/fpgas-pistat-shutdown.service

  # Pistat sender scripts
  - src: pistat-scripts/send.py
    dst: /usr/local/bin/fpgas-pistat-send.py
    file_info:
      mode: 0755
  - src: pistat-scripts/send_serial.py
    dst: /usr/local/bin/fpgas-pistat-send-serial.py
    file_info:
      mode: 0755
  - src: pistat-scripts/send_stat.py
    dst: /usr/local/bin/fpgas-pistat-send-stat.py
    file_info:
      mode: 0755
  - src: pistat-scripts/send_ncc.py
    dst: /usr/local/bin/fpgas-pistat-send-ncc.py
    file_info:
      mode: 0755
  - src: pistat-scripts/ncc.py
    dst: /usr/local/bin/fpgas-pistat-ncc.py
    file_info:
      mode: 0755

scripts:
  postinstall: |
    systemctl daemon-reload
```

- [ ] **Step 5: Create .gitignore**

```gitignore
*.deb
dist/
```

- [ ] **Step 6: Push**

```bash
git add nfpm.yaml .gitignore
git commit -m "Add nfpm config for deb packaging"
git remote add origin git@github.com:fpgas-online/fpgas.online-setup-pi.git
git push -u origin main
```

- [ ] **Step 7: Clean up**

```bash
rm -rf /tmp/pici-for-setup-pi
```

### Task 6: Create fpgas.online-netboot-pi

Extract netboot/NFS filesystem tools and server-side operator scripts.

**Files:**
- Create: `fpgas-online/fpgas.online-netboot-pi` (new GitHub repo)

- [ ] **Step 1: Create the GitHub repo**

```bash
gh repo create fpgas-online/fpgas.online-netboot-pi \
  --public \
  --description "Netboot filesystem preparation tools for fpgas.online Raspberry Pi boards" \
  --license apache-2.0
```

- [ ] **Step 2: Clone and filter (single call with path-rename)**

```bash
git clone ~/github/pici /tmp/pici-for-netboot
cd /tmp/pici-for-netboot
git filter-repo \
  --path tools/pinet/ \
  --path tools/mkpisd/ \
  --path tools/qemu/ \
  --path setup-nfs.sh \
  --path update_pi4/ \
  --path ansible/roles/img/files/img2files.sh \
  --path ansible/roles/fixpi/files/scripts/chroot-mount-pi-fs.bash \
  --path ansible/roles/fixpi/files/scripts/maintenance.sh \
  --path ansible/roles/fixpi/files/scripts/production.sh \
  --path ansible/roles/fixpi/files/scripts/mktftpln.sh \
  --path ansible/roles/fixpi/files/pipw.sh \
  --path-rename tools/pinet/:pinet/ \
  --path-rename tools/mkpisd/:mkpisd/ \
  --path-rename tools/qemu/:qemu/ \
  --path-rename update_pi4/:update-pi4/ \
  --path-rename ansible/roles/img/files/:img/ \
  --path-rename ansible/roles/fixpi/files/scripts/:scripts/ \
  --path-rename ansible/roles/fixpi/files/pipw.sh:scripts/pipw.sh
```

- [ ] **Step 3: Create .gitignore**

```gitignore
*.img
*.xz
```

- [ ] **Step 5: Push**

```bash
git add .gitignore
git commit -m "Add .gitignore"
git remote add origin git@github.com:fpgas-online/fpgas.online-netboot-pi.git
git push -u origin main
```

- [ ] **Step 6: Clean up**

```bash
rm -rf /tmp/pici-for-netboot
```

### Task 7: Create fpgas.online-tools

Extract miscellaneous utility scripts.

**Files:**
- Create: `fpgas-online/fpgas.online-tools` (new GitHub repo)

- [ ] **Step 1: Create the GitHub repo**

```bash
gh repo create fpgas-online/fpgas.online-tools \
  --public \
  --description "Utility scripts and tools for fpgas.online infrastructure" \
  --license apache-2.0
```

- [ ] **Step 2: Clone and filter (single call with path-rename)**

```bash
git clone ~/github/pici /tmp/pici-for-tools
cd /tmp/pici-for-tools
git filter-repo \
  --path tools/dhcp/ \
  --path tools/notes.txt \
  --path ansible/roles/fixpi/files/boot/ncc.py \
  --path-rename tools/dhcp/:dhcp/ \
  --path-rename tools/notes.txt:notes.txt \
  --path-rename ansible/roles/fixpi/files/boot/ncc.py:netconsole/ncc.py
```

- [ ] **Step 3: Push**

```bash
git remote add origin git@github.com:fpgas-online/fpgas.online-tools.git
git push -u origin main
```

- [ ] **Step 5: Clean up**

```bash
rm -rf /tmp/pici-for-tools
```

### Task 8: Phase 1 verification

Verify all 7 repos exist and have the expected content.

- [ ] **Step 1: List all repos**

```bash
gh repo list fpgas-online --limit 20
```

Expected: 12 repos total (5 existing + 7 new).

- [ ] **Step 2: Clone and spot-check each repo**

```bash
for repo in fpgas.online-infra fpgas.online-site fpgas.online-poe \
            fpgas.online-cam fpgas.online-setup-pi fpgas.online-netboot-pi \
            fpgas.online-tools; do
  echo "=== $repo ==="
  gh repo view "fpgas-online/$repo" --json name,description --jq '.name + ": " + .description'
  echo ""
done
```

- [ ] **Step 3: Verify no orphaned files**

Run this check against the original pici to make sure every file has a home:

Write a Python script `verify_split.py` that:
1. Lists all non-git files in `~/github/pici`
2. Checks each against the spec's file-to-repo mapping
3. Reports any files with no assignment
4. Ignores files in the "Content not migrated" table

Run it and verify the output shows no unassigned files.

- [ ] **Step 4: Commit verification script**

Commit `verify_split.py` to the fpgas-online workspace (not to any repo).

---

## Phase 2: Packaging Pipelines

### Task 9: Set up GitHub Pages apt repository

Create the `fpgas-online/apt` repo that serves as the deb package repository.

**Files:**
- Create: `fpgas-online/apt` (new GitHub repo)

- [ ] **Step 1: Create the repo**

```bash
gh repo create fpgas-online/apt \
  --public \
  --description "APT package repository for fpgas.online (served via GitHub Pages)"
```

- [ ] **Step 2: Clone and set up structure**

```bash
git clone git@github.com:fpgas-online/apt.git /tmp/fpgas-apt
cd /tmp/fpgas-apt
mkdir -p dists/bookworm/main/binary-armhf
mkdir -p dists/bookworm/main/binary-arm64
mkdir -p pool/main
```

- [ ] **Step 3: Add the GPG public key**

```bash
cp /tmp/fpgas-online-apt.gpg.asc pubkey.gpg
```

- [ ] **Step 4: Create the update script**

Create `update-repo.sh`:

```bash
#!/bin/bash
set -euo pipefail

# Generate Packages files for each architecture
for arch in armhf arm64; do
  apt-ftparchive packages pool/main > dists/bookworm/main/binary-${arch}/Packages
  gzip -k -f dists/bookworm/main/binary-${arch}/Packages
done

# Generate Release file
apt-ftparchive release \
  -o APT::FTPArchive::Release::Origin="fpgas-online" \
  -o APT::FTPArchive::Release::Label="fpgas.online" \
  -o APT::FTPArchive::Release::Suite="bookworm" \
  -o APT::FTPArchive::Release::Codename="bookworm" \
  -o APT::FTPArchive::Release::Architectures="armhf arm64" \
  -o APT::FTPArchive::Release::Components="main" \
  dists/bookworm > dists/bookworm/Release

# Sign
gpg --default-key apt@fpgas.online -abs -o dists/bookworm/Release.gpg dists/bookworm/Release
gpg --default-key apt@fpgas.online --clearsign -o dists/bookworm/InRelease dists/bookworm/Release
```

- [ ] **Step 5: Create GitHub Actions workflow for receiving debs**

Create `.github/workflows/receive-deb.yml`:

```yaml
name: Receive and publish deb package

on:
  repository_dispatch:
    types: [receive-deb]
  workflow_dispatch:
    inputs:
      package_name:
        description: "Package name"
        required: true
      package_version:
        description: "Package version"
        required: true
      run_id:
        description: "Run ID of the build workflow that produced the deb"
        required: true
      source_repo:
        description: "Source repo (e.g., fpgas-online/fpgas.online-cam)"
        required: true

permissions:
  contents: write
  pages: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set parameters from dispatch type
        id: params
        run: |
          if [ "${{ github.event_name }}" = "repository_dispatch" ]; then
            echo "source_repo=${{ github.event.client_payload.source_repo }}" >> "$GITHUB_OUTPUT"
            echo "run_id=${{ github.event.client_payload.run_id }}" >> "$GITHUB_OUTPUT"
            echo "package_name=${{ github.event.client_payload.package_name }}" >> "$GITHUB_OUTPUT"
            echo "package_version=${{ github.event.client_payload.package_version }}" >> "$GITHUB_OUTPUT"
          else
            echo "source_repo=${{ inputs.source_repo }}" >> "$GITHUB_OUTPUT"
            echo "run_id=${{ inputs.run_id }}" >> "$GITHUB_OUTPUT"
            echo "package_name=${{ inputs.package_name }}" >> "$GITHUB_OUTPUT"
            echo "package_version=${{ inputs.package_version }}" >> "$GITHUB_OUTPUT"
          fi

      - name: Download deb artifact
        uses: actions/download-artifact@v4
        with:
          name: deb-package
          github-token: ${{ secrets.GITHUB_TOKEN }}
          repository: ${{ steps.params.outputs.source_repo }}
          run-id: ${{ steps.params.outputs.run_id }}
          path: incoming/

      - name: Move deb to pool
        run: |
          mkdir -p pool/main
          mv incoming/*.deb pool/main/

      - name: Install apt-ftparchive
        run: sudo apt-get install -y apt-utils

      - name: Import GPG key
        run: echo "${{ secrets.APT_GPG_PRIVATE_KEY }}" | gpg --import

      - name: Update repository metadata
        run: bash update-repo.sh

      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add -A
          git commit -m "Add ${{ steps.params.outputs.package_name }} ${{ steps.params.outputs.package_version }}"
          git push

      - name: Deploy to GitHub Pages
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: .
          publish_branch: gh-pages
```

- [ ] **Step 6: Enable GitHub Pages**

```bash
gh api repos/fpgas-online/apt/pages \
  --method POST \
  --field source='{"branch":"gh-pages","path":"/"}' 2>&1 || echo "Pages may need manual setup"
```

- [ ] **Step 7: Store GPG private key as org secret**

```bash
gpg --armor --export-secret-keys apt@fpgas.online | \
  gh secret set APT_GPG_PRIVATE_KEY --repo fpgas-online/apt
```

- [ ] **Step 8: Push**

```bash
cd /tmp/fpgas-apt
git add -A
git commit -m "Initial apt repository structure with update script and CI"
git push -u origin main
```

- [ ] **Step 9: Clean up**

```bash
rm -rf /tmp/fpgas-apt
```

### Task 10: Add pip packaging CI to fpgas.online-site

**Files:**
- Modify: `fpgas-online/fpgas.online-site/.github/workflows/build.yml` (create)

- [ ] **Step 1: Clone the site repo**

```bash
git clone git@github.com:fpgas-online/fpgas.online-site.git /tmp/fpgas-site-ci
cd /tmp/fpgas-site-ci
```

- [ ] **Step 2: Create build workflow**

Create `.github/workflows/build.yml`:

```yaml
name: Build and release pip package

on:
  push:
    tags: ["v*"]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install build tools
        run: pip install build

      - name: Build wheel
        run: python -m build

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: pip-package
          path: dist/

  release:
    needs: build
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: pip-package
          path: dist/

      - name: Create GitHub release
        uses: softprops/action-gh-release@v2
        with:
          files: dist/*
```

- [ ] **Step 3: Push**

```bash
git add .github/workflows/build.yml
git commit -m "Add GitHub Actions workflow for pip package builds"
git push origin main
```

- [ ] **Step 4: Clean up**

```bash
rm -rf /tmp/fpgas-site-ci
```

### Task 11: Add pip packaging CI to fpgas.online-poe

Same pattern as Task 10 but for the poe repo.

**Files:**
- Modify: `fpgas-online/fpgas.online-poe/.github/workflows/build.yml` (create)

- [ ] **Step 1: Clone, create workflow, push**

```bash
git clone git@github.com:fpgas-online/fpgas.online-poe.git /tmp/fpgas-poe-ci
cd /tmp/fpgas-poe-ci
mkdir -p .github/workflows
```

Create `.github/workflows/build.yml` (identical to Task 10's workflow).

```bash
git add .github/workflows/build.yml
git commit -m "Add GitHub Actions workflow for pip package builds"
git push origin main
```

- [ ] **Step 2: Clean up**

```bash
rm -rf /tmp/fpgas-poe-ci
```

### Task 12: Add deb packaging CI to fpgas.online-cam

**Files:**
- Modify: `fpgas-online/fpgas.online-cam/.github/workflows/build-deb.yml` (create)

- [ ] **Step 1: Clone the cam repo**

```bash
git clone git@github.com:fpgas-online/fpgas.online-cam.git /tmp/fpgas-cam-ci
cd /tmp/fpgas-cam-ci
```

- [ ] **Step 2: Create deb build workflow**

Create `.github/workflows/build-deb.yml`:

```yaml
name: Build deb package

on:
  push:
    tags: ["v*"]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install nfpm
        run: |
          curl -sfL https://install.goreleaser.com/github.com/goreleaser/nfpm.sh | sh -s -- -b /usr/local/bin

      - name: Build deb (armhf)
        run: |
          NFPM_ARCH=armhf nfpm package --packager deb --target dist/

      - name: Upload deb artifact
        uses: actions/upload-artifact@v4
        with:
          name: deb-package
          path: dist/*.deb

  publish:
    needs: build
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    steps:
      - name: Trigger apt repo update
        uses: peter-evans/repository-dispatch@v3
        with:
          token: ${{ secrets.APT_REPO_TOKEN }}
          repository: fpgas-online/apt
          event-type: receive-deb
          client-payload: |
            {
              "package_name": "fpgas-online-cam",
              "package_version": "${{ github.ref_name }}",
              "run_id": "${{ github.run_id }}",
              "source_repo": "${{ github.repository }}"
            }
```

- [ ] **Step 3: Push**

```bash
git add .github/workflows/build-deb.yml
git commit -m "Add GitHub Actions workflow for deb package builds"
git push origin main
```

- [ ] **Step 4: Clean up**

```bash
rm -rf /tmp/fpgas-cam-ci
```

### Task 13: Add deb packaging CI to fpgas.online-setup-pi

Same pattern as Task 12 but for setup-pi. Use the same workflow structure
with `fpgas-online-setup-pi` as the package name.

**Files:**
- Modify: `fpgas-online/fpgas.online-setup-pi/.github/workflows/build-deb.yml` (create)

- [ ] **Step 1: Clone, create workflow, push**

```bash
git clone git@github.com:fpgas-online/fpgas.online-setup-pi.git /tmp/fpgas-setup-ci
cd /tmp/fpgas-setup-ci
mkdir -p .github/workflows
```

Create `.github/workflows/build-deb.yml` (same as Task 12 but with
`fpgas-online-setup-pi` as the package name).

```bash
git add .github/workflows/build-deb.yml
git commit -m "Add GitHub Actions workflow for deb package builds"
git push origin main
```

- [ ] **Step 2: Clean up**

```bash
rm -rf /tmp/fpgas-setup-ci
```

### Task 14: Phase 2 verification

- [ ] **Step 1: Trigger a test build of fpgas.online-site**

```bash
gh workflow run build.yml --repo fpgas-online/fpgas.online-site
gh run list --repo fpgas-online/fpgas.online-site --limit 1 --json status,conclusion
```

Expected: status "completed", conclusion "success".

- [ ] **Step 2: Trigger a test build of fpgas.online-poe**

```bash
gh workflow run build.yml --repo fpgas-online/fpgas.online-poe
gh run list --repo fpgas-online/fpgas.online-poe --limit 1 --json status,conclusion
```

- [ ] **Step 3: Trigger test builds of deb repos**

```bash
gh workflow run build-deb.yml --repo fpgas-online/fpgas.online-cam
gh workflow run build-deb.yml --repo fpgas-online/fpgas.online-setup-pi
```

- [ ] **Step 4: Verify apt repo is serving**

Once a deb package has been published:

```bash
curl -s https://fpgas-online.github.io/apt/dists/bookworm/Release | head -10
curl -s https://fpgas-online.github.io/apt/pubkey.gpg | head -5
```

Expected: Release file with Origin/Label/Suite, and the GPG public key.

---

## Phase 3: Refactor Infra Roles

### Task 15: Update site role to use pip packages

**Files:**
- Modify: `fpgas.online-infra/ansible/roles/site/tasks/install_django.yml`
- Modify: `fpgas.online-infra/ansible/roles/site/tasks/pibup.yml`
- Remove: `fpgas.online-infra/ansible/roles/site/files/pib/` (should already be gone from Phase 1)

- [ ] **Step 1: Clone the infra repo**

```bash
git clone git@github.com:fpgas-online/fpgas.online-infra.git /tmp/fpgas-infra-refactor
cd /tmp/fpgas-infra-refactor
```

- [ ] **Step 2: Update Django install tasks**

Replace the per-app pip installs with a single package install. In
`ansible/roles/site/tasks/install_django.yml`, replace the individual
`pip install ... @ git+https://github.com/CarlFK/pici.git#subdirectory=...`
lines with:

```yaml
- name: Install fpgas.online Django site
  ansible.builtin.pip:
    name:
      - "fpgas-online-site=={{ fpgas_online_site_version | default('0.1.0') }}"
      - "fpgas-online-poe[cli]=={{ fpgas_online_poe_version | default('0.1.0') }}"
    virtualenv: "{{ django_dir }}/venv"
    state: present
  tags:
    - django
```

- [ ] **Step 3: Update pibup task**

In `ansible/roles/site/tasks/pibup.yml`, remove the separate pibup pip
install (it's now part of the fpgas-online-site package). Update the nginx
conf copy to reference the pip package's data files:

```yaml
- name: Install per-app nginx confs from pip packages
  ansible.builtin.shell: |
    for conf in {{ django_dir }}/venv/lib/python3.*/site-packages/*/nginx/*.conf; do
      cp "$conf" /etc/nginx/includes/{{ conference_name }}-$(basename "$conf")
    done
  tags:
    - django
    - nginx
```

- [ ] **Step 4: Add version variables to group_vars**

Add to `ansible/inventory/group_vars/all/site.yml`:

```yaml
fpgas_online_site_version: "0.1.0"
fpgas_online_poe_version: "0.1.0"
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Update site role to install from fpgas-online pip packages"
git push origin main
```

- [ ] **Step 6: Clean up**

```bash
rm -rf /tmp/fpgas-infra-refactor
```

### Task 16: Update fixpi/onpi roles to use deb packages

**Files:**
- Modify: `fpgas.online-infra/ansible/roles/fixpi/tasks/main.yml`
- Modify: `fpgas.online-infra/ansible/roles/onpi/tasks/main.yml`
- Create: `fpgas.online-infra/ansible/roles/fpgas-apt/` (new role to add apt source)

- [ ] **Step 1: Clone infra repo**

```bash
git clone git@github.com:fpgas-online/fpgas.online-infra.git /tmp/fpgas-infra-deb
cd /tmp/fpgas-infra-deb
```

- [ ] **Step 2: Create fpgas-apt role**

This role adds the fpgas.online apt repository to any host that needs it.

Create `ansible/roles/fpgas-apt/tasks/main.yml`:

```yaml
- name: Add fpgas.online apt signing key
  ansible.builtin.get_url:
    url: https://fpgas-online.github.io/apt/pubkey.gpg
    dest: /usr/share/keyrings/fpgas-online.gpg
    mode: '0644'

- name: Add fpgas.online apt repository
  ansible.builtin.apt_repository:
    repo: "deb [signed-by=/usr/share/keyrings/fpgas-online.gpg] https://fpgas-online.github.io/apt bookworm main"
    state: present
    filename: fpgas-online

- name: Update apt cache
  ansible.builtin.apt:
    update_cache: yes
```

- [ ] **Step 3: Update fixpi role**

Remove file copy tasks for content now in the deb package. Add:

```yaml
- name: Install fpgas-online-setup-pi
  ansible.builtin.apt:
    name: fpgas-online-setup-pi
    state: present
```

Keep the Jinja2 template tasks (cmdline.txt.j2, config.txt.j2, fstab.j2,
resolve.conf.j2) and the eth1.conf handling.

- [ ] **Step 4: Update onpi role**

Remove all file copy tasks. Replace with the apt install (already covered by
the setup-pi deb from Step 3). Keep any host-specific configuration tasks.

- [ ] **Step 5: Update site.yml to include fpgas-apt role**

Add `fpgas-apt` before roles that need it:

```yaml
- hosts: pi
  roles:
    - fpgas-apt
    - fixpi
    - onpi
    - cam/pi
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Update fixpi/onpi to install from fpgas.online apt repo"
git push origin main
```

- [ ] **Step 7: Clean up**

```bash
rm -rf /tmp/fpgas-infra-deb
```

### Task 17: Update cam roles to use deb package

**Files:**
- Modify: `fpgas.online-infra/ansible/roles/cam/pi/tasks/main.yml`

- [ ] **Step 1: Clone and update**

```bash
git clone git@github.com:fpgas-online/fpgas.online-infra.git /tmp/fpgas-infra-cam
cd /tmp/fpgas-infra-cam
```

Replace the file copy tasks in `ansible/roles/cam/pi/tasks/main.yml` with:

```yaml
- name: Install fpgas-online-cam
  ansible.builtin.apt:
    name: fpgas-online-cam
    state: present
```

Keep the handler that restarts the camera service. Keep the
`cam/stream-server` role tasks unchanged (they manage nginx-rtmp on the
server side with Jinja2 templates).

- [ ] **Step 2: Commit and push**

```bash
git add -A
git commit -m "Update cam/pi role to install from fpgas.online apt repo"
git push origin main
```

- [ ] **Step 3: Clean up**

```bash
rm -rf /tmp/fpgas-infra-cam
```

### Task 18: Phase 3 verification — test ansible dry run

- [ ] **Step 1: Run ansible syntax check**

```bash
git clone git@github.com:fpgas-online/fpgas.online-infra.git /tmp/fpgas-infra-test
cd /tmp/fpgas-infra-test/ansible
ansible-playbook site.yml --syntax-check
```

Expected: no syntax errors.

- [ ] **Step 2: Run ansible in check mode against tweed**

```bash
ansible-playbook site.yml --check --diff --limit pig
```

Review the diff output — it should show the new package install tasks and
the removal of old file copy tasks.

- [ ] **Step 3: Run full ansible against a test Pi (if available)**

```bash
ansible-playbook site.yml --limit pi --check --diff
```

- [ ] **Step 4: Clean up**

```bash
rm -rf /tmp/fpgas-infra-test
```

---

## Phase 4: Cleanup

### Task 19: Update org profile and website

**Files:**
- Modify: `fpgas-online/.github/profile/README.md`
- Modify: `fpgas-online/website/README.md`

- [ ] **Step 1: Update .github org profile**

Update the repositories table in the org README to list all new repos.

- [ ] **Step 2: Update website repo**

Update the repos table in `website/README.md`.

- [ ] **Step 3: Push both changes**

### Task 20: Migrate todo issues to appropriate repos

- [ ] **Step 1: Review open issues**

```bash
gh issue list --repo fpgas-online/todo --limit 30
```

- [ ] **Step 2: For each issue, transfer to the appropriate repo**

Use the mapping from the spec:
- Pi tools issues (#1, #2, #3, #6) → fpgas.online-setup-pi
- Ansible issues (#12) → fpgas.online-infra
- Web UI issues (#15) → fpgas.online-site
- etc.

```bash
# Example: transfer issue #15 to fpgas.online-site
gh issue transfer 15 fpgas-online/fpgas.online-site --repo fpgas-online/todo
```

Note: some issues may span multiple repos. Leave those in `todo`.

- [ ] **Step 3: Verify transfers**

Check that transferred issues appear in the new repos.

### Task 21: Archive carlfk/pici

- [ ] **Step 1: Add deprecation notice to pici README**

Add to the top of `README.md`:

```markdown
> **This repository has been split into multiple repos under the
> [fpgas-online](https://github.com/fpgas-online) organization.**
>
> | Component | New Location |
> |-----------|-------------|
> | Ansible infrastructure | [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) |
> | Django web app | [fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) |
> | PoE switch management | [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) |
> | Camera streaming | [fpgas.online-cam](https://github.com/fpgas-online/fpgas.online-cam) |
> | Pi environment setup | [fpgas.online-setup-pi](https://github.com/fpgas-online/fpgas.online-setup-pi) |
> | Netboot tools | [fpgas.online-netboot-pi](https://github.com/fpgas-online/fpgas.online-netboot-pi) |
> | Utility scripts | [fpgas.online-tools](https://github.com/fpgas-online/fpgas.online-tools) |
> | FPGA test designs | [fpgas.online-test-designs](https://github.com/fpgas-online/fpgas.online-test-designs) |
>
> This repo is archived for historical reference.
```

- [ ] **Step 2: Archive the repo**

```bash
gh repo archive carlfk/pici --confirm
```

Note: this requires admin access to the carlfk/pici repo. If not available,
just add the deprecation notice and leave it unarchived.

- [ ] **Step 3: Final verification**

```bash
echo "=== All fpgas-online repos ==="
gh repo list fpgas-online --limit 20 --json name,description \
  --jq '.[] | .name + " -- " + .description'
```

Expected: 13+ repos listed with correct descriptions.
