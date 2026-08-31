# Fleet self-registration: design

Date: 2026-08-31. Revised same day after Tim's review: MQTT broker on tweed
is first-class site infrastructure (sensors2mqtt publishes to it, NOT to
Home Assistant — any HA forwarding happens downstream of the broker and the
Pis/web apps neither know nor care), and the legacy boot-event one-shots are
SUBSUMED: Pis report boot stages as they happen through this same pipeline.
Companion plan: `docs/superpowers/plans/2026-08-31-fleet-self-registration.md`.

## Problem

Board information is loaded at **install time** (the `pibfpgas.pi` fixture,
the `tt_boards` catalogue) and decays immediately:

- The 2026-08-26 tweed reinstall seeded a fresh DB with **zero** boards and
  welland.fpgas.online showed an empty board list for five days. The
  fixture-load path had broken silently when the fixtures moved between
  repos.
- The restored fixture (site PR #20) had to be rebuilt by hand-probing the
  fleet, and was already out of step with the day-old inventory sheet.
- Nothing on the site knows whether a Pi is actually **up**, what stage of
  boot it reached, or what hardware was ever attached over time. The old
  `pistat_*` units fire disconnected one-shot curls at boot.

## Goals

1. Every fleet Pi **registers itself** on boot with a complete, structured
   description of itself and everything attached to it.
2. Registration reaches **multiple web apps**: the site-local instance plus
   a future global aggregator (`all.fpgas.online`); welland →
   welland.fpgas.online + all, PS1 → ps1.fpgas.online + all.
3. The web app keeps **history**: hardware documents live in their own
   content-addressed table; a **new row appears only when the hardware
   actually changes**. Presence ("last seen") is separate and updated in
   place.
4. Registration covers: RPi hardware (model, revision code, memory, serial,
   MACs), software (kernel, Debian release, nfsroot build/update time,
   fpgas-online package versions), connection info (site, IPv4/IPv6, ssh
   host keys, login user + password — fleet credentials are public by
   design), connected hardware (HATs w/ serials, USB w/ serials, PCIe,
   FPGA boards with type / Digilent serial / DNA where safe, TT demo-board
   firmware + chip).
5. Pis publish an **"I'm alive"** signal at least every 60 seconds, and the
   broker flips them to offline the moment their TCP session dies (LWT).
6. Pis report **boot stages as they happen** (network up, time synced, ssh
   up, camera streaming, TT daemon up, registered, shutting down) through
   this same infrastructure — replacing `pistat_info`, `pistat_ssh`,
   `pistat_cam` and `arty_here` outright.
7. The system **self-heals** the motivating failure: after a web-app DB
   reset, the fleet's state rebuilds with no operator action and no Pi
   cooperation (retained messages re-ingest on consumer restart).

### Non-goals (this iteration)

- Replacing the ttsite `tt_boards` catalogue (curated editorial content
  stays hand-written; registration data corroborates it later).
- Environmental telemetry values (temps, PoE wattage): **sensors2mqtt**
  publishes those to the *same broker* under its own topics; the fleet
  consumer ignores them (a later fleet-page temperature column would be a
  small additive consumer change).
- Standing up the `all.fpgas.online` host (tweed-split aggregator study;
  DNS doesn't exist yet). Resolved decision D-1: this design is
  **config-ready only** — enabling `all` later is one bridge stanza per
  site broker, nothing on any Pi changes.

## Architecture

```
Pi (fleet-agent)                    tweed (per site)                 later
  |                                   |                               |
  | mqtt publish               +-- mosquitto broker --+   [bridge] -> all.fpgas.online
  |  fpgas/<site>/pi/<serial>/ |   (LWT, retained)    |               broker + same stack
  |    registration (retained) |                      |
  |    status       (retained) |     fleet-consumer (Django mgmt cmd,
  |    event                   |     systemd) --> Machine/HardwareSnapshot/
  |                            |     BootEvent --> /fleet/ pages
  sensors2mqtt-local ----------+     (sensors2mqtt topics: ignored here;
   (its own topics)                   optionally forwarded to HA downstream)
```

One broker per site, on the gateway/web host. Pis talk **only** to their
site broker (10.21.0.1 — every per-port VLAN already routes to the
gateway); multi-app fan-out is the broker's job via a bridge, so goal 2
costs the Pis nothing. The web app consumes from its local broker.

## Identity

CPU serial (`/sys/firmware/devicetree/base/serial-number`) identifies the
physical Pi across reinstalls and moves; hostname (`pi-sw2-p34`) is claim
data saying where it is plugged in. Each boot carries
`/proc/sys/kernel/random/boot_id` so reboots are distinguishable.

## Topics and payloads

```
fpgas/<site>/pi/<serial>/registration   retained, QoS 1
fpgas/<site>/pi/<serial>/status         retained, QoS 1, LWT
fpgas/<site>/pi/<serial>/event          QoS 1, not retained
```

- **registration**: the full document (below). Published on boot after
  collection, and re-published whenever a re-collection produces a
  different fingerprint (a 6-hourly re-collect timer catches hot-plugs).
  Retained ⇒ the broker always holds every Pi's latest document, which is
  what makes goal 7 free: a fresh consumer receives the whole fleet's
  registrations on subscribe.
- **status**: `{"online": true, "boot_id": "...", "uptime_s": 1234,
  "fingerprint": "...", "ts": "..."}` republished every 60 s
  (RandomizedDelaySec 10). The connection's **Last Will** is
  `{"online": false, "reason": "connection-lost"}` on the same topic,
  retained — so a yanked cable shows offline in seconds, not after a
  staleness window. Clean shutdown publishes `{"online": false,
  "reason": "shutdown"}` itself.
- **event**: `{"stage": "ssh-up", "boot_id": "...", "ts": "...",
  "detail": {}}`. Standard stages emitted by shipped units:
  `network-online`, `time-synced`, `ssh-up`, `cam-streaming`,
  `tt-daemon-up`, `fpga-detected`, `registered`, `shutdown`. Arbitrary
  stages are allowed (`fleet-event <stage> [--detail k=v]` CLI), so
  anything that today curls `/pistat/stat/<host>/<thing>/` becomes one
  line. This subsumes `pistat_info`/`pistat_ssh`/`pistat_cam`/`arty_here`
  (resolved decision D-3: all four retired when this deploys).

## The registration document

Identical to the pre-revision design (sections `machine`, `software`,
`connection`, `peripherals`, `fpga`; canonical JSON =
`json.dumps(doc, sort_keys=True, separators=(",", ":"))`, fingerprint =
SHA-256 hex of that, lists sorted for stability). Illustrative:

```json
{
  "schema": 1,
  "machine": {"serial": "c36b093f773d46b8",
              "model": "Raspberry Pi 5 Model B Rev 1.1",
              "revision_code": "a04171", "memory_mb": 1024,
              "macs": {"eth0": "98:fe:54:13:f5:75"}},
  "software": {"kernel": "6.12.20+rpt-rpi-2712",
               "os_release": "Debian GNU/Linux 13 (trixie)",
               "nfsroot_updated": "2026-08-29T02:11:04Z",
               "packages": {"fpgas-online-setup-pi": "1.4.2"}},
  "connection": {"site": "welland", "hostname": "pi-sw2-p47",
                 "ipv4": ["10.21.2.47"], "ipv6": ["2404:e80:a137:2102::47"],
                 "ssh_host_keys": ["ssh-ed25519 AAAA..."],
                 "login_user": "pi", "login_password": "from-banner"},
  "peripherals": {"hats": [], "usb": [], "pcie": [
                   {"vendor": "1cf0", "device": "0007"}], "cameras": ["ov5647"]},
  "fpga": {"boards": [{"kind": "acorn-cle-215+", "via": "pcie",
                       "ids": {"pci": "1cf0:0007"}}]}
}
```

Field notes (unchanged from v1): `login_password` from
`/etc/ssh/password.txt` (published by design); `nfsroot_updated` from
`/etc/fpgas-online/nfsroot-build.json` else `/var/lib/dpkg/status` mtime;
Arty = FTDI `0403:6010` with Digilent `210…` serial; Acorn = PCIe `1cf0`
(or Xilinx `10ee` AXI/debug when a user bitstream re-enumerated it —
record both); TT via the local fpgas-tt daemon `GET :8765/health` (kind,
slug, firmware version). **Xilinx DNA is never read by the scheduled
collector** — JTAG against a PCIe-attached Acorn wedges the link; a manual
`--read-dna` flag exists for operators.

## Server data model (Django app `fleet` in fpgas.online-site)

```
Machine            -- one row per physical Pi (identity + presence)
  serial unique · site · hostname · first_seen
  last_seen · online (bool, driven by status/LWT) · last_boot_id
  last_uptime_s · latest_snapshot FK

HardwareSnapshot   -- append-only, content-addressed hardware history
  machine FK · fingerprint (sha256, indexed) · document JSON
  first_seen · last_confirmed · unique (machine, fingerprint)

BootEvent          -- the boot-stage timeline (goal 6)
  machine FK · boot_id · stage · detail JSON · ts (indexed)
  (pruned by age — keep 90 days — so it cannot grow unbounded)
```

Ingest is **idempotent**: same-fingerprint registration bumps
`last_confirmed`; changed fingerprint appends a snapshot (a flap back to a
prior state re-points `latest_snapshot` at the existing row). Fingerprints
are recomputed server-side. Because ingest is idempotent, replaying every
retained message (consumer restart, DB reset) is harmless and is exactly
the self-healing mechanism.

The **fleet-consumer** is a Django management command
(`manage.py fleet_consumer`, paho-mqtt — a server-side-only dependency)
run as a systemd service next to gunicorn. It subscribes to
`fpgas/+/pi/+/#`, dispatches by topic suffix to
`services.register_document()` / `services.status()` /
`services.boot_event()`, and marks machines offline on LWT payloads. The
services layer stays transport-agnostic and unit-testable without a broker.

Pages: `/fleet/` (hostname, site, board, online badge — real LWT-driven
state, not staleness guessing — last_seen) and `/fleet/<serial>/`
(presence, snapshot history with diffs, boot-event timeline for recent
boot_ids). No public write API: nginx exposes only the pages; writes enter
via the broker.

## Pi side (fpgas.online-setup-pi)

`fleet-scripts/` shipped by the existing deb. Python with
`python3-paho-mqtt` from Debian (the one non-stdlib dependency, installed
into the nfsroot by apt — no pip).

- `collect.py` — pure collectors over /sys, /proc, dpkg, sysfs USB/PCI,
  fpgas-tt `/health`; fixture-tree testable (unchanged from v1).
- `fleet_agent.py` — a small long-running daemon (systemd service, not a
  timer): connects to the site broker with the LWT set, publishes
  registration (retained) once collected, then status every 60 s, and
  re-collects every 6 h (or on `SIGHUP`) republishing registration iff the
  fingerprint changed. A daemon rather than oneshots because LWT only
  works over a held connection.
- `fleet-event` — tiny CLI publishing one event; shipped drop-in units
  hook the standard stages (`ExecStartPost=` on ssh/cam/tt units or
  dedicated `After=` oneshots).
- Config `/etc/fpgas-online/fleet.toml` baked by infra per site:

  ```toml
  site = "welland"
  broker = "10.21.0.1"      # the gateway; per-port VLANs all route here
  port = 1883
  username = "fleet-pi"
  password = "..."           # public-by-design caveat applies
  ```

## Broker (fpgas.online-infra)

mosquitto on each site's gateway/web host, listening on eth-local (LAN
only; not exposed through ten64). Auth: a `fleet-pi` account for the fleet
(publish-only to `fpgas/<site>/pi/#`), a `fleet-web` account for the
consumer (read `fpgas/#`), a `sensors` account for sensors2mqtt's topics.
ACLs keep the namespaces apart. The `all.fpgas.online` fan-out is a
commented bridge stanza in the mosquitto config template
(`connection all-fpgas-online`, `topic fpgas/# out 1`), enabled when D-1's
host exists; PS1 gets the identical role. Any HA forwarding hangs off the
broker downstream and is invisible to this design.

## Why MQTT now (revised decision)

v1 chose HTTPS because a broker looked like four new services bought only
for liveness granularity. Tim's review changed the premise: **the broker
is site infrastructure regardless** (sensors2mqtt at fpgas.online sites
publishes to tweed's broker, not to HA), and boot-stage events (goal 6)
want pub/sub fan-out. With the broker in the baseline, MQTT also wins on
merits HTTPS can't match:

- **LWT**: offline detection in seconds, authoritative, no staleness math.
- **Retained registrations**: the broker is a free, always-current fleet
  cache; consumer restart/DB reset rebuilds state with zero Pi traffic
  (goal 7 without the `known:false` round-trip protocol v1 needed).
- **Bridging**: multi-app registration (goal 2) is broker config; Pis
  never talk off-site.
- One shared substrate for fleet state *and* sensors2mqtt telemetry.

Debugging: `mosquitto_sub -t 'fpgas/#' -v`. CI: mosquitto installs fine in
the VM test; paho is packaged in Debian for both Pi and server ends.

## Failure modes

- **Broker down at Pi boot**: paho auto-reconnects with backoff; retained
  publish lands on reconnect. Boot never blocks.
- **Consumer down / DB reset**: on restart it receives every retained
  registration + status; idempotent ingest rebuilds everything (~25 msgs).
- **Pi swapped on a port**: new serial's registration claims the hostname;
  the old machine's LWT already marked it offline. Both visible; operator
  archives via admin.
- **Hardware flap**: content-addressing reuses existing snapshot rows.
- **Broker restart**: retained store persists (`persistence true`); LWTs
  for still-connected Pis are re-established on their reconnect.
- **Event storms**: events are QoS 1 fire-and-forget, pruned at 90 days.

## Resolved decisions (Tim, 2026-08-31)

- **D-1 — all.fpgas.online: config-ready only.** Bridge stanza shipped
  commented; the aggregator host is the tweed-split study's problem.
- **D-2 — reframed**: no HA coupling anywhere in this design; sensors2mqtt
  publishes to the site broker like everything else. Fleet heartbeats stay
  minimal; telemetry rides sensors2mqtt topics on the shared broker.
- **D-3 — subsume, don't just retire**: boot stages become first-class
  `event` messages (goal 6); all four legacy one-shot curl units are
  removed when this ships.

## Remaining open decisions

- **D-4**: broker credentials — one shared `fleet-pi` account (simplest,
  matches the public-by-design posture) vs per-Pi accounts (revocable, but
  25× provisioning). Spec assumes shared.
- **D-5**: should the existing board pages' live-status widgets (daphne
  WebSocket) eventually source from BootEvent/status instead of the
  `/pistat/` path — proposed as a later phase, not in this plan.
- **D-6**: BootEvent retention window (spec says 90 days).
