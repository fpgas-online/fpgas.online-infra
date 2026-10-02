# Versions of the fleet NFS root: one directory per version

Status: draft for review, 2026-10-02. Nothing here is implemented.
Replaces the 2026-09-26 draft of this file.

## Scope

Tim's rule (2026-10-02): "All pi in normal operation should boot from the same
nfsroot." There is one fleet root. This spec keeps each **version** of that
root in its own directory, so an update never changes a tree a board is
running from. The only difference that may exist between two boards is
between the version a board runs now and the version it gets when it reboots.

Two neighbouring topics are separate and not designed here:

- A different NFS root that a Pi boots for a special job (EEPROM upgrade,
  reading the Wi-Fi MAC): infra issue #176. Out of scope.
- The automatic reboot when the root changes: nfsroot-watchdog, already
  deployed, owned by the nfsroot-watchdog repo. This spec only states the
  contract it needs from it (see "Contract with nfsroot-watchdog").

No variable in this design is per Pi or per board type.

A note on one word. nfsroot-watchdog's marker file is named
`/etc/nfsroot-watchdog/generation`, its server command is `nfsroot-generation`
and our role that wraps it is `roles/nfsroot_generation`. Those are names.
This spec says "marker" for the file's content and "version" for a root tree.

## Problem

A gateway exports one Pi root, `/srv/nfs/rpi/<dist>/{boot,root}`
(`ansible/inventory/group_vars/all/srv.yml:15`,
`ansible/roles/nfs/templates/exports.j2:3-4`), and every update is written
into it while the fleet is booted from it:

- `ansible/roles/img/tasks/pull.yml:106-112` runs
  `rsync -aHAX --delete --numeric-ids --checksum` from the pulled CI image into
  the live tree. rsync writes each transferred file to a temp file and renames
  it into place, so every transferred file gets a new inode.
- `ansible/roles/apt_cache/tasks/nfsroot.yml` and `ansible/roles/fixpi` then
  write the site layer with `template`, `copy`, `lineinfile` and `replace`,
  which also rename into place.

A booted board mounts that tree as the read-only lower layer of
`overlayroot=tmpfs` (`ansible/roles/fixpi/templates/boot/cmdline.txt.j2:1`).
Every replaced inode then answers `ESTALE` on that board until it reboots. The
two deploys on 2026-09-25 changed 12587 and 7418 files.
`/home/pi/.ssh/authorized_keys` was among them, which broke key SSH
fleet-wide. nfsroot-watchdog exists to reboot every board after such an
update.

### Why no mount option fixes it

This is overlayfs behaviour, not NFS caching. Linux v6.12,
`fs/overlayfs/super.c`:

```c
static int ovl_revalidate_real(struct dentry *d, unsigned int flags, bool weak)
...
	} else if (d->d_flags & DCACHE_OP_REVALIDATE) {
		ret = d->d_op->d_revalidate(d, flags);
		if (!ret) {
			if (!(flags & LOOKUP_RCU))
				d_invalidate(d);
			ret = -ESTALE;
		}
	}
```

When NFS reports that a lower dentry's name now maps to a different file
handle (`d_revalidate() == 0`), overlayfs returns `-ESTALE` instead of 0. The
VFS only looks a name up again on 0, so the overlay dentry stays tied to the
dead lower dentry. On a plain NFS mount the same rename heals on the next
lookup. `Documentation/filesystems/overlayfs.rst` (v6.12) states the rule:
"Changes to the underlying filesystems while part of a mounted overlay
filesystem are not allowed."

What this rules out:

- NFS caching options (`lookupcache=none`, `actimeo=0`, `noac`) and NFSv4.
  They change when NFS notices the change. overlayfs still turns it into
  ESTALE.
- Overlay options (`index`, `xino`, `redirect_dir`, `metacopy`). The same
  document says they make offline changes to the lower layer undefined too.
- Keeping replaced inodes alive (`rsync --backup-dir`). It helps files that
  are already open. Path lookups still fail revalidation.
- Dropping the overlay. Files would heal, but a running board would mix old
  and new versions of the same package set.

The fix has to be on the server: never change a tree a board is booted from.

infra #128 (`--checksum` on the pull) and #131 (the pull no longer deletes the
SSH host keys) are merged and deployed. They shrink the damage. This spec
removes the cause.

## Goals

1. A root update never modifies a tree a board is booted from. No ESTALE on
   the lower layer as a consequence of a deploy.
2. One directory per version of the fleet root. A bookworm to trixie upgrade
   (infra #37) is simply the next version.
3. Atomic switch-over and one-step rollback.
4. A board's kernel, initramfs, DTBs and `/lib/modules` always come from one
   version.
5. A converge that changes nothing publishes nothing and reboots nobody.
6. nfsroot-watchdog keeps working with an unchanged client.

## Non-goals

- Changing how CI builds or publishes the image, overlayroot, or the Pi-side
  mount options.
- Automatic deletion of old versions. Tim: "Don't worry about GC until it
  becomes a problem." Every version is kept until a person deletes it.
- A canary step. The VM CI boot test is the gate. A version is published, the
  fleet reboots onto it in its staggered slots, and if it is bad `current` is
  rolled back and the fleet reboots again.

## Terms

- **version**: one complete `{boot,root}` pair, built from one image digest
  plus one rendering of the site layer. Its id is
  `<UTC yyyymmddThhmmssZ>-<first 12 hex of the image digest>`, for example
  `20261005T013000Z-b41ea5c743cf`. The time is when the build started.
- **current**: the version a board gets when it boots.
- **published**: `current` names it now, or did at some time.
- **immutable**: once a version is published, nothing under it is written,
  with one exception: `root/etc/nfsroot-watchdog/`, which only
  nfsroot-watchdog's server command writes (see the contract).
- **legacy root**: today's in-place tree `/srv/nfs/rpi/bookworm` on tweed. It
  is treated as "version 0".

## Layout on the gateway

```
/srv/nfs/rpi/
  versions/                        # the one NFS export versioned boards use
    20261005T013000Z-b41ea5c743cf/
      boot/                        # TFTP payload; its cmdline names THIS version's root
      root/                        # the NFS lower layer
      .image-digest                # written by img, as today
    20261012T020000Z-0c9d1e2f3a4b/
  current -> versions/20261012T020000Z-0c9d1e2f3a4b   # swapped with rename(2)
  staging/<id>/{boot,root}         # a version being built; not exported
  site-state/root/...              # files that must survive across versions; mode 0700
  empty/{boot,root}                # fresh gateway only: what current names before the first publish
  <lock file>                      # flock for publish/rollback/inhibit; name is nfsroot-watchdog's
  bookworm/{boot,root}             # tweed only: the legacy root
```

There is no `dist` in the path. `nfs_root` and `tftp_root` stop being derived
from `dist` on sites that use this layout.

`staging/` is on the same filesystem as `versions/`, so publishing a built
version is a `rename(2)`, not a copy. The role asserts this (same `st_dev`)
before it builds.

tweed already has `/srv/nfs/rpi/bookworm.pre-pull-2026-09-25` and
`/srv/nfs/rpi/ssh-host-keys.replaced-2026-09-26`. No name is assumed free: the
layout step fails if `versions`, `staging`, `site-state` or `empty` exists and
is not a directory, or if `current` exists and is not a symlink.

Sizes, as recorded for tweed in the 2026-09-26 draft: a version is about 5 GB
(4.8 GB root, 133 MB boot) and `/srv` had about 188 GB free. Roughly 35
versions fit.

Hard-link sharing between versions (`rsync --link-dest=<previous>`) would cut
that to the changed files only. It is not in the first cut, because any
in-place write to a shared inode changes a published version. The site layer
has such writes today: `ansible/roles/apt_cache/tasks/nfsroot.yml:58` rewrites
apt source files with `open(path, "w")`, and
`ansible/roles/fixpi/tasks/userconf.yml:119-126` does a recursive `chown`.
Full copies make that class of bug impossible.

## Turning it on: one inventory variable

`nfsroot_versioned` (shared inventory variable in
`ansible/inventory/group_vars/all/srv.yml`, default `false`) selects the
layout per site. Everything in this spec is behind it. With it off, a site
behaves exactly as today. It is turned on for the CI VM first
(`tests/inventory/host_vars/test-vm.yml`), then for tweed
(`ansible/inventory/host_vars/fpgas.online.yml`). This follows the rule that
merged code must be deployable and that playbooks run whole: there are no tags
(`tests/test_no_tags.py`).

**ps1 stays single-root.** ps1 is on the legacy MAC-table scheme:
`tftp_root` is `/srv/tftp` with one symlink per Pi serial
(`ansible/roles/fixpi/tasks/netboot.yml:14-22`, from `switch.nos`). The VM CI
only boots the per-port scheme (`tests/inventory/host_vars/test-vm.yml`
defines `switches`), so the versioned layout on a MAC-table site could not be
tested before a deploy. The role therefore asserts `switches is defined` when
`nfsroot_versioned` is on. ps1 adopts the layout when it moves to per-port
VLANs. Until then it keeps `nfsroot-generation begin`/`end`.

## Building a version

### How every writer is pointed at the staging directory

`nfs_root` appears 188 times in the YAML and templates under `ansible/` (100 of
them in `roles/fixpi`). Almost all are writes of the form
`{{ nfs_root }}/root/...` or `{{ nfs_root }}/boot/...`. They are not edited.
Instead, `nfs_root` means "the tree this play works on":

- In `srv.yml`, on a versioned site, `nfs_root` is `/srv/nfs/rpi/current`.
  Every reader (`verify-server.yml`, the role `verify/` tasks) follows the
  symlink to the current version.
- The "Update the Pi NFS root" play (`ansible/site.yml:64-110`) sets play-level
  `vars:` that override it for that play only: `nfs_root` is
  `/srv/nfs/rpi/staging/<id>`, and a new variable `nfsroot_mount_dir` is
  `/srv/nfs/rpi/versions/<id>`. Play vars outrank inventory vars and end with
  the play, so nothing leaks into a later play. A play var cannot refer to
  the inventory variable of the same name, so `srv.yml` also defines the
  served path under its own name (`nfsroot_served_dir`).
- `nfsroot_mount_dir` is the path a board mounts. It defaults to `nfs_root`,
  so single-root sites and the CI image build render what they render today.

Paths baked into a version must name its final path, not the staging path.
Four templates carry the path and switch from `nfs_root` to
`nfsroot_mount_dir`:

- `ansible/roles/fixpi/templates/boot/cmdline.txt.j2:1` and
  `cmdline-pi5.txt.j2:1` (`nfsroot=10.21.0.1:<path>/root`);
- `ansible/roles/fixpi/templates/boot/default-arm-sunxi.j2:18` (the Orange Pi
  PXE config);
- `ansible/roles/fixpi/templates/etc/fstab.j2:2-3` (`noauto` entries).

So each version's boot files name its own root. A board that TFTP-boots a
version's `boot/` mounts that version's `root/`. The cmdline never names
`current`: a board must stay on the tree it booted.

Three writers do not go through `nfs_root` correctly today and need an edit:

- `ansible/roles/fixpi/tasks/userconf.yml:121` hardcodes
  `/srv/nfs/rpi/{{ dist }}/root/{{ item }}/.ssh` for the `chown` of pi's
  `.ssh`. With a staging build it would chown the legacy root and leave the new
  version's `.ssh` owned by root, so pi's key login would fail. It becomes
  `{{ nfs_root }}`. This is a bug fix on its own and goes first.
- `ansible/roles/fixpi/tasks/sunxi.yml:27,36,47,66,67,80` write the sunxi
  kernel, DTBs and PXE config to `{{ tftp_root }}`. On a per-port site that is
  `{{ nfs_root }}/boot` today (`srv.yml:23`). Under this layout `tftp_root` is
  the served path through `current`, so these writes change to the boot
  directory being built.
- `ansible/roles/fixpi/tasks/netboot.yml:8-12` links `/srv/tftp/bootcode.bin`
  to `{{ nfs_root }}/boot/bootcode.bin`. It must link through `current`, or it
  would point into `staging/`.

The other hardcoded paths need no change: see "Changes by file".

### The steps

The play keeps its order (`ansible/site.yml:64-110`). On a versioned site:

1. Download the GitHub keys (`fixpi/tasks/github_keys.yml`, unchanged, still
   first: a GitHub outage stops the run before anything is built).
2. `img`: wait for the background pull, pull, read the digest
   (`pull.yml:21-50`). The digest is needed to name the version, so
   `pull.yml` is split after it.
3. New role `nfsroot_version`, begin: delete anything left under `staging/`,
   choose the id, create `staging/<id>/`.
4. `img`: extract into `{{ nfs_root }}`, which is the empty staging directory.
   The rsync command is unchanged. Its `--delete` and its four excludes
   (`pull.yml:109-110`) have nothing to act on.
5. Copy site state into the staging tree (see "Site state").
6. `apt_cache` `nfsroot.yml` and `fixpi`: unchanged apart from the edits above.
7. Compare the staging tree with `current` (next section). If they are the
   same, delete the staging tree and stop.
8. Check the built tree: `root/bin/bash` and `boot/kernel8.img` exist, both
   cmdline files name `versions/<id>/root`, the host keys and pi's
   `authorized_keys` are present. These are a few `stat`/`grep` tasks on the
   gateway. A tree that fails is never served.
9. Rename `staging/<id>` to `versions/<id>`.
10. Call nfsroot-watchdog's `publish` (see the contract). It swaps `current`.

Step 3 replaces `nfsroot_generation`'s `begin.yml` and step 10 replaces its
`end.yml` (the two includes at `site.yml:83-86` and `site.yml:108-110`). Both
stay for single-root sites.

### When a new version is made

Every run builds into staging and then compares the result with `current`:
file type, content, mode, owner, group and symlink target, for `boot/` and
`root/`. Modification times are not compared, because every file Ansible
writes into a fresh tree has a new one. `root/etc/nfsroot-watchdog/` is
ignored. The four files that carry the version path are compared after each
tree's own id is replaced by a fixed token. If nothing differs, nothing is
published and no board reboots. On a fresh gateway (`current` names `empty`)
the compare is skipped.

Cost. Today a run with an unchanged image digest skips the extraction
(`pull.yml:52-65`), runs the site-layer tasks as no-ops, and
`nfsroot-generation end` walks the tree once comparing ctimes. A run with a
new digest reads both trees once (`--checksum`). Under this design every run
extracts about 5 GB and then reads both trees for the compare. That is a
little more than today's new-digest run, on every run. It has not been
measured on tweed; the cut-over PR must report it. In the VM CI the first
converge already extracts, and the compare is skipped, so the cost there does
not change.

### Site state

Each version is a fresh directory, so anything that must stay the same across
versions and is not in the image has to be carried. `pull.yml:109-110`
excludes exactly four patterns today. Going through them and through what
fixpi creates:

| what | today | under this layout |
|---|---|---|
| `/etc/ssh/ssh_host_*` | excluded from the pull (#131); `fixpi/tasks/netboot.yml:268-286` generates them when absent | kept in `site-state/` |
| pi's and root's `id_ssh_rsa` keypair | `fixpi/tasks/userconf.yml:107-112` creates them when absent. The pull does not exclude them and the image has none (`ci-nfsroot.yml:238-250` asserts it), so every new image digest deletes and regenerates them. Nothing in this repo reads them | kept in `site-state/` |
| `authorized_keys` (root, pi, ansible) | excluded from the pull; `fixpi/tasks/authorized_keys.yml:77-85` writes each file whole from the full key list (#140, #161) | no state needed |
| `/etc/nfsroot-watchdog/` | excluded from the pull | created by `publish` |
| pi password, apt sources, `fleet.toml`, `tt-boards.yaml`, config.txt edits | rendered from the inventory | no state needed |

`site-state/root/` mirrors the paths inside a root. Before fixpi runs, its
files are copied into the staging tree, so fixpi's create-if-absent tasks find
them and do nothing. After fixpi runs, any of those files that `site-state/`
does not have yet is copied back. On the first versioned run, `site-state/` is
seeded from the legacy root if there is one (tweed: the host key in
`docs/access.md:157-160` is kept), otherwise from what fixpi generated (a
fresh gateway). `site-state/` holds private keys: it is mode 0700 and outside
the export.

### Failure handling

- Anything that fails before step 10 leaves `current` and every published
  version untouched. No board notices.
- A half-built `staging/<id>` is deleted by step 3 of the next run. One
  `site.yml` run per gateway at a time is assumed, as today.
- A failure between steps 9 and 10 leaves a complete but unpublished
  `versions/<id>`. It is harmless. The next run builds a new id. The orphan
  stays until a person deletes it.
- If `publish` fails after the swap but before every older version has the new
  marker, boards on those versions are never told. The next run would find
  staging equal to `current` and publish nothing. `verify-server.yml` catches
  this: it asserts that every version holds the same marker.
- The free-space guard: the layout step, in `site.yml`'s first play, fails
  when the filesystem holding `/srv/nfs/rpi` has less than
  `nfsroot_min_free_gb` free (default 8: one version plus headroom). The
  message gives the free space, the need, and the manual clean-up procedure.

## NFS exports

`ansible/roles/nfs/templates/exports.j2` gains one line for the parent of all
versions, with today's options (tweed's values shown):

```
/srv/nfs/rpi/versions 10.21.0.1/16(ro,sync,no_subtree_check,no_root_squash)
```

All versions are on one filesystem. An NFSv3 client may mount a subdirectory
of an export, so each board mounts `versions/<id>/root` with no `exportfs` per
version. The VM CI's first Pi boot proves the subdirectory mount. `staging/`
and `site-state/` are outside the export.

File handles stay valid across the `current` swap. A knfsd file handle holds
an fsid that identifies the export point, plus the identity of the file's
inode (`fs/nfsd/nfsfh.h` `mk_fsid`; `fs/nfsd/nfsfh.c` looks the export up from
`fh_fsid`). Boards mount `versions/<id>/root` by its explicit path, never
through `current`. Swapping the symlink, adding a version or renaming
`staging/<id>` into `versions/` changes neither the export point nor any inode
a board holds.

The same fact gives the one hard rule: **an export line that a booted board
mounted through must never be removed.** Removing it makes that board's whole
mount stale at once. So the two legacy lines (`<legacy>/boot`, `<legacy>/root`)
stay in the template for as long as the legacy root directory exists.

## TFTP

On a versioned site `tftp_root` is `/srv/nfs/rpi/current/boot`.
`ansible/roles/pxe/templates/dnsmasq-base.conf.j2:31` needs no edit.

How dnsmasq treats the symlink, read from the dnsmasq v2.91 source (a GitHub
mirror of the upstream tag) and man page:

- Verified in `src/tftp.c`: `tftp-root` is kept as a string. For each request
  the path is built as prefix plus file name and passed to `open()`. There is
  no `realpath()` and no cached directory handle, so the kernel resolves
  `current` on every request. The only escape check is a search for `/../` in
  the path.
- Verified in `src/dnsmasq.c`: at start dnsmasq only checks that it can
  `opendir()` the root and exits if it cannot (unless `tftp-no-fail`). So
  `current` must exist before dnsmasq restarts. The layout step creates it
  before the `pxe` role runs: it names the legacy root if there is one, else
  `empty/`.
- Verified in `src/tftp.c` and the man page: `tftp-secure` checks the owner of
  the opened file. It is not set (`dnsmasq-base.conf.j2:30-31`).
- Verified: a transfer keeps its file descriptor, so one file always comes
  whole from one version.
- Assumed, to be proven by the VM CI when the layout is turned on there: the
  dnsmasq version on Debian 13 behaves the same, and nothing else on the
  gateway (for example an AppArmor profile) stops dnsmasq following the link.

**Residual race.** A board that is part-way through fetching its boot files
when `current` swaps can get files from two versions. Its cmdline names one
root:

- The old root. If the board snapshots the marker before `publish` has
  written the new one into that version (C2 step iii), it sees the change and
  reboots in its slot. If it snapshots after, it stays on the old version
  until the next publish or its next reboot. `verify-pi.yml` reports it.
- The new root with the old kernel. It runs mismatched until its next reboot.
  That only matters when the kernel changed between the two versions. A person
  or the fleet watchdog (infra #88) power-cycles it.

Nothing in this design avoids the race. The window is the few seconds of one
board's TFTP phase, and boards reboot for a new version at least 420 s after
the swap.

## Contract with nfsroot-watchdog (owned by the nfsroot-watchdog repo)

This section is for the nfsroot-watchdog session to check. That repo specifies
and implements `publish`, `rollback`, `inhibit`/`uninhibit` and `list`. This
spec does not design their flags.

- **C1. The client is unchanged.** It reads the lock, the marker and the fleet
  inhibit as `$LOWER$FILE`: plain NFS, not through the overlay, so from the
  version directory it booted. `nfsroot-watchdog-arm` snapshots
  `head -n1 $LOWER/etc/nfsroot-watchdog/generation` to `/run` at boot. The
  check reboots when the current content differs from the snapshot (string
  inequality), staggered from the marker's first field. That field stays an
  epoch: `<epoch> <iso> <free text>`. The free text can carry the version id.
- **C2. `publish` makes every version hold the same new marker, in this
  order:** (i) write the marker into the new version while `current` does not
  name it yet; (ii) swap `current`; (iii) write the same marker into every
  older version. A board that boots the new version then snapshots the new
  marker and stays put. A board on any older version sees a change. If (i)
  came after the swap, a board booting in between would snapshot the old
  marker and reboot a second time for nothing. `rollback` is the same
  operation with a fresh marker written into all versions. Writes are
  tmp plus rename inside each version's `root/etc/nfsroot-watchdog/`. That
  directory is the one place in a published version that is ever written.
- **C3. The fleet inhibit is per version.** A board only sees
  `/etc/nfsroot-watchdog/inhibit` in the version it booted. It has to exist in
  every version, and `publish` has to carry it into the new one.
  `inhibit`/`uninhibit` do that. Operators use them, not `touch`.
- **C4. `publish` creates `root/etc/nfsroot-watchdog/` in the new version.**
  Today img's `--exclude=/etc/nfsroot-watchdog/` protects those files from the
  in-place rsync. With a fresh directory per version there is nothing to
  protect. The CI image ships none of these files, and that stays so.
- **C5. Deleting a version is self-healing but not harmless.** A board whose
  version directory is deleted gets ESTALE on its mount root, and
  nfsroot-watchdog reboots it after 2 confirming checks. That path bypasses
  the update lock and the fleet inhibit; only the per-machine inhibit holds
  it. Until the reboot the board cannot exec anything new.
- **C6. The stale-probe trigger never fires under this layout.** It stays for
  other sites. No action.
- **C7. `publish` replaces `begin`/`end` on versioned sites.** Nothing changes
  under a booted board, so no client-visible lock is needed, and an
  `update.lock` inside a published version would only block boards.
  `begin`/`end`/`scan`/`run` stay in the package for single-root sites. Two
  concurrent runs are a gateway-local problem: `publish`, `rollback` and
  `inhibit` take a `flock` on a file beside the version directories, never
  inside one.
- **C8. Migration.** A board on today's in-place root has
  `/srv/nfs/rpi/bookworm/root` as its lower. The first versioned `publish`
  must write the marker there too, or those boards never reboot. The legacy
  root is "version 0" and counts as an older version in C2 (iii) for as long
  as its directory exists.
- **C9. Today's wiring.** `roles/nfsroot_generation/tasks/install.yml`
  installs the server package in `site.yml`'s first play. `begin.yml` takes the
  lock. `main.yml` is `end.yml`. On a versioned site the publish step replaces
  the two includes in the "Update the Pi NFS root" play. `install.yml` stays.

What this repo promises in return:

- Everything that writes the root (the img extraction, `apt_cache`'s
  `nfsroot.yml`, fixpi's site layer, the site-state copy) writes the staging
  directory, before `publish`. Nothing here touches a published version
  afterwards.
- `publish` is handed a complete tree that is already at its final path
  `versions/<id>` (step 9), plus the legacy root's path while it exists.

## Rollback

nfsroot-watchdog's `rollback` swaps `current` back to a kept version and
writes a fresh marker into all versions. Boards on the bad version reboot onto
the good one in their slots. Boards already on the good one reboot once too:
they have no way to know. No image pull and no site-layer run is needed. A
later `site.yml` run with the same inputs would build the bad tree again and
publish it. So before the next run the cause has to be fixed, or the run has
to name an older image with `-e img_nfsroot_image=...`.

## Cleaning up old versions by hand

There is no automatic deletion. The procedure:

1. See what exists and what is current: `ls /srv/nfs/rpi/versions` and
   `readlink /srv/nfs/rpi/current` (or nfsroot-watchdog's `list`).
2. Find out what the boards run. `verify-pi.yml` asserts that each board's
   booted `nfsroot=` is the current version, so a green fleet run means no
   reachable board runs an older one. A board that is off does not matter: it
   boots `current`.
3. Delete with `rm -rf /srv/nfs/rpi/versions/<id>`. Never the one `current`
   names. Keep the one before it as the rollback target.

Only delete versions nobody runs. A mistake heals itself (C5), but the board
is broken until its reboot.

## Migration on tweed

Before: every PR in "Implementation order" up to the VM test is merged, the
nfsroot-watchdog release with `publish` is installed on tweed, and the VM CI
is green with the layout on.

1. Check on tweed: `/srv/nfs/rpi` is one filesystem with at least 8 GB free;
   the names `versions`, `current`, `staging`, `site-state` and `empty` are
   free; no `update.lock`; note which boards hold a per-machine inhibit.
2. Merge the PR that sets `nfsroot_versioned: true` for `fpgas.online`. Run
   the whole `site.yml` with `--limit fpgas.online`.
3. That run, in order:
   - creates the layout, with `current -> bookworm` (the legacy root);
   - adds the `versions` export and keeps the two legacy lines;
   - restarts dnsmasq with `tftp-root=/srv/nfs/rpi/current/boot`, which still
     resolves to the legacy `boot/`, so nothing changes for a booting board;
   - seeds `site-state/` from `bookworm/root`;
   - builds `staging/<id>`, finds it differs from the legacy root (the cmdline
     path at least), renames it into `versions/`;
   - `publish`: marker into the new version, `current` swapped, the same
     marker into `bookworm/root/etc/nfsroot-watchdog/` (C8).
4. Every board is on the legacy root. Each sees the marker change and reboots
   in its slot (420 s plus 20 s per slot, about 40 minutes for both switches)
   onto `versions/<id>`. The legacy root was not modified apart from the
   marker, so nothing goes ESTALE while they wait.
5. The operator checks: `readlink /srv/nfs/rpi/current`; `exportfs -v` shows
   three lines; `verify-server.yml` passes; after the stagger, `verify-pi.yml`
   passes, which includes the booted `nfsroot=`; the Pi host key fingerprint
   is the one in `docs/access.md`. Boards with a per-machine inhibit stay on
   the legacy root until they reboot.
6. If it goes wrong: `rollback` to version 0 swaps `current` back to
   `bookworm`. The legacy exports were never removed.
7. The legacy root and its two export lines are removed by hand, and only
   once no board is booted from it (procedure above). Delete the directory,
   then run `site.yml`: the template drops the legacy lines when the directory
   is gone. Until then every `publish` keeps writing the marker there.

## Changes by file

| file | change |
|---|---|
| `ansible/inventory/group_vars/all/srv.yml:15,23` | new `nfsroot_versioned` (false), `nfsroot_base_dir`, `nfsroot_served_dir`, `nfsroot_legacy_dir`, `nfsroot_mount_dir`, `nfsroot_min_free_gb`; on a versioned site `nfs_root` is `<base>/current` and `tftp_root` is `<base>/current/boot` |
| `ansible/site.yml:13-16` | add the layout step (new role, `layout.yml`) after the install |
| `ansible/site.yml:64-110` | play `vars:` for `nfs_root` and `nfsroot_mount_dir`; the begin and publish steps replace the includes at 83-86 and 108-110 on a versioned site |
| `ansible/roles/nfsroot_version/` (new) | `layout.yml` (directories, first `current`, same-filesystem and free-space checks), `begin.yml`, `state.yml`, `main.yml` (compare, checks, rename, `publish`) |
| `ansible/roles/img/tasks/pull.yml` | split after the digest (line 50); the comments at 7-10 and 73-101 describe the in-place rsync and are rewritten; the rsync itself is unchanged |
| `ansible/roles/apt_cache/tasks/nfsroot.yml` | none |
| `ansible/roles/fixpi/templates/boot/cmdline.txt.j2:1`, `cmdline-pi5.txt.j2:1`, `default-arm-sunxi.j2:18`, `templates/etc/fstab.j2:2-3` | `nfs_root` becomes `nfsroot_mount_dir` |
| `ansible/roles/fixpi/tasks/userconf.yml:121` | hardcoded `/srv/nfs/rpi/{{ dist }}` becomes `{{ nfs_root }}`; the comment at 36-57 about a replaced `shadow` no longer applies on a versioned site |
| `ansible/roles/fixpi/tasks/sunxi.yml:27-80` | write to the boot directory being built, not `tftp_root` |
| `ansible/roles/fixpi/tasks/netboot.yml:8-22` | the `/srv/tftp` links go through `current` |
| `ansible/roles/fixpi/tasks/verify/main.yml:94` | expect the resolved version path in the sunxi PXE config |
| `ansible/roles/nfs/tasks/main.yml:13-21`, `templates/exports.j2:3-4`, `tasks/verify/main.yml:45-58` | create and export `versions/`; keep the legacy lines while the legacy directory exists |
| `ansible/roles/pxe` | none (`tftp_root` changes value) |
| `ansible/roles/nfsroot_generation` | `begin.yml`/`end.yml` only on single-root sites; README rewritten (`touch .../inhibit` becomes the `inhibit` subcommand) |
| `ansible/verify-server.yml:193-204`, `ansible/verify-pi.yml` | see "Verification" |
| `tests/vm/run_tests.py:462`, `tests/inventory/host_vars/test-vm.yml` | the hardcoded `/srv/nfs/rpi/bookworm/root` goes through `current`; the layout is turned on |
| `tests/test_nfsroot_version.py` (new) | unit tests of the new role against temporary directories |
| `docs/access.md:157-176`, `CLAUDE.md`, `ansible/roles/nfsroot_generation/README.md` | describe the layout |

Hardcoded root paths that need no change: `ansible/roles/site/tasks/snmp.yml:54-58`
(legacy MAC-table sites only, which stay single-root);
`ansible/inventory-ci-nfsroot/hosts:13`, `ansible/roles/img/files/img2files.sh:11`,
`tests/ci/nfsroot_publish.py:44` and `.github/workflows/nfsroot-build.yml:121,158`
(the CI image build, on the runner); `tests/vm/cloud_init.py:73` (a fixture
for a retired account).

The cmdline and PXE template edits are generic, not per site, and stay in
`fixpi/tasks/netboot.yml` and its templates.

Conventions for the new code: variables a role defines start with the role
name (`nfsroot_version_...`); `register` and `set_fact` results are
`__nfsroot_version_<name>`; shared inventory variables keep the short
`nfsroot_` prefix and are never defined inside a role; no tags; no per-Pi
variables; no `{{ role_path }}/../other` reads. This spec uses today's names.
The planned renames (nfs to nfs_server, pxe to dnsmasq, fixpi split into
nfsroot_site and nfsroot_netboot, img to nfsroot_image, dist to
raspios_release, nfs_root to nfsroot_dir, nbp to gateway, pig to web) come
after this work and rebase over it.

## Verification

`verify-server.yml`, on a versioned site:

- `current` is a symlink that resolves to a directory holding `boot/` and
  `root/`.
- Every version's two cmdline files name that version's own `root`.
- Every version, and the legacy root while it exists, holds the same marker.
- No version holds an `update.lock` (today's check at lines 193-204, per
  version).
- `staging/` is empty: a leftover means the last `site.yml` run did not
  finish.
- `/etc/exports` and `exportfs -v` have the `versions` line.

The existing checks keep reading `{{ nfs_root }}`, which is `current`.

`verify-pi.yml`: the booted `nfsroot=` (already collected from
`/proc/cmdline`) is the gateway's current version. After a publish this fails
for a board until its slot has passed, with a message naming the version it
runs.

## Testing in the VM CI

Budget, measured by the CI session on current main: the VM job takes
709-944 s, median 828 s. The target is 15 minutes for the whole run, and 17 of
the last 44 runs already exceed it. On the emulated Pi, power-on to kernel is
12-17 s, kernel to SSH 75-97 s, and each SSH round trip 3-12 s. A second real
pull and extract on the server is 1-2 minutes or more, so the test never does
one.

Free, with the layout on: the first converge builds and publishes a version
through the real path, and the Pi's first boot fetches its boot files through
`current`, mounts `versions/<id>/root` as a subdirectory of the export, and
`verify-pi.yml` asserts it.

Added, after `verify-pi.yml` has passed on version A:

1. On the server, make version B from A with `cp -al` (hard links, seconds),
   replace the four path-carrying files and one test file by rename, and
   `publish` it. Hard links are safe here only because the test replaces files
   and never edits one.
2. One exec on the booted Pi (one round trip): read a file B replaced, run a
   binary, and `nfsroot-watchdog status`. It must show no ESTALE and a pending
   reboot.
3. Power-cycle the Pi from the harness, as a PoE cycle would. Wait for the
   second "Booting Linux" on the serial console (`wait_for_pi_boot` in
   `tests/vm/run_tests.py` already watches it), not for SSH. That it booted B
   is read on the server, from rpc.mountd's log line for
   `versions/<B>/root`. Whether mountd logs it at Debian 13's default level
   is to be confirmed in that PR.
4. While the Pi reboots, one server-side script checks that `current` names B,
   all markers are equal and TFTP serves B's cmdline. Once step 3 has seen the
   mount of B, it runs `rollback` to A and repeats the checks. The Pi is not
   rebooted again.

Estimate: 45-80 s, against 72 s of room at the median. The PR that adds the
test must state measured before and after totals. If it does not fit, step 3
is dropped first (step 4's TFTP fetch shows what a rebooting board would get),
and next the first boot is restructured so that B is the only boot
`verify-pi.yml` checks.

The compare, the staging clean-up, the site-state seeding and the free-space
guard are unit-tested in the pytest job, outside the VM budget.

## Implementation order

Each PR is mergeable alone, with green CI and `nfsroot_versioned` off.

1. Fix `fixpi/tasks/userconf.yml:121`.
2. The inert variables and the layout step.
3. The staging build: the img split, the play vars, the template and writer
   edits, site state, the compare. With the variable on, a version is built
   and then left unpublished.
4. Exports.
5. TFTP through `current`.
6. Publish wiring. **Blocked on the nfsroot-watchdog release with `publish`.**
7. `verify-server.yml` and `verify-pi.yml` checks.
8. The VM test, and the layout turned on for the CI VM. Needs 6, and `rollback`.
9. tweed cut-over (the host_vars change and the migration above). Needs
   `inhibit`/`uninhibit` and `list` as well.
10. Docs: `docs/access.md`, `CLAUDE.md`, the role README.

## Risks: other open PRs on the same files

- **#45** (netboot clock) adds a timer on the gateway that rewrites
  `fake-hwclock.data` inside the NFS root. That is a second writer into a
  published version. See open question 3.
- **#88** (fleet watchdog) must skip boards older than the marker, so that it
  does not power-cycle a fleet whose SSH broke after an update. Under this
  layout an update no longer breaks SSH on a booted board, so that rule gets
  simpler.
- **#37** (trixie) changes `dist` in `srv.yml` and edits
  `fixpi/tasks/netboot.yml`. With no `dist` in the path it becomes the next
  version. `nfsroot_legacy_dir` is a literal path, so it does not move.
- **#55** (config.txt single owner) edits `fixpi/tasks/netboot.yml`,
  `tweeks.yml` and `verify-server.yml`.
- **#58** (gateway API role) edits `site.yml` and `verify-server.yml`.
- **#107** (`docs/ci.md`): sections 3.2, 6.4 and 8 describe the in-place
  rsync. Whichever lands second updates them.

## Open questions

1. Should boards report their booted version through fleet self-registration
   (site `/fleet/<serial>/`, repos fpgas.online-site and
   fpgas.online-setup-pi)? It is already in `/proc/cmdline`. It is optional
   now that clean-up is manual. Who owns it?
2. For the nfsroot-watchdog session:
   - Is the boundary right: this repo renames the built tree to
     `versions/<id>` and then calls `publish`, or should `publish` do the
     rename?
   - How does `publish` learn the legacy root's path (C8)?
   - Can the VM test make a board's reboot slot due at once without changing
     the image? If not, the harness power-cycles the Pi, and the
     watchdog-triggered reboot is covered only by that repo's own tests.
3. #45 writes into the root on a timer. Does it change to fit this layout
   (how?), or does "immutable" get a second exception? A file replaced in a
   published version goes ESTALE for boards that read it through the overlay.
