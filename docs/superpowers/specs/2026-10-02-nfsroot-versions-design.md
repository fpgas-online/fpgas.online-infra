# Multiple NFS roots per gateway: immutable generations

Status: draft for review, 2026-09-26. Nothing here is implemented.

## Problem

A gateway such as tweed exports exactly one Pi root, `/srv/nfs/rpi/<dist>/{boot,root}`,
and every update is written into it while the fleet is booted from it:

- `img/tasks/pull.yml` runs `rsync -aHAX --delete` from the pulled CI image into
  the live `root/`. Without `--inplace`, rsync writes each transferred file to a
  temp file and renames it into place, so every transferred file gets a **new
  inode**.
- `apt_cache/tasks/nfsroot.yml` and `fixpi` then write the site layer with
  Ansible `copy`/`template`/`lineinfile`, which also rename into place.

A booted board mounts that tree as the read-only lower layer of
`overlayroot=tmpfs`. Every replaced inode then answers `ESTALE` on that board
**until it reboots**. The two deploys on 2026-09-25 changed 12587 and 7418
files. `/home/pi/.ssh/authorized_keys` was among them, which broke key SSH
fleet-wide. nfsroot-watchdog exists to reboot every board after such an update.

### Why no mount option fixes it

This is overlayfs behaviour, not NFS caching. In v6.12
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
VFS only looks a name up again on 0, so the overlay dentry stays pinned to the
dead lower dentry. On a plain NFS mount the same rename heals on the next
lookup. That is why the damage is exactly "the files that were replaced" and
why only a reboot clears it. `Documentation/filesystems/overlayfs.rst`
(v6.12) states the rule: *"Changes to the underlying filesystems while part of
a mounted overlay filesystem are not allowed. If the underlying filesystem is
changed, the behavior of the overlay is undefined, though it will not result
in a crash or deadlock."*

What this rules out:

- **NFS caching options** (`lookupcache=none`, `actimeo=0`, `noac`) and NFSv4:
  they only change *when* NFS notices the change, and overlayfs still turns it
  into ESTALE.
- **Overlay options** (`index`, `xino`, `redirect_dir`, `metacopy`): per the
  same document, they make offline changes to the lower layer undefined too.
- **Keeping replaced inodes alive** (e.g. `rsync --backup-dir` on the same
  filesystem): it helps already-open files, but path lookups still fail
  revalidation.
- **Dropping the overlay** (a read-only NFS root plus tmpfs directories): files
  would heal, but running boards would silently mix old and new versions of
  the same package set.

The fix has to be on the server: **never change a tree a board is booted from.**

infra #128 (`rsync --checksum` on the pull) is a stopgap that shrinks the set of
replaced files to those whose bytes changed. This spec removes the problem.

### A second defect the same pull causes

The image carries no SSH host keys (`fixpi_generate_host_keys: false` in the CI
build). The pull's `--delete` therefore removes the site's keys, and fixpi's
"Pre-generate SSH host keys" task, which fires whenever they are absent,
generates new ones. So **every new image digest changes the fleet's host key**.
On tweed, the ed25519 key was `…IBlPFtDmvYsblUMvTiDNVLMGtIbJgeDeGj4SeG4hN/k2`
(recorded 2026-09-23, still in `bookworm.pre-pull-2026-09-25`). It is now
`…IBKgX405eQ+gs64ITckBsnzYc6xziAdAjHbisjfdDSzv`, written 2026-09-25 12:58:04Z by
the second pull. This design fixes it (see "Site state"), but it should also be
fixed on its own before this lands.

## Goals

1. A root update never modifies a tree any board is booted from. No ESTALE on
   the lower layer, ever, as a consequence of a deploy.
2. More than one root can be installed on a gateway at the same time:
   - successive **generations** of the same root (old boards stay on N while new
     boots get N+1);
   - different **roots** (e.g. `bookworm` and `trixie`, see infra #37), selectable
     per board.
3. Atomic switch-over and one-step rollback.
4. Canary rollout: pin one board to a new generation or root before switching the
   default.
5. A board's kernel, initramfs, DTBs and `/lib/modules` always come from the same
   generation.
6. nfsroot-watchdog keeps working. Its job changes from "the root is broken,
   reboot now" to "a newer generation is the default, reboot in your slot".

## Non-goals

- Changing how CI builds or publishes the image.
- Changing overlayroot, or the Pi-side mount options.
- Automatic garbage collection that could delete a generation a board is still
  using. GC is conservative by design (see "Garbage collection").

## Terms

- **root**: one image stream, named by `dist` (today only `bookworm`).
- **generation**: one complete, immutable `{boot,root}` pair of a root, built from
  one image digest plus one rendering of the site layer. Id:
  `<UTC yyyymmddThhmmssZ>-<first 12 hex of image digest>`, e.g.
  `20260926T031500Z-b41ea5c743cf`.
- **default**: the generation a board gets when nothing pins it.
- **pin**: a per-board override naming a root and optionally a generation.

## Layout on the gateway

```
/srv/nfs/rpi/
  tftp -> bookworm/current/boot         # dnsmasq tftp-root; flipping the default root = swap this
  bookworm/
    gen/
      20260925T125924Z-b41ea5c743cf/
        boot/                           # TFTP payload + cmdline naming THIS gen's root
        root/                           # the NFS lower layer; never written once published
        .image-digest
        .published                      # time it became default (absent while staging)
      20260926T031500Z-.../
    current -> gen/20260926T031500Z-... # swapped with rename(2): atomic
    staging/                            # a generation being built; renamed into gen/ on publish
  trixie/                               # a second root, same shape
  site-state/                           # persistent per-site files that are NOT image content
    ssh/ssh_host_{rsa,ecdsa,ed25519}_key{,.pub}
```

Sizes on tweed: a generation is ~4.8 GB root + ~133 MB boot. `/srv` is ext4 on
LVM with 188 GB free, so no btrfs or ZFS. A full copy per generation fits, and
`rsync --link-dest` shares files whose content **and** attributes (including
mtime) match the previous generation. This was tested locally: an identical
file is shared; an mtime-only or content change is copied; the previous
generation is untouched in every case.

## NFS exports

Replace the two per-tree lines in `roles/nfs/templates/exports.j2` with one
export of the parent:

```
/srv/nfs/rpi 10.21.0.1/16(ro,sync,no_subtree_check,no_root_squash)
```

NFSv3 clients may mount any subdirectory of an export, so every generation of
every root is reachable without an `exportfs` per generation. With
`no_subtree_check`, file handles stay valid for the life of the inode, whatever
the path. Because every generation is mounted through this one export, adding
or removing generations never changes an export a board depends on. An export
line must never be removed while a board is booted through it (see
"Migration").

## Boot selection

### Default

Each generation's `boot/cmdline.txt`, `boot/cmdline-pi5.txt` and
`boot/pxelinux.cfg/default-arm-sunxi` name **that generation's own** root
explicitly:

```
nfsroot=10.21.0.1:/srv/nfs/rpi/bookworm/gen/<id>/root,nfsvers=3,tcp ro ... overlayroot=tmpfs
```

A generation is therefore self-consistent: a board that TFTP-booted from its
`boot/` mounts its `root/`. The cmdline deliberately does not name `current`.
A board must stay on the tree it booted, and whether rpc.mountd resolves a
symlink at mount time is not something to rely on.

dnsmasq's `tftp-root` becomes the stable path `/srv/nfs/rpi/tftp`, a symlink to
`<default root>/current/boot`. Changing the default generation swaps `current`;
changing the default root swaps `tftp`. Both swaps are `ln -sfn` to a temp name,
then `rename(2)`.

**Mid-boot race.** A board that is part-way through its TFTP fetches at the
instant of the swap can get early files from N and later ones from N+1. That
only matters if the kernel changed between them, and a board that fails to
boot is caught by the fleet watchdog (infra #88) or a person. This is accepted,
not solved.

### Pins (per board)

Pins are keyed on board identity, not placement, consistent with the
`hat_uuid`/MAC rule:

- **Raspberry Pi**: the bootloader first requests `<serial>/<file>`, and only
  when `<serial>/start4.elf` is absent does it clear the prefix and use the
  root (the comment on `tftp_root` in `group_vars/all/srv.yml`; `TFTP_PREFIX` in
  the Raspberry Pi bootloader docs). A pin is a `<serial>` symlink inside the
  default generation's `boot/` pointing at the pinned generation's `boot/`.
- **Orange Pi (sunxi, U-Boot)**: U-Boot's PXE client tries
  `pxelinux.cfg/01-<mac>` before `default-arm-sunxi`. A pin is such a file whose
  kernel, initrd, FDT and `nfsroot=` paths name the pinned generation.

`boot/` may be written after publish. It is only read over TFTP; the boards
mount `/boot/firmware` `noauto` (`fixpi/templates/etc/fstab.j2`), so nothing
holds its inodes. Pins are data (`nfsroot_pins` in the inventory, keyed by
serial or MAC). They are rendered into the default generation's `boot/` at
publish and whenever the pins change, and removed when they are dropped.

**To verify before relying on it:**

- Pi 3B+ (bootcode.bin network boot) uses the same `<serial>/` prefix.
- dnsmasq follows a symlink inside `tftp-root` whose target is outside it.
  `tftp-secure` is not set today.

### ps1 (legacy MAC-table host)

ps1 has no `switches`, so `tftp_root` is `/srv/tftp` with per-serial symlinks
(`switch.nos`). The same model applies: those symlinks point at
`<root>/current/boot`, or at a pinned generation, instead of at the single
`<dist>/boot`.

## Building and publishing a generation

The last `nbp` play in `site.yml` keeps its order, with a different target
directory:

1. `nfsroot_generation` **begin**: create `staging/` (empty it if a failed run
   left one), and record the image digest.
2. `img`: `rsync -aHAX --numeric-ids --checksum --link-dest=<current>/root
   <image>/root/ staging/root/`, and the same for `boot/`. There is no
   `--delete` against a live tree any more; `staging/` starts empty.
3. `apt_cache` `nfsroot.yml` and `fixpi`: unchanged, except that `nfs_root` now
   means `staging/` for this play (see "Changes by file").
4. Site state (below) is copied in from `site-state/`.
5. **Compare** `staging/` with `current/` (`rsync -rlpgoDc --dry-run
   --itemize-changes`, ignoring the watchdog's ignore list). If nothing differs,
   delete `staging/` and stop: an unchanged converge makes no generation. This
   replaces the ctime scan `nfsroot-generation end` does today.
6. `verify-server`'s NFS-root checks run against `staging/`, **before** anything
   can boot from it. This is a new gate: a broken root today is live the moment
   it is written.
7. **Publish**: rename `staging/` to `gen/<id>/`, write `.published`, render the
   pins into its `boot/`, then swap `current`.
8. **Tell the old generations** (next section).

A run that fails anywhere before step 7 leaves `current` untouched and the
fleet unaffected. The update lock (`update.lock`) protects nothing any more,
because nothing live is written, so `begin` no longer needs to hold the boards.

## nfsroot-watchdog

Today the client reads its protocol files as `$LOWER$GEN_FILE`,
`$LOWER$LOCK_FILE` and `$LOWER$FLEET_INHIBIT` (`src/nfsroot-watchdog-check`),
always **inside the NFS root it booted from**. A board on generation N never
reads anything in N+1.

**Recommended (no client change):** at publish, `nfsroot-generation` writes the
new marker into `root/etc/nfsroot-watchdog/generation` of **every** published
generation that is not the new default. That directory is the only path in a
published root that is ever written again, and it is already excluded from the
pull. The client reads it through `$LOWER` (`/media/root-ro`), which is the
**plain NFS mount, not the overlay**, so a rename there heals on the next
lookup like any NFS file. Trigger 1 ("generation differs from the one it booted
with") then fires, and the board reboots in its stagger slot onto the default.
The fleet inhibit is written to, and removed from, every published generation
the same way.

**Alternative:** a separate small `control/` export mounted on every board,
with a new `CONTROL=` prefix in nfsroot-watchdog for the three protocol files.
This is cleaner in principle, but needs a client code change, a mount unit,
and the first-rollout cycle again. It is not needed for the goals above.

Server side, `nfsroot-generation` gains:

- `publish <base> <id>`: swap `current` and write the markers;
- `rollback <base> [<id>]`: swap `current` back and write the markers;
- `list <base>`: generations, which is default, and which are pinned.

Trigger 3 (the NFS mount itself is stale) now fires only when a generation is
deleted under a board. That is GC's safety net, not a normal path.

## Site state

Anything that must be identical across generations, and that is not image
content, moves out of the root into `/srv/nfs/rpi/site-state/` and is copied
into each staging tree:

- **SSH host keys**: generated there once, if absent, by the task that
  generates them in the root today. This fixes the host-key churn above.
- Anything else fixpi *generates* rather than *renders from inventory*: audit
  `fixpi` and `onpi` for `stat` + create-if-absent patterns. Everything rendered
  from the inventory is already reproducible.

## Garbage collection

A generation may be deleted only when **no board is booted from it**. NFSv3 gives
the server no reliable per-path client list, so the boards must say so:

- Fleet self-registration (site `/fleet/<serial>/`) gains the board's root and
  generation, parsed from `nfsroot=` in `/proc/cmdline`.
- GC (a site.yml task and `nfsroot-generation gc`) deletes a generation only if
  **all** of these hold:
  - it is not `current` of any root;
  - no pin names it;
  - no board the registry lists as online reports it;
  - its `.published` time is more than `nfsroot_gc_min_age` ago (default 7
    days, which also covers boards pinned by a local `nfsroot-watchdog
    inhibit`);
  - more than `nfsroot_gc_keep` (default 3) generations of that root would
    remain.
- If the registry cannot be reached, GC does nothing.

Space is not tight (188 GB free, ~5 GB per generation), so there is no pressure
to be clever.

## Rollback

`nfsroot-generation rollback`, or `site.yml -e nfsroot_rollback_to=<id>`, swaps
`current` back to a kept generation and writes the markers. Boards on the bad
generation reboot onto it in their slots, and boards already on it stay put.
No image pull or site-layer run is needed.

## Migration from the single-root layout

1. The first run with this design builds `bookworm/gen/<id>/` next to the
   existing `bookworm/{boot,root}`, adds the parent export, points `tftp` at the
   new `current`. It **keeps** the old per-tree export lines. A booted board's
   file handles name the export they were issued through (the fsid in a knfsd
   handle identifies the export point). Removing those lines would make every
   booted board's whole mount stale at once, which is the very failure this
   design removes. They go only when the old tree is GC'd.
2. It writes the new generation marker into the old
   `bookworm/root/etc/nfsroot-watchdog/generation`. Boards that already run
   nfsroot-watchdog reboot onto the new layout in their slots. Boards that don't
   need the manual cycle they needed anyway.
3. The old `bookworm/{boot,root}` becomes a GC candidate like any generation;
   the registry tells us when no board uses it.

## Changes by file (fpgas.online-infra)

| file | change |
|---|---|
| `inventory/group_vars/all/srv.yml` | new `nfsroot_base: /srv/nfs/rpi/{{ dist }}`; `nfs_root` defaults to `{{ nfsroot_base }}/current` for readers; `tftp_root: /srv/nfs/rpi/tftp` on per-port hosts |
| `site.yml` last `nbp` play | `set_fact: nfs_root: "{{ nfsroot_base }}/staging"` for the build roles, then the compare/verify/publish steps. the many `nfs_root` references in `fixpi`, `apt_cache` and `img` need no edit |
| `roles/img/tasks/pull.yml` | extract into an empty `staging/` with `--link-dest`; no `--delete` against a live tree; the digest stamp moves to the generation |
| `roles/nfsroot_generation` | begin = create staging; end = compare, publish, write markers; new gc and rollback tasks |
| `roles/nfs/templates/exports.j2` | one parent export |
| `roles/pxe/templates/dnsmasq-base.conf.j2` | none (`tftp_root` changes) |
| `roles/fixpi/templates/boot/*.j2` | `nfsroot=` names the generation (it already uses `nfs_root`, which is the staging path at render time; it must render the **final** `gen/<id>` path instead) |
| `roles/fixpi/tasks/netboot.yml` | host keys copied from `site-state/`, generated there if absent |
| `verify-server.yml`, `roles/*/tasks/verify` | check `staging/` before publish, and `current` + exports + `tftp` symlink after |
| `verify-pi.yml` | assert the booted `nfsroot=` is `current` (or the board's pin) |
| fpgas.online-site | registry field for the booted root/generation |
| nfsroot-watchdog | server subcommands `publish`/`rollback`/`list`/`gc`; no client change |

## Testing (VM CI)

The VM test can prove the property directly. It needs one addition:

1. Converge as today; the virtual Pi boots generation A.
2. Converge again with a changed site layer (e.g. a test-only file under
   `/etc`), producing generation B, **while the Pi is still up**.
3. Assert on the Pi that `/etc/<changed file>` and `authorized_keys` still read
   without ESTALE, and that `nfsroot-watchdog status` reports the pending
   generation.
4. Let the watchdog reboot the Pi (CI slot override), then assert it booted B's
   `nfsroot=` path.
5. Rollback to A, and assert the same.

This test would have failed on every deploy before this design, which makes it
the regression test for the whole ESTALE class.

## Risks and open questions for Tim

1. **Scope of "multiple roots".** Is per-board selection of a *different root*
   (bookworm vs trixie) wanted now, or only generations of one root? The layout
   supports both; the pins and the registry field can come later.
2. **GC policy defaults**: keep 3 generations and 7 days. Should an inhibited
   board block GC indefinitely?
3. **Host-key fix first**: fix the host-key churn as its own small PR now, by
   excluding `/etc/ssh/ssh_host_*` from the pull's `--delete`, or wait for this
   design?
4. **Pi 3B+ serial prefix and dnsmasq symlinks** must be verified on hardware
   and in the VM before pins are relied on. Default and rollback do not depend
   on them.
5. **Kernel payload.** Each generation carries its own matched boot payload.
   That removes the old "never sync the netboot payload with a userspace
   deploy" caution, because a board only ever boots a pair built together.
   Kernel changes still roll out to the whole fleet through the stagger, so
   canary-pinning one board to a kernel-changing generation first is advised.
