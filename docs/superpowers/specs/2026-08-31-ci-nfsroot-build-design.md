# CI-built Pi NFS root published to GHCR — design

**Date:** 2026-08-31
**Status:** Proposal (no implementation started; no prior issues or PRs exist for this work)
**Related:** apt repo secretless pull model, PR #28 (Orange Pi netboot design), todo#4 (trixie upgrade)

## Problem

A fresh Ansible rebuild of tweed spends most of its wall-clock time building the
Pi NFS root on the server:

1. `img` role downloads `2024-07-04-raspios-bookworm-armhf-lite.img.xz` from
   downloads.raspberrypi.org, loop-mounts it and rsyncs the two partitions to
   `/srv/nfs/rpi/bookworm/{boot,root}` (`ansible/roles/img/`).
2. The `pi` play in `ansible/site.yml` starts a chroot-over-SSH environment on
   that root (`nspawn-pi` role: qemu-user-static + binfmt + `piroot` user whose
   shell chroots in) and runs the Pi roles `fpgas-apt`, `cam/pi`, `onpi`
   against it. Every apt/dpkg operation executes under **qemu-arm user-mode
   emulation on the x86 server** — this is the dominant cost. In the QEMU VM CI
   the same phase is the long pole of a ~2 h run.
3. `fixpi` (server-side file edits on the NFS root: netboot config, user
   config, tweaks) and `pxe`/`nfs` roles do the fast server-side work.

The Orange Pi H3 boards share the byte-identical root and add one more slow
chroot-apt step (install `linux-image-*-armmp`, PR #28 design), so the cost
grows with every board family.

Nothing in the `fpgas-online` org currently builds this root off-server: a
search of all repos (2026-08-31) for nfsroot/rootfs/container/ghcr issues and
PRs finds none. This document is the starting point.

## Goal

Build the provisioned Pi root filesystem in GitHub Actions and publish it as an
OCI image to the GitHub Container Registry (GHCR). Tweed's Ansible run then
*pulls and extracts* a ready-made root instead of building one, cutting the
rebuild time from hours to minutes. The pull is secretless (public package),
mirroring the apt repo's rolling-deb model.

## Non-goals

- Changing what ends up in the NFS root. The image must contain exactly what
  today's build produces (minus the site-specific layer, below).
- Replacing the on-server build path. It remains for development and as
  fallback; CI-pull becomes the default.
- Running the root as an actual container. GHCR is used as a content-addressed,
  versioned tarball store with free public hosting; the OCI image is never
  `docker run`.

## Design

### The CI/site seam

**Threat model:** the Pi credentials are intentionally public — the pi/root
passwords are published on the fpgas.online page so anyone in the world can
log in, and the devices are ephemeral by design (read-only NFS root +
overlayroot tmpfs; a reboot resets them to the default state). Nothing in
today's Pi root is a secret: the password hash in `boot/userconf.txt`, the
generated pi/root keypairs under `.ssh/`, and the sshd host keys are all
readable by any member of the public who logs in. Publishing the root as a
public image therefore leaks nothing that isn't already deliberately exposed.

The seam is instead about what is **generic fleet content** (identical for
every site, buildable from public inputs) vs **site-specific** (derived from
each site's inventory):

**CI builds (public image, generic):**
- RasPiOS lite armhf base (img role's download + extract, reimplemented on the
  runner without loop devices — see below)
- `fpgas-apt` role (adds the public fpgas.online apt repo)
- `cam/pi` role (`apt install fpgas-online-cam`)
- `onpi` role (`apt install fpgas-online-setup-pi`)
- Orange Pi armmp kernel + DTBs (`fixpi` `sunxi.yml`, once PR #28's
  implementation lands) — a chroot-apt step, so it belongs in CI
- The generic `fixpi/netboot.yml` steps: netboot cmdline/fstab (both sites
  use 10.21.0.1, so these are generic), pi user creation + sudoers,
  self-hostname service, timesyncd pin, `apt install nfs-common overlayroot`
  (chroot-apt — this is what makes the initramfs NFS/overlay-capable), ssh
  enablement, and — **last, after every chroot-apt step including the armmp
  kernel** — the matched kernel+initramfs+DTB payload sync from
  `root/boot/firmware/` into `boot/`. Ordering is load-bearing: a stale
  `boot/` paired with upgraded root kernels reproduces the C1-3
  nondeterministic boot loops (see netboot.yml's own comment).
- Other generic `fixpi` file tweaks (e.g. `nogrow`, `ispi`, the `issue.d`
  banner, sshd password config)
- Potentially the published pi password and the pi/root keypair generation
  (see D-7): these are public and fleet-wide already, so they *could* be
  baked, at the cost of coupling a password change to an image rebuild.

**Tweed applies after extraction (site-specific):**
- Controller/operator `authorized_keys` entries — each site authorizes its
  own controller key (`ansible_ssh_private_key_file`), so these come from
  the site's inventory at deploy time.
- The pi password hash, if sites want to differ or rotate without a
  rebuild (D-7).
- Anything else derived from inventory host_vars (switch config etc.)
- Server-side roles are unchanged: `nfs`, `pxe`, TFTP per-board boot files

**Guard rail:** should a genuine secret ever need to land in the Pi root
(e.g. a per-site API token), it must go in the tweed-side layer — the image
is public and content-addressed, so a published secret is unrevokable.

This seam still puts all the slow emulated apt work in CI and only fast local
file edits on tweed, so the split costs nothing operationally.

### Hardware coverage (requirement: every board at both sites keeps booting)

The deployed fleet the image must serve (inventory census, 2026-08-31):

- **welland** (per-port scheme): RPi 4 / RPi 5 fleet boards, Compute Blade
  CM4/CM5 boards, the TT boxes on sw2 p1–10, and 5× Orange Pi PC (H3,
  ARMv7) on sw2 p20–24 (FEL-booted, PR #28).
- **ps1** (legacy MAC-table scheme, `switch.nos`): 4× Pi 3B, 2× Pi 3B+,
  2× Pi 4, and Compute Blades with 2× CM4 and 2× CM5 Lite.

Why one image covers all of them, and why moving the build to CI cannot
change that:

1. **One armhf userland already serves every board.** RasPiOS armhf is built
   ARMv6-compatible, runs on every RPi generation (BCM2712 boards run it
   under a 64-bit kernel), and the H3 spike proved it byte-identical on the
   Orange Pis. The image reproduces today's tree exactly — CI changes *where*
   the tree is built, not *what is in it* — so the fleet itself is the
   existence proof.
2. **All board-specific boot material lives inside the image.** Per-model
   kernel selection is the firmware's own filename convention
   (`kernel7l.img`, `kernel8.img`, `kernel_2712.img`, …) against the `boot/`
   tree, which on per-port hosts *is* the TFTP root
   (`tftp_root: nfs_root/boot`, see `group_vars/all/srv.yml`) — the RPi 4/5
   bootloader falls back from `<serial>/` to the root, so new/swapped boards
   need no registration. The payload-sync step above keeps every model's
   kernel matched to the root's modules. The OPi armmp kernel + sun8i DTBs
   join the same tree in phase 4.
3. **Site scheme differences are entirely server-side.** ps1's per-serial
   `/srv/tftp` symlinks (from `switch.nos`) and welland's direct-serve TFTP
   both point into the same `boot/` tree; dnsmasq/DHCP/VLAN wiring never
   touches the image.

**CI enforcement:** the in-CI verification (before tagging) must assert the
per-model boot files exist in `boot/` — `kernel.img`, `kernel7.img`,
`kernel7l.img`, `kernel8.img` and the Pi 3/3+/4/5 DTBs (note: the armhf
image ships no `kernel_2712.img`, that is arm64-only; Pi 5/CM5 boot
`kernel8.img` + `bcm2712-rpi-5-b.dtb`, as the ps1 CM5 blades do today —
phase 2's gate caught exactly this), and from phase 4 `vmlinuz-*-armmp` +
`sun8i-h3-*.dtb` — so an upstream image or kernel-packaging change that
drops a board family fails the build instead of bricking part of the fleet
on the next pull.

### Build pipeline (new workflow in fpgas.online-infra)

The roles live in this repo, so the workflow does too:
`.github/workflows/nfsroot-build.yml`.

- **Runner:** `ubuntu-24.04-arm` (free for public repos). armhf executes
  natively (AArch32 EL0 — confirmed by the Phase 1 spike, see D-2); do not
  install qemu-user-static on the runner, its binfmt handlers could shadow
  native execution (`fixpi_install_qemu: false`).
- **Base extraction:** loop-mounting an image needs privileged access; runners
  allow `sudo losetup`, so `img2files.sh` can run as-is. (If that proves
  flaky, extract partitions with `guestfish`/`fdisk`+`dd` offsets instead.)
- **Provisioning:** run the *same roles* via a CI-only inventory that sets
  `ansible_connection=community.general.chroot` against the extracted root,
  in a new thin playbook `ansible/ci-nfsroot.yml` that includes the same role
  list as `site.yml`'s `pi` play plus the generic fixpi tasks. This honours
  the repo convention that only inventory differs between environments.
- **Verify in CI:** run the NFS-root package/config assertions from
  `verify-server.yml` against the chroot, plus the per-model kernel
  assertions from "Hardware coverage" above, before publishing — a broken
  root never gets a tag.
- **Package + push:** tar the `/srv/nfs/rpi/bookworm`-shaped tree (top-level
  `boot/` and `root/` directories) with numeric owners, xattrs and hardlinks
  preserved (`tar --numeric-owner --xattrs --acls`), import as a single-layer
  image and push with the workflow's `GITHUB_TOKEN` (`packages: write`):
  - `ghcr.io/fpgas-online/nfsroot:bookworm-armhf` (rolling)
  - `ghcr.io/fpgas-online/nfsroot:bookworm-armhf-YYYYMMDD-<sha7>` (pinnable)
  The package is made public once, manually, in GHCR settings.
- **Triggers:** push to `main` touching the Pi roles or the workflow;
  weekly cron (picks up new debs from the rolling apt repo); manual
  `workflow_dispatch`.

### Consumption on tweed

The `img` role grows a second mode selected by an inventory var
(`nfsroot_source: pull | build`, default `pull` in production host_vars):

1. `apt install podman` (in Debian; used only as a pull/export tool).
2. Pull the pinned tag, `podman create` + `podman export` to a staging
   directory, then `rsync -a --delete` into `{{ nfs_root }}` — same in-place
   update semantics as today's build (Pis mount read-only with an overlayroot
   tmpfs, but updating a live root should still happen with the fleet powered
   off, as now).
3. The `pi` nspawn play in `site.yml` is skipped when `nfsroot_source ==
   pull`; the secret `fixpi` tasks (userconf) still run server-side.
4. `verify-server.yml` runs unchanged — the pulled root must pass the exact
   assertions the built root passes.

Pinning: production host_vars reference the dated tag (or digest) so a rebuild
is reproducible; bumping the pin is a one-line PR, the same shape as bumping a
deb version.

### Why OCI/GHCR rather than a rolling release asset

The debs model uses GitHub release assets, which would also work here
(`get_url` instead of podman). GHCR was chosen because: content-addressed
digests give tamper-evident pinning; no 2 GiB per-file release limit looming
over a growing root (compressed root is ~0.5–1 GiB today); and registry
storage/bandwidth is free for public packages. If the podman dependency on
tweed is unwanted, the release-asset variant is the documented fallback
(decision D-3).

### Secondary consumer: the VM CI

`vm-test.yml`'s server phase spends most of its 60–90 min doing this same
emulated provisioning inside a TCG VM. Once the image exists, the VM test can
pre-seed `/srv/nfs/rpi/bookworm` from the pulled image and exercise the `pull`
path — making CI faster *and* covering the production code path. Follow-up,
not part of the initial implementation.

## Open decisions

- **D-1 Workflow home:** fpgas.online-infra (recommended — roles live here) vs
  a new repo. New repo would need role vendoring; not worth it.
- **D-2 armhf on arm64 runners:** **SETTLED (native)** — the Phase 1 spike
  (PR #40, run 33348567635) executed the RasPiOS armhf userland directly on
  `ubuntu-24.04-arm`; chroot `apt-get install lldpd` took 9.3 s, the whole
  spike (download, extract, chroot-apt, import, GHCR push) 2m10s. No
  qemu-user-static on the runner — and none must be installed there, since
  its binfmt handlers could shadow native execution
  (`fixpi_install_qemu: false`).
- **D-3 Transport:** GHCR OCI image (recommended, per above) vs rolling
  release asset tarball (fallback if podman-on-tweed is unwanted).
- **D-4 Image contents:** single image holding `boot/` + `root/` (recommended,
  they version together) vs separate images.
- **D-5 Cadence:** weekly cron + on-merge (recommended) vs cron-only.
- **D-6 Trixie:** todo#4 wants the fleet on trixie; tag scheme already encodes
  dist (`bookworm-armhf`) so a trixie image is additive, and arm64 runners
  would build a future arm64 root natively.
- **D-7 Credential layer placement:** the pi password and pi/root keypairs
  are deliberately public (published on fpgas.online; devices ephemeral), so
  they *may* be baked into the CI image. Recommended: keep them tweed-side
  anyway — it keeps password rotation a fast local edit instead of an image
  rebuild, keeps the image byte-identical for any future site regardless of
  its password, and costs nothing (they're already fast file edits). Baking
  them is acceptable if one org-wide password is the norm.

## Implementation phases (one PR each, CI green at every stage)

1. **Spike** — **DONE** (PR #40, run 33348567635, 2m10s): AArch32 is native
   (D-2 settled), sudo losetup/chroot work, chroot apt 9.3 s, GHCR push
   60 s for the 1.67 GB base image.
2. **Build + publish** — in review (PR #41): `ansible/ci-nfsroot.yml` + CI
   inventory (chroot connection; `srv.yml` group_vars symlinked to the
   production copy), reusing `fpgas-apt`/`cam/pi`/`onpi` unchanged;
   hardware-coverage assertions; final kernel-payload re-sync (site.yml
   syncs before the Pi roles, which is a latent C1-3 in one-shot builds);
   tagging as above; weekly cron. Image is complete and public but nothing
   consumes it yet.
3. **Pull path on tweed:** `nfsroot_source` switch in the `img` role +
   `site.yml`, staging+rsync extraction, production host_vars pin;
   deploy to tweed during a maintenance window and verify with
   `verify-server.yml` + `verify-pi.yml` against real boards.
4. **Orange Pi kernel in the image:** move the armmp kernel install into the
   CI build (depends on PR #28's implementation landing).
5. **VM CI consumes the image** (optional speedup, exercises the pull path).

Each phase gets its own worktree/branch off `main` in this repo, per the
standard dev process.
