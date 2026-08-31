# Fleet Self-Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fleet Pis register a complete hardware/software/connection document
with every configured web app on boot, keep a content-addressed hardware
history server-side, and heartbeat every 60 s.

**Architecture:** New Django app `fleet` in fpgas.online-site (Machine +
append-only HardwareSnapshot, HTTPS register/heartbeat API, list/detail
pages); new stdlib-only collector + registrar in fpgas.online-setup-pi run
by systemd units; fpgas.online-infra bakes `/etc/fpgas-online/fleet.toml`
into the nfsroot and wires nginx/local_settings. Heartbeat `known:false`
triggers re-registration, so a reset DB self-heals fleet-wide in ~1 minute.

**Tech Stack:** Django 4.2+/JSONField, pytest-django, Python 3.11+ stdlib
(tomllib/urllib) on the Pi, systemd timers, Ansible, nfpm debs.

**Spec:** `docs/superpowers/specs/2026-08-31-fleet-self-registration-design.md`
(same commit). Read it first — schema, canonicalisation and transport
decisions are argued there, and every task below implements a section of it.

## Global Constraints

- Dev process: each repo gets a feature branch in a worktree; land via PR
  with CI green; never push main; infra repo: no `gh pr merge --auto`.
- Pi-side code is **stdlib-only** Python ≥ 3.11 (nfsroot has no pip venv).
- Site repo conventions: `uv run pytest`, `uv run ruff check .`, apps use
  `<app>/src/<app>/` layout WITH the explicit-`AppConfig.path` fix (see
  `ttsite/src/ttsite/apps.py`), wheel packaging must be extended for every
  new app (`pyproject.toml` find/include lists + `tests/test_packaging.py`).
- setup-pi repo: files ship via `nfpm.yaml` contents entries; ruff lints
  `pistat-scripts/` siblings — match its style.
- Fingerprints: SHA-256 hex of
  `json.dumps(doc, sort_keys=True, separators=(",", ":"))`, computed
  server-side; the client's value is advisory only.
- API auth: `Authorization: Bearer <token>`, tokens from
  `settings.FLEET_TOKENS` (list). 403 on mismatch, 400 on bad JSON,
  256 KB body cap.
- Dates in docs/UI: ISO 8601.

## Repo/branch map

| Repo | Branch | Tasks |
|---|---|---|
| fpgas.online-site | `fleet-app` | 1–5 |
| fpgas.online-setup-pi | `fleet-scripts` | 6–9 |
| fpgas.online-infra | `fleet-deploy` | 10–12 |
| fpgas.online-site | `fleet-drives-boards` (after 1–12 proven) | 13 |

---

### Task 1: `fleet` app — models + migration

**Files:**
- Create: `fleet/src/fleet/__init__.py`, `fleet/src/fleet/apps.py`,
  `fleet/src/fleet/models.py`, `fleet/src/fleet/migrations/__init__.py`
- Modify: `pib/settings.py` (INSTALLED_APPS), `pyproject.toml`
  (packages.find where/include)
- Test: `tests/test_fleet_models.py`

**Interfaces:**
- Produces: `fleet.models.Machine(serial, site, hostname, first_seen,
  last_seen, last_boot_id, last_uptime_s, latest_snapshot)` and
  `fleet.models.HardwareSnapshot(machine, fingerprint, document,
  first_seen, last_confirmed)` with
  `unique_together (machine, fingerprint)`; `Machine.live` property
  (last_seen within 90 s).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fleet_models.py
from datetime import timedelta

import pytest
from django.utils import timezone
from fleet.models import HardwareSnapshot, Machine


@pytest.mark.django_db
def test_machine_live_within_90s():
    m = Machine.objects.create(serial="c36b093f773d46b8", site="welland",
                               hostname="pi-sw2-p47", last_seen=timezone.now())
    assert m.live is True
    m.last_seen = timezone.now() - timedelta(seconds=120)
    assert m.live is False


@pytest.mark.django_db
def test_snapshot_unique_per_machine_and_fingerprint():
    m = Machine.objects.create(serial="s1", site="welland", hostname="pi-sw2-p1",
                               last_seen=timezone.now())
    HardwareSnapshot.objects.create(machine=m, fingerprint="ab" * 32,
                                    document={"schema": 1})
    with pytest.raises(Exception):
        HardwareSnapshot.objects.create(machine=m, fingerprint="ab" * 32,
                                        document={"schema": 1})
```

- [ ] **Step 2: Run it** — `uv run pytest tests/test_fleet_models.py -q`;
  expect `ModuleNotFoundError: fleet`.

- [ ] **Step 3: Implement**

```python
# fleet/src/fleet/apps.py  (same namespace-package fix as ttsite/pibfpgas)
import os

from django.apps import AppConfig


class FleetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fleet"
    verbose_name = "fleet registration"
    path = os.path.dirname(os.path.abspath(__file__))
```

```python
# fleet/src/fleet/models.py
"""Self-registered fleet machines.

Machine = identity + presence (mutable, heartbeat churn). HardwareSnapshot =
append-only content-addressed history: a new row appears ONLY when the
registered document's fingerprint changes. See the 2026-08-31 fleet
self-registration design doc.
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone

LIVE_WINDOW = timedelta(seconds=90)  # one missed 60s beat + slack


class Machine(models.Model):
    serial = models.CharField(max_length=32, unique=True)
    site = models.CharField(max_length=32)
    hostname = models.CharField(max_length=64, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField()
    last_boot_id = models.CharField(max_length=40, blank=True)
    last_uptime_s = models.PositiveIntegerField(default=0)
    latest_snapshot = models.ForeignKey(
        "HardwareSnapshot", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["site", "hostname", "serial"]

    def __str__(self):
        return f"{self.hostname or self.serial} @ {self.site}"

    @property
    def live(self):
        return timezone.now() - self.last_seen <= LIVE_WINDOW


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
```

Wire up: add `'fleet',` to `INSTALLED_APPS` in `pib/settings.py` (after
`'ttsite'`); in `pyproject.toml` add `"fleet/src"` to
`tool.setuptools.packages.find.where` and `"fleet*"` to `include`. Then
`uv run python manage.py makemigrations fleet`.

- [ ] **Step 4: Run** `uv run pytest tests/test_fleet_models.py -q` — PASS;
  `uv run python manage.py makemigrations --check --dry-run` — clean.

- [ ] **Step 5: Commit** — `feat(fleet): Machine + HardwareSnapshot models`

### Task 2: fingerprint + registration service

**Files:**
- Create: `fleet/src/fleet/services.py`
- Test: `tests/test_fleet_services.py`

**Interfaces:**
- Produces: `fleet.services.fingerprint(doc: dict) -> str` (sha256 hex);
  `fleet.services.register_document(doc: dict) -> tuple[Machine, bool]`
  (bool = a new snapshot row was created); `fleet.services.beat(serial,
  boot_id, uptime_s, fingerprint) -> bool` (known?). Consumed by Task 3/4
  views and Task 13.
- Consumes: Task 1 models. Doc shape: `doc["machine"]["serial"]`,
  `doc["connection"]["site"]`, `doc["connection"]["hostname"]` (see spec).

- [ ] **Step 1: Failing tests**

```python
# tests/test_fleet_services.py
import pytest
from fleet.models import Machine
from fleet.services import beat, fingerprint, register_document

DOC = {"schema": 1,
       "machine": {"serial": "c36b093f773d46b8"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p47"},
       "peripherals": {"usb": []}}


def test_fingerprint_is_stable_and_order_insensitive():
    a = fingerprint({"x": 1, "y": [2, 3]})
    b = fingerprint({"y": [2, 3], "x": 1})
    assert a == b and len(a) == 64


@pytest.mark.django_db
def test_register_creates_then_dedupes_then_snapshots_change():
    m, changed = register_document(DOC)
    assert changed is True and m.snapshots.count() == 1
    m2, changed = register_document(DOC)          # identical → no new row
    assert changed is False and m2.pk == m.pk and m2.snapshots.count() == 1
    first_confirmed = m2.latest_snapshot.last_confirmed
    doc2 = {**DOC, "peripherals": {"usb": [{"vid": "0403", "pid": "6010"}]}}
    m3, changed = register_document(doc2)         # hardware changed → new row
    assert changed is True and m3.snapshots.count() == 2
    assert m3.latest_snapshot.fingerprint == fingerprint(doc2)
    register_document(DOC)                        # flap back → reuse old row
    assert Machine.objects.get(pk=m.pk).snapshots.count() == 2
    assert m.snapshots.get(fingerprint=fingerprint(DOC))
    assert (m.snapshots.get(fingerprint=fingerprint(DOC)).last_confirmed
            > first_confirmed)


@pytest.mark.django_db
def test_beat_updates_presence_and_reports_known():
    register_document(DOC)
    assert beat("c36b093f773d46b8", "boot-1", 120, fingerprint(DOC)) is True
    m = Machine.objects.get(serial="c36b093f773d46b8")
    assert m.last_boot_id == "boot-1" and m.last_uptime_s == 120
    assert beat("c36b093f773d46b8", "boot-1", 180, "0" * 64) is False  # doc drift
    assert beat("ffffffffffffffff", "boot-9", 5, "0" * 64) is False    # unknown pi
```

- [ ] **Step 2: Run** — fails with `ModuleNotFoundError: fleet.services`.

- [ ] **Step 3: Implement**

```python
# fleet/src/fleet/services.py
"""Registration/heartbeat logic, transport-agnostic (views call these; a
future MQTT consumer would too)."""

import hashlib
import json

from django.utils import timezone

from .models import HardwareSnapshot, Machine


def fingerprint(doc):
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def register_document(doc):
    serial = doc["machine"]["serial"]
    conn = doc.get("connection", {})
    now = timezone.now()
    fp = fingerprint(doc)

    machine, _ = Machine.objects.update_or_create(
        serial=serial,
        defaults={"site": conn.get("site", ""),
                  "hostname": conn.get("hostname", ""),
                  "last_seen": now})
    snap, created = HardwareSnapshot.objects.get_or_create(
        machine=machine, fingerprint=fp, defaults={"document": doc})
    changed = machine.latest_snapshot_id != snap.pk
    snap.last_confirmed = now
    snap.save(update_fields=["last_confirmed"])
    if changed:
        machine.latest_snapshot = snap
        machine.save(update_fields=["latest_snapshot"])
    return machine, created or changed


def beat(serial, boot_id, uptime_s, fp):
    now = timezone.now()
    machine = Machine.objects.filter(serial=serial).first()
    if machine is None:
        return False
    machine.last_seen = now
    machine.last_boot_id = boot_id
    machine.last_uptime_s = uptime_s
    machine.save(update_fields=["last_seen", "last_boot_id", "last_uptime_s"])
    return bool(machine.latest_snapshot
                and machine.latest_snapshot.fingerprint == fp)
```

(Nuance the test pins down: `changed` is true when the *latest* snapshot
moved — including a flap back to an older stored row, which reuses that row
via `get_or_create` and merely re-points `latest_snapshot`.)

- [ ] **Step 4: Run** `uv run pytest tests/test_fleet_services.py -q` — PASS.
- [ ] **Step 5: Commit** — `feat(fleet): content-addressed registration service`

### Task 3: register API endpoint

**Files:**
- Create: `fleet/src/fleet/views.py`, `fleet/src/fleet/urls.py`
- Modify: `pib/urls.py` (add `path('fleet/', include('fleet.urls'))`)
- Test: `tests/test_fleet_api.py`

**Interfaces:**
- Produces: `POST /fleet/api/register/` per the spec (`{"ok", "changed",
  "fingerprint"}`); `fleet.views._authorized(request) -> bool` reused by
  Task 4. URL names: `fleet-register`, `fleet-heartbeat`, `fleet-list`,
  `fleet-detail` (Tasks 4–5 fill the rest of `urls.py`).
- Consumes: Task 2 services; `settings.FLEET_TOKENS` (tests set it; prod
  comes from local_settings via Task 10).

- [ ] **Step 1: Failing tests**

```python
# tests/test_fleet_api.py
import json

import pytest
from django.test import Client
from fleet.models import Machine

DOC = {"schema": 1, "machine": {"serial": "c36b093f773d46b8"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p47"}}


@pytest.fixture
def api(settings):
    settings.FLEET_TOKENS = ["sekrit"]
    return Client(HTTP_HOST="welland.fpgas.online",
                  HTTP_AUTHORIZATION="Bearer sekrit")


@pytest.mark.django_db
def test_register_roundtrip(api):
    r = api.post("/fleet/api/register/", json.dumps(DOC),
                 content_type="application/json")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["changed"] is True
    assert Machine.objects.get(serial="c36b093f773d46b8").hostname == "pi-sw2-p47"
    assert api.post("/fleet/api/register/", json.dumps(DOC),
                    content_type="application/json").json()["changed"] is False


@pytest.mark.django_db
def test_register_rejects_bad_token_and_bad_json(api, settings):
    bad = Client(HTTP_AUTHORIZATION="Bearer wrong")
    assert bad.post("/fleet/api/register/", json.dumps(DOC),
                    content_type="application/json").status_code == 403
    assert api.post("/fleet/api/register/", "{nope",
                    content_type="application/json").status_code == 400
```

- [ ] **Step 2: Run** — 404s/import errors expected.

- [ ] **Step 3: Implement**

```python
# fleet/src/fleet/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path("api/register/", views.register, name="fleet-register"),
]
```

```python
# fleet/src/fleet/views.py
import json

from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services

MAX_BODY = 256 * 1024


def _authorized(request):
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip()
    return token and token in getattr(settings, "FLEET_TOKENS", [])


def _payload(request):
    if len(request.body) > MAX_BODY:
        raise ValueError("body too large")
    return json.loads(request.body)


@csrf_exempt
@require_POST
def register(request):
    if not _authorized(request):
        return HttpResponseForbidden()
    try:
        doc = _payload(request)
        machine, changed = services.register_document(doc)
    except (ValueError, KeyError, TypeError) as exc:
        return HttpResponseBadRequest(str(exc))
    return JsonResponse({"ok": True, "changed": changed,
                         "fingerprint": machine.latest_snapshot.fingerprint})
```

And in `pib/urls.py` add `path('fleet/', include('fleet.urls')),` to
`urlpatterns`.

- [ ] **Step 4: Run** `uv run pytest tests/test_fleet_api.py -q` — PASS.
- [ ] **Step 5: Commit** — `feat(fleet): register endpoint`

### Task 4: heartbeat API endpoint

**Files:**
- Modify: `fleet/src/fleet/views.py`, `fleet/src/fleet/urls.py`
- Test: `tests/test_fleet_api.py` (append)

**Interfaces:**
- Produces: `POST /fleet/api/heartbeat/` body `{"serial","boot_id",
  "uptime_s","fingerprint"}` → `{"ok": true, "known": bool}`. The Pi
  registrar (Task 8) re-registers on `known:false`.

- [ ] **Step 1: Failing tests** (append to `tests/test_fleet_api.py`)

```python
@pytest.mark.django_db
def test_heartbeat_known_and_unknown(api):
    api.post("/fleet/api/register/", json.dumps(DOC),
             content_type="application/json")
    fp = Machine.objects.get().latest_snapshot.fingerprint
    r = api.post("/fleet/api/heartbeat/",
                 json.dumps({"serial": "c36b093f773d46b8", "boot_id": "b1",
                             "uptime_s": 61, "fingerprint": fp}),
                 content_type="application/json")
    assert r.json() == {"ok": True, "known": True}
    r = api.post("/fleet/api/heartbeat/",
                 json.dumps({"serial": "unknown", "boot_id": "b1",
                             "uptime_s": 1, "fingerprint": "0" * 64}),
                 content_type="application/json")
    assert r.json() == {"ok": True, "known": False}
```

- [ ] **Step 2: Run** — 404 on the new path.

- [ ] **Step 3: Implement** — add to `urls.py`:
`path("api/heartbeat/", views.heartbeat, name="fleet-heartbeat"),` and to
`views.py`:

```python
@csrf_exempt
@require_POST
def heartbeat(request):
    if not _authorized(request):
        return HttpResponseForbidden()
    try:
        b = _payload(request)
        known = services.beat(b["serial"], b.get("boot_id", ""),
                              int(b.get("uptime_s", 0)),
                              b.get("fingerprint", ""))
    except (ValueError, KeyError, TypeError) as exc:
        return HttpResponseBadRequest(str(exc))
    return JsonResponse({"ok": True, "known": known})
```

- [ ] **Step 4: Run tests** — PASS. **Step 5: Commit** —
  `feat(fleet): heartbeat endpoint`

### Task 5: fleet pages (list + machine history)

**Files:**
- Create: `fleet/src/fleet/templates/fleet/list.html`,
  `fleet/src/fleet/templates/fleet/detail.html`
- Modify: `fleet/src/fleet/views.py`, `fleet/src/fleet/urls.py`,
  `tests/test_packaging.py` (fleet templates ship in the wheel)
- Test: `tests/test_fleet_pages.py`

**Interfaces:**
- Produces: `GET /fleet/` (table: hostname, site, serial, model, FPGA
  boards, live badge, last_seen ISO) and `GET /fleet/<serial>/` (presence +
  snapshot history, newest first, each with first_seen/last_confirmed and a
  `<pre>` of the document; consecutive-snapshot key diff optional).

- [ ] **Step 1: Failing tests**

```python
# tests/test_fleet_pages.py
import pytest
from django.test import Client
from fleet.services import register_document

DOC = {"schema": 1, "machine": {"serial": "abc123", "model": "Raspberry Pi 5"},
       "connection": {"site": "welland", "hostname": "pi-sw2-p47"},
       "fpga": {"boards": [{"kind": "acorn-cle-215+"}]}}


@pytest.fixture
def c():
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_list_shows_machine_and_liveness(c):
    register_document(DOC)
    html = c.get("/fleet/").content.decode()
    assert "pi-sw2-p47" in html and "acorn-cle-215+" in html and "live" in html


@pytest.mark.django_db
def test_detail_shows_snapshot_history(c):
    register_document(DOC)
    register_document({**DOC, "fpga": {"boards": []}})
    html = c.get("/fleet/abc123/").content.decode()
    assert html.count("snapshot") >= 2 and "pi-sw2-p47" in html
```

- [ ] **Step 2: Run** — 404.

- [ ] **Step 3: Implement** — views:

```python
from django.shortcuts import get_object_or_404, render

from .models import Machine


def machine_list(request):
    return render(request, "fleet/list.html",
                  {"machines": Machine.objects.select_related("latest_snapshot")})


def machine_detail(request, serial):
    machine = get_object_or_404(Machine, serial=serial)
    return render(request, "fleet/detail.html",
                  {"machine": machine,
                   "snapshots": machine.snapshots.order_by("-first_seen")})
```

urls: `path("", views.machine_list, name="fleet-list")` and
`path("<str:serial>/", views.machine_detail, name="fleet-detail")` (keep
the `api/` paths ABOVE the catch-all serial pattern). Templates: plain
tables in the ttsite visual style; list row =
`hostname | site | serial | machine.latest_snapshot.document.machine.model |
fpga kinds | live/stale badge | last_seen ISO`; detail = presence block +
one `<details class="snapshot">` per snapshot with the pretty-printed
document. Extend `tests/test_packaging.py` `APP_PACKAGES` with `"fleet"`.

- [ ] **Step 4: Run full suite** `uv run pytest -q` + `uv run ruff check .`
  — all green.
- [ ] **Step 5: Commit** — `feat(fleet): fleet list and history pages`, push
  branch, open PR "Fleet self-registration: server side", wait for CI.

### Task 6: collector — machine/software/connection sections

**Files (fpgas.online-setup-pi, branch `fleet-scripts`):**
- Create: `fleet-scripts/collect.py`
- Test: `tests/test_collect.py` (create `tests/` mirroring how
  `pistat-scripts` are linted; wire ruff/pytest if the repo lacks them —
  `uv run pytest` from repo root)

**Interfaces:**
- Produces: `collect.document(root="/", tt_url="http://127.0.0.1:8765") ->
  dict` returning the spec's document; every reader takes `root` so tests
  point it at a fixture tree. Section helpers `machine_section(root)`,
  `software_section(root)`, `connection_section(root, site)`,
  `peripherals_section(root)`, `fpga_section(peripherals, tt_health)`.
- Consumes: fixture tree `tests/data/pi5-acorn/` with files:
  `proc/device-tree/model`, `sys/firmware/devicetree/base/serial-number`,
  `proc/cpuinfo`, `proc/meminfo`, `etc/os-release`,
  `sys/class/net/eth0/address`, `etc/ssh/password.txt`,
  `etc/ssh/ssh_host_ed25519_key.pub`.

- [ ] **Step 1: Failing test**

```python
# tests/test_collect.py
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "fleet-scripts"))
import collect  # noqa: E402

ROOT = pathlib.Path(__file__).parent / "data" / "pi5-acorn"


def test_machine_section_reads_devicetree_and_cpuinfo():
    m = collect.machine_section(ROOT)
    assert m["serial"] == "c36b093f773d46b8"
    assert m["model"].startswith("Raspberry Pi 5")
    assert m["revision_code"] == "a04171"
    assert m["memory_mb"] > 256
    assert m["macs"]["eth0"] == "98:fe:54:13:f5:75"


def test_software_and_connection_sections():
    s = collect.software_section(ROOT)
    assert s["os_release"].startswith("Debian") and s["kernel"]
    c = collect.connection_section(ROOT, site="welland")
    assert c["site"] == "welland" and c["login_user"] == "pi"
    assert c["ssh_host_keys"][0].startswith("ssh-ed25519")
    assert c["login_password"]
```

Populate `tests/data/pi5-acorn/` with the exact values probed from
pi-sw2-p47 on 2026-08-31 (model `Raspberry Pi 5 Model B Rev 1.1`, serial
`c36b093f773d46b8`, mac `98:fe:54:13:f5:75`; cpuinfo `Revision : a04171`;
os-release PRETTY_NAME `Debian GNU/Linux 13 (trixie)`; a dummy
password.txt and host key).

- [ ] **Step 2: Run** `uv run pytest tests/test_collect.py -q` — fails.

- [ ] **Step 3: Implement** (representative core — the rest follows the
  same read-and-strip pattern):

```python
# fleet-scripts/collect.py
"""Collect this Pi's registration document. Stdlib only; every reader takes
a root path so tests run against a canned fixture tree."""

import json
import os
import pathlib
import platform
import re
import subprocess


def _read(root, rel, default=""):
    try:
        return (pathlib.Path(root) / rel).read_text().replace("\0", "").strip()
    except OSError:
        return default


def machine_section(root="/"):
    cpuinfo = _read(root, "proc/cpuinfo")
    rev = re.search(r"^Revision\s*:\s*(\S+)", cpuinfo, re.M)
    mem = re.search(r"^MemTotal:\s*(\d+) kB", _read(root, "proc/meminfo"), re.M)
    macs = {}
    for iface in sorted(pathlib.Path(root, "sys/class/net").glob("*")):
        if iface.name.startswith(("eth", "wlan", "end", "enx")):
            addr = _read(root, f"sys/class/net/{iface.name}/address")
            if addr and addr != "00:00:00:00:00:00":
                macs[iface.name] = addr
    return {"serial": _read(root, "sys/firmware/devicetree/base/serial-number"),
            "model": _read(root, "proc/device-tree/model"),
            "revision_code": rev.group(1) if rev else "",
            "memory_mb": int(mem.group(1)) // 1024 if mem else 0,
            "macs": macs}


def software_section(root="/"):
    osr = dict(line.split("=", 1) for line in
               _read(root, "etc/os-release").splitlines() if "=" in line)
    stamp = _read(root, "etc/fpgas-online/nfsroot-build.json")
    if stamp:
        updated = json.loads(stamp).get("built", "")
    else:
        try:
            mtime = os.stat(pathlib.Path(root, "var/lib/dpkg/status")).st_mtime
            import datetime
            updated = datetime.datetime.fromtimestamp(
                mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            updated = ""
    return {"kernel": platform.release(),
            "os_release": osr.get("PRETTY_NAME", "").strip('"'),
            "nfsroot_updated": updated,
            "packages": _fpgas_packages()}


def _fpgas_packages():
    try:
        out = subprocess.run(
            ["dpkg-query", "-W", "-f", "${Package} ${Version}\n",
             "fpgas-online-*"], capture_output=True, text=True, timeout=10)
        return dict(line.split(" ", 1) for line in out.stdout.splitlines())
    except (OSError, ValueError):
        return {}
```

`connection_section(root, site)`: hostname via `platform.node()` (fixture
override param), `ip -json addr` (subprocess, filtered to global scope,
sorted) with a `root`-based fallback for tests, host keys =
`sorted(glob etc/ssh/ssh_host_*.pub)` contents, `login_user="pi"`,
`login_password=_read(root, "etc/ssh/password.txt")` last non-empty line.
`document()` assembles all sections plus
`{"schema": 1}` and sorts every list for fingerprint stability.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** —
  `feat(fleet): collector machine/software/connection sections`

### Task 7: collector — peripherals + FPGA detection

**Files:**
- Modify: `fleet-scripts/collect.py`
- Test: `tests/test_collect.py` (append) + extend the fixture tree with
  `sys/bus/usb/devices/1-1.2/{idVendor,idProduct,product,serial}`
  (0403/6010/`FT2232C/D/H Dual UART/FIFO IC`/`210319B3E5C5`) and
  `sys/bus/pci/devices/0001:01:00.0/{vendor,device}` (`0x1cf0`/`0x0007`),
  `proc/device-tree/hat/{vendor,product,product_id,uuid}`,
  `sys/class/video4linux/v4l-subdev0/name` (`ov5647`).

**Interfaces:**
- Produces: `peripherals_section(root) -> {"hats": [...], "usb": [...],
  "pcie": [...], "cameras": [...]}` and
  `fpga_section(peripherals, tt_health: dict | None) -> {"boards": [...]}`
  with kinds `arty-a7` (FTDI 0403:6010 + serial startswith "210"),
  `acorn-cle-215+` (pci vendor 1cf0), `xilinx-pcie` (pci vendor 10ee —
  an Acorn re-enumerated under a user bitstream), `tt-demo-board`
  (tt_health board present; carries `slug`, `kind`, `firmware`).
  **No DNA read here** — passive sources only (spec: JTAG on a
  PCIe-attached Acorn wedges the link).

- [ ] **Step 1: Failing test**

```python
def test_peripherals_and_fpga_classification():
    p = collect.peripherals_section(ROOT)
    assert {"vid": "0403", "pid": "6010",
            "product": "FT2232C/D/H Dual UART/FIFO IC",
            "serial": "210319B3E5C5"} in p["usb"]
    assert {"vendor": "1cf0", "device": "0007"} in [
        {"vendor": d["vendor"], "device": d["device"]} for d in p["pcie"]]
    assert "ov5647" in p["cameras"]

    f = collect.fpga_section(p, tt_health=None)
    kinds = sorted(b["kind"] for b in f["boards"])
    assert kinds == ["acorn-cle-215+", "arty-a7"]
    assert [b for b in f["boards"] if b["kind"] == "arty-a7"
            ][0]["ids"]["digilent_serial"] == "210319B3E5C5"

    f = collect.fpga_section({"usb": [], "pcie": [], "hats": [], "cameras": []},
                             tt_health={"board": {"present": True},
                                        "kind": "fpga", "slug": "fpga-1",
                                        "version": "1.2.2"})
    assert f["boards"] == [{"kind": "tt-demo-board", "via": "tt-daemon",
                            "ids": {"slug": "fpga-1", "board_kind": "fpga",
                                    "firmware": "1.2.2"}}]
```

- [ ] **Step 2: Run** — fails. **Step 3: Implement** the sysfs walks
  (usb: every `sys/bus/usb/devices/*` dir with an `idVendor` file, skip
  `1d6b` root hubs and hubs `0424`/`2109`; pci: every
  `sys/bus/pci/devices/*` reading `vendor`/`device`, strip `0x`, skip
  bridge class `0x0604` and the RP1 `1de4`), the hat/camera reads, and the
  classification table exactly as the test pins it. `document()` gains
  `tt_url` handling: `urllib.request.urlopen(tt_url + "/health", timeout=2)`
  → `fpga_section(peripherals, tt_health)`; any exception → `tt_health=None`.
- [ ] **Step 4: Run** — PASS. **Step 5: Commit** —
  `feat(fleet): peripherals + FPGA board detection`

### Task 8: registrar CLI

**Files:**
- Create: `fleet-scripts/register.py`
- Test: `tests/test_register.py`

**Interfaces:**
- Produces: `register.py register|heartbeat --config /etc/fpgas-online/fleet.toml`
  (default). `load_config(path) -> dict` (tomllib); `post(url, token,
  payload) -> dict | None` (urllib, 5 s timeout, None on any failure —
  logged to stderr, never raises); `run_register(cfg, doc)` posts the doc
  to `<endpoint>/api/register/` for every endpoint; `run_heartbeat(cfg,
  beat_fn, register_fn)` posts `{"serial","boot_id","uptime_s",
  "fingerprint"}` to every endpoint and calls `register_fn` once if ANY
  endpoint answered `known: false`. Exit code 0 unless the config is
  unreadable (exit 2) — a down web app must not fail the systemd unit.
- Consumes: Task 6/7 `collect.document()`; fingerprint must equal the
  server's: same `json.dumps(doc, sort_keys=True, separators=(",", ":"))`
  + sha256.

- [ ] **Step 1: Failing test**

```python
# tests/test_register.py
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "fleet-scripts"))
import register  # noqa: E402


def test_load_config(tmp_path):
    cfg = tmp_path / "fleet.toml"
    cfg.write_text('site = "welland"\ntoken = "t"\n'
                   'endpoints = ["https://welland.fpgas.online/fleet"]\n')
    c = register.load_config(cfg)
    assert c["endpoints"] == ["https://welland.fpgas.online/fleet"]


def test_heartbeat_reregisters_on_unknown(monkeypatch):
    calls = []
    cfg = {"site": "w", "token": "t", "endpoints": ["http://a", "http://b"]}
    monkeypatch.setattr(register, "post",
                        lambda url, token, payload:
                        {"ok": True, "known": url.startswith("http://a")})
    register.run_heartbeat(cfg, beat_payload={"serial": "s", "boot_id": "b",
                                              "uptime_s": 1,
                                              "fingerprint": "f"},
                           register_fn=lambda: calls.append("reg"))
    assert calls == ["reg"]     # b said unknown → one full re-register


def test_post_failure_returns_none(monkeypatch):
    assert register.post("http://127.0.0.1:1/fleet", "t", {}) is None
```

- [ ] **Step 2: Run** — fails. **Step 3: Implement** with `argparse`
  (subcommands `register`, `heartbeat`; `--config`, `--tt-url`,
  `--read-dna` reserved/no-op for now), `tomllib.load`, urllib POST with
  `Authorization: Bearer` header, boot_id from
  `/proc/sys/kernel/random/boot_id`, uptime from `/proc/uptime`.
- [ ] **Step 4: Run + ruff** — PASS. **Step 5: Commit** —
  `feat(fleet): registrar CLI`

### Task 9: systemd units + deb packaging

**Files:**
- Create: `onpi/fleet/fleet-register.service`,
  `onpi/fleet/fleet-heartbeat.service`, `onpi/fleet/fleet-heartbeat.timer`
- Modify: `nfpm.yaml` (ship `fleet-scripts/*.py` to
  `/usr/local/lib/fpgas-online/fleet/`, units to `/etc/systemd/system/`,
  enable via the package's existing postinstall pattern)

**Interfaces:**
- Produces: on-boot registration + 60 s beats on every nfsroot Pi. Config
  file `/etc/fpgas-online/fleet.toml` is NOT in this deb — infra bakes it
  (Task 10); the units are inert without it (`ConditionPathExists`).

- [ ] **Step 1: Write the units**

```ini
# onpi/fleet/fleet-register.service
[Unit]
Description=Register this Pi with the fpgas.online fleet
Wants=network-online.target
After=network-online.target fpgas-tt.service
ConditionPathExists=/etc/fpgas-online/fleet.toml

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/fpgas-online/fleet/register.py register

[Install]
WantedBy=multi-user.target
```

```ini
# onpi/fleet/fleet-heartbeat.service
[Unit]
Description=fpgas.online fleet heartbeat
ConditionPathExists=/etc/fpgas-online/fleet.toml

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/fpgas-online/fleet/register.py heartbeat
```

```ini
# onpi/fleet/fleet-heartbeat.timer
[Unit]
Description=fpgas.online fleet heartbeat every 60s

[Timer]
OnBootSec=90
OnUnitActiveSec=60
RandomizedDelaySec=10

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: nfpm entries** — add the three units and both scripts to
  `nfpm.yaml` `contents:` following the existing `pistat-scripts` entries;
  enable `fleet-register.service` + `fleet-heartbeat.timer` wherever the
  package's postinstall enables its other units (match the repo's existing
  mechanism exactly).
- [ ] **Step 3: Build check** — `nfpm package -f nfpm.yaml -p deb -t /tmp`
  equivalent used by the repo's CI (or `uv run pytest` if the repo tests
  packaging) succeeds; `dpkg-deb -c` shows the six new paths.
- [ ] **Step 4: Commit** — `feat(fleet): systemd units + packaging`, push,
  PR "Fleet self-registration: Pi side", CI green.

### Task 10: infra — bake config, tokens, nginx

**Files (fpgas.online-infra, branch `fleet-deploy`):**
- Create: `ansible/roles/onpi/templates/fleet.toml.j2`,
  `ansible/roles/site/templates/pib-fleet.conf.j2`
- Modify: `ansible/roles/onpi/tasks/main.yml` (bake config into nfsroot),
  `ansible/roles/site/tasks/nginx.yml` (install the include),
  `ansible/roles/site/tasks/django.yml` (FLEET_TOKENS lineinfile),
  `ansible/inventory/host_vars/fpgas.online.yml` +
  `ansible/inventory/host_vars/ps1.fpgas.online.yml` (`fleet_endpoints`,
  vaulted `vault_fleet_token`), `ansible/verify-server.yml` (fleet page
  answers), `ansible/verify-pi.yml` (timer active)

**Interfaces:**
- Consumes: the deb from Task 9 (installed by the existing `onpi` role via
  the fpgas apt repo) and the site wheel from Tasks 1–5 (existing pip
  install task).
- Produces: welland Pis get
  `endpoints = ["https://welland.fpgas.online/fleet"]`, ps1 Pis
  `["https://ps1.fpgas.online/fleet"]` (append `https://all.fpgas.online/fleet`
  to both lists when D-1 lands — that is the entire multi-site change).

- [ ] **Step 1: fleet.toml template**

```jinja
# Ansible managed -- fleet self-registration (see fleet-self-registration design)
site = "{{ fleet_site }}"
token = "{{ vault_fleet_token }}"
endpoints = [
{% for url in fleet_endpoints %}
  "{{ url }}",
{% endfor %}
]
```

Baked at `/etc/fpgas-online/fleet.toml` inside the NFS root during the `pi`
play (guard `when: fleet_endpoints is defined` — the multi-host guard rule:
CI VM and hosts without the var must skip cleanly).

- [ ] **Step 2: nginx include** `includes/pib-fleet.conf` (welland vhost
  already globs `includes/pib-*.conf`):

```
# Ansible managed -- fleet registration API + pages
  location /fleet/ {
    include proxy_params;
    proxy_pass http://unix:/run/gunicorn.sock;
  }
```

- [ ] **Step 3: FLEET_TOKENS** — lineinfile into
  `{{ django_dir }}/pib/local_settings.py`:
  `FLEET_TOKENS = ['{{ vault_fleet_token }}']`, tagged `django`, guarded
  `when: vault_fleet_token is defined`. Generate the welland/ps1 tokens
  (`openssl rand -hex 24`) into the vault.
- [ ] **Step 4: verifies** — `verify-server.yml`: `uri` GET
  `https://{{ domain_name }}/fleet/` expect 200; `verify-pi.yml`: assert
  `systemctl is-active fleet-heartbeat.timer` = active (guarded like the
  other fleet-var checks so the CI VM without a fleet config skips or —
  better — the CI inventory gains `fleet_*` vars pointing at the VM's own
  app so the VM test covers the whole loop).
- [ ] **Step 5: Lint** `uv run yamllint ansible/` + syntax-check both
  playbooks; commit `feat(fleet): bake registration config + tokens +
  nginx`, push, PR "Fleet self-registration: deploy", wait for the VM CI.

### Task 11: deploy to welland + prove the loop

- [ ] **Step 1**: merge order — site PR, setup-pi PR (deb reaches the apt
  repo via its publish flow), then infra PR.
- [ ] **Step 2**: `uv run ansible-playbook ansible/web.yml --limit
  fpgas.online --vault-password-file <file>` (site wheel + nginx + tokens),
  then `uv run ansible-playbook ansible/site.yml --limit fpgas.online,pi
  --tags pi,fpgas-apt,onpi --vault-password-file <file>` (bake deb +
  config into the NFS root; recap MUST show a `pi` play line — the known
  `--limit` gotcha).
- [ ] **Step 3**: immediate fleet-wide test without reboots: install into
  the running overlay on two probe Pis
  (`ssh root@10.21.2.47 'apt-get update && apt-get install -y
  fpgas-online-setup-pi && systemctl start fleet-register.service
  fleet-heartbeat.timer'`), then confirm `https://welland.fpgas.online/fleet/`
  lists them live with correct Acorn/Arty classification.
- [ ] **Step 4**: staged PoE-cycle reboots for the rest (≥30 s spacing —
  the thundering-herd rule), then check every expected machine on /fleet/.
- [ ] **Step 5**: prove self-healing: `heartbeat` after deleting one
  Machine row in the admin → row reappears within 60 s. Update the
  tinytapeout/welland status memory notes.

### Task 12: (site, follow-on branch) fleet drives the /fpgas/ board list

**Files:**
- Create: `fleet/src/fleet/sync.py`, `tests/test_fleet_sync.py`
- Modify: `fleet/src/fleet/services.py` (call sync after registration)

**Interfaces:**
- Consumes: `pibfpgas.models.Pi` (Task "part 1" restored it: `switch`,
  `port`, `mac`, `serial_no`, `model`, `fpga_board`).
- Produces: `sync.upsert_pi(machine) -> Pi | None` — parse
  `pi-sw(?P<switch>\d+)-p(?P<port>\d+)` from the registered hostname; if
  the doc's `fpga.boards` is non-empty, upsert the `Pi` row (switch, port,
  mac=eth0, serial_no, model, fpga_board=human name of the first board
  kind); if empty, delete any existing row for that (switch, port). Called
  from `register_document` when the snapshot changed. The fixture then
  remains only as bootstrap; the 2026-08-26 incident class is closed.

- [ ] Test-first as in Tasks 1–5 (register a doc with an Arty → `/fpgas/`
  lists `pi-sw2-p38`; re-register without the board → row gone; legacy
  hostname `pi9` → no crash, no row). Retire the `pistat_info`/
  `pistat_ssh`/`pistat_cam`/`arty_here` one-shot units in a matching
  setup-pi PR (decision D-3).

## Self-review checklist (done at authoring time)

- Spec coverage: goals 1–6 map to Tasks 6–9 (collect+register), 10
  (multi-site config), 1–2 (history/content-addressing), 6–7 (doc
  contents), 8–9 (60 s beats), 2+4+8 (self-healing known:false).
  MQTT/sensors2mqtt question answered in the spec's Transport section
  (decision: HTTPS v1).
- No placeholder steps; each code task carries its test and code.
- Interfaces consistent: `fingerprint`/`register_document`/`beat` names
  match across Tasks 2–4 and 8; document section names match the spec JSON.
