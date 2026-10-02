# Versions of the fleet NFS root: one directory per version

Status: draft for review, 2026-10-03. Nothing here is implemented.
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

This is overlayfs behaviour, not NFS caching. In Linux v6.12
`fs/overlayfs/super.c`, `ovl_revalidate_real()` maps a lower
`d_revalidate() == 0` (NFS saying "this name is now another file") to
`-ESTALE`. The VFS only looks a name up again on 0, so the overlay dentry stays
tied to the dead lower dentry until reboot. On a plain NFS mount the same
rename heals on the next lookup. The kernel documents the rule:
[changes to underlying filesystems](https://www.kernel.org/doc/html/v6.12/filesystems/overlayfs.html#changes-to-underlying-filesystems)
of a mounted overlay "are not allowed".

So NFS caching options and NFSv4 only change when NFS notices; overlay options
(`index`, `xino`, `redirect_dir`, `metacopy`) do not allow lower changes
either; keeping replaced inodes alive helps open files but not path lookups;
and dropping the overlay would let a running board mix old and new packages.
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
4. A board's kernel, initramfs, DTBs and `/lib/modules` come from one version.
   The one exception is a board that fetches its boot files across a swap
   (see "Booting during a swap").
5. A converge that changes nothing publishes nothing and reboots nobody.
6. nfsroot-watchdog keeps working. Its client gains one backward-compatible
   rule.

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
- **immutable**: once a tree is in `versions/`, nothing under it is written,
  with one exception: `root/etc/nfsroot-watchdog/`, which only
  nfsroot-watchdog's server command writes (see the contract).
- **legacy root**: today's in-place tree `/srv/nfs/rpi/bookworm` on tweed. It
  is "version 0". After the cut-over it is as immutable as any other version.

## Layout on the gateway

```
/srv/nfs/rpi/
  versions/                        # the one NFS export versioned boards use
    20261005T013000Z-b41ea5c743cf/
      boot/                        # TFTP payload; its cmdline names THIS version's root
      root/                        # the NFS lower layer
      .image-digest                # written by img, as today
    20261012T020000Z-0c9d1e2f3a4b/
    legacy-bookworm -> ../bookworm # tweed only: the legacy root as an entry
  current -> versions/20261012T020000Z-0c9d1e2f3a4b   # swapped with rename(2)
  staging/<id>/{boot,root}         # a version being built; not exported
  staging.lock/                    # exists while a build runs (mkdir is atomic)
  site-state/root/...              # files that must survive across versions; mode 0700
  empty/{boot,root}                # fresh gateway only: what current names before the first publish
  bookworm/{boot,root}             # tweed only: the legacy root
```

nfsroot-watchdog also keeps its `flock` file beside the version directories;
its name is that repo's choice.

There is no `dist` in the path. A version id starts with a digit, so the name
`legacy-bookworm` cannot collide with one.

`staging/` is on the same filesystem as `versions/`, so moving a built tree
into `versions/` is a `rename(2)`, not a copy. The role asserts this (same
`st_dev`) before it builds.

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

## Variables

`<base>` is `/srv/nfs/rpi`. "Build play" is the "Update the Pi NFS root" play
(`ansible/site.yml:64-110`).

| variable | set in | single-root site | versioned site |
|---|---|---|---|
| `nfsroot_versioned` | inventory (`srv.yml`; host_vars to turn on) | `false` | `true` |
| `nfsroot_base_dir` | inventory (`srv.yml`) | `<base>` | `<base>` |
| `nfsroot_served_dir` | inventory (`srv.yml`) | `<base>/{{ dist }}` | `<base>/current` |
| `nfs_root` | inventory (`srv.yml`); build play `vars:` | `{{ nfsroot_served_dir }}` everywhere | `{{ nfsroot_served_dir }}`; in the build play `<base>/staging/<id>` |
| `tftp_root` (what dnsmasq serves) | inventory (`srv.yml`) | as today (`srv.yml:23`) | `{{ nfsroot_served_dir }}/boot` |
| `nfsroot_boot_build_dir` (where `sunxi.yml` writes) | inventory (`srv.yml`) | today's `tftp_root` expression | `{{ nfs_root }}/boot`, so the staging tree in the build play |
| `nfsroot_mount_dir` (the path a board mounts) | build play `vars:`; `inventory-ci-nfsroot` for the image build | `{{ nfs_root }}` | `<base>/versions/<id>`; **undefined** outside the build play |
| `nfsroot_legacy_dir` | inventory (`srv.yml`), a literal | unused | `<base>/bookworm`; used only if it exists |
| `nfsroot_versions_min_free_gb`, `nfsroot_versions_legacy_name`, `nfsroot_versions_ignore` | role defaults | unused | `8`, `legacy-bookworm`, `[]` |

`nfs_root` appears 188 times in the YAML and templates under `ansible/` (100
of them in `roles/fixpi`), nearly all as `{{ nfs_root }}/root/...` or
`{{ nfs_root }}/boot/...`. They are not edited: `nfs_root` means "the tree this
play works on". Play vars outrank inventory vars and end with the play, so the
staging path cannot leak into a later play. A play var cannot refer to the
inventory variable of the same name, which is why the served path has its own
name.

`nfsroot_mount_dir` is deliberately not defined in the shared inventory. On a
versioned site its only correct value needs the id, and `<base>/current/root`
is not an exported path and must never be baked into a cmdline. A template
rendered outside the build play fails instead.

The four templates that carry the path (cmdline, cmdline-pi5, the sunxi PXE
config, fstab) use `nfsroot_mount_dir`. So each version's boot files name its
own root, never `current`: a board must stay on the tree it booted.

## Building a version

### The steps

The build play keeps its order. On a versioned site:

1. Download the GitHub keys (`fixpi/tasks/github_keys.yml`, unchanged, still
   first: a GitHub outage stops the run before anything is built).
2. `img`: wait for the background pull, pull, read the digest
   (`pull.yml:21-50`). The digest is needed to name the version, so
   `pull.yml` is split after it.
3. New role `nfsroot_versions`, begin: take the build lock (`mkdir
   <base>/staging.lock`), delete anything left under `staging/`, choose the
   id, create `staging/<id>/`.
4. `img`: extract into `{{ nfs_root }}`, which is the empty staging directory.
   The rsync command is unchanged. Its `--delete` and its four excludes
   (`pull.yml:109-110`) have nothing to act on.
5. Copy site state into the staging tree (see "Site state").
6. `apt_cache` `nfsroot.yml` and `fixpi`, with the edits in "Changes by file".
7. Compare the staging tree with `current` (next section). If they are the
   same, delete the staging tree, release the lock and stop.
8. Check the built tree. `root/bin/bash`, `root/etc/os-release` and
   `boot/kernel8.img` exist. `.image-digest` is the pulled digest. Both
   cmdline files, `root/etc/fstab` and (on a site with `sunxi_boards`) the
   sunxi PXE config name `versions/<id>`. The host keys and pi's
   `authorized_keys` are present. A tree that fails is never served.
9. Rename `staging/<id>` to `versions/<id>`.
10. Call nfsroot-watchdog's `publish <id>`. It swaps `current`. The task
    retries: `publish` is idempotent (C7).
11. Release the build lock.

Steps 3 to 11 sit in one block whose `always:` removes `staging.lock`, so a
failed run does not leave it. A killed run does. Step 3 then fails with a
message that says so and how to clear it (`rmdir`, once no other `site.yml`
is running). Without the lock, a second overlapping run would delete the
first run's tree while it is being built.

Step 3 replaces `nfsroot_generation`'s `begin.yml` and step 10 replaces its
`end.yml` (the two includes at `site.yml:83-86` and `site.yml:108-110`). Both
stay for single-root sites.

### When a new version is made

Every run builds into staging and then compares the result with `current`,
for `boot/` and `root/`: file type, content, mode, owner, group, symlink
target, device numbers, ACLs, xattrs (which hold file capabilities) and
hard-link grouping. That is an rsync dry run with the extraction's `-HAX`
flags and `--checksum`, without `-t`. Modification times are not compared,
because every file Ansible writes into a fresh tree has a new one.
`root/etc/nfsroot-watchdog/` is ignored. The four files that carry the version
path are compared after each tree's own id is replaced by a fixed token. If
nothing differs, nothing is published and no board reboots. On a fresh gateway
(`current` names `empty`) the compare is skipped.

This replaces `nfsroot_generation_bump` and the ctime scan's ignore list
(`ansible/roles/nfsroot_generation/defaults/main.yml:24-32`) on a versioned
site:

- `auto` is the only mode: publish if and only if the trees differ.
- `never` has no equivalent. To converge without rebooting anyone, set the
  fleet inhibit first (`inhibit`, C5).
- `always` has no equivalent in this role. Rebooting the fleet with no change
  is an nfsroot-watchdog operation, if it is wanted at all.
- The package's ignore list (`tmp`, `var/cache`, `root/.ansible`, ...) exists
  because the in-place root collects run-time debris. Two fresh builds do not,
  so the compare ignores nothing else. `nfsroot_versions_ignore` is there for
  a site that finds it needs an entry.

Cost. Today a run with an unchanged image digest skips the extraction
(`pull.yml:52-65`), runs the site-layer tasks as no-ops, and
`nfsroot-generation end` walks the tree once comparing ctimes. A run with a
new digest reads both trees once (`--checksum`). Under this design **every
tweed `site.yml` run extracts about 5 GB and then reads two trees**. That is a
little more than today's new-digest run, on every run. It has not been
measured; the cut-over PR must report it. In the VM CI the first converge
already extracts, and the compare is skipped, so the cost there does not
change.

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
files are copied into the staging tree, keeping owner and mode, so fixpi's
create-if-absent tasks find them and do nothing. After fixpi runs, any of
those files that `site-state/` does not have yet is copied back. On the first
versioned run, `site-state/` is seeded from the legacy root if there is one,
otherwise from what fixpi generated (a fresh gateway).

If seeding from the legacy root were skipped on tweed, fixpi would generate new
keys and the fleet's host key would change, noticed only after the fleet had
rebooted. So the build checks it: while `nfsroot_legacy_dir` exists, the
ed25519 host public key in `site-state/` must equal the legacy root's, or the
run fails before step 9.

`site-state/` holds private keys: it is mode 0700 and outside the export. The
same private keys also sit in every exported version, as they sit in the
exported root today. That is no new exposure.

### Failure handling

- Anything that fails before step 10 leaves `current` and every published
  version untouched. No board notices.
- A half-built `staging/<id>` is deleted by step 3 of the next run.
- A `publish` that died is repaired by running the same command again (C7).
  Step 10 retries, and the next `site.yml` run is not needed for the repair.
- A tree that reached `versions/` but was never published (a failure between
  steps 9 and 10 that the retry did not fix) has no marker and no board can
  boot it. It is not deleted automatically. `verify-server.yml` lists such
  entries and leaves them out of its marker check; nfsroot-watchdog's `list`
  shows them as never published. A person deletes them, or publishes them
  with `publish <id>`.
- The free-space guard: the layout step fails when the filesystem holding
  `<base>` has less than `nfsroot_versions_min_free_gb` free (default 8: one
  version plus headroom). It runs in `site.yml`'s first play, before the
  background image pull starts. It counts one staging tree; it does not count
  podman's image store. The message gives the free space, the need, and the
  manual clean-up procedure.

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
stay in the template for as long as the legacy root directory exists. Boards
on the legacy root mount it by its own path, not through the
`versions/legacy-bookworm` link.

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
  before the `pxe` role runs: it names `versions/legacy-bookworm` if there is
  a legacy root, else `empty/`.
- Verified in `src/tftp.c` and the man page: `tftp-secure` checks the owner of
  the opened file. It is not set (`dnsmasq-base.conf.j2:30-31`).
- Verified: a transfer keeps its file descriptor, so one file always comes
  whole from one version.
- Assumed, to be proven by the VM CI when the layout is turned on there: the
  dnsmasq version on Debian 13 behaves the same, and nothing else on the
  gateway (for example an AppArmor profile) stops dnsmasq following the link.

### Booting during a swap

A board that boots while `publish` runs can end up on the old version. It only
has to fetch the old `cmdline.txt` before the swap: no mixed files are needed.
The window is from its cmdline fetch to the moment the watchdog arms, about
40-90 s per booting board. It happens when someone PoE-cycles a board during a
deploy, or when a second publish lands inside the stagger of the first (about
39 minutes for two switches).

With today's client such a board would arm after `publish` had already written
the new marker into the old version, snapshot it, and never see a change. The
version rule in the contract (C2) closes this whatever the timing: the board
reads its own version name and a marker that names another, at arm time or at
any later check, and reboots in its slot. Until a version carries a client
with that rule, the window stays open for that version. That is safe (no
ESTALE) and `verify-pi.yml` reports it.

A board can also fetch files from two versions: the old kernel with the new
root, or the new kernel with the old root. Either runs with a kernel that does
not match `/lib/modules`, which only matters when the kernel changed between
the two versions. The second case reboots by the version rule. The first is on
the current version and stays until a person or the fleet watchdog
(infra #88) power-cycles it. Nothing in this design avoids fetching across a
swap. No board reboots for a new version sooner than 300 s after the swap
(C4).

## Contract with nfsroot-watchdog (owned by the nfsroot-watchdog repo)

This section is for the nfsroot-watchdog session to check. It records that
repo's decisions as of 2026-10-03 (its main `1e72776`). That repo specifies and
implements `publish`, `rollback`, `inhibit`/`uninhibit` and `list`. This spec
does not design their flags.

- **C1. The client gains one backward-compatible rule (C2) and is otherwise
  unchanged.** It reads the lock, the marker and the fleet inhibit as
  `$LOWER$FILE`: plain NFS, not through the overlay, so from the version
  directory it booted. `nfsroot-watchdog-arm` snapshots
  `head -n1 $LOWER/etc/nfsroot-watchdog/generation` to `/run` at boot. A
  version with no marker file snapshots as "none". An unreadable marker is
  never treated as a change. The client package installs nothing under
  `/etc/nfsroot-watchdog`, so the CI image ships none of these files, and that
  stays so.
- **C2. Each version says who it is, and the marker says who is current.**
  `publish` writes `root/etc/nfsroot-watchdog/version` holding the entry's
  name in `versions/` (the id; for the legacy entry, the symlink's name). It
  is written once, before `current` names the version, and never changed. The
  marker is `<epoch> <iso> <id of the current version>`: one line, first field
  still the epoch, so old clients and the stagger are unaffected. Client rule:
  if `$LOWER/etc/nfsroot-watchdog/version` exists, the trigger is "the
  marker's third field is not my version" instead of "the marker differs from
  my boot snapshot". If the file does not exist (single-root sites,
  `begin`/`end`), the client behaves exactly as today. The client does not
  parse `nfsroot=` from `/proc/cmdline`.
- **C3. `publish <id>` makes every version hold the same new marker, in this
  order:** (i) create `root/etc/nfsroot-watchdog/` in the new version and
  write its version file and the marker, while `current` does not name it yet;
  (ii) swap `current`; (iii) write the same marker into every other entry.
  `publish` stamps the marker immediately before the swap, so its epoch is the
  swap time to within a second. With the version rule the order is no longer
  needed for correctness. It is kept because it is what keeps old clients
  right: a board that boots the new version snapshots the new marker (not
  "none") and stays put, and a board on an older version sees a change.
  `rollback` follows the same order: target first, swap, then the rest. All
  writes are tmp plus rename, or create/unlink, inside each entry's
  `root/etc/nfsroot-watchdog/`, never in place. That directory is the one
  place in a version that is ever written.
- **C4. Reboot timing.** A board's deadline is marker epoch + 420 s + slot x
  20 s + jitter (under 10 s), when its clock finds the epoch plausible (not
  more than 300 s in its future, less than 86400 s old). Otherwise it counts
  from the board's own now. It is never sooner than 300 s after the board
  notices. The check timer runs every 60 s.
- **C5. The fleet inhibit is per version.** A board only sees
  `/etc/nfsroot-watchdog/inhibit` in the version it booted. It has to exist in
  every entry, and `publish` carries it into the new one. `inhibit` and
  `uninhibit` do that. Operators use them, not `touch`.
- **C6. Which entries the commands act on.** Every entry of `versions/` that
  resolves to a directory containing `root/`. No argument and no config file
  names the legacy root: it is the symlink `versions/legacy-bookworm ->
  ../bookworm`, which this repo creates in the migration and removes with the
  legacy tree. `rollback` to that entry is allowed. `list` shows an entry with
  no marker as never published.
- **C7. `publish` replaces `begin`/`end` on versioned sites, and is
  idempotent.** Nothing changes under a booted board, so no client-visible
  lock is needed, and an `update.lock` inside a version would only block
  boards. `publish`, `rollback` and `inhibit` take a `flock` on a file beside
  the version directories, never inside one. Run again when `current` already
  names `<id>`, `publish` re-reads that version's marker and writes it into
  every entry that lacks it, with no new stamp. `begin`/`end`/`scan`/`run`
  stay in the package for single-root sites.
- **C8. Deleting a version is self-healing but not harmless.** A board whose
  version directory is deleted gets ESTALE on its mount root. After 2
  confirming checks the client schedules a reboot, staggered and warned like
  any other: now + 420 s + slot x 20 s + jitter, never sooner than now +
  300 s. So the board is broken for roughly 8 to 40 minutes, depending on its
  slot. That path bypasses the update lock and the fleet inhibit; only the
  per-machine inhibit holds it.
- **C9. Migration.** Boards on today's in-place root run today's client and
  have `/srv/nfs/rpi/bookworm/root` as their lower. They see the first
  versioned `publish` as a marker change by string inequality and reboot. The
  legacy entry gets the marker on every publish for as long as it exists.
- Informational: the stale-probe trigger never fires under this layout and
  stays for other sites. Today `roles/nfsroot_generation/tasks/install.yml`
  installs the server package in `site.yml`'s first play (it stays),
  `begin.yml` takes the lock and `main.yml` is `end.yml`.

The boundary, and what this repo promises:

- This repo renames `staging/<id>` to `versions/<id>`, then calls
  `publish <id>`. The rename needs no `flock`: ids are unique and an
  unpublished tree is inert. `publish` checks that `versions/<id>/boot` and
  `versions/<id>/root` exist.
- Everything that writes the root (the img extraction, `apt_cache`'s
  `nfsroot.yml`, fixpi's site layer, the site-state copy) writes the staging
  directory, before the rename. Nothing here touches a tree in `versions/`
  afterwards. That includes the legacy tree after the cut-over.
- No switch exists to make a reboot slot due at once, and none will be added
  for tests. The watchdog-triggered reboot itself is covered by that repo's
  own tests, not by the VM test here.

Release order in that repo: the client rule first (inert until a version file
exists), then `publish`, `rollback`, `list`, `inhibit`/`uninhibit`.

## Rollback

nfsroot-watchdog's `rollback` swaps `current` back to a kept version and
writes a fresh marker naming it into every entry. Boards on the bad version
reboot onto the good one in their slots. Boards already on the good one stay
put: the marker names their version again. Only a board running an old client
(no version rule) reboots once too. No image pull and no site-layer run is
needed.

Rollback to the legacy entry is allowed, and the first versioned publish is
when it is most likely. It works because nothing writes the legacy tree after
the cut-over, its cmdline names `/srv/nfs/rpi/bookworm/root`, and its export
lines are kept, so TFTP through `current/boot` and NFS both still serve it.

A later `site.yml` run with the same inputs would build the bad tree again and
publish it. So before the next run the cause has to be fixed, or the run has
to name an older image with `-e img_nfsroot_image=...`.

## Cleaning up old versions by hand

There is no automatic deletion. The procedure:

1. See what exists and what is current: `ls -l /srv/nfs/rpi/versions` and
   `readlink /srv/nfs/rpi/current` (or nfsroot-watchdog's `list`).
2. Check what the boards run before deleting anything. With the version rule
   a board cannot stay on an old version past its reboot slot unless it is
   inhibited or unreachable, so wait for the stagger to finish. Then run
   `verify-pi.yml`: it fails for any reachable board that is not on the
   current version. For boards it cannot reach, `showmount -a` on the gateway
   (`/var/lib/nfs/rmtab`) is a second source. It can list mounts that are long
   gone, so treat it as a hint. A board that is off does not matter: it boots
   `current`.
3. Delete with `rm -rf /srv/nfs/rpi/versions/<id>`. Never the one `current`
   names. Keep the one before it as the rollback target.

Only delete versions nobody runs. A mistake heals itself, but the board cannot
exec anything new for roughly 8 to 40 minutes (C8).

## Migration on tweed

Before:

- Every PR in "Implementation order" before the cut-over is merged, and the VM
  CI is green with the layout on.
- The nfsroot-watchdog server release with `publish`, `rollback` and
  `inhibit`/`uninhibit` is installed on tweed.
- The image tweed will publish as its first version contains the client
  release with the version rule. `ansible/roles/onpi/tasks/stale_root.yml:12-15`
  installs `nfsroot-watchdog` with `state: latest`, so an image built after
  that release has it. Check the package version in the image before the run.

1. Check on tweed: `/srv/nfs/rpi` is one filesystem with at least 8 GB free;
   the names `versions`, `current`, `staging`, `staging.lock`, `site-state`
   and `empty` are free; no `update.lock`; note which boards hold a
   per-machine inhibit.
2. Merge the PR that sets `nfsroot_versioned: true` for `fpgas.online`. Run
   the whole `site.yml` with `--limit fpgas.online`.
3. What is specific to this first run: the layout step creates
   `versions/legacy-bookworm -> ../bookworm` and `current ->
   versions/legacy-bookworm`. dnsmasq restarts with
   `tftp-root=/srv/nfs/rpi/current/boot`, which still resolves to the legacy
   `boot/`, so nothing changes for a booting board. `site-state/` is seeded
   from `bookworm/root`. The build differs from the legacy root (the cmdline
   path at least), so it is published. `publish` writes the marker into the
   legacy entry too (C9).
4. Every board is on the legacy root. Each sees the marker change and reboots
   in its slot onto `versions/<id>`: no sooner than 300 s after the swap, the
   last one about 39 minutes after it. The legacy root was not modified apart
   from `root/etc/nfsroot-watchdog/`, so nothing goes ESTALE while they wait.
5. The operator checks: `readlink /srv/nfs/rpi/current`; `exportfs -v` shows
   three lines; `verify-server.yml` passes; after the stagger, `verify-pi.yml`
   passes; the Pi host key fingerprint is the one in `docs/access.md:157-160`.
   Boards with a per-machine inhibit stay on the legacy root until they
   reboot.
6. If it goes wrong: `rollback` to `legacy-bookworm`.
7. The legacy root, its link in `versions/` and its two export lines are
   removed by hand, and only once no board is booted from it (procedure
   above). Delete the link and the directory, then run `site.yml`: the
   template drops the legacy lines when the directory is gone.

## Changes by file

| file | change |
|---|---|
| `ansible/inventory/group_vars/all/srv.yml:15,23` | the inventory variables in "Variables" |
| `ansible/inventory-ci-nfsroot/group_vars/all/all.yml` | `nfsroot_mount_dir: "{{ nfs_root }}"` for the image build |
| `ansible/site.yml:13-16` | add the layout step (new role, `layout.yml`) after the install |
| `ansible/site.yml:64-110` | play `vars:` for `nfs_root` and `nfsroot_mount_dir`; on a versioned site the begin and publish steps replace the includes at 83-86 and 108-110 |
| `ansible/roles/nfsroot_versions/` (new) | `layout.yml` (directories, the legacy link, first `current`, same-filesystem and free-space checks), `begin.yml`, `state.yml`, `main.yml` (compare, checks, rename, `publish`) |
| `ansible/roles/img/tasks/pull.yml` | split after the digest (line 50); the comments at 7-10 and 73-101 describe the in-place rsync and are rewritten; the rsync itself is unchanged |
| `ansible/roles/apt_cache/tasks/nfsroot.yml` | none |
| `ansible/roles/fixpi/templates/boot/cmdline.txt.j2:1`, `cmdline-pi5.txt.j2:1`, `default-arm-sunxi.j2:18`, `templates/etc/fstab.j2:2-3` | `nfs_root` becomes `nfsroot_mount_dir` |
| `ansible/roles/fixpi/tasks/userconf.yml:121` | hardcoded `/srv/nfs/rpi/{{ dist }}` becomes `{{ nfs_root }}`. Harmless today; with a staging build it would chown the legacy root and leave the new version's `.ssh` owned by root, so pi's key login would fail. The comment at 36-57 about a replaced `shadow` no longer applies on a versioned site |
| `ansible/roles/fixpi/tasks/sunxi.yml:27,36,47,66,67,80` | `tftp_root` becomes `nfsroot_boot_build_dir` |
| `ansible/roles/fixpi/tasks/netboot.yml:8-22` | the `/srv/tftp` links name `{{ nfsroot_served_dir }}/boot`, not `{{ nfs_root }}/boot`, which would point into `staging/`. On a fresh gateway the target does not exist yet (`current -> empty`), so the link task needs `force: true`, which it does not have today |
| `ansible/roles/fixpi/tasks/verify/main.yml:94` | expect the resolved version path in the sunxi PXE config |
| `ansible/roles/nfs/tasks/main.yml:13-21`, `templates/exports.j2:3-4`, `tasks/verify/main.yml:45-58` | create and export `versions/`; keep the legacy lines while the legacy directory exists |
| `ansible/roles/pxe` | none (`tftp_root` changes value) |
| `ansible/roles/nfsroot_generation` | `begin.yml`/`end.yml` only on single-root sites; README rewritten |
| `ansible/verify-server.yml:193-204`, `ansible/verify-pi.yml` | see "Verification" |
| `tests/vm/run_tests.py:462`, `tests/inventory/host_vars/test-vm.yml` | the hardcoded `/srv/nfs/rpi/bookworm/root` goes through `current`; the layout is turned on |
| `tests/test_nfsroot_versions.py` (new), `.github/workflows/lint.yml` | see "Testing" |
| `docs/access.md:157-176`, `CLAUDE.md`, `ansible/roles/nfsroot_generation/README.md` | describe the layout and the `inhibit` command |

The cmdline and PXE template edits are generic, not per site, and stay in
`fixpi/tasks/netboot.yml` and its templates.

Conventions for the new code: variables a role defines start with the role
name (`nfsroot_versions_...`); `register` and `set_fact` results are
`__nfsroot_versions_<name>`; shared inventory variables keep the short
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
- Every real version directory's cmdline files name that version's own `root`.
- Every entry that has a marker holds the same marker, the legacy entry
  included. Entries with no marker (never published) are listed, not failed.
- No entry holds an `update.lock` (today's check at lines 193-204, per entry).
- `staging/` is empty and `staging.lock` is gone: a leftover means the last
  `site.yml` run did not finish.
- `/etc/exports` and `exportfs -v` have the `versions` line.

The existing checks keep reading `{{ nfs_root }}`, which is `current`.

`verify-pi.yml`: the board's `$LOWER/etc/nfsroot-watchdog/version` equals the
third field of the marker it reads in the same directory. Both are read on the
Pi, in the existing collector call, so the playbook needs no lookup on the
gateway and does not parse `nfsroot=`. When the board's root has no version
file (a single-root site) the check is skipped.

This changes the deploy routine. After a publish, `verify-pi.yml` fails for
every board that has not reached its slot yet, for up to about 39 minutes.
Run `verify-server.yml` straight after `site.yml`, and `verify-pi.yml` once
the stagger has finished. The publish step prints when the last slot ends.

## Testing

### In the pytest job (outside the VM budget)

- **Build twice.** Run the real build chain (begin, site state,
  `apt_cache` `nfsroot.yml`, fixpi, compare, rename, publish) twice against a
  temporary `<base>`, with a tiny fixture tree in place of the img extraction
  (`podman image mount` needs root). Assert that the second run publishes
  nothing and that no inode under version A changed. No VM test runs a second
  converge, so this is the only test of "an unchanged converge publishes
  nothing" and of "no writer escapes the staging redirect". A false difference
  would reboot the whole fleet on every tweed converge.
- **Static.** In the style of `tests/test_no_tags.py`: no task in the build
  play's roles uses `tftp_root`, `nfsroot_served_dir` or a literal `/srv/nfs`.
  The `/srv/tftp` link tasks in `netboot.yml` are the one listed exception.
- The compare's id substitution, the build lock, the site-state seeding and
  its legacy-key check, and the free-space guard.
- **Where the tool comes from.** `tests/test_nfsroot_generation.py:45-47`
  skips when no nfsroot-watchdog checkout is found, and no workflow provides
  one, so tests written that way would skip in CI. The pytest job
  (`.github/workflows/lint.yml`) gets the tool, either the
  `nfsroot-watchdog-server` package or a checkout at a release tag, and these
  tests fail instead of skipping when `CI` is set.

### In the VM CI

Budget, measured by the CI session on current main: the VM job takes
709-944 s, median 828 s. The target is 15 minutes for the whole run, and 17 of
the last 44 runs already exceed it. On the emulated Pi, power-on to kernel is
12-17 s, kernel to SSH 75-97 s, and each SSH round trip 3-12 s. A second real
pull and extract on the server is 1-2 minutes or more, so the test never does
one.

Free, with the layout on: the first converge builds and publishes a version
through the real path, and the Pi's first boot fetches its boot files through
`current`, mounts `versions/<id>/root` as a subdirectory of the export, and
`verify-pi.yml` asserts its version.

Added, after `verify-pi.yml` has passed on version A:

1. On the server, make version B from A with `cp -al` (hard links, seconds),
   replace the four path-carrying files and one test file by rename, and
   `publish` it. Hard links are safe here: the test replaces files and never
   edits one, and every write the server tool makes is tmp plus rename or
   create/unlink, so the two trees never share a changed inode.
2. One exec on the booted Pi (one round trip): read a file B replaced, run a
   binary, then `sudo nfsroot-watchdog check` and `nfsroot-watchdog status`.
   The status shows a pending reboot only after a check has run, and the timer
   fires only every 60 s. It must show no ESTALE and a scheduled reboot. That
   proves the board noticed the publish.
3. Power-cycle the Pi from the harness, as a PoE cycle would. The kernel
   prints `Kernel command line: ... nfsroot=.../versions/<B>/root` on the
   serial console within a second of "Booting Linux" (line 31 of
   `pi-serial.log` in run 37020489834), and `wait_for_pi_boot`
   (`tests/vm/run_tests.py:204-245`) already watches that console. The test
   waits for the second such line and reads the version from it. It does not
   wait for the NFS mount or for SSH.
4. While the Pi reboots, one server-side script checks that `current` names B,
   all markers are equal and TFTP serves B's cmdline. Once step 3 has seen B,
   it runs `rollback` to A and repeats the checks. The Pi is not rebooted
   again.

The harness power-cycles the Pi because nothing can make a reboot slot due at
once (see the contract). The watchdog-triggered reboot is not tested here.

Estimate: 5-10 s for steps 1 and 4, 3-12 s for step 2, 13-18 s for step 3:
about 20-40 s. **That does not fit today.** At the median it gives 848-868 s,
under 900 s, but 17 of 44 runs are already over 900 s without it, and this
puts more over. Whether to accept that is open question 2. The PR that adds
the test must state measured before and after totals.

## Implementation order

Each PR is mergeable alone, with green CI and `nfsroot_versioned` off. Steps 2
to 7 are green but exercised only by the pytest job until step 8 turns the
layout on in the VM.

1. Fix `fixpi/tasks/userconf.yml:121`.
2. The variables (including the versioned value of `tftp_root`) and the layout
   step.
3. The staging build: the img split, the play vars, the template and writer
   edits, the build lock, site state, the compare. With the variable on, a
   version is built and then left unpublished.
4. Exports.
5. TFTP: the `/srv/tftp` links and the `pxe` and `fixpi` verify tasks. The
   `tftp_root` value itself came with step 2.
6. Publish wiring. **Needs the nfsroot-watchdog release with `publish`.**
7. `verify-server.yml` and `verify-pi.yml` checks. The `verify-pi.yml` check
   needs the client rule's version file.
8. The VM test, and the layout turned on for the CI VM. **Needs `rollback`.**
9. Docs: the role README, `docs/access.md`, `CLAUDE.md`. These land with or
   before step 10, not after. After the cut-over the documented
   `touch .../root/etc/nfsroot-watchdog/inhibit` reaches only boards on that
   one tree, so a fleet-wide stop would silently not work.
10. tweed cut-over (the host_vars change and the migration above). **Needs
    `inhibit`/`uninhibit`, and an image with the client rule.** `list` is not
    required: `ls` and `readlink` do.

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
- **#56** (per-site inventory groups) edits `host_vars/fpgas.online.yml`,
  `inventory/hosts` and `tests/inventory/host_vars/test-vm.yml`, where
  `nfsroot_versioned` is turned on.
- **#58** (gateway API role) edits `site.yml` and `verify-server.yml`.
- **#107** (`docs/ci.md`): sections 3.2, 6.4 and 8 describe the in-place
  rsync. Whichever lands second updates them.

## Open questions

1. Should boards report their booted version through fleet self-registration
   (site `/fleet/<serial>/`, repos fpgas.online-site and
   fpgas.online-setup-pi)? It is optional now that clean-up is manual. Who
   owns it?
2. For Tim, the VM budget. The full test adds about 20-40 s to a job that
   takes 709-944 s (median 828 s) against a 900 s target that 17 of 44 runs
   already miss. Either accept the added seconds, or leave step 3 (the
   power-cycle) out, which saves 13-18 s. Then no VM run shows a board booting
   the new version after a swap: only the server-side fetch of B's cmdline
   over TFTP shows what a rebooting board would get.
3. For Tim, #45. It writes into the root on a timer. A file replaced in a
   published version goes ESTALE for boards that read it through the overlay.
   The reviewer's suggestion: the board reads the clock reference through
   `$LOWER` (plain NFS, which heals), as nfsroot-watchdog reads its marker.
   That still makes "immutable" carry a second exception. Does #45 change, or
   does the definition?
