# Fleet Self-Registration Implementation Plan (MQTT revision)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fleet Pis publish a complete registration document, 60 s status
beats with LWT, and boot-stage events to their site's MQTT broker; the web
app consumes them into a content-addressed hardware history.

**Architecture:** mosquitto on each site's gateway/web host (bridge-ready
for all.fpgas.online, commented). Pi runs a small `fleet-agent` daemon
(collect → publish retained registration → 60 s retained status, LWT
offline) plus a `fleet-event` CLI hooked into boot units. Django app
`fleet` (Machine / HardwareSnapshot / BootEvent + transport-agnostic
services) ingests via a paho management-command consumer; pages under
`/fleet/`. Retained messages + idempotent ingest = DB resets self-heal
with zero Pi traffic.

**Tech Stack:** mosquitto 2.x, paho-mqtt (Debian-packaged on both ends),
Django 4.2+/JSONField, pytest-django, systemd, Ansible, nfpm debs.

**Spec:** `docs/superpowers/specs/2026-08-31-fleet-self-registration-design.md`
(same commit — the revised MQTT edition; topic scheme, payloads, broker
config and the resolved decisions D-1..D-6 live there and are normative).

## Global Constraints

- Dev process: feature branch per repo in a worktree; land via PR, CI
  green; never push main; infra: no `gh pr merge --auto`. **Develop only —
  nothing deploys without Tim's explicit go (Task 11 is gated).**
- Pi-side Python: stdlib + `python3-paho-mqtt` (apt, baked into the
  nfsroot) only. Server-side adds `paho-mqtt` to the site package deps.
- Site repo: `uv run pytest` / `uv run ruff check .`; apps use
  `<app>/src/<app>/` layout WITH explicit `AppConfig.path` (ttsite
  pattern); extend `pyproject.toml` find/include and
  `tests/test_packaging.py` for every new app.
- Topics/payloads exactly as the spec: `fpgas/<site>/pi/<serial>/`
  `registration|status|event`; canonical JSON
  `json.dumps(doc, sort_keys=True, separators=(",", ":"))`; fingerprint =
  SHA-256 hex, recomputed server-side.
- Standard boot stages: `network-online`, `time-synced`, `ssh-up`,
  `cam-streaming`, `tt-daemon-up`, `fpga-detected`, `registered`,
  `shutdown`.
- Multi-host guard rule: every new infra task is guarded (`when:
  fleet_broker is defined` etc.) so ps1/CI hosts without the vars skip
  cleanly until enabled.
- Dates ISO 8601.

## Repo/branch map

| Repo | Branch | Tasks |
|---|---|---|
| fpgas.online-site | `fleet-app` | 1–5 |
| fpgas.online-setup-pi | `fleet-scripts` | 6–8 |
| fpgas.online-infra | `fleet-deploy` | 9–10 |
| (gated) deploy + legacy-unit retirement + board-list sync | — | 11–12 |

---

### Task 1: `fleet` app — models

**Files:**
- Create: `fleet/src/fleet/__init__.py`, `fleet/src/fleet/apps.py`,
  `fleet/src/fleet/models.py`, `fleet/src/fleet/migrations/__init__.py`
- Modify: `pib/settings.py` (INSTALLED_APPS), `pyproject.toml`
- Test: `tests/test_fleet_models.py`

**Interfaces:**
- Produces: `Machine(serial unique, site, hostname, first_seen, last_seen,
  online: bool, last_boot_id, last_uptime_s, latest_snapshot FK)`;
  `HardwareSnapshot(machine, fingerprint, document, first_seen,
  last_confirmed)` unique on (machine, fingerprint);
  `BootEvent(machine, boot_id, stage, detail JSON, ts db_index)`.

- [ ] **Step 1: Failing test**

```python
# tests/test_fleet_models.py
import pytest
from django.utils import timezone
from fleet.models import BootEvent, HardwareSnapshot, Machine


@pytest.mark.django_db
def test_machine_online_flag_and_snapshot_uniqueness():
    m = Machine.objects.create(serial="c36b093f773d46b8", site="welland",
                               hostname="pi-sw2-p47", last_seen=timezone.now(),
                               online=True)
    assert m.online is True
    HardwareSnapshot.objects.create(machine=m, fingerprint="ab" * 32,
                                    document={"schema": 1})
    with pytest.raises(Exception):
        HardwareSnapshot.objects.create(machine=m, fingerprint="ab" * 32,
                                        document={"schema": 1})


@pytest.mark.django_db
def test_boot_events_order_by_ts():
    m = Machine.objects.create(serial="s", site="welland",
                               last_seen=timezone.now())
    BootEvent.objects.create(machine=m, boot_id="b1", stage="ssh-up",
                             detail={}, ts=timezone.now())
    assert m.events.count() == 1
```

- [ ] **Step 2: Run** `uv run pytest tests/test_fleet_models.py -q` — fails
  (`ModuleNotFoundError: fleet`).
- [ ] **Step 3: Implement** — `apps.py` copies the ttsite
  `AppConfig.path = os.path.dirname(os.path.abspath(__file__))` pattern
  (name `fleet`). Models:

```python
# fleet/src/fleet/models.py
"""Self-registered fleet machines (see the fleet self-registration design).

Machine = identity + presence (mutable; status/LWT churn). HardwareSnapshot
= append-only content-addressed history: a new row ONLY when the document
fingerprint changes. BootEvent = the boot-stage timeline, kept forever (D-6)."""

from django.db import models


class Machine(models.Model):
    serial = models.CharField(max_length=32, unique=True)
    site = models.CharField(max_length=32)
    hostname = models.CharField(max_length=64, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField()
    online = models.BooleanField(default=False)
    last_boot_id = models.CharField(max_length=40, blank=True)
    last_uptime_s = models.PositiveIntegerField(default=0)
    latest_snapshot = models.ForeignKey(
        "HardwareSnapshot", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["site", "hostname", "serial"]

    def __str__(self):
        return f"{self.hostname or self.serial} @ {self.site}"


class HardwareSnapshot(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE,
                                related_name="snapshots")
    fingerprint = models.CharField(max_length=64, db_index=True)
    document = models.JSONField()
    first_seen = models.DateTimeField(auto_now_add=True)
    last_confirmed = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["first_seen"]
        constraints = [models.UniqueConstraint(
            fields=["machine", "fingerprint"], name="uniq_machine_fingerprint")]


class BootEvent(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE,
                                related_name="events")
    boot_id = models.CharField(max_length=40)
    stage = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    ts = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["ts"]
```

Wire `'fleet',` into `INSTALLED_APPS`; add `"fleet/src"` / `"fleet*"` to
`pyproject.toml` packages.find; `uv run python manage.py makemigrations fleet`.
- [ ] **Step 4: Run** — PASS; `makemigrations --check` clean.
- [ ] **Step 5: Commit** — `feat(fleet): Machine/HardwareSnapshot/BootEvent models`

### Task 2: transport-agnostic services

**Files:**
- Create: `fleet/src/fleet/services.py`
- Test: `tests/test_fleet_services.py`

**Interfaces:**
- Produces (consumed by Task 3 consumer and Task 12 sync):
  `fingerprint(doc) -> str`;
  `register_document(doc) -> tuple[Machine, bool]` (bool = latest snapshot
  moved);
  `status(serial, payload: dict) -> Machine | None` — payload is the
  status-topic JSON; sets `online`, `last_seen=now`, `last_boot_id`,
  `last_uptime_s`; returns None for unknown serial (logged, dropped);
  `boot_event(serial, payload: dict) -> BootEvent | None` — payload
  `{"stage","boot_id","ts","detail"}`, ts ISO 8601 (fallback: now);
  (No prune helper: boot events are kept forever -- resolved D-6.)

- [ ] **Step 1: Failing tests**

```python
# tests/test_fleet_services.py
import pytest
from fleet.models import Machine
from fleet.services import (boot_event, fingerprint, register_document,
                            status)

DOC = {"schema": 1, "machine": {"serial": "c36b093f773d46b8"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p47"},
       "peripherals": {"usb": []}}


def test_fingerprint_stable_and_order_insensitive():
    assert fingerprint({"x": 1, "y": [2]}) == fingerprint({"y": [2], "x": 1})
    assert len(fingerprint(DOC)) == 64


@pytest.mark.django_db
def test_register_dedupes_and_appends_only_on_change():
    m, changed = register_document(DOC)
    assert changed and m.snapshots.count() == 1
    _, changed = register_document(DOC)
    assert not changed and m.snapshots.count() == 1
    doc2 = {**DOC, "peripherals": {"usb": [{"vid": "0403", "pid": "6010"}]}}
    m, changed = register_document(doc2)
    assert changed and m.snapshots.count() == 2
    register_document(DOC)                       # flap back reuses the row
    assert Machine.objects.get().snapshots.count() == 2


@pytest.mark.django_db
def test_status_drives_online_flag_and_ignores_unknown():
    register_document(DOC)
    m = status("c36b093f773d46b8", {"online": True, "boot_id": "b1",
                                    "uptime_s": 61})
    assert m.online and m.last_boot_id == "b1"
    m = status("c36b093f773d46b8", {"online": False, "reason": "connection-lost"})
    assert m.online is False
    assert status("nope", {"online": True}) is None


@pytest.mark.django_db
def test_boot_event_recorded_with_stage_and_boot_id():
    register_document(DOC)
    ev = boot_event("c36b093f773d46b8",
                    {"stage": "ssh-up", "boot_id": "b1",
                     "ts": "2026-08-31T07:00:00Z", "detail": {}})
    assert ev.stage == "ssh-up"
    assert boot_event("nope", {"stage": "x", "boot_id": "b"}) is None
```

- [ ] **Step 2: Run** — fails.
- [ ] **Step 3: Implement** — `fingerprint`/`register_document` exactly as
  the canonical-JSON + `update_or_create`/`get_or_create` +
  re-point-latest_snapshot logic the tests force (hash server-side; bump
  `last_confirmed` always; machine `last_seen`/`site`/`hostname` refresh
  on every registration). `status()`/`boot_event()` look up by serial,
  return None if absent; `status` writes the four presence fields;
  `boot_event` parses `ts` with
  `django.utils.dateparse.parse_datetime` (fallback `timezone.now()`).
  `prune_events` deletes `ts < now - days`.
- [ ] **Step 4: Run** — PASS. **Step 5: Commit** —
  `feat(fleet): idempotent ingest services`

### Task 3: MQTT consumer

**Files:**
- Create: `fleet/src/fleet/management/__init__.py`,
  `fleet/src/fleet/management/commands/__init__.py`,
  `fleet/src/fleet/management/commands/fleet_consumer.py`,
  `fleet/src/fleet/consumer.py`
- Modify: `pyproject.toml` (add `paho-mqtt>=2` dependency)
- Test: `tests/test_fleet_consumer.py`

**Interfaces:**
- Produces: `consumer.dispatch(topic: str, payload: bytes) -> str` — pure
  routing testable without a broker; returns which handler ran
  (`"registration" | "status" | "event" | "ignored"`). Topic parse:
  `fpgas/<site>/pi/<serial>/<kind>`; anything else (e.g. sensors2mqtt
  topics) → `"ignored"`. Malformed JSON → `"ignored"` + log, never raise.
  `Command` (fleet_consumer) connects with paho (anonymous -- D-4), subscribes
  `fpgas/+/pi/+/+`, calls `dispatch` per message, reconnects forever.
  Settings: `FLEET_MQTT = {"host": "127.0.0.1", "port": 1883}` from
  local_settings (no credentials -- the LAN listener is anonymous, D-4).

- [ ] **Step 1: Failing tests**

```python
# tests/test_fleet_consumer.py
import json

import pytest
from fleet import consumer
from fleet.models import Machine

DOC = {"schema": 1, "machine": {"serial": "abc"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p9"}}


@pytest.mark.django_db
def test_dispatch_routes_registration_status_event():
    t = "fpgas/welland/pi/abc/"
    assert consumer.dispatch(t + "registration", json.dumps(DOC).encode()) \
        == "registration"
    assert Machine.objects.get(serial="abc").hostname == "pi-sw2-p9"
    assert consumer.dispatch(t + "status",
                             b'{"online": true, "boot_id": "b", "uptime_s": 5}') \
        == "status"
    assert Machine.objects.get(serial="abc").online is True
    assert consumer.dispatch(t + "event",
                             b'{"stage": "ssh-up", "boot_id": "b"}') == "event"


@pytest.mark.django_db
def test_dispatch_ignores_foreign_topics_and_garbage():
    assert consumer.dispatch("sensors/tweed/cpu_temp", b"41.2") == "ignored"
    assert consumer.dispatch("fpgas/welland/pi/abc/registration", b"{nope") \
        == "ignored"
```

- [ ] **Step 2: Run** — fails.
- [ ] **Step 3: Implement** — `consumer.dispatch` splits the topic,
  requires exactly `["fpgas", site, "pi", serial, kind]` with kind in
  the three known suffixes, `json.loads` under try/except, then calls the
  Task 2 service (for `registration` it also cross-checks
  `doc["machine"]["serial"] == serial` from the topic and ignores
  mismatches). The management command is a thin paho v2 client:
  `mqtt.Client(...)`, `username_pw_set`, `on_message` → `dispatch`,
  `subscribe("fpgas/+/pi/+/+", qos=1)`, `loop_forever(retry_first_connection=True)`.
- [ ] **Step 4: Add the widget bridge (resolved D-5)** — failing test
  first, in `tests/test_fleet_consumer.py`:

```python
@pytest.mark.django_db
def test_events_bridge_into_the_board_page_channel_group():
    # the board pages (dcws.js) subscribe to pistat_pi<port>; the bridge
    # keeps their status log working after the legacy curls are retired
    import asyncio

    from channels.layers import get_channel_layer

    t = "fpgas/welland/pi/abc/"
    consumer.dispatch(t + "registration", json.dumps(DOC).encode())

    async def listen_and_fire():
        layer = get_channel_layer()
        ch = await layer.new_channel()
        await layer.group_add("pistat_pi9", ch)   # DOC hostname pi-sw2-p9
        consumer.dispatch(t + "event", b'{"stage": "ssh-up", "boot_id": "b"}')
        return await asyncio.wait_for(layer.receive(ch), timeout=1)

    msg = asyncio.run(listen_and_fire())
    assert msg["type"] == "stat.message" and msg["status"] == "ssh-up"
    assert msg["message"].startswith("piview: ")
```

  Implement in `consumer.py`: `_widget_group(hostname)` parses
  `pi-sw<s>-p<p>` (also accepts legacy `pi<p>`) → `f"pistat_pi{port}"`,
  returns None otherwise; after `boot_event`/`status` ingest succeeds,
  `async_to_sync(get_channel_layer().group_send)(group,
  {"type": "stat.message", "status": stage, "message": f"piview: {stage}"})`
  (status bridge sends `online`/`offline (<reason>)` as the stage). The
  bridge failing (no channel layer) must never break ingest — wrap and log.
- [ ] **Step 5: Run full suite + ruff** — green.
- [ ] **Step 6: Commit** — `feat(fleet): mqtt consumer + board-page widget bridge`

### Task 4: fleet pages

**Files:**
- Create: `fleet/src/fleet/views.py`, `fleet/src/fleet/urls.py`,
  `fleet/src/fleet/templates/fleet/list.html`,
  `fleet/src/fleet/templates/fleet/detail.html`
- Modify: `pib/urls.py` (`path('fleet/', include('fleet.urls'))`),
  `tests/test_packaging.py` (add `fleet` to APP_PACKAGES)
- Test: `tests/test_fleet_pages.py`

**Interfaces:**
- Produces: `GET /fleet/` — one row per machine: hostname, site, serial,
  model (from `latest_snapshot.document.machine.model`), FPGA kinds,
  online/offline badge (the `Machine.online` flag — LWT-driven, no
  staleness math), last_seen ISO. `GET /fleet/<serial>/` — presence block,
  snapshot history (newest first: fingerprint, first_seen, last_confirmed,
  `<details><pre>` document), boot-event timeline for the latest boot_id.

- [ ] **Step 1: Failing tests**

```python
# tests/test_fleet_pages.py
import pytest
from django.test import Client
from django.utils import timezone
from fleet.models import BootEvent, Machine
from fleet.services import register_document

DOC = {"schema": 1, "machine": {"serial": "abc123", "model": "Raspberry Pi 5"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p47"},
       "fpga": {"boards": [{"kind": "acorn-cle-215+"}]}}


@pytest.fixture
def c():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_list_shows_machine_model_board_and_badge(c):
    register_document(DOC)
    html = c.get("/fleet/").content.decode()
    assert "pi-sw2-p47" in html and "acorn-cle-215+" in html
    assert "Raspberry Pi 5" in html and "offline" in html  # no status yet


@pytest.mark.django_db
def test_detail_shows_history_and_events(c):
    register_document(DOC)
    register_document({**DOC, "fpga": {"boards": []}})
    m = Machine.objects.get()
    BootEvent.objects.create(machine=m, boot_id="b1", stage="ssh-up",
                             detail={}, ts=timezone.now())
    html = c.get("/fleet/abc123/").content.decode()
    assert html.count("<details") >= 2 and "ssh-up" in html
```

- [ ] **Step 2: Run** — 404. **Step 3: Implement** — two plain views
  (`machine_list`, `machine_detail`) + templates in the ttsite visual
  style; urls `""` and `"<str:serial>/"`.
- [ ] **Step 4: Full suite + ruff** — green.
- [ ] **Step 5: Commit**, push branch `fleet-app`, open PR "Fleet
  self-registration: server side", CI green. **STOP — no deploy.**

### Task 4b: pistat widget repairs (same `fleet-app` branch)

**Files:**
- Modify: `pistat/src/pistat/views.py`
- Test: `tests/test_pistat_widgets.py`

**Interfaces:**
- Consumes: `pibfpgas.models.Pi` derived properties (site PR #20).
- Produces: the board-page ping button works on VLAN-per-port sites.

The widgets' ping view still computes `10.21.0.(100+port)` — dead on
welland since the VLAN migration (D-5 says widgets must work properly, so
this rides along). Test-first: create `Pi(port=34, switch=2)`, POST
`/pistat/ping/pi34`, monkeypatch `subprocess.run` capturing argv, assert
the target is `10.21.2.34`; a legacy row (switch NULL) still yields
`10.21.0.134`. Implement: `pi = Pi.objects.filter(port=int(pi_name[2:]))
.first()`; `pi_ip = pi.ip if pi else f"10.21.0.{100 + int(pi_name[2:])}"`.

### Task 5: collector — full document

**Files (fpgas.online-setup-pi, branch `fleet-scripts`):**
- Create: `fleet-scripts/collect.py`, `tests/test_collect.py`,
  fixture tree `tests/data/pi5-acorn/`

**Interfaces:**
- Produces: `collect.document(root="/", site="", hostname="",
  tt_url="http://127.0.0.1:8765") -> dict` per the spec's schema; section
  helpers `machine_section(root)`, `software_section(root)`,
  `connection_section(root, site, hostname)`, `peripherals_section(root)`,
  `fpga_section(peripherals, tt_health)`. All readers take `root` for
  fixture-tree tests; all lists sorted (fingerprint stability).
- Fixture tree carries pi-sw2-p47's real values (2026-08-31 probe):
  model `Raspberry Pi 5 Model B Rev 1.1`, serial `c36b093f773d46b8`,
  cpuinfo `Revision : a04171`, eth0 MAC `98:fe:54:13:f5:75`, os-release
  trixie, dummy `etc/ssh/password.txt` + ed25519 pubkey, USB FTDI
  `0403/6010/210319B3E5C5`, PCIe `0x1cf0/0x0007`, hat files, v4l name
  `ov5647`.

- [ ] **Step 1: Failing tests** — machine/software/connection assertions:

```python
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "fleet-scripts"))
import collect  # noqa: E402

ROOT = pathlib.Path(__file__).parent / "data" / "pi5-acorn"


def test_machine_section():
    m = collect.machine_section(ROOT)
    assert m["serial"] == "c36b093f773d46b8"
    assert m["model"].startswith("Raspberry Pi 5")
    assert m["revision_code"] == "a04171"
    assert m["macs"]["eth0"] == "98:fe:54:13:f5:75"


def test_software_and_connection():
    s = collect.software_section(ROOT)
    assert s["os_release"].startswith("Debian") and s["kernel"]
    c = collect.connection_section(ROOT, site="welland", hostname="pi-sw2-p47")
    assert c["site"] == "welland" and c["login_user"] == "pi"
    assert c["ssh_host_keys"][0].startswith("ssh-ed25519")


def test_peripherals_and_fpga():
    p = collect.peripherals_section(ROOT)
    assert {"vid": "0403", "pid": "6010",
            "product": "FT2232C/D/H Dual UART/FIFO IC",
            "serial": "210319B3E5C5"} in p["usb"]
    assert "ov5647" in p["cameras"]
    f = collect.fpga_section(p, tt_health=None)
    assert sorted(b["kind"] for b in f["boards"]) == ["acorn-cle-215+", "arty-a7"]
    f = collect.fpga_section({"usb": [], "pcie": [], "hats": [], "cameras": []},
                             tt_health={"board": {"present": True},
                                        "kind": "fpga", "slug": "fpga-1",
                                        "version": "1.2.2"})
    assert f["boards"][0]["kind"] == "tt-demo-board"
```

- [ ] **Step 2: Run** — fails. **Step 3: Implement** — `_read(root, rel)`
  (strip NULs), cpuinfo/meminfo regexes, `/sys/class/net/*/address` walk
  (eth/wlan/en*, skip zero MACs), os-release parse,
  `etc/fpgas-online/nfsroot-build.json` else dpkg-status mtime,
  `dpkg-query -W fpgas-online-*`, ssh host-key glob, password.txt last
  non-empty line, sysfs USB walk (skip root hubs `1d6b` and hubs
  `0424`/`2109`), sysfs PCI walk (strip `0x`, skip class `0x0604` bridges
  and RP1 `1de4`), device-tree hat, v4l names. Classification:
  FTDI 0403:6010 + serial `210…` → `arty-a7` (`ids.digilent_serial`);
  pci `1cf0` → `acorn-cle-215+`; pci `10ee` → `xilinx-pcie` (Acorn under a
  user bitstream — record both ids); tt_health present → `tt-demo-board`
  with `ids` slug/board_kind/firmware. `document()` = sections +
  `{"schema": 1}`; tt fetch via urllib, 2 s timeout, `None` on any error.
  **No DNA read** (JTAG-vs-PCIe hazard; `--read-dna` reserved for the
  operator CLI only).
- [ ] **Step 4: Run + ruff** — green. **Step 5: Commit** —
  `feat(fleet): registration document collector`

### Task 6: fleet-agent daemon + fleet-event CLI

**Files:**
- Create: `fleet-scripts/fleet_agent.py`, `fleet-scripts/fleet_event.py`
- Test: `tests/test_fleet_agent.py`

**Interfaces:**
- Produces: `fleet_agent.load_config(path) -> dict` (tomllib: site,
  broker, port -- no credentials, D-4); `fleet_agent.topics(site, serial) ->
  dict(registration=..., status=..., event=...)`;
  `fleet_agent.status_payload(boot_id, uptime_s, fingerprint) -> dict`
  (`online: True`, ISO `ts`); `fleet_agent.run(cfg, client, collect_fn,
  now_fn)` — the loop, injectable client for tests: on connect set LWT
  (`{"online": false, "reason": "connection-lost"}` retained) BEFORE
  connect, publish registration retained iff fingerprint differs from the
  last published, publish status every 60 s, re-collect every 6 h or on
  SIGHUP, publish `{"online": false, "reason": "shutdown"}` + event
  `shutdown` on SIGTERM. `fleet_event.py` CLI:
  `fleet-event <stage> [--detail k=v ...]` publishes one event QoS 1 and
  exits.
- Consumes: Task 5 `collect.document()`; fingerprint identical to the
  server (canonical JSON + sha256 — import the same two-liner, do not
  drift).

- [ ] **Step 1: Failing tests** — with a `FakeClient` recording
  `will_set`/`publish` calls: (a) LWT set on the status topic retained
  before connect; (b) registration published retained once, and again only
  after `collect_fn` returns a changed doc; (c) status published with
  `online: true` and the current fingerprint; (d) SIGTERM path publishes
  shutdown status + event. `fleet_event` test: topic + payload for
  `fleet-event ssh-up`.
- [ ] **Step 2–4**: red → implement → green (+ ruff).
- [ ] **Step 5: Commit** — `feat(fleet): agent daemon + event CLI`

### Task 7: systemd units + deb packaging

**Files:**
- Create: `onpi/fleet/fleet-agent.service`, event hook units
  `onpi/fleet/fleet-event@.service` (`ExecStart=/usr/bin/python3
  /usr/local/lib/fpgas-online/fleet/fleet_event.py %i`) and drop-ins
  hooking the standard stages: `network-online` (After=network-online),
  `time-synced` (After=time-sync), `ssh-up` (WantedBy/After ssh.service),
  `cam-streaming` (After the cam service), `tt-daemon-up` (After
  fpgas-tt.service)
- Modify: `nfpm.yaml` (scripts →
  `/usr/local/lib/fpgas-online/fleet/`, units, enables via the existing
  postinstall pattern)

`fleet-agent.service`:

```ini
[Unit]
Description=fpgas.online fleet agent (registration + status + LWT)
Wants=network-online.target
After=network-online.target fpgas-tt.service
ConditionPathExists=/etc/fpgas-online/fleet.toml

[Service]
ExecStart=/usr/bin/python3 /usr/local/lib/fpgas-online/fleet/fleet_agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] Build the deb via the repo's CI mechanism; `dpkg-deb -c` shows the
  new paths; `python3-paho-mqtt` added to the deb's Depends. Commit, push
  `fleet-scripts`, PR "Fleet self-registration: Pi side", CI green.
  **STOP — no deploy.**

### Task 8: infra — mosquitto role

**Files (fpgas.online-infra, branch `fleet-deploy`):**
- Create: `ansible/roles/mqtt/tasks/main.yml`,
  `ansible/roles/mqtt/templates/fpgas-fleet.conf.j2` (mosquitto conf.d),
  `ansible/roles/mqtt/templates/fleet-acl.j2`
- Modify: `ansible/web.yml` (add the role to the pig play, guarded),
  host_vars for `fpgas.online` + `ps1.fpgas.online` (`fleet_broker:
  true`; no vaulted broker credentials -- anonymous LAN listener, D-4)

Template essentials:

```jinja
# Ansible managed -- fpgas.online fleet broker
listener 1883 {{ eth_local_address | default('10.21.0.1') }}
persistence true
# Anonymous on the LAN listener (resolved D-4): per-port VLAN isolation +
# the firewall are the trust boundary; sensors2mqtt shares this listener.
allow_anonymous true

# all.fpgas.online fan-out (D-1: config-ready only; enable when the
# aggregator exists -- the bridge authenticates OUTBOUND over TLS, so
# anonymity never extends off-site)
#connection all-fpgas-online
#address all.fpgas.online:8883
#topic fpgas/# out 1
```

Everything behind `when: fleet_broker | default(false)`.

- [ ] yamllint + syntax-check green; commit
  `feat(fleet): per-site mosquitto broker (bridge-ready)`.

### Task 9: infra — config bake, consumer service, pages, CI coverage

**Files:**
- Create: `ansible/roles/onpi/templates/fleet.toml.j2` (spec's TOML,
  values from host_vars; baked at `/etc/fpgas-online/fleet.toml` in the
  nfsroot `pi` play, guarded `when: fleet_broker | default(false)`),
  `ansible/roles/site/templates/fleet-consumer.service.j2`
  (`ExecStart={{ django_dir }}/venv/bin/python manage.py fleet_consumer`,
  `WorkingDirectory={{ django_dir }}`, `User={{ user_name }}`,
  `Restart=always`), `ansible/roles/site/templates/pib-fleet.conf.j2`
  (nginx `location /fleet/ { include proxy_params; proxy_pass
  http://unix:/run/gunicorn.sock; }` — pages only; no write API exists)
- Modify: `ansible/roles/site/tasks/django.yml` (FLEET_MQTT dict into
  local_settings via lineinfile, guarded), `verify-server.yml`
  (mosquitto active; `GET /fleet/` returns 200; fleet-consumer active),
  `verify-pi.yml` (fleet-agent.service active), CI test inventory gains
  `fleet_broker: true` so the VM test covers
  broker + consumer + (once the deb is in the apt repo) the agent
- [ ] yamllint + both playbook syntax-checks green; push `fleet-deploy`,
  PR "Fleet self-registration: deploy wiring", VM CI green.
  **STOP — no deploy.**

### Task 10 (GATED — only on Tim's explicit go): deploy welland

- [ ] Merge order: site → setup-pi (deb reaches the apt repo) → infra.
- [ ] `web.yml --limit fpgas.online` (broker, consumer, wheel, nginx),
  then `site.yml --limit fpgas.online,pi --tags pi,fpgas-apt,onpi`
  (recap MUST show a `pi` play line).
- [ ] Overlay-install on two probe Pis first; watch
  `mosquitto_sub -t 'fpgas/#' -v`, confirm /fleet/ rows + boot events;
  staged PoE-cycle reboots (≥30 s spacing) for the rest.
- [ ] Self-heal proof: restart fleet-consumer with an emptied table →
  retained registrations rebuild it in seconds with zero Pi traffic.

### Task 11 (site follow-on, after Task 10 has run for a while): fleet drives /fpgas/

- `fleet/src/fleet/sync.py`: `upsert_pi(machine)` — parse
  `pi-sw(?P<switch>\d+)-p(?P<port>\d+)` from hostname; doc has FPGA
  boards → upsert `pibfpgas.Pi` (switch, port, mac, serial_no, model,
  fpga_board); none → delete that (switch, port) row; called from
  `register_document` when the snapshot changed. Test-first as Tasks 1–4.
  The fixture becomes bootstrap-only; the 2026-08-26 incident class closes.

### Task 12 (setup-pi follow-on, same gate): retire the legacy one-shots

- **Precondition (resolved D-5)**: the Task 3 widget bridge is deployed
  and verified on a live board page — boot a probe Pi and watch its
  stages appear in the page's status log — BEFORE anything is removed.
- Then remove `pistat_info`, `pistat_ssh`, `pistat_cam`, `arty_here`
  units + scripts from the deb (resolved D-3: subsumed by `fleet-event`
  stages). The daphne `/ws/pistat/` consumer, `dcws.js`, and the
  `/pistat/stat/` HTTP endpoint all stay — external callers may still use
  the endpoint, and the pages keep their existing subscribe path.

## Self-review (authoring time)

- Spec goals 1–7 ↔ tasks: 1 (5,6), 2 (8 bridge stanza + broker-side
  fan-out; Pi unaffected), 3 (1,2), 4 (5), 5 (6 status/LWT), 6 (6,7 events
  + hooks), 7 (2 idempotent ingest + 3 retained replay + Task 10 proof).
- Resolved D-1..D-3 honoured: bridge commented; no HA anywhere; legacy
  units subsumed (Task 12) not merely deleted.
- Names consistent across tasks: `fingerprint`/`register_document`/
  `status`/`boot_event`/`dispatch`/`document()`/`fleet_agent.run`; topic
  scheme identical in Tasks 3, 6, 8.
- Nothing deploys without the Task 10 gate.
