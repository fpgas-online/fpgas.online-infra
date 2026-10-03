# Versions of the fleet NFS root: one directory per version

Status: draft for review, 2026-10-03. Nothing here is implemented.

## Scope

Tim's rule (2026-10-02): "All pi in normal operation should boot from the same
nfsroot." There is one fleet root. This spec keeps each **version** of that
root in its own directory, so an update never changes a tree a board is
running from. The only difference that may exist between two boards is
between the version a board runs now and the version it gets when it reboots.
No variable in this design is per Pi or per board type.

Not designed here:

- A different NFS root that a Pi boots for a special job (EEPROM upgrade,
  reading the Wi-Fi MAC): infra issue #176.
- The automatic reboot when the root changes: nfsroot-watchdog, owned by the
  nfsroot-watchdog repo. This spec states the contract it needs (see
  "Contract with nfsroot-watchdog").
- The design of the CI image comparison: this series implements it, and only
  its requirement is stated here (see "CI publishing").
- Boards reporting their booted version through fleet self-registration:
  fpgas-online/fpgas.online-site#43 and fpgas-online/fpgas.online-setup-pi#17.
  Nothing here depends on them.

Names: `nfsroot-watchdog` is the board-side client, and its `inhibit` holds one
machine. `nfsroot-generation` is the gateway command, and its `inhibit` holds
the fleet. "Marker" is the content of `/etc/nfsroot-watchdog/generation`.

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
  write the site layer. `template`, `copy`, `lineinfile` and `replace` rename
  into place; `apt_cache/tasks/nfsroot.yml:58` rewrites files in place with
  `open(path, "w")`.

A booted board mounts that tree as the read-only lower layer of
`overlayroot=tmpfs` (`ansible/roles/fixpi/templates/boot/cmdline.txt.j2:1`).
Every replaced inode then answers `ESTALE` on that board until it reboots. The
two deploys on 2026-09-25 changed 12587 and 7418 files, among them
`/home/pi/.ssh/authorized_keys`, which broke key SSH fleet-wide. No mount
option fixes this: overlayfs does not allow
[changes to underlying filesystems](https://www.kernel.org/doc/html/v6.12/filesystems/overlayfs.html#changes-to-underlying-filesystems).
The fix has to be on the server: never change a tree a board is booted from.

## Goals

1. A deploy never modifies a tree a board is booted from.
2. One directory per version. A bookworm to trixie upgrade (infra #37) is
   simply the next version.
3. Atomic switch-over and one-step rollback.
4. A board's kernel, initramfs, DTBs and `/lib/modules` come from one version,
   except for a board that fetches its boot files across a swap (see "TFTP").
5. Identical files are shared between versions by hard links, so a version
   costs about what changed.
6. nfsroot-watchdog keeps working. Its client gains one backward-compatible
   rule.

Non-goals: changing overlayroot or the Pi-side mount options; automatic
deletion of old versions (Tim: "Don't worry about GC until it becomes a
problem"); a canary step (the VM CI boot test is the gate; a bad version is
rolled back).

## Every deploy is a new version

Tim's decision (2026-10-03): every full `site.yml` run on a versioned site
publishes a new version, whether or not anything changed, and the fleet
reboots onto it in its slots. There is no tree comparison on the gateway. So
every `site.yml` deploy, including one made for an unrelated role, reboots
the fleet (`web.yml` run on its own does not touch the root). To deploy
without rebooting anyone, hold the fleet first with
`nfsroot-generation inhibit` and release it later with `uninhibit`; the boards
then reboot when the inhibit is removed.

The fleet is through its slots about 39 minutes after a publish, plus up to an
hour for each board with someone logged in (C4). A second run inside that time
publishes again: boards that already rebooted reboot a second time, and
late-slot boards go straight to the newest. Nothing breaks; it is just more
reboots.

## Terms

- **version**: one complete `{boot,root}` pair in `versions/`, made by one
  deploy. Its name (id) is one word,
  `<UTC yyyymmddThhmmssZ>-<first 12 hex of the image digest after sha256:>`,
  for example `20261005T013000Z-b41ea5c743cf`. The time is when the deploy
  started; the build lock makes it unique per deploy, and the role refuses an
  id that already exists.
- **current**: the version a board gets when it boots.
- **published**, **orphan**: defined at the top of the contract.
- **immutable**: nothing under a tree in `versions/` is ever written, with two
  exceptions (next section). Nothing writes a file in place there: with hard
  links, one write would change every version sharing that inode.
- **legacy root**: today's in-place tree `/srv/nfs/rpi/bookworm` on tweed.
  After the cut-over it is as immutable as any version, and it is a
  `--link-dest` reference, so its inodes are shared with later versions.

### The two exceptions

Both are written by temp file and rename, never in place, so a hard-linked
inode is never changed.

- (a) `root/etc/nfsroot-watchdog/`, written only by `nfsroot-generation`.
  Boards read it through plain NFS, which heals after a rename (C1).
- (b) `root/etc/fake-hwclock.data`, the clock reference, written only by PR
  #45's timer on the gateway (Tim, 2026-10-03): `fpgas-hwclock-ref.service`
  runs `/usr/local/sbin/fpgas-hwclock-ref
  {{ nfs_root }}/root/etc/fake-hwclock.data`, which writes a temp file in the
  same directory and calls `os.replace`.

Exception (b) is exactly the case overlayfs turns into ESTALE for a board that
has the dentry cached. It is acceptable because it is one file that a board
reads once, at boot (`fake-hwclock load`). The condition: nothing on a running
board may read `/etc/fake-hwclock.data` again through the overlay and treat an
error as fatal. #45 removes the board-side saves that would. nfsroot-watchdog
neither reads nor probes that file.

What #45 must do to fit (for its author; this spec does not change #45):

- Write into the current version. Its unit path comes from `nfs_root`, which is
  `/srv/nfs/rpi/current` outside the deploy, so the unit must not be rendered
  inside `deploy.yml`. Never write into `work/`.
- Resolve `current` once per run and do the temp file and the rename inside
  that one directory, so a publish in between cannot split them.
- Tolerate `current` naming the legacy entry.
- Expect a new version to carry the image's stale `fake-hwclock.data` until the
  timer's next run.

## Layout on the gateway

```
/srv/nfs/rpi/                      # <base>
  versions/                        # the one NFS export versioned boards use
    20261005T013000Z-b41ea5c743cf/
      boot/                        # TFTP payload; its cmdline names THIS version's root
      root/                        # the NFS lower layer
      .image-digest
    legacy-bookworm -> ../bookworm # tweed only: the legacy root as an entry
  current -> versions/20261005T013000Z-b41ea5c743cf   # written by nfsroot-generation
  work/                            # a deploy's temporary trees; outside the export
  work.lock/                       # the build lock (mkdir is atomic)
  nfsroot-generation.lock          # nfsroot-generation's flock file
  empty/{boot,root}                # fresh gateway only: what current names before the first publish
  bookworm/{boot,root}             # tweed only: the legacy root
```

`work/` is on the same filesystem as `versions/` (the role asserts the same
`st_dev`), so `--link-dest` can hard-link and the finished tree can be renamed
into `versions/`. It is outside the export; as for any directory on that
filesystem today, a root client that forges a file handle could still reach it
(`no_subtree_check`). No name is assumed free: tweed already has
`bookworm.pre-pull-2026-09-25` and `ssh-host-keys.replaced-2026-09-26`. The
layout step fails if `versions`, `work`, `work.lock` or `empty` exists and is
not a directory, `nfsroot-generation.lock` is not a regular file, or `current`
is not a symlink.

The layout step creates `current` and `versions/legacy-bookworm` only when
they are absent (a `stat`, then the link task under `when:`) and never
re-points an existing `current`. A plain `file: state=link` would re-point it
on every run and roll the fleet back to the legacy root.

Disk: a full tree is about 5 GB (measured on tweed on 2026-09-26, with 188 GB
free on `/srv`). A version costs its changed files plus its own directory
inodes, which are never shared. A running deploy holds `work/<id>` and
`work/<id>.version` at once: about 10 GB when little is shared, as for the
trixie upgrade. The deploy PR measures both.

## Turning it on, and never off

`nfsroot_versioned` (shared inventory variable in `srv.yml`, default `false`)
selects the layout per site; with it off a site behaves exactly as today. It
is turned on for the CI VM first (`tests/inventory/host_vars/test-vm.yml`),
then for tweed (`ansible/inventory/host_vars/fpgas.online.yml`). Merged code
stays deployable and playbooks run whole: there are no tags
(`tests/test_no_tags.py`).

Once a site has the layout, the flag must never go back to `false`, and
reverting the cut-over PR is not an undo. A single-root run would render the
exports without the `versions` line (every booted board's mount goes ESTALE),
point TFTP back at `bookworm/boot`, and run today's pull (`--checksum -a`
updates the attributes of identical files in place), `apt_cache`'s
`open(path, "w")` and fixpi's recursive `chown` into the legacy tree, whose
inodes every version shares. So the first play of `site.yml` starts, before
anything writes, with a guard on `nbp` hosts that **fails when
`nfsroot_versioned` is false and `<base>/versions` or `<base>/current`
exists**, saying why and that the undo is
`nfsroot-generation rollback /srv/nfs/rpi legacy-bookworm`.

ps1 stays single-root. It is on the legacy MAC-table scheme (`tftp_root` is
`/srv/tftp` with one symlink per Pi serial, `fixpi/tasks/netboot.yml:14-22`),
which the VM CI does not boot (`test-vm.yml` defines `switches`). The role
asserts `switches is defined` when `nfsroot_versioned` is on.

## Variables

| variable | set in | single-root site | versioned site |
|---|---|---|---|
| `nfsroot_versioned` | inventory (`srv.yml`; host_vars to turn on) | `false` | `true` |
| `nfsroot_base_dir` | inventory (`srv.yml`) | `<base>` | `<base>` |
| `nfsroot_served_dir` | inventory (`srv.yml`) | `<base>/{{ dist }}` | `<base>/current` |
| `nfs_root` | inventory; `deploy.yml`'s include `vars:` | `{{ nfsroot_served_dir }}` | `{{ nfsroot_served_dir }}`; inside the deploy `<base>/work/<id>` |
| `tftp_root` (what dnsmasq serves) | inventory (`srv.yml`) | as today (`srv.yml:23`) | `{{ nfsroot_served_dir }}/boot` |
| `nfsroot_boot_build_dir` (where `sunxi.yml` writes) | inventory (`srv.yml`) | today's `tftp_root` expression | `{{ nfs_root }}/boot` |
| `nfsroot_mount_dir` (the path a board mounts) | `deploy.yml`'s include `vars:`; `inventory-ci-nfsroot` | `{{ nfs_root }}` | `<base>/versions/<id>`; **undefined** outside the deploy |
| `nfsroot_legacy_dir` | inventory (`srv.yml`), a literal | unused | `<base>/bookworm`; used only after a `stat` shows it exists |
| `vault_nfsroot_*`, `nfsroot_*_pub` (the keys) | host_vars | unused | see "Keys" |
| `nfsroot_versions_min_free_gb`, `nfsroot_versions_legacy_name`, `nfsroot_versions_publish`, `nfsroot_versions_root_uid`, `nfsroot_versions_pi_uid` | role defaults | unused | `12`, `legacy-bookworm`, `true`, `0`, `1000` |

The 188 uses of `nfs_root` under `ansible/` are not edited: `nfs_root` means
"the tree this run works on", and `deploy.yml` sets it and `nfsroot_mount_dir`
as `vars:` on its own `include_role` calls (task vars outrank inventory vars;
a var cannot refer to the inventory one of the same name, hence
`nfsroot_served_dir`). The four templates that carry the path (cmdline,
cmdline-pi5, the sunxi PXE config, fstab) use `nfsroot_mount_dir`, so each
version's boot files name its own root, never `current`.

## Deploying a version

### The steps

All of this is Ansible in this repo (Tim: "The work should be an ansible task
in infra"), in one entry point, `roles/nfsroot_versions/tasks/deploy.yml`.
On a versioned site the "Update the Pi NFS root" play
(`ansible/site.yml:64-110`) is one `include_role` of it, and the VM test's
playbook includes the same file, so the two cannot drift. It does:

1. Download the GitHub keys (`fixpi/tasks/github_keys.yml`, unchanged, first).
2. `img`: wait for the background pull, pull, read the digest
   (`pull.yml:21-50`); `pull.yml` is split after the digest, which names the
   version.
3. Take the build lock, delete anything left under `work/`, choose the id,
   create `work/<id>/`.
4. `img`: extract into `{{ nfs_root }}` = `work/<id>/`, an empty directory,
   with today's unchanged command,
   `rsync -aHAX --delete --numeric-ids --checksum <excludes> <mount>/<item>/ {{ nfs_root }}/<item>/`
   (`pull.yml:106-112`). It keeps the image's mtimes; the `--delete` and the
   four excludes have nothing to act on in an empty directory.
5. Write the keys from the vault into `work/<id>/root` (see "Keys").
6. `apt_cache` `nfsroot.yml` and `fixpi` on `work/<id>/`. Every in-place write
   of the site layer (`apt_cache/tasks/nfsroot.yml:58`, fixpi's recursive
   `chown` at `userconf.yml:119-126`) lands here, on unshared inodes.
7. Check the tree: `root/bin/bash`, `root/etc/os-release` and
   `boot/kernel8.img` exist; `.image-digest` is the pulled digest; both
   cmdline files, `root/etc/fstab` and (with `sunxi_boards`) the sunxi PXE
   config name `versions/<id>`; the keys and pi's `authorized_keys` are
   present; **`root/etc/nfsroot-watchdog/` does not exist**.
8. `rsync -aHAX --numeric-ids --checksum --link-dest=<ref>... work/<id>/
   work/<id>.version/`, then rename `work/<id>.version` to `versions/<id>` and
   delete `work/<id>/`.
9. `nfsroot-generation publish /srv/nfs/rpi <id>`, retried (C7). Skipped when
   `nfsroot_versions_publish` is false, which leaves an orphan on purpose (the
   VM test uses it).
10. Release the build lock.

**Rule: a new version is only ever made from a fresh extraction (step 4),
never from a copy of an existing version (`cp -al` or similar).** A copy would
carry that version's marker and version file; `publish` would then treat it as
published and refuse it.

The `--link-dest` references, at most rsync's 20: `current`'s target first,
then the other id-named entries of `versions/` in descending name order (the
id starts with the UTC time, so that is newest first), then the legacy entry
last; each directory appears once (the others matter after a rollback, when
`current` is older than the newest tree). With `--checksum` rsync links a file
only when its contents and its preserved attributes (owner, mode, mtime, ACLs,
xattrs) match; without it, a changed file with the same size and mtime could be
linked to old contents. The cost is reading both sides of every same-sized
pair once per deploy, as `pull.yml:94-101` already accepts. A file the site
layer rewrote has a fresh mtime and does not link. The deploy PR reports the
share of linked files; a low share is a bug.

The build lock is `command: mkdir <base>/work.lock`, which fails when the
directory exists (`file: state=directory` would succeed). It is taken
**before** the block holding steps 3 to 10, whose `always:` removes it, so
only the run whose `mkdir` succeeded removes it. A killed run, or a run whose
host went unreachable (Ansible runs no `always:` then; `ansible.cfg:30-36`
records transient UNREACHABLE on tweed), leaves the lock. The next run stops
at the lock task with a message saying how to clear it (`rmdir`, once no other
`site.yml` is running).

Step 3 replaces `nfsroot_generation`'s `begin.yml` and step 9 its `end.yml`
(the includes at `site.yml:83-86` and `108-110`); both stay for single-root
sites. `nfsroot_generation_bump` has no meaning on a versioned site; a
leftover `-e nfsroot_generation_bump=never` fails the run with a message
pointing at `nfsroot-generation inhibit`. fixpi's warning at
`userconf.yml:52-56` ("PoE-cycle their switch ports") would fire on every
versioned deploy, because every fresh tree lacks the password hash; it is
skipped on a versioned site.

Cost per deploy: one full extraction (about 5 GB written), the site layer and
one checksumming rsync pass; the deploy PR reports the time on tweed.

### Keys

Tim: "Keypairs should probably be from the ansible-vault so they persist even
if tweed is completely replaced." The SSH host keys and the pi/root keypairs
are site secrets in the vault, written into `work/<id>/root` at step 5 by
`keys.yml`. fixpi's generators then find them: the host-key task
(`netboot.yml:268-286`) only runs when the key is absent, and the
`openssh_keypair` task (`userconf.yml:107-112`) gets `regenerate: never`, so a
vault key that differs from its defaults is never replaced. There is no key
state on the gateway.

| file in the root | owner | mode | variable (host_vars, inline `!vault` like `vault_ansible_ssh_private_key` at `host_vars/fpgas.online.yml:346`) |
|---|---|---|---|
| `etc/ssh/ssh_host_rsa_key` | 0:0 | 0600 | `vault_nfsroot_ssh_host_rsa_key` |
| `etc/ssh/ssh_host_ecdsa_key` | 0:0 | 0600 | `vault_nfsroot_ssh_host_ecdsa_key` |
| `etc/ssh/ssh_host_ed25519_key` | 0:0 | 0600 | `vault_nfsroot_ssh_host_ed25519_key` |
| `etc/ssh/ssh_host_{rsa,ecdsa,ed25519}_key.pub` | 0:0 | 0644 | `nfsroot_ssh_host_{rsa,ecdsa,ed25519}_key_pub` (plain) |
| `root/.ssh/id_ssh_rsa` | 0:0 | 0600 | `vault_nfsroot_root_id_ssh_rsa` |
| `root/.ssh/id_ssh_rsa.pub` | 0:0 | 0644 | `nfsroot_root_id_ssh_rsa_pub` (plain) |
| `home/pi/.ssh/id_ssh_rsa` | 1000:1000 | 0600 | `vault_nfsroot_pi_id_ssh_rsa` |
| `home/pi/.ssh/id_ssh_rsa.pub` | 1000:1000 | 0644 | `nfsroot_pi_id_ssh_rsa_pub` (plain) |

Every task that handles private key material has `no_log: true` and
`diff: false`, because the deploy runbooks use `--diff`, which would print a
`copy content:` in full, on failure too. The owners come from
`nfsroot_versions_root_uid` and `nfsroot_versions_pi_uid`, which the pytest
job sets to its own uid. On a versioned site the role fails before building if
any key variable is undefined. The CI VM gets throwaway test keys in plain
text in `tests/inventory/host_vars/test-vm.yml`. While `nfsroot_legacy_dir`
exists, the role also fails if `nfsroot_ssh_host_ed25519_key_pub` differs from
the legacy root's, so a missed capture cannot change the fleet's host key.

Nothing else in the root is site state: `authorized_keys` is written whole
from the key list on every deploy (`fixpi/tasks/authorized_keys.yml:77-85`),
`root/etc/nfsroot-watchdog/` is written by `nfsroot-generation`, and the rest
is rendered from the inventory.

**Exposure.** `versions/` is exported read-only with `no_root_squash`, and a
board user has root (pi has passwordless sudo), so every kept version's
private keys stay readable for as long as that version exists. With vault keys
the host key is the same in every version unless rotated. After a rotation the
old private key stays exported in every older version, and a rollback past a
key revocation brings the revoked `authorized_keys` back. **So rotating a site
secret or revoking a key means deleting every older version, or never rolling
back past that point.** The rollback and clean-up sections refer to this rule.

### Failure handling

- Anything that fails before step 9 leaves `current` and every published
  version untouched.
- A half-built `work/<id>` is deleted by step 3 of the next run.
- A `publish` that died is repaired by running it again (C7); step 9 retries.
- An orphan (step 9 skipped or failed after its retries) cannot be booted and
  is not deleted automatically. `verify-server.yml` lists it. A person deletes
  it, or publishes it with `nfsroot-generation publish`.
- Free space: the layout step, in `site.yml`'s first play and before the
  background image pull, fails when `<base>` has less than
  `nfsroot_versions_min_free_gb` free (default 12: two full trees plus
  headroom; podman's image store is not counted). The message gives the free
  space, the need and the clean-up procedure.

## NFS exports

On a versioned site `exports.j2` renders one line for the parent of all
versions, with today's options (tweed's values shown):

```
/srv/nfs/rpi/versions 10.21.0.1/16(ro,sync,no_subtree_check,no_root_squash)
```

plus the two legacy lines, from `nfsroot_legacy_dir`, only after a `stat`
shows it exists. It never renders `nfs_root` or `current`: an export of
`current/root` would be re-resolved by a later `exportfs -ra` and withdraw a
path boards still mount. Single-root sites keep today's two lines. The `nfs`
role's "Create some dirs" task (`nfs/tasks/main.yml:13-21`) creates
`<base>/versions` on a versioned site, not `{{ nfs_root }}`. Each board mounts
`versions/<id>/root` as a subdirectory of the export (NFSv3 allows it; the VM
CI's first boot proves it).

File handles stay valid across the `current` swap: a knfsd handle holds an
fsid that identifies the export point plus the file's inode (`fs/nfsd/nfsfh.h`
`mk_fsid`), and boards mount by explicit path, never through `current`. The
same fact gives the one hard rule: **an export line a booted board mounted
through must never be removed**; it would make that board's whole mount stale.
So the legacy lines stay while the legacy directory exists.

## TFTP

On a versioned site `tftp_root` is `/srv/nfs/rpi/current/boot`;
`dnsmasq-base.conf.j2:31` is unchanged. From the dnsmasq v2.91 source (a GitHub
mirror of the upstream tag): each request is an `open()` of the root string
plus the file name, with no `realpath()`, so `current` is resolved per
request, and a transfer keeps its file descriptor, so one file comes whole
from one version. At start dnsmasq exits if it cannot `opendir()` the root, so
the layout step creates `current` before the `pxe` role runs: it names
`versions/legacy-bookworm` if there is a legacy root, else `empty/`. Assumed,
proven by the VM CI once the layout is on: Debian 13's dnsmasq behaves the
same and nothing (e.g. AppArmor) stops it following the links.

**Booting across a swap.** A board that fetches its boot files while
`publish` runs may get the old `cmdline.txt`, or files from both versions.
On the old root it reboots by the version rule (C2). A mixed kernel and
`/lib/modules` only matters when the kernel changed; it can fail to boot at
all (no watchdog runs; `verify-pi.yml` sees the board unreachable) or run on
the current version with missing modules, which `verify-pi.yml` finds by
checking `/lib/modules/$(uname -r)`. Either needs a power-cycle.

## CI publishing (part of this series)

Tim: "When a new image has identical contents to an old image, the old image
just gets a second revision number." It lives in `tests/ci/nfsroot_publish.py`
and the promote guard (`tests/ci/nfsroot_promote_guard.py`), in this series;
CI reviews it. The requirement:

1. "Identical" means the whole published tree (`boot/` and `root/`) has the
   same set of paths, and each path has the same type, contents (SHA-256 of
   regular files), size, mode, uid/gid, xattrs (including
   `security.capability` and ACLs), symlink target, device major/minor, and
   hard-link grouping. mtime, atime and ctime are excluded.
2. A fresh build is almost never identical under that definition: apt/dpkg
   leave volatile files (`/var/lib/apt/lists/*`, `/var/cache/apt`,
   `/var/log/*`, dpkg `*-old` files, `/var/cache/ldconfig/aux-cache`, man-db
   caches) and initramfs images embed their build time. Those paths are either
   excluded from the comparison (listed in one place next to the comparison
   code) or normalised by the build; which one is the implementer's choice.
3. Compare against the last image published from main, not the promoted one.
   An identical result still goes through the VM test and promotion; it just
   reuses the digest.
4. Revision tags, e.g. `bookworm-armhf-<date>-<sha7>` pointing at the existing
   digest, plus a label or annotation recording which commit first produced
   the content, so every main commit has a tag.
5. It touches `--reuse` (input-based) and the `base-built` label in the #173
   design (PR #181); the implementer keeps those consistent.

For tweed this changes nothing: every deploy makes a new version from the
digest it is given.

## Contract with nfsroot-watchdog (owned by the nfsroot-watchdog repo)

For the nfsroot-watchdog session to check. It records that repo's decisions as
of 2026-10-03 (fpgas-online/nfsroot-watchdog): client rule and `version:`
line in PR #8 (merged, 1803480); gateway commands in PR #9; the uninhibit and
stale-probe fixes in PR #10 (stacked on #9, not merged). The details of the
commands are in PR #9's README; this section keeps what infra relies on. Items
marked **[changed 2026-10-03]** differ in meaning from the previous text.

**Published and orphan.** An entry of `versions/` is published if it has a
marker (`root/etc/nfsroot-watchdog/generation`). The legacy root already has
one, written by today's `begin`/`end` flow. An orphan, a tree renamed into
`versions/` but never published, has no `root/etc/nfsroot-watchdog/` at all.
`publish` and `list` see every entry that resolves to a directory containing
`root/`. Marker, version-file and inhibit writes go only to published entries
plus the one being published, so an orphan is never touched.

**Commands [changed 2026-10-03].** `BASE` is `/srv/nfs/rpi` (holds `versions/`
and `current`); `NAME` is an entry name in `versions/`.

- `nfsroot-generation publish BASE NAME`
- `nfsroot-generation rollback BASE NAME`
- `nfsroot-generation list BASE`
- `nfsroot-generation inhibit BASE [--reason TEXT]`
- `nfsroot-generation uninhibit BASE`

On success they print JSON. A refusal writes nothing to any entry, prints a
message on stderr and exits 1; a missing `versions/` exits 2. The flock file
`BASE/nfsroot-generation.lock` is created even on a refusal; `--wait SECONDS`
(default 300) bounds the wait for it. `current` is written as the relative
link `versions/<name>`. Refusals: `NAME` is not one word (clients read it as
the marker's third field); `NAME` has no `boot/` and `root/`; `current` exists
and is not a symlink; `NAME`'s version file names another version; any
published entry (or `NAME`) holds `update.lock`; `publish` of a version
published before (use `rollback`); `rollback` to a never-published entry;
`inhibit` or `uninhibit` when nothing is published.

- **C1. The client gains one backward-compatible rule (C2) and is otherwise
  unchanged.** It reads the lock, the marker and the fleet inhibit as
  `$LOWER$FILE` (plain NFS, not the overlay), so from the version it booted.
  **[changed 2026-10-03, PR #10]** Where the version file is readable, the
  client does not probe the marker through the overlay, so a replaced marker
  never triggers its stale-probe.
  The client package installs nothing under `/etc/nfsroot-watchdog`, so the
  image has none of these files.
- **C2. Each version says who it is; the marker says who is current.**
  `publish` writes `root/etc/nfsroot-watchdog/version`, holding the entry's
  name, into any published entry that lacks one, and never changes it. The
  marker is `<epoch> <iso> <name of the current version>`, epoch first, so old
  clients and the stagger are unaffected. Client rule: if the version file
  exists, the trigger is "the marker's third field is not my version" instead
  of "the marker differs from my boot snapshot". This also covers a board that
  boots an old version across a swap.
- **C3. `publish` order [changed 2026-10-03]:** the new entry gets its
  directory (0755), the current fleet inhibit state, the marker and its version
  file before `current` names it; then `current` is swapped; then every other
  published entry gets the marker, and a version file if it lacks one. The
  marker is stamped just before the swap. `rollback` uses the same order. Every
  write is temp file plus rename, or create/unlink, never in place, so a
  hard-linked file is never changed (that repo tests this).
- **C4. Reboot timing [changed 2026-10-03: the user grace].** A board reboots
  no sooner than 300 s after a publish, in slot order from the marker's epoch
  (420 s plus 20 s per slot); the fleet is through about 39 minutes after the
  publish. A due reboot is deferred up to `USER_GRACE` (3600 s) while `who`
  shows sessions, web-terminal users included.
- **C5. The fleet inhibit is per entry [changed 2026-10-03: the commands].** A
  board sees `/etc/nfsroot-watchdog/inhibit` only in the version it booted.
  `nfsroot-generation inhibit`/`uninhibit` act on every published entry;
  `publish` applies the current state to the entry it publishes. **[changed
  2026-10-03, PR #10]** `uninhibit` first writes a fresh marker (same current
  version, new epoch) into every published entry while the inhibit still
  holds, then removes the inhibit, so the stagger restarts from the release;
  with nothing held it writes nothing. Its JSON has `marker` (the new one, or
  null). Operators use these, not `touch`. On a single-root site
  `touch .../inhibit` keeps the old weakness (a release reboots the fleet
  together); that is out of scope.
- **C6. The legacy root is an entry.** It is the symlink
  `versions/legacy-bookworm -> ../bookworm`, created by this repo in the
  migration and removed with the legacy tree; no argument or config names it.
  `rollback` to it is allowed.
- **C7. `publish` replaces `begin`/`end` on versioned sites, and a re-run
  repairs a failed publish [changed 2026-10-03].** Re-running `publish` for the
  `NAME` that `current` already names is a no-op if every published entry has
  the marker. If the earlier run stopped part-way, it reuses that marker when
  it is at most 60 s old (Ansible's immediate retries) and otherwise stamps
  afresh, so that boards do not all count their slot from an old epoch and
  reboot together. `begin`/`end`/`scan`/`run` stay for single-root sites.
- **C8. Deleting a version heals but is not harmless [changed 2026-10-03: the
  range].** A board whose version directory is deleted gets ESTALE on its mount
  root and reboots after two confirming checks plus its slot delay, bypassing
  the update lock and the fleet inhibit (only the per-machine inhibit holds
  it). Until then, about 9 to about 100 minutes, it cannot exec anything new.
- **C9. Migration.** Boards on the legacy root see the first versioned
  `publish` as a marker change (today's client) or as a marker naming another
  version (a newer client, if an in-place converge put one there) and reboot.
  The legacy entry gets the marker on every publish while it exists.
- **C10. Three `status` lines are an interface.** `nfsroot-watchdog status`
  prints `NFS root:`, `root generation:` and, with the version rule,
  `version:`: label, colon, whitespace, value, nothing else. That repo tests
  them; `verify-pi.yml` reads them.
- Informational: the stale-probe trigger never fires under this layout and
  stays for other sites. `roles/nfsroot_generation/tasks/install.yml` keeps
  installing the server package in `site.yml`'s first play.

The boundary, and what this repo promises:

- This repo builds the tree (steps 3 to 8), renames it into `versions/<id>`,
  and calls `publish`. The rename needs no flock: ids are unique and an
  unpublished tree is inert. A new version never contains
  `root/etc/nfsroot-watchdog/` before `publish`.
- Nothing here writes a tree in `versions/` afterwards, the legacy tree after
  the cut-over included. The only other writer is #45's timer (exception b).
- No switch makes a reboot slot due at once, and none will be added for
  tests; the watchdog-triggered reboot is covered by that repo's tests.

Release order in that repo: the client rule with the `version:` line first
(inert until a version file exists), then `publish`, `rollback`, `list`,
`inhibit`/`uninhibit`.

## Rollback

`nfsroot-generation rollback /srv/nfs/rpi <name>` swaps `current` back to a
published version and writes a fresh marker naming it into every published
entry. Boards on the bad version reboot onto the target in their slots; boards
already on the target stay put, as they do after a C7 restamp (C1, C2); only a
board with an old client reboots once. No
image pull and no site-layer run is needed. The trees share inodes, but no
write ever reaches a shared inode (see "immutable"), so the target is exactly
as it was published. Rollback to `legacy-bookworm` is allowed: nothing writes
the legacy tree after the cut-over, its cmdline names
`/srv/nfs/rpi/bookworm/root`, and its export lines are kept.

The next deploy publishes a new version again, from whatever image it is
given, so fix the cause first or name an older image with
`-e img_nfsroot_image=...`. Mind the key rule in "Keys".

## Cleaning up old versions by hand

1. See what exists and what is current: `nfsroot-generation list /srv/nfs/rpi`,
   or `ls -l /srv/nfs/rpi/versions` and `readlink /srv/nfs/rpi/current`.
2. Check what the boards run. With the version rule a board cannot stay on an
   old version past its slot (C4) unless it is inhibited or unreachable. Run
   `verify-pi.yml`: it fails for any reachable board not on the current
   version. For boards it cannot reach, `showmount -a` (`/var/lib/nfs/rmtab`)
   is a hint; it can list mounts long gone. A board that is off boots
   `current`.
3. `rm -rf /srv/nfs/rpi/versions/<id>`. Never the one `current` names; keep
   the one before it as the rollback target. Deleting a hard-linked tree only
   drops link counts; other versions keep their files. The key rule in "Keys"
   may require deleting more.

## Migration on tweed

Before:

- Every PR in "Implementation order" before the cut-over is merged, and the VM
  CI is green with the layout on.
- `nfsroot-generation` with `publish`, `rollback` and `inhibit`/`uninhibit` is
  installed on tweed.
- The image tweed will deploy contains the client with the version rule and
  PR #10's change (`roles/onpi/tasks/stale_root.yml:12-15` installs
  `nfsroot-watchdog` with `state: latest`, so any image built after both
  releases has them). Check the package version in the image.
- **The keys are in the vault.** An operator copies the ten files in the "Keys"
  table from `/srv/nfs/rpi/bookworm/root` on tweed, encrypts each private key
  with `ansible-vault encrypt_string --stdin-name <variable>`, and adds them
  and the public halves to `ansible/inventory/host_vars/fpgas.online.yml` in a
  PR. The ed25519 fingerprint must match `docs/access.md:157-160`.

1. Check on tweed: `/srv/nfs/rpi` is one filesystem with at least 12 GB free;
   `versions`, `current`, `work`, `work.lock`, `empty` and
   `nfsroot-generation.lock` are free; no `update.lock`; the legacy root has a
   marker (`bookworm/root/etc/nfsroot-watchdog/generation`; without one it is
   an orphan and its boards never reboot); note which boards hold a
   per-machine inhibit.
2. Merge the PR that sets `nfsroot_versioned: true` for `fpgas.online` and run
   the whole `site.yml` with `--limit fpgas.online`.
3. That first run creates `versions/legacy-bookworm` and `current ->
   versions/legacy-bookworm`, so dnsmasq's new `tftp-root` still serves the
   legacy `boot/`. It adds the `versions` export, keeps the legacy lines,
   builds the first version with the legacy root among the `--link-dest`
   references, and publishes it; `publish` writes the marker into the legacy
   entry (C9).
4. Each board reboots in its slot onto the new version (C4). The legacy root
   was not modified apart from `root/etc/nfsroot-watchdog/`, so nothing goes
   ESTALE while they wait.
5. Check: `readlink /srv/nfs/rpi/current`; `exportfs -v` shows three lines;
   `verify-server.yml` passes; after the slots, `verify-pi.yml` passes; the Pi
   host key fingerprint is unchanged. A board still on the legacy root (per
   machine inhibit) fails `verify-pi.yml` until it reboots: the legacy entry
   now has a version file. Record the deploy's time and link share in a
   comment on the cut-over PR.
6. **The undo is `nfsroot-generation rollback /srv/nfs/rpi legacy-bookworm`,
   never turning `nfsroot_versioned` off or reverting the cut-over PR** (see
   "Turning it on, and never off"; the guard refuses it). If the legacy root
   carries today's client, `verify-pi.yml` then fails for every board although
   each is on the current version (version file present, no `version:` line).
   With a newer client converged in place, it passes.
7. The legacy root, its link and its export lines are removed by hand, only
   once a passing `verify-pi.yml` shows no board is on it. Delete the link and
   the directory, then run `site.yml`; the template drops the legacy lines.

## Changes by file

| file | change |
|---|---|
| `ansible/inventory/group_vars/all/srv.yml:15,23` | the inventory variables in "Variables" |
| `ansible/inventory/host_vars/fpgas.online.yml` | the key variables (migration), then `nfsroot_versioned: true` |
| `ansible/inventory-ci-nfsroot/group_vars/all/all.yml` | `nfsroot_mount_dir: "{{ nfs_root }}"` for the image build |
| `ansible/site.yml:1-16` | the guard as the first play's first task (`pre_tasks`), and the layout step after the install |
| `ansible/site.yml:64-110` | on a versioned site the play is one `include_role` of `nfsroot_versions/tasks/deploy.yml`; today's tasks stay for single-root sites |
| `ansible/roles/nfsroot_versions/` (new) | `guard.yml`, `layout.yml`, `deploy.yml` (the steps), `keys.yml`, and the checks, link-dest rsync, rename and publish it includes |
| `ansible/roles/img/tasks/pull.yml` | split after the digest (line 50); comments at 7-10 and 73-101 rewritten; the rsync is unchanged |
| `ansible/roles/apt_cache/tasks/nfsroot.yml` | none |
| `ansible/roles/fixpi/templates/boot/cmdline.txt.j2:1`, `cmdline-pi5.txt.j2:1`, `default-arm-sunxi.j2:18`, `templates/etc/fstab.j2:2-3` | `nfs_root` becomes `nfsroot_mount_dir` |
| `ansible/roles/fixpi/tasks/userconf.yml:52-56,107-112,121` | the shadow warning skipped on a versioned site; `regenerate: never`; hardcoded `/srv/nfs/rpi/{{ dist }}` becomes `{{ nfs_root }}` (it would chown the legacy root and leave the new `.ssh` owned by root) |
| `ansible/roles/fixpi/tasks/sunxi.yml:27,36,47,66,67,80` | `tftp_root` becomes `nfsroot_boot_build_dir` |
| `ansible/roles/fixpi/tasks/netboot.yml:8-22` | the `/srv/tftp` links name `{{ nfsroot_served_dir }}/boot`, with `force: true` (the target is absent while `current -> empty`) |
| `ansible/roles/fixpi/tasks/verify/main.yml:94` | expect the resolved version path in the sunxi PXE config |
| `ansible/roles/nfs/tasks/main.yml:13-21`, `templates/exports.j2:3-4`, `tasks/verify/main.yml:45-58` | "NFS exports"; verify checks the `versions` line and the legacy lines when present |
| `ansible/roles/nfsroot_generation` | `begin.yml`/`end.yml` only on single-root sites; README rewritten |
| `ansible/verify-server.yml:193-204`, `ansible/verify-pi.yml:358-373` | "Verification" |
| `tests/vm/run_tests.py:462`, `tests/inventory/host_vars/test-vm.yml` | the path goes through `current`; test keys; the layout is turned on |
| `tests/test_nfsroot_versions.py` (new), `.github/workflows/lint.yml` | "Testing" |
| `docs/access.md:157-176`, `CLAUDE.md`, the role README | the layout and `nfsroot-generation inhibit` |

Hardcoded root paths left alone: `ansible/roles/site/tasks/snmp.yml:54-58`
(MAC-table sites only), the CI image build (`inventory-ci-nfsroot/hosts:13`,
`img/files/img2files.sh:11`, `tests/ci/nfsroot_publish.py:44`,
`.github/workflows/nfsroot-build.yml:121,158`) and `tests/vm/cloud_init.py:73`
(the retired piroot account's fixture).

Conventions: role variables `nfsroot_versions_*`, results
`__nfsroot_versions_*`, shared inventory variables `nfsroot_*` and never in a
role; no tags; no per-Pi variables. The planned renames (nfs, pxe, fixpi, img,
dist, nfs_root, nbp, pig) come after this work and rebase over it.

## Verification

`verify-server.yml` on a versioned site: `current` is a symlink to an entry
with `boot/` and `root/`; every real version directory's cmdline files name
its own `root`; every published entry holds the same marker and a version file
with its own name; orphans are listed, not failed; no entry holds an
`update.lock` (today's check at 193-204, per entry); `work/` is empty and
`work.lock` is gone; `/etc/exports` and `exportfs -v` have the `versions`
line. The existing checks read `{{ nfs_root }}`, which is `current`.

`verify-pi.yml`: the task that runs `nfsroot-watchdog status`
(`verify-pi.yml:358-366`, asserted at `:373`) gains these checks, all from that
one output:

- With a `version:` line: it equals the third field of the `root generation:`
  line.
- No `version:` line but `etc/nfsroot-watchdog/version` exists under the
  printed `NFS root:` path: fail. That is an old client on a versioned root
  (`status` prints `/` with no overlay; a double slash resolves).
- Neither (a single-root site): skipped.

The collector also checks that `/lib/modules/$(uname -r)` exists.

After a deploy `verify-pi.yml` fails for each board until it has rebooted, so
run it once the slots have passed (C4); `verify-server.yml` can run at once.

## Testing

### In the pytest job (outside the VM budget)

Tests never escalate (`ANSIBLE_BECOME=False`).

- **Guard.** With `nfsroot_versioned: false`, the guard fails when a temporary
  base holds `versions/` or `current`, and passes on an empty one.
- **Deploy chain.** Against a temporary base, with a fixture tree in place of
  the img extraction (`podman image mount` needs root) and the key owners set
  to the test's uid: run the layout, the keys, the link-dest rsync and the
  rename twice. Assert that version A's inodes did not change, that identical
  files are hard links and changed ones are not (including a changed file with
  the same size and mtime), and that the new tree has no
  `root/etc/nfsroot-watchdog/`. Step 3's PR adds this; step 6's PR adds
  `publish` to it and asserts the marker and version files. It runs only task
  files that need neither root nor the machine: the new role's, and fixpi's
  `tt-site.yml` and `fleet-site.yml`. fixpi's `manage.yml`, `netboot.yml`,
  `userconf.yml`, `authorized_keys.yml`, `ansible-home.yml`, `sunxi.yml`,
  `sunxi-image.yml`, `nogrow.yml` and `tweeks.yml` stay out (other uids,
  `/usr/local/sbin`, `/srv/tftp`, apt, systemd). For them the static test is
  the only guard before the VM run.
- **Layout and migration paths.** `layout.yml` against a temporary base with a
  fake `bookworm/` (with and without `current` present: it must never
  re-point), and a rendered `/etc/exports` with and without the legacy
  directory.
- **Static.** No task file `deploy.yml` includes uses `tftp_root`,
  `nfsroot_served_dir` or a literal `/srv/nfs`, except the `/srv/tftp` link
  tasks in `netboot.yml` and the new role's reads of `current`.
- The build lock, the key checks and the free-space guard.
- **The tool.** The pytest job checks out fpgas-online/nfsroot-watchdog at
  `1803480` (PR #8's merge) and sets `NFSROOT_GENERATION` to its
  `src/nfsroot-generation`; its apt repo has no Ubuntu suites, and its only tag
  is `v0.0`. Step 6's PR moves the pin to the merge commit of the later of PRs
  #9 and #10, since #10 also changes the client. With `CI` set
  these tests fail instead of skipping, including
  `tests/test_nfsroot_generation.py`, which skips without a checkout
  (lines 45-47) and has never run in CI. Step 2 adds the checkout.

Untested before tweed: dnsmasq through two links to the legacy `boot/`, the
legacy export lines under real NFS, the key capture, and the full site layer
deployed twice. The cut-over watches these.

### In the VM CI

Budget, measured on main: the VM job takes 709-944 s, median 828 s, against a
900 s target that 17 of the last 44 runs exceed. On the emulated Pi, power-on
to kernel is 12-17 s, kernel to SSH 75-97 s, an SSH round trip 3-12 s. A real
extraction on the server takes 1-2 minutes.

With the layout on, the converge itself deploys and publishes version A, and
the Pi's first boot goes through `current`, mounts `versions/A/root` and
passes `verify-pi.yml`. Added:

1. Version B comes from the deploy path, not from a copy. Today
   `verify-server.yml` runs in a background thread while the Pi boots
   (`tests/vm/run_tests.py:733-742`); the same thread then runs a test
   playbook that includes `nfsroot_versions/tasks/deploy.yml` with
   `nfsroot_versions_publish: false` (same image, already pulled). Running it
   after `verify-server.yml` keeps `work/` and `work.lock` out of that check.
2. After `verify-pi.yml` passes on A and the thread has finished:
   `nfsroot-generation publish /srv/nfs/rpi <B>`, then one exec on the Pi: read
   `/home/pi/.ssh/authorized_keys` (the file whose ESTALE broke SSH in
   September) and `/etc/fstab` (which differs in B), run a binary,
   `sudo nfsroot-watchdog check`, then `nfsroot-watchdog status`. None of it
   may answer ESTALE, and the status must show a scheduled reboot. The check
   does not read `/etc/nfsroot-watchdog/generation` through the overlay:
   `publish` replaced A's marker, so that one file can answer ESTALE there,
   which is expected and harmless. The timer only runs every 60 s, hence the
   explicit check.
3. Power-cycle the Pi. The harness has none (`-monitor none`,
   `tests/vm/vm_manager.py:416`), so it kills QEMU and runs `start_pi` again.
   The kernel prints `Kernel command line: ... nfsroot=.../versions/<B>/root`
   on the serial console within a second of "Booting Linux" (line 31 of
   `pi-serial.log` in run 37020489834). `wait_for_pi_boot`
   (`tests/vm/run_tests.py:204-269`, which also reads the `.uboot` log) returns
   on the first "Booting Linux" in the file, so the harness looks only after
   the offset recorded before the cycle, or from the start if the file got
   shorter (QEMU reopens it).
4. While the Pi reboots, a server-side script checks that `current` names B,
   all markers are equal and TFTP serves B's cmdline; once step 3 has seen B,
   it rolls back to A and repeats the checks. The Pi is not rebooted again.

Estimate on the critical path: about 20-40 s (steps 2 to 4), plus any time by
which `verify-server.yml` and step 1 together outlast the Pi's boot and
`verify-pi.yml` (about 3 minutes). Median about 848-868 s if they do not. Tim
accepted the extra time on 2026-10-03. The PR that adds the test reports
measured before and after totals, and whether step 1 slowed the emulated Pi.

## Implementation order

Each PR is mergeable alone with green CI and `nfsroot_versioned` off. Steps 2
to 7 are exercised only by the pytest job until step 8.

1. Fix `fixpi/tasks/userconf.yml:121`.
2. The variables (including the versioned `tftp_root`), the guard, the layout
   step, and the nfsroot-watchdog checkout in the pytest job.
3. The deploy: `deploy.yml`, the img split, the template and writer edits, the
   build lock, the keys, the link-dest rsync and rename.
4. Exports.
5. TFTP: the `/srv/tftp` links and the `pxe`/`fixpi` verify tasks.
6. Publish wiring. **Needs `publish` (nfsroot-watchdog #9) and #10.**
7. `verify-server.yml` and `verify-pi.yml`. **Needs the client release with
   the `version:` line (#8).**
8. The VM test and the layout on for the CI VM. **Needs `rollback`, and an
   image with the `version:` line**, or `verify-pi.yml`'s old-client case
   fails.
9. Docs: the role README, `docs/access.md`, `CLAUDE.md`, with or before step
   10: after the cut-over, `touch .../root/etc/nfsroot-watchdog/inhibit`
   reaches only boards on that one tree.
10. tweed cut-over (key capture, then the migration). **Needs #9 and #10
    merged and an image built after both** (#10 changes the client).
    `list` is not required.
11. CI publishing: the image comparison and one revision tag per main commit,
    in `tests/ci/nfsroot_publish.py` and the promote guard, reviewed by CI. It
    must stay consistent with `nfsroot_publish.py --reuse` (input-based) and
    the `base-built` label in the #173 design (infra PR #181). It depends on
    neither nfsroot-watchdog nor the tweed layout, so it can land at any point.

## Risks: other open PRs on the same files

- **#45** (netboot clock): exception (b); see what it must do to fit.
- **#88** (fleet watchdog) must not count a board as dead while it waits for
  its slot.
- **#37** (trixie) changes `dist` and edits `fixpi/tasks/netboot.yml`; it
  becomes the next version. `nfsroot_legacy_dir` is a literal.
- **#55** (config.txt) edits `netboot.yml`, `tweeks.yml`, `verify-server.yml`.
- **#56** (per-site inventory) edits `host_vars/fpgas.online.yml`,
  `inventory/hosts` and `test-vm.yml`.
- **#58** (gateway API) edits `site.yml` and `verify-server.yml`.
- **#107** (`docs/ci.md` 3.2, 6.4, 8) describes the in-place rsync; whichever
  lands second updates it.
- **#181** (CI #173 design) is touched by "CI publishing".
