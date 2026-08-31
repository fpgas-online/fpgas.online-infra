# Fleet self-registration: design

Date: 2026-08-31. Companion plan:
`docs/superpowers/plans/2026-08-31-fleet-self-registration.md`.

## Problem

Board information is loaded at **install time** (the `pibfpgas.pi` fixture,
the `tt_boards` catalogue) and decays immediately:

- The 2026-08-26 tweed reinstall seeded a fresh DB with **zero** boards and
  welland.fpgas.online showed an empty board list for five days before anyone
  debugged it. The fixture-load path had broken silently when the fixtures
  moved between repos.
- The fixture that was eventually restored (site PR #20) had to be rebuilt by
  hand-probing the fleet, and was already out of step with the hardware
  inventory sheet generated one day earlier (three Arty boards and two Acorns
  had moved).
- Nothing on the site knows whether a Pi is actually **up**. The old
  `pistat_*` units fire one-shot curl events at boot; there is no liveness
  signal, no record of what hardware was ever attached, and no way to see
  hardware drift over time.

## Goals

1. Every fleet Pi **registers itself** with the web app(s) on boot with a
   complete, structured description of itself and everything attached to it.
2. A Pi registers with **multiple web apps**: its site-local instance plus a
   future global aggregator (`all.fpgas.online`); welland Pis →
   `welland.fpgas.online` + `all.fpgas.online`, PS1 Pis →
   `ps1.fpgas.online` + `all.fpgas.online`.
3. The web app keeps **history**: hardware descriptions are stored in their
   own content-addressed table and a **new row is created only when the
   hardware actually changes**. Presence ("last seen") lives separately and
   is updated in place, so history stays clean.
4. Registration covers: RPi hardware (model, revision code, memory, serial,
   MAC addresses), running software (kernel, Debian release, nfsroot build /
   last-update time, fpgas-online package versions), connection info (site,
   IPv4/IPv6 addresses, ssh host keys, login user + password — the fleet's
   credentials are public by design), and connected hardware (HATs with
   serials, USB devices with serials, PCIe devices, detected FPGA boards with
   type / Digilent serial / DNA where safely readable, Tiny Tapeout board
   info incl. demo-board firmware version and mounted chip).
5. Pis send an **"I'm alive"** message at least every 60 seconds.
6. The system **self-heals** the exact failure mode that motivated it: after
   a web-app DB reset, the fleet re-registers itself with no operator action.

### Non-goals (this iteration)

- Replacing the ttsite `tt_boards` catalogue (curated editorial content —
  titles, blurbs, links — stays hand-written; registration data can later
  *corroborate* it).
- Environmental telemetry (temperatures, PoE wattage). That is
  [sensors2mqtt](https://github.com/mithro/sensors2mqtt)'s job — see
  "Transport" below.
- Building the `all.fpgas.online` aggregator host itself (that is the
  `tweed-split-design` study's aggregator role; DNS for the name does not
  exist yet). This design only has to make adding it be *pure configuration*.

## Identity

The **CPU serial** (`/sys/firmware/devicetree/base/serial-number`, 16 hex
chars on Pi 4/5, 8 significant on Pi 3) identifies a physical Pi across
reinstalls, network moves and hostname changes. `hostname` (`pi-sw2-p34`)
identifies where it is *plugged in* — that is claim data inside the
registration document, not identity. Each boot also carries the kernel's
`/proc/sys/kernel/random/boot_id` so reboots are distinguishable.

## The registration document

A single JSON object, collected on the Pi. Section layout (values
illustrative, from pi-sw2-p47):

```json
{
  "schema": 1,
  "machine": {
    "serial": "c36b093f773d46b8",
    "model": "Raspberry Pi 5 Model B Rev 1.1",
    "revision_code": "a04171",
    "memory_mb": 1024,
    "macs": {"eth0": "98:fe:54:13:f5:75", "wlan0": "98:fe:54:13:f5:76"}
  },
  "software": {
    "kernel": "6.12.20+rpt-rpi-2712",
    "os_release": "Debian GNU/Linux 13 (trixie)",
    "nfsroot_updated": "2026-08-29T02:11:04Z",
    "packages": {"fpgas-online-setup-pi": "1.4.2", "fpgas-online-cam": "0.9.1"}
  },
  "connection": {
    "site": "welland",
    "hostname": "pi-sw2-p47",
    "ipv4": ["10.21.2.47"],
    "ipv6": ["2404:e80:a137:2102::47"],
    "ssh_host_keys": ["ssh-ed25519 AAAA... root@buildhost"],
    "login_user": "pi",
    "login_password": "printed-in-banner"
  },
  "peripherals": {
    "hats": [{"product": "PoE+ HAT", "vendor": "Raspberry Pi",
              "product_id": "0x0502", "uuid": "..." }],
    "usb": [{"vid": "0403", "pid": "6010",
             "product": "FT2232C/D/H Dual UART/FIFO IC",
             "serial": "210319B3E5C5"}],
    "pcie": [{"vendor": "1cf0", "device": "0007",
              "name": "Squirrels Research Labs Acorn CLE-215+"}],
    "cameras": ["ov5647"]
  },
  "fpga": {
    "boards": [{"kind": "acorn-cle-215+", "via": "pcie",
                "ids": {"pci": "1cf0:0007"}}]
  }
}
```

Notes on specific fields:

- `login_password` comes from `/etc/ssh/password.txt` (the sshd banner —
  already world-published by design; see the standing rule that fleet Pi
  credentials are public and the VLAN scheme provides isolation).
- `nfsroot_updated`: the build stamp `/etc/fpgas-online/nfsroot-build.json`
  when present (the CI nfsroot build will write one), otherwise the mtime of
  `/var/lib/dpkg/status` (last package operation in the image).
- **FPGA detection is passive.** Arty = FTDI `0403:6010` with a Digilent
  `2103...` serial (the serial *is* the Digilent programmer identity);
  Acorn = PCIe vendor `1cf0` (or the Xilinx `10ee` AXI/debug id when a user
  bitstream has re-enumerated the endpoint — record both); TT boards =
  the local `fpgas-tt` daemon's `GET http://127.0.0.1:8765/health` (kind,
  slug, firmware `version`, board-present). **Xilinx DNA is NOT read by
  default**: reading it needs JTAG, and on the Acorns JTAG touching a
  PCIe-attached FPGA wedges the link (established the hard way — see the
  Acorn bitstream-loading notes). A `--read-dna` flag exists for operator
  use on boards where it is safe; the scheduled collector never passes it.
- Canonicalisation: `json.dumps(doc, sort_keys=True, separators=(",", ":"))`
  of everything EXCEPT volatile keys. Uptime, timestamps and ordering are
  either excluded or normalised (lists sorted) so that a doc's SHA-256
  **fingerprint** is stable across boots when nothing real changed.

## Server data model (Django app `fleet` in fpgas.online-site)

Two kinds of truth, two lifetimes, three tables:

```
Machine            -- one row per physical Pi (identity + presence)
  serial          unique
  site            e.g. "welland"
  hostname        latest claimed
  first_seen      set once
  last_seen       touched by every heartbeat/registration  <- churn lives here
  last_boot_id
  last_uptime_s
  latest_snapshot FK -> HardwareSnapshot (nullable)

HardwareSnapshot   -- append-only hardware history (content-addressed)
  machine         FK -> Machine
  fingerprint     sha256 hex of the canonical document, indexed
  document        JSONField (the full registration doc)
  first_seen      when this exact hardware state first appeared
  last_confirmed  most recent registration that matched it unchanged
  unique_together (machine, fingerprint)
```

Registration flow: canonicalise + hash **server-side** (the client's hash is
advisory), then:

- fingerprint == `machine.latest_snapshot.fingerprint` → bump that
  snapshot's `last_confirmed` and the machine's presence. **No new row.**
- different fingerprint → insert a new snapshot (or re-point to an existing
  older row with the same hash if hardware flapped back), update
  `latest_snapshot`. **This is the only time history grows.**

Heartbeat flow: update `Machine.last_seen/last_boot_id/last_uptime_s` only.
A machine is **live** when `last_seen` is within 90 s (one missed beat +
slack). "What changed over time" is `machine.snapshots.order_by(first_seen)`
with a JSON diff rendered between consecutive documents.

## HTTP API (same app on every instance: welland, ps1, all)

```
POST /fleet/api/register/   body: the registration document
                            auth: Authorization: Bearer <site token>
                            resp: {"ok": true, "changed": bool,
                                   "fingerprint": "..."}

POST /fleet/api/heartbeat/  body: {"serial", "boot_id", "uptime_s",
                                   "fingerprint"}
                            resp: {"ok": true, "known": bool}
```

`known: false` (fingerprint or serial unknown — e.g. the DB was reset, or
the collector's doc changed) tells the Pi to follow up with a full
registration. That closes the self-healing loop: a re-seeded/fresh DB
repopulates itself within one heartbeat interval, fleet-wide.

Pages: `/fleet/` (machine table: hostname, site, board, live badge, last
seen) and `/fleet/<serial>/` (presence + snapshot history with diffs).

Auth: a per-site bearer token, in `local_settings.py`
(`FLEET_TOKENS = ["..."]`, list so rotation can overlap) and baked into the
Pi config by Ansible from vault. The token stops drive-by junk, not a
determined LAN attacker — the nfsroot is world-readable by design and the
threat model accepts that (same standing rule as the published credentials).
Payload cap 256 KB; malformed JSON → 400; unknown bearer → 403.

## Pi side (fpgas-online-setup-pi)

A new `fleet-scripts/` collector + registrar, stdlib-only Python (no
third-party deps in the nfsroot), shipped by the existing deb:

- `collect.py` — pure functions over `/sys`, `/proc`, `/etc/os-release`,
  `dpkg-query`, sysfs USB/PCI walks and the fpgas-tt `/health` endpoint.
  Testable with canned fixture trees.
- `register.py` — CLI: `register` (collect, POST to every endpoint),
  `heartbeat` (POST beat; on `known: false` run a full register). Config
  from `/etc/fpgas-online/fleet.toml`:

  ```toml
  site = "welland"
  endpoints = [
    "https://welland.fpgas.online/fleet",
    # "https://all.fpgas.online/fleet",   # uncomment when it exists
  ]
  token = "..."
  ```

- systemd: `fleet-register.service` (oneshot, after network-online +
  fpgas-tt), `fleet-heartbeat.service` + `fleet-heartbeat.timer`
  (`OnUnitActiveSec=60`, `RandomizedDelaySec=10` so 25 Pis don't beat in
  phase). Endpoint failures are logged and retried at the next tick — a Pi
  never blocks boot on the web app being up, and each endpoint is
  independent (welland down must not stop the `all` registration or vice
  versa).

Multi-site is therefore **configuration**: the per-site Ansible vars list
the endpoints, and adding `all.fpgas.online` later means appending one URL
to two site configs and re-baking nfsroots.

## Transport: why HTTPS, and where MQTT/sensors2mqtt fits

The "MQTT broker next to the web app + sensors2mqtt" option was considered
seriously — it is the right architecture for *telemetry*, and the wrong
first tool for *registration*:

| | HTTPS POST (chosen) | MQTT + broker |
|---|---|---|
| New moving parts | none (nginx + Django exist) | mosquitto per site, bridge to `all`, auth config, a broker→DB consumer daemon |
| Multi-site fan-out | loop over endpoint URLs | broker bridges (elegant, but each is config + a failure mode) |
| Delivery semantics for a 100 KB doc + server-side dedupe/ack | request/response, `changed`/`known` in the reply | needs a reply topic or blind fire-and-forget |
| Liveness granularity | 60 s beat, 90 s staleness | **better**: LWT flips a retained status the moment TCP drops |
| Debuggability | `curl` | `mosquitto_sub` (fine, but one more credential set) |
| CI | Django test client + VM test as-is | broker in CI VM too |

At 25 Pis × 1 beat/min the HTTP load is ~0.4 req/s — nothing. The one real
MQTT advantage (sub-second offline detection via LWT) is not worth four new
services across three sites for v1; 90-second staleness is fine for a
status page. **Decision: HTTPS for registration + heartbeat now.** The
server model is transport-agnostic (a future MQTT consumer would call the
same `register_document()` service function), so a later
`heartbeat-over-MQTT` upgrade is additive, not a rewrite.

**sensors2mqtt verdict:** keep it aimed at Home Assistant telemetry (CPU
temps, PoE per-port power via its snmp collector — both already useful for
the fleet) rather than bending its HA-discovery topic model into carrying
registration documents. If/when a broker lands next to the web app for
LWT-liveness, sensors2mqtt plugs into the same broker unchanged.

## Relationship to the existing board pages

Phase 1 (this design) runs alongside the fixture-seeded `pibfpgas.pi` table
that PR #20 restored. Phase 2 (final task group) derives the `/fpgas/`
board list from `fleet` data — a registered machine whose document contains
an FPGA board upserts the matching `Pi` row (switch/port parsed from the
`pi-sw<s>-p<p>` hostname) — at which point the fixture becomes a bootstrap
fallback only, and the "board list is wrong after a reinstall" class of
incident is closed for good. The legacy one-shot `pistat_info` /
`pistat_ssh` / `arty_here` curl units are retired in the same phase.

## Failure modes

- **Web app down at Pi boot**: register unit logs and exits 0; the 60 s
  heartbeat keeps attempting; first success triggers `known:false` → full
  register. No cron-storm, no boot dependency.
- **DB reset / fresh converge**: every heartbeat gets `known: false`; the
  whole fleet re-registers within ~1 minute, unprompted.
- **Pi swapped on a port**: new serial registers claiming the same
  hostname; old machine's `last_seen` goes stale. The fleet page shows
  both; no automatic deletion (operator archives via admin).
- **Hardware flap** (board unplugged/replugged): snapshots A→B→A resolve to
  the two existing rows via (machine, fingerprint) uniqueness;
  `last_confirmed` shows the full story without row spam.
- **Endpoint list divergence**: each endpoint is tried independently every
  time; one site being down never starves another.

## Open decisions for Tim

- **D-1**: `all.fpgas.online` hosting (ties into tweed-split aggregator
  role) — this design only requires that the same Django app deploy there.
- **D-2**: should heartbeats also carry cheap health stats (CPU temp,
  throttle bits)? Deliberately excluded for v1 (that's sensors2mqtt's lane);
  cheap to add to the beat payload later if wanted on the fleet page.
- **D-3**: retire which of the legacy pistat one-shot units in phase 2 —
  proposed: all of `pistat_info`, `pistat_ssh`, `pistat_cam`, `arty_here`
  (the live pages' WebSocket status flow via daphne is untouched).
