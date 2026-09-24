# `fleet-watchdog`

Runs `fpgas-fleet-watchdog` (from `fpgas.online-poe`) as a systemd service on
the gateway. It sweeps every access port on every configured switch every 5
minutes, checks the attached board answers an SSH login, and PoE-cycles
anything that has failed two sweeps in a row or has been up more than 8 hours.

It also puts unusable ports back into service: it clears PoE faults and
re-enables ports that have been switched off (see **Port recovery** below).

Design: `docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md`.

## What this role does and does not do

It creates the `fleetwd` account, generates its SSH key, authorises that key
for `pi` in the Pi NFS root, pins the NFS root's host key into a `known_hosts`,
renders config and the unit, and sets the service state.

The `watchdog.env` template only emits a switch's SNMP write community when
that switch has `snmp_rw_community` defined in its inventory entry. A switch
without one is simply left unwatched rather than failing the whole converge;
`fpgas-fleet-watchdog` logs it at runtime. Nothing else about the role's
behaviour changed from the design.

It installs **no software**. `roles/switch-vlans` already pip-installs
`fpgas-online-poe[cli]` into `/opt/fpgas-switch/venv` on every converge, and
the console script comes with it.

It runs in the `nbp` play **after `pxe`**, because it writes into the NFS root
and both `img` (fresh image extract) and `fixpi` (authorized_keys) run earlier
in that play.

## Turning it on

It ships disabled, and that is not timidity. The key only reaches the boards
through the NFS root, so before the root is updated every probe fails. The
circuit breaker then refuses to cycle anything and logs an alarm, which is
correct but useless.

1. Converge normally. The key is generated and authorised in the NFS root.
2. Update the NFS root and cycle the fleet:
   `site.yml --tags pi,fpgas-apt,onpi,cam` detached from ten64, then a fleet
   PoE cycle.
3. Check the key works by hand:
   ```bash
   sudo -u fleetwd ssh -n -o BatchMode=yes -o StrictHostKeyChecking=yes \
     -o UserKnownHostsFile=/var/lib/fleet-watchdog/known_hosts \
     -i /var/lib/fleet-watchdog/id_ed25519 pi@10.21.2.42 'cat /proc/uptime; who'
   ```
4. Dry-run a sweep and read what it proposes. It exits non-zero if the sweep
   is unhealthy, so `echo $?` is part of the check. The SNMP write communities live
   only in `/etc/fpgas/watchdog.env` (0600, owned by `fleetwd`), which
   systemd's `EnvironmentFile=` reads for you when the service runs -- by
   hand you must source it yourself, or `community_for()` raises, every
   switch is skipped, and the sweep reports `occupied=0`, looking exactly
   like a working, empty fleet instead of a broken dry run:
   ```bash
   sudo -u fleetwd bash -c '
     set -a; . /etc/fpgas/watchdog.env; set +a
     export HOME=/var/lib/fleet-watchdog
     /opt/fpgas-switch/venv/bin/fpgas-fleet-watchdog \
       --config /etc/fpgas/watchdog.yml --once --dry-run --verbose
   '
   ```
5. Set `fleet_watchdog_enabled: true` in `host_vars` and converge.
6. `journalctl -u fleet-watchdog -f` and watch one sweep.

## Port recovery

A port that is not delivering PoE has no board to probe, so nothing the
watchdog does to boards can ever bring it back. Two states get it there:

* **PoE fault** -- the switch cut the port to protect itself from an
  over-current or a short. Never a deliberate operator state, so the watchdog
  clears it: it re-arms the port and polls until the fault clears to
  delivering or searching.
* **Switched off by this service** -- a cycle died between the off and the
  on. The watchdog remembers the ports it switched off and turns those back
  on.

A port switched off by anyone *else* is left off and reported on every sweep.
The board page's PoE control turns a port off and leaves it off with no timer,
so re-enabling those would make the site's own off switch stop working within
five minutes. That memory does not survive a restart, which is why the
reporting matters: after a restart, a port this service stranded looks exactly
like one a person switched off, so it is named every sweep rather than quietly
fixed or quietly ignored. Turn it back on from the board page or with
`fpgas-switch` when you see it.

A port whose PoE state the switch reports as `unknown` is also reported and
never acted on: the watchdog will not guess at a fix for a state it cannot
read.

Fault clearing is capped by `fleet_watchdog_max_recovery_attempts` (3). A port that
will not stay in service is reported on every sweep as needing on-site
attention rather than re-armed forever. The count resets the moment the port
is back in service.

## When it gives up

The service exits non-zero rather than logging into a journal nobody reads:

* a sweep that raised -- a bad config, an unreadable switch list, a bug --
  exits immediately;
* `fleet_watchdog_unhealthy_exit_after` (3) consecutive sweeps that found no
  occupied ports at all, or tripped the circuit breaker, also exit.

`Restart=always` brings it back. If the condition persists, the unit's
`StartLimitBurst` is exhausted and systemd puts it in **failed**, which is the
point: `systemctl is-failed fleet-watchdog` is the only health signal that
exists outside the journal, and a watchdog that has stopped watching otherwise
looks exactly like one with nothing to do.

A structural fault burns that budget in about two minutes; a persistently
unhealthy fleet takes about an hour. After fixing the cause, clear the state
with `systemctl reset-failed fleet-watchdog && systemctl start fleet-watchdog`.

One switch being unreachable is deliberately NOT fatal. It is logged every
sweep and its ports are skipped, because exiting would stop watching the
switches that are answering, and a restart cannot fix an unreachable switch.
When *no* switch answers, the sweep finds nothing and that does count.

## Reading the logs

Every line names the board as `sw<switch>/p<port> pi-sw<s>-p<p> <ip>`, so one
board's whole story is `journalctl -u fleet-watchdog | grep pi-sw2-p20`. A
failing board is named on every sweep it fails, with the reason and how close
it is to being cycled -- not just the first time.

The sweep line counts the whole picture:

```
sweep: ports=48 occupied=35 ok=34 failed=1 internal=0 in_use=2 faulted=1 off=0 cycling=0 deferred=1
```

`internal=` is the one that means *this service* is broken rather than a
board: the probe raised instead of the board failing to answer. Those are
logged with a traceback and are never cycled on, because a bug here is not a
reason to cut power to somebody's hardware.

The line to worry about is `circuit breaker:`. It means most of the fleet
failed at once, which almost always means the watchdog is broken rather than
the fleet: a wrong key, a wrong user, a routing fault, a switch that stopped
answering, or an NFS root whose host key changed. It names the failing boards
on a second line.

## Excluding a board

Add its port to `fleet_watchdog_exclude` in `host_vars`, keyed by switch index,
with a comment saying why. Excluded ports are dropped from the scan before
anything decides to act, so they are never probed, never cycled, and never
recovered -- this is the only way to stop the watchdog touching a port.

Trunk and uplink ports need no entry: the role hands them to the switch as
protected ports, so a write to one raises rather than cutting the link to
another switch.

Never set `force: true` on the key generation task. This is the single most
damaging mistake possible here: a regenerated key locks the service out of
every board until the next NFS root rebuild AND a full fleet power cycle.
