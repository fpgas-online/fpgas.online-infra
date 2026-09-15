# `fleet-watchdog`

Runs `fpgas-fleet-watchdog` (from `fpgas.online-poe`) as a systemd service on
the gateway. It sweeps every access port on every configured switch every 5
minutes, checks the attached board answers an SSH login, and PoE-cycles
anything that has failed two sweeps in a row or has been up more than 8 hours.

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
4. Dry-run a sweep and read what it proposes:
   ```bash
   sudo -u fleetwd HOME=/var/lib/fleet-watchdog \
     /opt/fpgas-switch/venv/bin/fpgas-fleet-watchdog \
     --config /etc/fpgas/watchdog.yml --once --dry-run --verbose
   ```
5. Set `fleet_watchdog_enabled: true` in `host_vars` and converge.
6. `journalctl -u fleet-watchdog -f` and watch one sweep.

## Reading the logs

Every line names the board as `sw<switch>/p<port> pi-sw<s>-p<p> <ip>`, so one
board's whole story is `journalctl -u fleet-watchdog | grep pi-sw2-p20`.

The line to worry about is `circuit breaker:`. It means most of the fleet
failed at once, which almost always means the watchdog is broken rather than
the fleet: a wrong key, a wrong user, a routing fault, a switch that stopped
answering, or an NFS root whose host key changed.

## Excluding a board

Add its port to `fleet_watchdog_exclude` in `host_vars`, keyed by switch index,
with a comment saying why. Trunk and uplink ports need no entry: the role hands
them to the switch as protected ports, so a write to one raises rather than
cutting the link to another switch.

Never set `force: true` on the key generation task. This is the single most
damaging mistake possible here: a regenerated key locks the service out of
every board until the next NFS root rebuild AND a full fleet power cycle.
