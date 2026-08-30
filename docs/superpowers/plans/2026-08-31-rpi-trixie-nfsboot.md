# RPi nfsboot bookworm → trixie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Raspberry Pi network-boot NFS root from Raspberry Pi OS bookworm (2024-07-04 lite armhf) to trixie (2026-06-18 lite armhf), verified end-to-end by the QEMU VM CI.

**Architecture:** The Pi NFS root is built on the server by the `img` role
(download raspios image, extract to `/srv/nfs/rpi/{{ dist }}`), then
provisioned via nspawn+qemu-user-static by the `fixpi`, `fpgas-apt`,
`cam/pi`, and `onpi` roles. `dist` is a single inventory variable; the
upgrade is a variable bump plus fixes for the few places bookworm is
hard-coded or where trixie renamed/removed packages.

**Tech Stack:** Ansible, QEMU VM test harness (`tests/vm/run_tests.py`), GitHub Actions (`vm-test.yml`, `lint.yml`).

**Spec:** This plan is its own spec — requirements below.

## Global Constraints

- Work only in worktree `.worktrees/rpi-trixie`, branch `rpi-trixie`; never touch the main checkout (another branch with local mods lives there).
- New image: `2026-06-18-raspios-trixie-armhf-lite.img.xz` in directory `raspios_lite_armhf-2026-06-19` (note: **dir date ≠ file date**).
- Merge via PR only; branch protection on main; no force-push (use `git safe-force-push <branch>` if ever needed).
- Small discrete commits; yamllint must stay green (blocking); ansible-lint is advisory.
- CI (`VM Integration Tests` workflow, ~2 h) must pass — that is the acceptance test. No QEMU-specific workarounds in Ansible roles.
- The server VM distro (`run_tests.py --distro`, Debian cloud image) is **out of scope** — it is the x86 server's OS, not the Pi's.

## Pre-verified facts (2026-08-31)

- fpgas apt repo publishes a `trixie` suite containing fpgas-online-cam,
  fpgas-online-setup-pi, fpgas-online-tt, fpgas-online-tt-demos (armhf+arm64).
- All `onpi` + `cam/pi` apt packages exist in trixie armhf indexes
  (deb.debian.org + archive.raspberrypi.com) **except**:
  - `software-properties-common` — removed from trixie → drop it.
  - `libfreetype6-dev` — virtual only → rename to `libfreetype-dev`.
- `raspberrypi-sys-mods` has no `trixie` branch (now `pios/trixie`), but the
  only task using `{{ dist }}` as a branch is `when: false` (dead) → no change.
- `fixpi/nogrow.yml` already masks the trixie-era rpi-resize/zram units.
- CI's `vm-images-bookworm-v2` cache holds only the **server** Debian 12
  cloud image → key unchanged.

---

### Task 1: Bump dist + image dates in both inventories

**Files:**
- Modify: `ansible/inventory/group_vars/all/srv.yml:3-11`
- Modify: `tests/inventory/group_vars/all/srv.yml:3-11`

**Interfaces:**
- Produces: `dist=trixie`, `dir_date=2026-06-19`, `release_date=2026-06-18`,
  hence `nfs_root=/srv/nfs/rpi/trixie` and image URL
  `https://downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2026-06-19/2026-06-18-raspios-trixie-armhf-lite.img.xz`.

- [ ] **Step 1:** In both files set `dir_date: 2026-06-19`, replace `release_date: "{{ dir_date }}"` (spacing varies between the two files) with a literal `release_date: 2026-06-18`, and set `dist: trixie`. Also switch `img_host` to `https://downloads.raspberrypi.org` (was `http://`) only if it stays reachable — otherwise leave as-is.
- [ ] **Step 2:** Verify the resulting URL exists: `curl -sI -o /dev/null -w '%{http_code}' <url>` → expect `200` (or `302`).
- [ ] **Step 3:** `uvx yamllint -c .yamllint.yml ansible/` → clean.
- [ ] **Step 4:** Commit: `feat(img): move Pi NFS root to raspios trixie 2026-06-18 lite armhf`

### Task 2: De-hardcode bookworm in fpgas-apt suite and test-vm nfs_root

**Files:**
- Modify: `ansible/roles/fpgas-apt/tasks/main.yml:12` — `https://fpgas-online.github.io/apt bookworm main` → `https://fpgas-online.github.io/apt {{ dist }} main`
- Modify: `tests/inventory/host_vars/test-vm.yml:88` — `nfs_root: "/srv/nfs/rpi/bookworm/root"` → `nfs_root: "/srv/nfs/rpi/{{ dist }}/root"`

**Interfaces:**
- Consumes: `dist` from Task 1 (defined in `group_vars/all` of both inventories; fpgas-apt only runs in the `pi` play, `site.yml:37`).

- [ ] **Step 1:** Make both edits.
- [ ] **Step 2:** `uvx yamllint -c .yamllint.yml ansible/` → clean.
- [ ] **Step 3:** Commit: `fix: derive fpgas-apt suite and test-vm nfs_root from dist var`

### Task 3: Fix trixie package renames in onpi role

**Files:**
- Modify: `ansible/roles/onpi/tasks/apt.yml` — in the `Install packages` list: delete `software-properties-common` (removed in trixie; provided add-apt-repository, unused on the Pi); replace `libfreetype6-dev` with `libfreetype-dev` (real package in both bookworm and trixie).

- [ ] **Step 1:** Make the edits.
- [ ] **Step 2:** `uvx yamllint -c .yamllint.yml ansible/` → clean.
- [ ] **Step 3:** Commit: `fix(onpi): trixie package availability (drop software-properties-common, libfreetype-dev)`

### Task 4: Push branch, open PR, confirm CI green

- [ ] **Step 1:** `git push -u origin rpi-trixie`
- [ ] **Step 2:** `gh pr create` — title `Upgrade RPi nfsboot from bookworm to trixie`, body summarising the dist bump, the two package fixes, the de-hardcoding, and the pre-verified facts above.
- [ ] **Step 3:** Watch the `Lint` and `VM Integration Tests` runs (`gh run list/watch`, poll in background; full VM run takes up to ~2 h under TCG). Report status periodically.
- [ ] **Step 4:** On failure: pull serial-log artifacts (`gh run download`), apply superpowers:systematic-debugging, fix in this worktree, commit, push, re-watch. Likely failure classes: apt package resolution in the nspawn chroot, trixie systemd unit name drift in `fixpi`/`verify-pi.yml`, boot-partition file layout differences.
- [ ] **Step 5:** When both workflows are green, report PR URL + CI links. Do NOT merge — leave the PR for review (production deploy builds a new `/srv/nfs/rpi/trixie` tree on next site.yml run; that rollout is a separate decision).
