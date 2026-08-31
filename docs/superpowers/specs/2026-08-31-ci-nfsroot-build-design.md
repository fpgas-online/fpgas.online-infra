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
  today's build produces (minus the secret layer, below).
- Replacing the on-server build path. It remains for development and as
  fallback; CI-pull becomes the default.
- Running the root as an actual container. GHCR is used as a content-addressed,
  versioned tarball store with free public hosting; the OCI image is never
  `docker run`.

## Design

### The public/secret seam

The build splits at a security boundary, not just a speed boundary:

**CI builds (public image):**
- RasPiOS lite armhf base (img role's download + extract, reimplemented on the
  runner without loop devices — see below)
- `fpgas-apt` role (adds the public fpgas.online apt repo)
- `cam/pi` role (`apt install fpgas-online-cam`)
- `onpi` role (`apt install fpgas-online-setup-pi`)
- Orange Pi armmp kernel + DTBs (`fixpi` `sunxi.yml`, once PR #28's
  implementation lands) — a chroot-apt step, so it belongs in CI
- Generic, secret-free `fixpi` file tweaks (e.g. `nogrow`, `ispi`)

**Tweed applies after extraction (never published):**
- `fixpi/userconf.yml` (tags `pipw`, `keys`) — three kinds of credential
  material, two of them secret: the pi password crypt hash written to
  `boot/userconf.txt` (offline-crackable if published); **generated
  *private* keypairs** for the pi and root users at
  `root/.ssh/id_ssh_rsa` and `home/pi/.ssh/id_ssh_rsa` (publishing these
  would give every image consumer identical, world-readable private
  keys); and the `authorized_keys` files (public halves only, harmless
  to publish but site-specific anyway). The whole layer stays
  server-side.
- Anything derived from inventory host_vars/secrets (switch config etc.)
- Server-side roles are unchanged: `nfs`, `pxe`, TFTP per-board boot files

This seam is exactly "slow emulated apt work" vs "fast local file edits", so
the split costs nothing operationally.

### Build pipeline (new workflow in fpgas.online-infra)

The roles live in this repo, so the workflow does too:
`.github/workflows/nfsroot-build.yml`.

- **Runner:** `ubuntu-24.04-arm` (free for public repos). armhf binaries may
  execute natively (AArch32 EL0 support depends on the runner's CPU —
  spike required); if not, install `qemu-user-static` + binfmt exactly as
  `nspawn-pi` does. Even emulated, runner CPUs beat tweed, and the build no
  longer occupies tweed at all.
- **Base extraction:** loop-mounting an image needs privileged access; runners
  allow `sudo losetup`, so `img2files.sh` can run as-is. (If that proves
  flaky, extract partitions with `guestfish`/`fdisk`+`dd` offsets instead.)
- **Provisioning:** run the *same roles* via a CI-only inventory that sets
  `ansible_connection=community.general.chroot` against the extracted root,
  in a new thin playbook `ansible/ci-nfsroot.yml` that includes the same role
  list as `site.yml`'s `pi` play plus the generic fixpi tasks. This honours
  the repo convention that only inventory differs between environments.
- **Verify in CI:** run the NFS-root package/config assertions from
  `verify-server.yml` against the chroot before publishing, so a broken root
  never gets a tag.
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
- **D-2 armhf on arm64 runners:** native AArch32 EL0 or qemu-user-static
  fallback. Settled by the Phase 1 spike; both paths are acceptable.
- **D-3 Transport:** GHCR OCI image (recommended, per above) vs rolling
  release asset tarball (fallback if podman-on-tweed is unwanted).
- **D-4 Image contents:** single image holding `boot/` + `root/` (recommended,
  they version together) vs separate images.
- **D-5 Cadence:** weekly cron + on-merge (recommended) vs cron-only.
- **D-6 Trixie:** todo#4 wants the fleet on trixie; tag scheme already encodes
  dist (`bookworm-armhf`) so a trixie image is additive, and arm64 runners
  would build a future arm64 root natively.

## Implementation phases (one PR each, CI green at every stage)

1. **Spike** (`workflow_dispatch`-only workflow): on `ubuntu-24.04-arm`,
   check AArch32 execution, loop-mount the RasPiOS image, chroot-apt one
   package, push a throwaway image to GHCR, record timings in the PR.
   Settles D-2 and validates the runner assumptions before any refactor.
2. **Build + publish:** `ansible/ci-nfsroot.yml` + CI inventory
   (chroot connection), reusing `fpgas-apt`/`cam/pi`/`onpi` unchanged;
   in-CI verification; tagging as above; weekly cron. Image is complete and
   public but nothing consumes it yet.
3. **Pull path on tweed:** `nfsroot_source` switch in the `img` role +
   `site.yml`, staging+rsync extraction, production host_vars pin;
   deploy to tweed during a maintenance window and verify with
   `verify-server.yml` + `verify-pi.yml` against real boards.
4. **Orange Pi kernel in the image:** move the armmp kernel install into the
   CI build (depends on PR #28's implementation landing).
5. **VM CI consumes the image** (optional speedup, exercises the pull path).

Each phase gets its own worktree/branch off `main` in this repo, per the
standard dev process.
