# nfsroot_generation: the netbooted boards reboot themselves after a root update

The mechanism is the generic
[fpgas-online/nfsroot-watchdog](https://github.com/fpgas-online/nfsroot-watchdog)
package. Its README covers the stale-file-handle problem, both triggers, the
stagger and the lock protocol. This file covers how fpgas.online wires it in.

## Why

Rebuilding tweed's Pi NFS root replaces files the running boards still hold.
Measured on pi-sw2-p33 on 2026-09-24, the morning after a rebuild: every
replaced file answered `Stale file handle`. That broke `dpkg-query`, and
because `/home/pi/.ssh/authorized_keys` was among the replaced files, it
broke key-based ssh on every board at once. The camera streams kept running,
so the fleet looked healthy. Before this, the fix was a hand-run,
one-board-at-a-time PoE cycle after every root update.

## The pieces

| where | what |
|-------|------|
| `site.yml` nbp play, `pre_tasks` (`tasks/begin.yml`, tagged `always`) | installs `nfsroot-watchdog-server` on the gateway, then `nfsroot-generation begin <root>`: the update lock at `/etc/nfsroot-watchdog/update.lock` |
| `img`, `apt_cache`, `fixpi` | the only things that change the root on the gateway: img rsyncs the CI-built image into it (excluding `/etc/nfsroot-watchdog/`, so a pull never deletes the lock, the marker or the fleet inhibit), apt_cache rewrites its apt sources, fixpi applies the site layer |
| `site.yml` nbp play, the `nfsroot_generation` role right after `fixpi` (`tasks/end.yml`) | `nfsroot-generation end <root>`: bumps `/etc/nfsroot-watchdog/generation` if any file changed (ctime scan), then removes the lock. If a role before it failed, the play has stopped and the lock stays |
| `roles/fpgas_apt` (`tasks/nfsroot-watchdog.yml`, via the shared `tasks/repo.yml`) | the package's apt source: through the gateway's apt cache (remap `nfsrootwatchdog`) for the Pi root; for the gateway, upstream, or the site caching proxy's `/nfsroot-watchdog` remap when `nfsroot_generation_apt_cache` is set (tweed: `https://apt-proxy.welland.mithis.com`, ten64's apt-cacher-ng, which caches https repositories only through a remap); key pinned by fingerprint |
| `roles/onpi`, `tasks/stale_root.yml` (in the CI image build) | installs `nfsroot-watchdog` in the root and writes `/etc/default/nfsroot-watchdog`: fpgas.online's probe files (including both `authorized_keys`) and the switch-placement stagger |

The stagger is `SLOT_SED='s/^pi-sw([0-9]+)-p([0-9]+)$/\1 \2/'`, stride 48,
base 49, so slot = (switch − 1) × 48 + (port − 1). Each board reboots at
`generation time + 420 s + slot × 20 s`. No two boards on a site share a slot,
and a two-switch site is done within about 40 minutes. At least five minutes
before its reboot, each board broadcasts a warning, with how to stop it, to
its console, every logged-in terminal (including the web terminal), the
journal and the kernel log.

## Operating it

- **Pin one board** (e.g. for a JTAG session): `sudo nfsroot-watchdog inhibit`
  on the board. The pin lasts until the next reboot, or `nfsroot-watchdog release`.
- **See what a board plans**: `nfsroot-watchdog status`.
- **Pin the whole fleet**: on the gateway,
  `touch {{ nfs_root }}/root/etc/nfsroot-watchdog/inhibit`. Remove it to
  release the fleet.
- **Converge without rebooting anyone**: `-e nfsroot_generation_bump=never`.
  Boards keep their stale handles until they next reboot for another reason.
- **Force a fleet reboot**: `-e nfsroot_generation_bump=always`.
- **Dry run on the boards**: `onpi_nfsroot_watchdog_dry_run: true` (roles/onpi).
- **Hand edits in the chroot**: wrap them so the boards wait and then pick
  them up: `nfsroot-generation run {{ nfs_root }}/root -- chroot ... apt ...`.
- **Logs**: `journalctl -u nfsroot-watchdog -u nfsroot-watchdog-arm` on the
  board. Decisions also go to the kernel log, which netconsole carries to the
  gateway.

## When the lock is stuck

A `site.yml` run that fails part-way leaves the lock behind on purpose: the
root may be half-upgraded, and the boards stay on the old one rather than
reboot into it. `verify-server.yml` fails while the lock exists. Fix whatever
broke and re-run `site.yml`. The new run keeps the old lock's start time, so
its end step publishes both runs' changes. Boards ignore a lock older than 24
hours.

## First rollout

Boards only get the watchdog by booting a root that contains it. After the
first `site.yml` run that carries this role, every board still needs one last
manual PoE cycle (one board at a time, as before). From then on they handle
themselves. Installing the package (in the CI image build) swaps the root's
dynamic `busybox` for `busybox-static`.

## Interaction with the fleet watchdog (infra #88, not yet enabled)

The watchdog PoE-cycles a board that fails its SSH check on two sweeps in a
row (10 minutes). A stale `authorized_keys` makes that SSH check fail on every
board at once, well before most boards reach their stagger slot. If both are
enabled, the watchdog must not count a board as dead while the root's
generation marker is newer than the board's uptime. Otherwise it will cycle
the fleet together, which is exactly what the stagger exists to avoid.
