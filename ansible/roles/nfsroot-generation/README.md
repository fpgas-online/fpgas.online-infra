# nfsroot-generation: the netbooted boards reboot themselves after a root update

## The problem

The netbooted boards run `overlayroot=tmpfs` over a read-only NFSv3 export
of `{{ nfs_root }}/root`. When a `site.yml` run replaces a file in that tree
(dpkg unpacking a package, dpkg rewriting `/var/lib/dpkg/status`, Ansible's
`copy`/`template`, `ssh-import-id` rewriting `authorized_keys`), every board
that booted earlier keeps a handle to the old inode. From then on that one
file answers `Stale file handle` through the overlay. Measured on
pi-sw2-p33 on 2026-09-24, the morning after a rebuild:

- `dpkg-query` fails: `/var/lib/dpkg/status`
- key-based ssh fails fleet-wide: `/home/pi/.ssh/authorized_keys`
- the camera streams, `sshd`, `systemctl` and every untouched binary keep
  working, so the fleet looks healthy from outside.

The only fix is a reboot. Before this role that meant a hand-run,
one-board-at-a-time PoE cycle after every root update.

## How the two halves fit

| when | where | what |
|------|-------|------|
| first task of `site.yml` | server, `tasks/begin.yml` | write `/etc/fpgas-online/nfsroot-update.lock` into the root (kept if already there) |
| `pi` play start / end | chroot, `pi_started.yml` / `pi_done.yml` | facts that `end.yml` checks |
| last task of `site.yml` | server, `tasks/end.yml` | scan the root for files with a ctime newer than the lock (`files/nfsroot_changed.py`); if any, write a new `/etc/fpgas-online/nfsroot-generation`; then delete the lock |
| every minute on each board | `roles/onpi/tasks/stale_root.yml` | `fpgas-stale-root.timer` reads the lock and marker through `/media/root-ro` (the NFS lower, which sees the new files) and reboots when the marker differs from the one it booted with |

Each board reboots at `marker time + 60 s + slot × 20 s`, with up to 10 s of
jitter. The slot is `(switch − 1) × 48 + (port − 1)` from its `pi-sw<S>-p<P>`
hostname, so no two boards on a site ever share a slot, and a whole
two-switch site is done within about 33 minutes. The checker, and the static
busybox it runs under, are copied into `/run` at boot, so a root that has gone
stale cannot stop the reboot. The full decision logic is at the top of
`roles/onpi/files/stale-root/fpgas-stale-root-check`.

A second trigger covers root changes made outside `site.yml`: probe files
(`dpkg/status`, both `authorized_keys`, the marker itself) that stay stale on
two consecutive checks, once the lower root's busiest directories have been
untouched for an hour.

## Operating it

- **Pin one board** (e.g. for a JTAG session): on the board,
  `sudo touch /run/fpgas-no-auto-reboot`. The pin lasts until the next reboot.
- **Pin the whole fleet**: on the server,
  `touch {{ nfs_root }}/root/etc/fpgas-online/no-auto-reboot`; remove it to
  release the fleet. The change scan ignores this file.
- **Converge without rebooting anyone**: `-e nfsroot_generation_bump=never`.
  Boards keep their stale handles until they next reboot for another reason.
- **Force a fleet reboot** after a change the scan cannot see:
  `-e nfsroot_generation_bump=always`.
- **Dry run on the boards**: set `stale_root_dry_run: true` (roles/onpi). The
  boards then only log `DRY_RUN: would reboot`.
- **Where it logs**: `journalctl -u fpgas-stale-root -u fpgas-stale-root-arm`
  on the board. Schedule and reboot decisions also go to the kernel log, which
  netconsole sends to the server, so the reason for a reboot survives it.
- Logged-in users (`who`) delay a due reboot by up to an hour.

## When the lock is stuck

A `site.yml` run that fails part-way leaves
`{{ nfs_root }}/root/etc/fpgas-online/nfsroot-update.lock` behind on purpose:
the root may be half-upgraded, and the boards stay on the old one rather than
reboot into it. `verify-server.yml` fails while the lock exists. Fix whatever
broke and re-run `site.yml`. The new run keeps the old lock's start time, so
its end step publishes both runs' changes. The boards ignore a lock older than
24 hours (`stale_root_lock_max_age`).

## First rollout

Boards only get the checker by booting a root that contains it. After the
first `site.yml` run that carries this role, every board still needs one
last manual PoE cycle (one board at a time, as before). From then on they
handle themselves.

## Interaction with the fleet watchdog (infra #88, not yet enabled)

The watchdog PoE-cycles a board that fails its SSH check on two sweeps in a
row (10 minutes). A stale `authorized_keys` makes that SSH check fail on
every board at once, well before most boards reach their stagger slot. If
both are enabled, the watchdog must not count a board as dead while
`/etc/fpgas-online/nfsroot-generation` is newer than the board's uptime, or
it will cycle the fleet together, which is exactly what the stagger exists
to avoid.
