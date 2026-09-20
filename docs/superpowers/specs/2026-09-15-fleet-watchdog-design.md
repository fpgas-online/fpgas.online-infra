# Design: fleet watchdog (`fleet-watchdog` role, `fleet_watchdog` module)

Date: 2026-09-15
Status: implemented 2026-09-16 (`roles/fleet-watchdog`, `fleet_watchdog` in
fpgas.online-poe). Plan: `docs/superpowers/plans/2026-09-15-fleet-watchdog.md`.
Revised 2026-09-20 after a fail-loud review -- see **Failing loud** below.

## Problem

Nothing watches the welland fleet. A board that hangs stays hung until a human
notices and power-cycles its switch port by hand.

This is not hypothetical. The 2026-09-15 fleet enumeration found four sw2 ports
that had been written off as empty but in fact held real devices that had merely
hung, and recorded that sw2 ports 18-24 (Orange Pi PC / Allwinner H3 boards
sharing the Pis' armhf NFS root) hang repeatedly: ports 18 and 19 dead since
3 September, port 24 since 14 September, and ports 20, 21 and 23 dead again
within 15 hours of a successful cycle on 2026-09-14. In every case a PoE cycle
revived the board.

Recovery today is a human running `tmp/cycle_pi.py` from a laptop. That script
is a throwaway: it shells out to `ngsw` on ten64 over SSH, hard-codes the two
switches and their write communities, and has no scheduling, no retry policy and
no safety rails.

Separately, boards that stay up for a long time drift from a known-good state.
Visitors leave background processes, bitstreams and mounted overlays behind.
Since the NFS root is read-only with a tmpfs overlay, a reboot restores a board
exactly, so periodic recycling is cheap and effective.

**Requirement (Tim, 2026-09-15):** a service on tweed walks every switch port,
checks the attached device answers and accepts an SSH login, and PoE-cycles it
(off, 30 seconds, on) when it does not. It also cycles any device up for more
than 8 hours. It deploys as part of the tweed Ansible setup.

## Non-goals

- A fleet status page on the Django site, or any HTTP surface. Reporting is the
  journal only (Tim, 2026-09-15).
- A persistent state file, time-series store or metrics exporter.
- Fixing sw2 p11, whose link never comes up and which a PoE cycle does not
  revive. It needs on-site attention and is excluded instead.
- Using the TinyTapeout daemon's `clients` counter as a second "in use" signal.
  The `fpgas_tt` daemon reports it on `:8765/health`, but it covers only TT
  boards and only Commander websockets, not the web terminal.
- Replacing `/snmp/toggle`, the board pages' Reset button, or `tmp/cycle_pi.py`.
- Any change to ten64 or to `ngsw`.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Code home | `fpgas.online-poe` | Already owns switch access, already depends on `python-netgear-switch-library`, already installed on tweed. infra holds no application code (its CLAUDE.md). Tim, 2026-09-15. |
| Occupancy test | PoE delivering, MAC table ignored | Tim, 2026-09-15: "All the ports which are drawing PoE power most certainly have a device connected to them." A board hung hard enough to stop transmitting ages out of the MAC table, and that is precisely the board to rescue. |
| Port scope | Every access port 1..`access_ports`, minus exclusions | Tim, 2026-09-15. |
| Process model | Long-lived systemd service, not a timer | Failure counters and the post-cycle grace live in memory; journald-only reporting leaves no state file to reload. |
| Cadence | 300 s sweep, cycle after 2 consecutive failures | Tim, 2026-09-15. Rides out a transient SSH timeout; worst case is about 10 minutes from death to power cycle. |
| Escalation | Keep cycling, indefinitely | Tim, 2026-09-15, having been shown the hardware-wear tradeoff. Hopeless boards are handled by the exclusion list, not by giving up. |
| Reporting | journald only | Tim, 2026-09-15. |
| Safety rail | Refuse to cycle when most of the fleet fails at once | Tim, 2026-09-15. |
| SSH identity | Dedicated watchdog key in the NFS root | Tim, 2026-09-15, re-confirmed after being shown that it needs a root rebuild and fleet cycle first. |
| Uptime hard cap | 12 h | Tim, 2026-09-15. |
| Enabled on deploy | No, ships disabled | The key reaches boards only after an NFS root update and fleet cycle. |
| Port recovery | Clear PoE faults and re-enable switched-off ports | Tim, 2026-09-20: "The watch dog should also be clearing any PoE fault status and pushing the ports back into searching/delivering state." |
| Unknown config keys | Fatal | Tim, 2026-09-20. A typo that loads as a default is a fleet cycled on a rule nobody wrote. |
| Persistent trouble | Exit and let systemd restart | Tim, 2026-09-20: "When things are going wrong, the daemon should exit and systemd restarts it." |

## Components

### `fleet_watchdog` (new package in `fpgas.online-poe`)

A new `src/fleet_watchdog/` alongside the existing `snmp_switch` and
`switch_setup` packages, exposed as console script `fpgas-fleet-watchdog` in
`pyproject.toml` `[project.scripts]` and added to
`[tool.hatch.build.targets.wheel] packages`.

| Module | Responsibility |
|---|---|
| `config.py` | Load `/etc/fpgas/watchdog.yml` into a frozen `WatchdogConfig`; resolve switch specs and communities. |
| `switches.py` | Build a `SyncSwitch` per switch index, with `protected_ports` populated. Enumerate occupied ports. |
| `probe.py` | The SSH health check, behind a plain callable so tests inject a fake. |
| `policy.py` | Pure decision function: state plus observations in, actions out. No I/O, no clock, no sleeping. Also owns the `Observation` type, so nothing in the decision path imports anything that touches the network. |
| `cycle.py` | Execute a PoE cycle with the 30 second dwell. |
| `service.py` | The sweep loop, logging and signal handling. |
| `cli.py` | Argument parsing, `--once`, `--dry-run`, logging setup. |

`policy.py` holding no I/O is the point of the split. Every rule below is then
testable with a fake clock and no switch, no network and no sleeping.

### `roles/fleet-watchdog` (new infra role)

Runs in the `nbp` play in `ansible/site.yml`, **after `pxe`**, guarded by
`when: switches is defined`. After `pxe` because the role writes into the NFS
root, and `img` (fresh extract) and `fixpi` (authorized_keys) run earlier in
that play; running before them would have the key wiped by the next extract.

The role installs no software. `roles/switch-vlans` already pip-installs
`fpgas-online-poe[cli]` into `/opt/fpgas-switch/venv` with
`state: forcereinstall`, so `fpgas-fleet-watchdog` appears there on every
converge. The role depends on that and does not create a second venv.

Tasks:

1. Create the `fleetwd` system user and `/var/lib/fleet-watchdog` (0700).
2. Generate `/var/lib/fleet-watchdog/id_ed25519` with
   `community.crypto.openssh_keypair` (idempotent; never regenerates).
3. Authorise its public key for `pi` in the NFS root, via
   `ansible.posix.authorized_key` with
   `path: {{ nfs_root }}/root/home/pi/.ssh/authorized_keys`. Additive, so the
   `videoteam` and controller keys `fixpi` installs are preserved.
4. Render `/var/lib/fleet-watchdog/known_hosts` from the NFS root's
   `/etc/ssh/ssh_host_ed25519_key.pub`, one entry per managed IP.
5. Render `/etc/fpgas/watchdog.yml` (0644, no secrets).
6. Render `/etc/fpgas/watchdog.env` (0600, owner `fleetwd`) with
   `FPGAS_SWITCHES_CONFIG` and `FPGAS_SWITCH_COMMUNITY_<index>`, mirroring
   `roles/site/templates/gunicorn-poe.conf.j2`.
7. Render and enable `fleet-watchdog.service`.

## Port model

`ansible/filter_plugins/port_vlans.py` (which registers the `port_vlan_map`
filter) is the single source of truth on the Ansible side and the watchdog
reproduces its formulas, which are fixed by the VLAN-per-port design:

| Quantity | Formula | Example (switch 2, port 42) |
|---|---|---|
| IPv4 | `<pib_network>.<switch>.<port>` | `10.21.2.42` |
| Hostname | `pi-sw<switch>-p<port>` | `pi-sw2-p42` |
| VLAN | `2000 + 100*switch + port` | `2242` |

`pib_network` is `10.21` on tweed. The watchdog takes the prefix from its config
file rather than hard-coding it.

### Occupied ports

For each switch in `/etc/fpgas/switches.yml`, ports `1..access_ports`, keeping a
port when its `PoEStatus.detect` is `DELIVERING`. Then remove:

- every port in the configured exclusion list for that switch;
- the switch's own `gateway_trunk_port`, `downstream_trunk_ports` and
  `house_uplink_port`.

The trunk and uplink ports are also passed to `SyncSwitch(protected_ports=...)`,
so a bug in port selection raises `ProtectedPortError` rather than cutting the
link that carries the second switch or tweed's uplink. Belt and braces, on
purpose: one of these is a filter, the other is a hard stop in the library.

### Default exclusions

From the 2026-09-15 fleet enumeration:

| Switch | Port | Why |
|---|---|---|
| 1 | 13 | `rpiz-4`, SD-booted, own host key. |
| 2 | 11 | Pi 5 `2c:cf:67:16:bc:30`. Draws 5.6 W, link never comes up, a PoE cycle does not revive it. Needs on-site attention; cycling it forever would achieve nothing. |
| 2 | 12 | `rpi5-netv2pcie-test`, boots locally, rejects every key held. |
| 2 | 27 | `rpi5-new-13f56e`, SD-booted. |
| 2 | 30 | `rpi5-new-13f59c`, SD-booted; also the EEPROM-recovery host with a USB SD reader. |

The Orange Pi boards on sw2 18-24 are **not** excluded. They share the Pis' NFS
root, so the watchdog key and host key both apply, and they are the boards that
hang most often and that a cycle reliably revives.

## Health check

One SSH command per occupied port, eight at a time:

```
cat /proc/uptime; who
```

`/proc/uptime` rather than `uptime -s` because netbooted Pis have no RTC. Boot
time from `uptime -s` is only as good as NTP; seconds-since-boot needs no clock
at all. Neither command needs `sudo`.

Invocation:

```
ssh -n -o BatchMode=yes -o ConnectTimeout=<ssh_timeout>
    -o StrictHostKeyChecking=yes
    -o UserKnownHostsFile=/var/lib/fleet-watchdog/known_hosts
    -i /var/lib/fleet-watchdog/id_ed25519
    pi@<ip> 'cat /proc/uptime; who'
```

wrapped in `subprocess.run(timeout=...)` with a hard timeout above
`ConnectTimeout`. The hard timeout is not belt-and-braces: `tmp/cycle_pi.py`
records that a board on stale NFS handles accepts the TCP connection and then
never finishes the SSH banner, so `ConnectTimeout` alone never fires.

Outcome:

- **OK** if the exit status is 0 and the first token parses as a float. That
  float is the uptime in seconds. Any `who` output at all means in use.
- **FAIL** otherwise, for any reason: timeout, non-zero exit, unparseable
  output, host key mismatch.

A non-interactive SSH command allocates no pty and therefore writes no `utmp`
record, so the watchdog's own probe never registers as a user. This is covered
by a unit test (`test_the_command_never_allocates_a_pty`), not by the
deployment checks.

The web terminal and direct SSH both land as `sshd` sessions on the board, so
`who` sees both. That matters because the Django site has no server-side notion
of a board being in use at all: no login, no reservations, and the web terminal
is a separate unauthenticated Tornado app that Django never observes. The board
itself is the only place the information exists.

## Cycle mechanics

`SyncSwitch.cycle_poe()` cannot be used. Its `PoeCycleTimeouts.off_timeout` is
a deadline for *confirming* the port went off, not a dwell: `_poe_rearm` sets
the port off, polls until detect leaves DELIVERING and link drops, then
immediately sets it back on. A board would lose power for a second or two, not
the required 30.

The watchdog therefore does:

1. `sw.set_poe(port, False)`.
2. Poll `get_poe()` every 2 s until the port is not delivering, up to 30 s.
   Failure to go off is an error; no dwell, no turn-on, log and move on.
3. Sleep `poe_off_seconds` (30).
4. `sw.set_poe(port, True)`.
5. Poll every 2 s until delivering, up to 60 s. Failure to come back is logged
   at ERROR.

At most `cycle_concurrency` (2) cycles run at once. Two reasons: the switch has
a finite PoE budget and simultaneous turn-on inrush across many ports risks
tripping it, and serialising keeps SNMP writes to one switch predictable.

Adding a `dwell_seconds` field to `PoeCycleTimeouts` upstream in
`python-netgear-switch-library` was considered and rejected for now. It is the
tidier home for this logic, but it turns a one-repo change into a library
release plus a version bump in two consumers. Worth revisiting if a second
caller ever needs a dwell.

## Policy

Per-port in-memory state: `consecutive_failures`, `last_cycle_monotonic`,
`cycles_this_run`.

One sweep, in order:

1. **Enumerate and probe** as above.
2. **Circuit breaker.** Let `f` be the number of FAIL ports and `n` the number
   of occupied ports. If `f >= breaker_min_failures` **and**
   `f > breaker_fraction * n`, log one ERROR naming `f` and `n`, cycle nothing
   at all this sweep, and return. Failure counters still advance.

   Both conditions are needed, and the count is what protects small sites. On a
   two-board site with both boards dead the fraction test trips (2 > 1) and the
   fleet could never recover itself; the count floor of 3 keeps the breaker out
   of the way. Conversely the count alone would trip on any 3 failures, which on
   a 35-board fleet is an ordinary Tuesday.

   This catches the cases where the watchdog, not the fleet, is broken: a wrong
   key, a wrong username, a routing fault, a switch that stopped answering, or
   an NFS root whose host key changed. It also covers first deploy, before the
   key reaches the boards.

3. **First sweep observes only.** The first sweep after process start records
   state and cycles nothing. A crash loop then cannot become a reboot loop, and
   the post-cycle grace of a board cycled just before a restart is respected.

4. **Health cycles.** Cycle every port with
   `consecutive_failures >= fail_threshold` whose last cycle is more than
   `boot_grace` ago. Not capped per sweep: a genuine fleet-wide outage under the
   breaker threshold should recover in one pass.

   The grace matters given cycling continues indefinitely. Without it, a board
   taking 3 minutes to boot would be cut again mid-boot every sweep and never
   come up. With 300 s sweeps, a 2-failure threshold and a 300 s grace, a
   permanently dead board is cycled roughly every 10 to 15 minutes.

5. **Scheduled cycles.** For each OK port, compute

   ```
   threshold = max_uptime_hours*3600 + jitter(switch, port)
   jitter    = (crc32(f"{switch}:{port}") % (uptime_jitter_minutes*60))
   ```

   `crc32` rather than `hash()`, because Python's string hash is salted per
   process and would move every board's slot on each restart.

   If `uptime >= threshold`: when the board is in use and
   `uptime < hard_cap_hours*3600`, defer and log at INFO with the reason.
   Otherwise cycle it. Candidates are sorted by descending uptime and at most
   `max_scheduled_cycles_per_sweep` are taken.

   The jitter and the per-sweep cap both exist because a fleet-wide cycle leaves
   all 35 boards crossing 8 hours within minutes of each other. Jitter spreads
   them over an hour and is derived from port numbers, so it survives restarts
   and needs no stored state. The cap bounds the worst case regardless: 2 per
   sweep is 24 boards an hour, so even a fully synchronised fleet drains in
   under two hours.

Health cycles are evaluated before scheduled ones, and the per-sweep cap applies
only to scheduled cycles.

### Sweep timing

Sweeps never overlap. The loop measures how long a sweep took and sleeps
`max(0, interval - elapsed)`, so a sweep that overruns simply starts the next
one immediately rather than stacking.

Overrun is expected, not exceptional. Probing 35 boards eight at a time with a
20 s timeout costs at most about 90 seconds, but cycling 15 failed boards two at
a time at roughly 45 seconds each takes over 5 minutes on its own. A large
outage therefore stretches the sweep, which is the right behaviour: the work is
the point, and the cadence is a floor on idle polling, not a deadline.

## Configuration

`/etc/fpgas/watchdog.yml`, rendered by the role from these variables:

| Variable | Default | Meaning |
|---|---|---|
| `fleet_watchdog_enabled` | `false` | Whether the service is started and enabled. |
| `fleet_watchdog_interval` | `300` | Seconds between sweeps. |
| `fleet_watchdog_fail_threshold` | `2` | Consecutive failed sweeps before cycling. |
| `fleet_watchdog_ssh_timeout` | `20` | Per-board SSH deadline, seconds. |
| `fleet_watchdog_probe_concurrency` | `8` | Parallel SSH probes. |
| `fleet_watchdog_poe_off_seconds` | `30` | Dwell with power off. |
| `fleet_watchdog_boot_grace` | `300` | Quiet period after a cycle, seconds. |
| `fleet_watchdog_max_uptime_hours` | `8` | Scheduled recycle threshold. |
| `fleet_watchdog_uptime_jitter_minutes` | `60` | Spread added to that threshold. |
| `fleet_watchdog_hard_cap_hours` | `12` | Cycle even if in use past this. |
| `fleet_watchdog_max_scheduled_cycles_per_sweep` | `2` | Stagger for scheduled cycles. |
| `fleet_watchdog_cycle_concurrency` | `2` | Simultaneous cycles. |
| `fleet_watchdog_breaker_fraction` | `0.5` | Breaker trips above this share failing. |
| `fleet_watchdog_breaker_min_failures` | `3` | And at least this many failing. |
| `fleet_watchdog_ssh_user` | `pi` | Login user on the boards. |
| `fleet_watchdog_unhealthy_exit_after` | `3` | Consecutive sweeps finding nothing, or tripping the breaker, before the process exits. |
| `fleet_watchdog_max_recovery_attempts` | `3` | Attempts to put one port back in service before reporting it instead. |
| `fleet_watchdog_start_limit_interval` | `3600` | systemd's give-up window, seconds. |
| `fleet_watchdog_start_limit_burst` | `4` | Starts within that window before the unit is marked failed. |
| `fleet_watchdog_exclude` | see table above | Map of switch index to port list. Excluded ports are never probed, cycled or recovered. |

Secrets stay out of this file. `/etc/fpgas/watchdog.env` (0600) carries
`FPGAS_SWITCHES_CONFIG=/etc/fpgas/switches.yml` and one
`FPGAS_SWITCH_COMMUNITY_<index>` per switch, from the vaulted
`snmp_rw_community` values, exactly as the gunicorn PoE drop-in does.

## systemd unit

```
[Unit]
Description=fpgas.online fleet watchdog
StartLimitIntervalSec=3600
StartLimitBurst=4
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=fleetwd
Environment=HOME=/var/lib/fleet-watchdog
EnvironmentFile=/etc/fpgas/watchdog.env
ExecStart=/opt/fpgas-switch/venv/bin/fpgas-fleet-watchdog --config /etc/fpgas/watchdog.yml
Restart=always
RestartSec=30
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/fleet-watchdog

[Install]
WantedBy=multi-user.target
```

`Restart=always` with `RestartSec=30` is safe because the first sweep after any
start only observes.

`HOME` is set explicitly because `ProtectHome=true` hides `/home` and `ssh`
otherwise has nowhere to look for a config. It never writes there: the known
hosts file and identity are both passed on the command line, and
`StrictHostKeyChecking=yes` means no host key is ever added.

## Logging

One line per event, to stdout, picked up by journald.

| Level | Event |
|---|---|
| INFO | One summary per sweep: occupied, ok, failed, in use, cycled, deferred. |
| INFO | A deferred scheduled cycle, with uptime and the in-use reason. |
| WARNING | A cycle starting, with switch, port, hostname and the reason. |
| WARNING | A board's first failed probe, with the error. |
| ERROR | Breaker tripped, with the failing and occupied counts. |
| ERROR | A cycle that failed to turn off or come back. |
| DEBUG | Per-port probe result each sweep. |

Every line carries `sw<switch>/p<port>` and the hostname so
`journalctl -u fleet-watchdog | grep pi-sw2-p20` tells the whole story of one
board.

## Testing

Unit tests in `fpgas.online-poe`, written first.

`policy.py` is pure, so its tests need no switch and no network:

- a board failing once is not cycled; failing twice is;
- a board inside its boot grace is not cycled however many times it failed;
- the breaker trips at the configured fraction and count, and suppresses both
  health and scheduled cycles;
- the breaker does not trip when one board of two fails (count floor);
- the first sweep cycles nothing;
- an in-use board past 8 h is deferred, and the same board past 12 h is not;
- jitter is stable across processes and within the configured range;
- scheduled cycles are capped per sweep and taken oldest first.

Switch interaction is tested against the library's `VirtualSwitch`, which
`tests/conftest.py` already uses for the `snmp_switch` tests:

- occupancy keeps delivering ports and drops excluded, trunk and uplink ports;
- a cycle sets off, waits the dwell, sets on, and verifies both transitions,
  with an injected sleep so the test does not take 30 seconds;
- a cycle of a protected port raises `ProtectedPortError`.

`probe.py` is tested against a fake `ssh` on `PATH` covering a healthy board, a
board with a login, a timeout, a bad host key and garbage output.

The QEMU VM test is not extended. It has one virtual Pi and no switch, and
`switches_manage` is false there, so the role's converge path is exercised but
the watchdog itself has nothing to watch. The role must therefore stay inert
when disabled, which the VM test does assert by default.

## Deployment

Order matters, because the dedicated key only reaches the boards through the NFS
root.

1. Merge the `fpgas.online-poe` change. `roles/switch-vlans` picks it up on the
   next converge through `state: forcereinstall`.
2. Merge the infra change and converge. The role generates the key, authorises
   it in the NFS root, renders config and installs the unit, but leaves the
   service stopped because `fleet_watchdog_enabled` is false.
3. Update the NFS root and cycle the fleet, per the standing procedure:
   `site.yml --tags pi,fpgas-apt,onpi,cam` run detached from ten64, then a fleet
   PoE cycle.
4. Verify by hand from tweed that the watchdog key logs into a board.
5. Set `fleet_watchdog_enabled: true` in `host_vars/fpgas.online.yml` and
   converge.
6. Run `fpgas-fleet-watchdog --config ... --once --dry-run` as `fleetwd` and
   confirm it names the right ports and proposes nothing alarming.
7. Start the service and watch one sweep in the journal.

Skipping step 3 is safe but useless: every board fails, the breaker trips, and
the watchdog logs an alarm and cycles nothing.

## Failing loud

Added 2026-09-20 after reviewing the service against the principle that it must
not silently paper over problems. A watchdog has an inverted failure profile:
it fails by doing nothing, which is byte-for-byte identical to having nothing
to do. "No alarms" is therefore not evidence of health, and these five changes
exist so that a watchdog which has stopped watching cannot be mistaken for a
quiet one.

**Ports are recovered, not lost.** The scan returned only DELIVERING ports, so
a port the switch had faulted off and a port a half-finished cycle had left
switched off were both invisible and unrecoverable -- a port that is not
delivering is not a board to probe, and nothing else ever looked at it.
`scan_ports` now returns every watchable access port with its PoE state. Each
sweep clears faults with `SyncSwitch.clear_poe_fault`, which re-arms the port
and polls until detect leaves FAULT for DELIVERING or SEARCHING, and re-enables
ports left off. Capped by `max_recovery_attempts`, after which the port is
reported every sweep rather than re-armed forever.

This means the watchdog re-enables a port an operator has admin-disabled.
`fleet_watchdog_exclude` is the one way to make it keep its hands off a port,
and exclusions are applied before anything decides to act.

**Our own bugs never cut power.** `probe_all` converted any exception into an
ordinary board failure, which two sweeps later is a power cycle: a `TypeError`
in this code would have rebooted hardware. Internal errors now carry a
traceback, are marked on the `Observation`, count toward the circuit breaker --
they are evidence the watchdog is broken, which is what the breaker is for --
and are never cycled on.

**A failing board is named on every sweep.** Only the first failure carried the
board and the reason, so a board stuck failing for a week read as a bare
`failed=1`, and the `journalctl | grep pi-sw2-p20` this spec promises returned
nothing. Under a breaker one summary line names the boards, rather than 35
warnings burying the breaker itself.

**The daemon exits when it cannot do its job.** `run()` caught every exception
and looped forever, so a missing `switches.yml` logged a traceback every five
minutes while systemd reported the unit active and healthy. A raising sweep now
exits at once; `unhealthy_exit_after` consecutive sweeps that found no occupied
port or tripped the breaker also exit. With the unit's `StartLimitIntervalSec`
and `StartLimitBurst`, a persistent fault ends as a **failed** unit, which is
the only health signal that exists outside the journal.

One unreachable switch is deliberately not fatal: exiting would stop watching
the switches that answer, and a restart cannot fix an unreachable switch. It is
logged every sweep, and when no switch answers the sweep finds nothing, which
does count.

**A misread config is fatal.** An unknown key used to load as its default, so
`max_uptime_hrs: 1` silently left `max_uptime_hours` at 8 with nothing
downstream able to detect it. Unknown keys, uncoercible values and a malformed
`exclude` block all now raise `ConfigError` and name the key.

## Risks

| Risk | Mitigation |
|---|---|
| Watchdog itself broken, mass reboot | Circuit breaker, first-sweep observation, dry-run, ships disabled. |
| A board cycled mid-boot, forever | Post-cycle boot grace of 300 s. |
| Permanently broken board cycled forever | Accepted (Tim, 2026-09-15). Exclusion list handles known cases; p11 is already listed. |
| Turn-on inrush trips the PoE budget | At most 2 concurrent cycles. |
| Whole fleet crosses 8 h together | Deterministic per-port jitter plus a per-sweep cap. |
| A visitor interrupted at the 12 h cap | Accepted. The site already tells visitors boards are shared. |
| NFS root host key changes | Rendered `known_hosts` means every probe fails, which trips the breaker rather than cycling the fleet. The role re-renders on converge. |
| Excluded ports drift from reality | The exclusion list is inventory, reviewed when boards move. Ports are named in every log line. |

## Open questions

1. Should the Orange Pi boards get a shorter uptime threshold than the Pis,
   given they hang within 15 hours of a cycle? Deliberately not done now: one
   threshold for everything is simpler, and the health check already catches
   them within about 10 minutes of hanging.
2. Should ps1 run this too? The design is host-agnostic and keys off `switches`,
   which ps1 does not define, so ps1 is unaffected until it moves to the
   VLAN-per-port scheme.

## References

- `ansible/filter_plugins/port_vlans.py` — port, VLAN, IP and hostname map (the `port_vlan_map` filter).
- `ansible/roles/switch-vlans/` — `/etc/fpgas/switches.yml` and the shared venv.
- `ansible/roles/site/templates/gunicorn-poe.conf.j2` — the community env pattern.
- `ansible/roles/fixpi/tasks/userconf.yml` — how NFS root `authorized_keys` is built.
- `fpgas.online-poe` `src/snmp_switch/switches.py` — the two switch config shapes.
- `python-netgear-switch-library` `src/netgear_switch/snmp_write.py` — `_poe_rearm`.
- `docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md`.
- Fleet enumeration, 2026-09-15 (memory `welland-fleet-enumeration`).
