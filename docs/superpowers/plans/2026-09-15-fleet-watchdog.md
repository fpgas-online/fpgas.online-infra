# Fleet Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A service on tweed that sweeps every switch access port every 5 minutes, checks the attached board answers an SSH login, PoE-cycles anything that does not (off, 30 seconds, on), and recycles healthy boards that have been up past 8 hours.

**Architecture:** A new `fleet_watchdog` package in `fpgas.online-poe` holds all logic, with the decision rules isolated in a pure `policy.py` that takes a clock as a parameter and performs no I/O. A new `fleet-watchdog` role in `fpgas.online-infra` generates the service's SSH key, authorises it in the Pi NFS root, renders config, and installs a systemd unit. The role installs no software: `roles/switch-vlans` already pip-installs `fpgas-online-poe[cli]` into `/opt/fpgas-switch/venv` on every converge, so the console script appears there.

**Tech Stack:** Python 3.11+, `python-netgear-switch-library` (`SyncSwitch`, `VirtualSwitch`), PyYAML, pytest, `uv`, Ansible, systemd.

**Spec:** `docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md` (this repo)

## Global Constraints

- **Two repos, two worktrees.** Tasks 1-8 run in
  `/home/tim/github/fpgas-online/fpgas.online-poe/.worktrees/fleet-watchdog`
  (branch `fleet-watchdog`, based on `origin/main` at `b42efd2`). Tasks 9-12 run
  in
  `/home/tim/github/fpgas-online/fpgas.online-infra/.worktrees/fleet-watchdog`
  (branch `fleet-watchdog`, based on `origin/main` at `80e7212`). Never `cd` to
  the parent checkouts.
- **Python via `uv` only.** `uv run pytest`, `uv run ruff`. Never bare `python`,
  `python3` or `pip`.
- **Dates** in ISO 8601 (`YYYY-MM-DD`) or day-first. Never month-first.
- **Commit after every task.** Small, discrete commits.
- **Commit trailers.** Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV
  ```
- **Never force push.** Branch protection is on in both repos.
- **Temporary files** go in a project-local `tmp/`, never `/tmp`. Both repos
  gitignore `tmp/`. Clean up afterwards.
- **Never redirect stderr to `/dev/null`.**
- **The poe tests need net-snmp CLI tools** (`apt install snmp`). `VirtualSwitch`
  is spoken to over real SNMP on localhost.
- **Licence** is Apache 2.0; match each repo's existing file headers (neither
  repo puts per-file licence headers on Python source, so add none).
- **Exact default values**, copied from the spec, used verbatim in Task 1 and
  Task 10:

  | Setting | Default |
  |---|---|
  | `interval` | `300` |
  | `fail_threshold` | `2` |
  | `ssh_timeout` | `20` |
  | `probe_concurrency` | `8` |
  | `poe_off_seconds` | `30` |
  | `boot_grace` | `300` |
  | `max_uptime_hours` | `8` |
  | `uptime_jitter_minutes` | `60` |
  | `hard_cap_hours` | `12` |
  | `max_scheduled_cycles_per_sweep` | `2` |
  | `cycle_concurrency` | `2` |
  | `breaker_fraction` | `0.5` |
  | `breaker_min_failures` | `3` |
  | `ssh_user` | `pi` |

- **Default exclusions**, copied from the spec: switch 1 port 13; switch 2
  ports 11, 12, 27, 30.
- **The virtual S3300 (`gsm7228ps`) PoE seed**, relied on by several tests:
  ports 44 and 48 are DELIVERING, port 46 is FAULT, ports 1-43, 45 and 47 are
  SEARCHING. Writing PoE admin off sets detect to unused immediately; writing it
  on sets detect to delivering immediately.

---

## File Structure

### `fpgas.online-poe`

| File | Responsibility |
|---|---|
| `src/fleet_watchdog/__init__.py` | Empty package marker. |
| `src/fleet_watchdog/config.py` | `WatchdogConfig` and `load_config`. |
| `src/fleet_watchdog/switches.py` | `Board`, opening a `SyncSwitch`, listing occupied boards. |
| `src/fleet_watchdog/probe.py` | `Observation`, `SshProbe`, `probe_all`. |
| `src/fleet_watchdog/policy.py` | `BoardState`, `Reason`, `Decision`, `jitter_seconds`, `decide`. Pure. |
| `src/fleet_watchdog/cycle.py` | `CycleError`, `cycle_port`. |
| `src/fleet_watchdog/service.py` | `Watchdog`: sweep loop and logging. |
| `src/fleet_watchdog/cli.py` | `main`: argument parsing, logging setup. |
| `tests/test_watchdog_config.py` | Task 1. |
| `tests/test_watchdog_switches.py` | Task 2. |
| `tests/test_watchdog_policy_health.py` | Tasks 3 and 4. |
| `tests/test_watchdog_policy_uptime.py` | Task 5. |
| `tests/test_watchdog_probe.py` | Task 6. |
| `tests/test_watchdog_cycle.py` | Task 7. |
| `tests/test_watchdog_service.py` | Task 8. |
| `pyproject.toml` | Add the package and the console script. |
| `README.md` | Document the watchdog. |

### `fpgas.online-infra`

| File | Responsibility |
|---|---|
| `ansible/roles/fleet-watchdog/defaults/main.yml` | Every tunable and the default exclusions. |
| `ansible/roles/fleet-watchdog/tasks/main.yml` | User, key, NFS root authorisation, known_hosts, config, unit. |
| `ansible/roles/fleet-watchdog/tasks/verify/main.yml` | Post-deploy assertions. |
| `ansible/roles/fleet-watchdog/templates/watchdog.yml.j2` | Non-secret config. |
| `ansible/roles/fleet-watchdog/templates/watchdog.env.j2` | SNMP communities (0600). |
| `ansible/roles/fleet-watchdog/templates/known_hosts.j2` | Pinned NFS root host key. |
| `ansible/roles/fleet-watchdog/templates/fleet-watchdog.service.j2` | The unit. |
| `ansible/roles/fleet-watchdog/README.md` | Role documentation. |
| `ansible/site.yml` | Add the role to the `nbp` play after `pxe`. |
| `ansible/verify-server.yml` | Include the role's verify tasks. |
| `ansible/inventory/host_vars/fpgas.online.yml` | Welland's exclusions, enabled flag. |

---

## Task 1: Package skeleton and configuration

**Repo:** `fpgas.online-poe`

**Files:**
- Create: `src/fleet_watchdog/__init__.py`
- Create: `src/fleet_watchdog/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_watchdog_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `WatchdogConfig` (frozen dataclass, fields exactly as listed in
  Step 3) and `load_config(path: str) -> WatchdogConfig`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchdog_config.py`:

```python
"""Loading /etc/fpgas/watchdog.yml into a WatchdogConfig."""

import textwrap

import pytest

from fleet_watchdog.config import WatchdogConfig, load_config


def write(tmp_path, body):
    p = tmp_path / "watchdog.yml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_defaults_apply_when_only_required_keys_are_given(tmp_path):
    cfg = load_config(write(tmp_path, """
        switches_config: /etc/fpgas/switches.yml
        pib_network: "10.21"
        ssh_key: /var/lib/fleet-watchdog/id_ed25519
        known_hosts: /var/lib/fleet-watchdog/known_hosts
    """))
    assert isinstance(cfg, WatchdogConfig)
    assert cfg.interval == 300
    assert cfg.fail_threshold == 2
    assert cfg.ssh_timeout == 20
    assert cfg.probe_concurrency == 8
    assert cfg.poe_off_seconds == 30
    assert cfg.boot_grace == 300
    assert cfg.max_uptime_hours == 8
    assert cfg.uptime_jitter_minutes == 60
    assert cfg.hard_cap_hours == 12
    assert cfg.max_scheduled_cycles_per_sweep == 2
    assert cfg.cycle_concurrency == 2
    assert cfg.breaker_fraction == 0.5
    assert cfg.breaker_min_failures == 3
    assert cfg.ssh_user == "pi"
    assert cfg.exclude == {}


def test_values_override_defaults(tmp_path):
    cfg = load_config(write(tmp_path, """
        switches_config: /etc/fpgas/switches.yml
        pib_network: "10.21"
        ssh_key: /k
        known_hosts: /kh
        interval: 60
        fail_threshold: 5
        ssh_user: debian
    """))
    assert cfg.interval == 60
    assert cfg.fail_threshold == 5
    assert cfg.ssh_user == "debian"


def test_exclusions_are_keyed_by_switch_index_as_int(tmp_path):
    cfg = load_config(write(tmp_path, """
        switches_config: /etc/fpgas/switches.yml
        pib_network: "10.21"
        ssh_key: /k
        known_hosts: /kh
        exclude:
          1: [13]
          2: [11, 12, 27, 30]
    """))
    assert cfg.exclude == {1: frozenset({13}), 2: frozenset({11, 12, 27, 30})}


def test_a_missing_required_key_is_a_clear_error(tmp_path):
    with pytest.raises(KeyError, match="pib_network"):
        load_config(write(tmp_path, """
            switches_config: /etc/fpgas/switches.yml
            ssh_key: /k
            known_hosts: /kh
        """))


def test_the_config_is_immutable(tmp_path):
    cfg = load_config(write(tmp_path, """
        switches_config: /s
        pib_network: "10.21"
        ssh_key: /k
        known_hosts: /kh
    """))
    with pytest.raises(Exception):
        cfg.interval = 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watchdog_config.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'fleet_watchdog'`.

- [ ] **Step 3: Write the implementation**

Create `src/fleet_watchdog/__init__.py` as an empty file.

Create `src/fleet_watchdog/config.py`:

```python
"""The watchdog's on-disk configuration.

Rendered by the infra `fleet-watchdog` role to /etc/fpgas/watchdog.yml. Holds
no secrets: the SNMP write communities arrive in the environment instead (see
switches.community_for), exactly as the gunicorn PoE drop-in does it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import yaml

# Required keys have no default; everything else falls back to the spec's
# table. Keeping the defaults HERE rather than only in the Ansible template
# means --once against a hand-written file behaves the same as the service.
_DEFAULTS: dict[str, object] = {
    "interval": 300.0,
    "fail_threshold": 2,
    "ssh_timeout": 20.0,
    "probe_concurrency": 8,
    "poe_off_seconds": 30.0,
    "boot_grace": 300.0,
    "max_uptime_hours": 8.0,
    "uptime_jitter_minutes": 60.0,
    "hard_cap_hours": 12.0,
    "max_scheduled_cycles_per_sweep": 2,
    "cycle_concurrency": 2,
    "breaker_fraction": 0.5,
    "breaker_min_failures": 3,
    "ssh_user": "pi",
}

_REQUIRED = ("switches_config", "pib_network", "ssh_key", "known_hosts")


@dataclass(frozen=True)
class WatchdogConfig:
    switches_config: str
    pib_network: str
    ssh_key: str
    known_hosts: str
    interval: float = 300.0
    fail_threshold: int = 2
    ssh_timeout: float = 20.0
    probe_concurrency: int = 8
    poe_off_seconds: float = 30.0
    boot_grace: float = 300.0
    max_uptime_hours: float = 8.0
    uptime_jitter_minutes: float = 60.0
    hard_cap_hours: float = 12.0
    max_scheduled_cycles_per_sweep: int = 2
    cycle_concurrency: int = 2
    breaker_fraction: float = 0.5
    breaker_min_failures: int = 3
    ssh_user: str = "pi"
    exclude: Mapping[int, frozenset[int]] = field(default_factory=dict)


def load_config(path: str) -> WatchdogConfig:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    for key in _REQUIRED:
        if key not in data:
            raise KeyError(f"{path}: required key {key!r} is missing")
    kwargs: dict[str, object] = {k: data[k] for k in _REQUIRED}
    for key, default in _DEFAULTS.items():
        value = data.get(key, default)
        # YAML gives ints where the dataclass wants floats; normalise so
        # comparisons against timestamps never mix types surprisingly.
        kwargs[key] = type(default)(value)
    kwargs["exclude"] = {
        int(index): frozenset(int(p) for p in ports)
        for index, ports in (data.get("exclude") or {}).items()
    }
    return WatchdogConfig(**kwargs)  # type: ignore[arg-type]
```

Modify `pyproject.toml`. Change the wheel packages line:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/snmp_switch", "src/switch_setup", "src/fleet_watchdog"]
```

and add the console script alongside the existing one:

```toml
[project.scripts]
fpgas-switch-setup = "switch_setup.cli:main"
fpgas-fleet-watchdog = "fleet_watchdog.cli:main"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_watchdog_config.py -v`

Expected: 5 passed.

Note: the console script points at `fleet_watchdog.cli:main`, which does not
exist until Task 8. That is fine for an editable install and for `pytest`;
nothing imports it until then.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_watchdog/__init__.py src/fleet_watchdog/config.py \
        tests/test_watchdog_config.py pyproject.toml
git commit -m "feat(watchdog): configuration file loading with spec defaults

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 2: Switch access and port occupancy

**Repo:** `fpgas.online-poe`

**Files:**
- Create: `src/fleet_watchdog/switches.py`
- Test: `tests/test_watchdog_switches.py`

**Interfaces:**
- Consumes: `WatchdogConfig` from Task 1; `switch_setup.cli.load_specs` and
  `switch_setup.plan.SwitchSpec` (existing, fields `index`, `model`,
  `mgmt_host`, `access_ports`, `gateway_trunk_port`, `downstream_trunk_ports`,
  `house_uplink_port`).
- Produces:
  - `Board` frozen dataclass with fields `switch: int`, `port: int`,
    `ip: str`, `hostname: str`.
  - `make_board(switch: int, port: int, pib_network: str) -> Board`
  - `community_for(index: int) -> str`
  - `protected_ports(spec) -> frozenset[int]`
  - `open_switch(spec, community: str) -> SyncSwitch`
  - `occupied_boards(sw, spec, excluded: frozenset[int], pib_network: str) -> list[Board]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchdog_switches.py`:

```python
"""Board identity and which ports count as occupied, against the virtual S3300.

The gsm7228ps seed is a transcription of the real sw2 capture: ports 44 and 48
deliver PoE, port 46 is in FAULT, everything else is SEARCHING.
"""

import os
import textwrap

import pytest
from netgear_switch.errors import ProtectedPortError
from netgear_switch.virtual.server import VirtualSwitch
from switch_setup.cli import load_specs

from fleet_watchdog.switches import (
    community_for,
    make_board,
    occupied_boards,
    open_switch,
    protected_ports,
)

DELIVERING = {44, 48}


@pytest.fixture()
def virtual_switch():
    vs = VirtualSwitch("gsm7228ps")
    vs.start()
    yield vs
    vs.stop()


@pytest.fixture()
def spec(virtual_switch, tmp_path):
    cfg = tmp_path / "switches.yml"
    cfg.write_text(textwrap.dedent(f"""
        switches:
          - index: 2
            model: s3300
            mgmt_host: {virtual_switch.host}:{virtual_switch.port}
            access_ports: 48
            gateway_trunk_port: 51
            downstream_trunk_ports: []
            house_uplink_port: 52
    """))
    return load_specs(str(cfg))[0]


def test_board_identity_follows_the_port_vlan_map_formulas():
    b = make_board(2, 42, "10.21")
    assert b.switch == 2
    assert b.port == 42
    assert b.ip == "10.21.2.42"
    assert b.hostname == "pi-sw2-p42"


def test_protected_ports_are_the_trunks_and_the_uplink(spec):
    assert protected_ports(spec) == frozenset({51, 52})


def test_protected_ports_include_every_downstream_trunk():
    class Spec:
        gateway_trunk_port = 47
        downstream_trunk_ports = (50, 51)
        house_uplink_port = 48

    assert protected_ports(Spec()) == frozenset({47, 48, 50, 51})


def test_community_prefers_the_per_switch_variable(monkeypatch):
    monkeypatch.setenv("FPGAS_SWITCH_COMMUNITY", "shared")
    monkeypatch.setenv("FPGAS_SWITCH_COMMUNITY_2", "specific")
    assert community_for(2) == "specific"


def test_community_falls_back_to_the_shared_variable(monkeypatch):
    monkeypatch.delenv("FPGAS_SWITCH_COMMUNITY_2", raising=False)
    monkeypatch.setenv("FPGAS_SWITCH_COMMUNITY", "shared")
    assert community_for(2) == "shared"


def test_a_missing_community_is_a_clear_error(monkeypatch):
    for k in list(os.environ):
        if k.startswith("FPGAS_SWITCH_COMMUNITY"):
            monkeypatch.delenv(k)
    with pytest.raises(KeyError, match="FPGAS_SWITCH_COMMUNITY_2"):
        community_for(2)


def test_only_delivering_ports_are_occupied(spec, virtual_switch):
    sw = open_switch(spec, virtual_switch.community)
    boards = occupied_boards(sw, spec, frozenset(), "10.21")
    assert {b.port for b in boards} == DELIVERING


def test_excluded_ports_are_dropped(spec, virtual_switch):
    sw = open_switch(spec, virtual_switch.community)
    boards = occupied_boards(sw, spec, frozenset({44}), "10.21")
    assert {b.port for b in boards} == {48}


def test_boards_carry_their_ip_and_hostname(spec, virtual_switch):
    sw = open_switch(spec, virtual_switch.community)
    boards = {b.port: b for b in occupied_boards(sw, spec, frozenset(), "10.21")}
    assert boards[48].ip == "10.21.2.48"
    assert boards[48].hostname == "pi-sw2-p48"


def test_the_switch_refuses_to_touch_a_protected_port(spec, virtual_switch):
    sw = open_switch(spec, virtual_switch.community)
    with pytest.raises(ProtectedPortError):
        sw.set_poe(spec.house_uplink_port, False)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watchdog_switches.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'fleet_watchdog.switches'`.

- [ ] **Step 3: Write the implementation**

Create `src/fleet_watchdog/switches.py`:

```python
"""Which boards exist, and how the watchdog talks to their switch.

Board identity reproduces the formulas in the infra repo's
ansible/filter_plugins/port_vlan_map.py, which is the source of truth for the
VLAN-per-port scheme: IPv4 <pib_network>.<switch>.<port>, hostname
pi-sw<switch>-p<port>.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from netgear_switch import PoEDetect, SyncSwitch, get_model

COMMUNITY_ENV = "FPGAS_SWITCH_COMMUNITY"


@dataclass(frozen=True)
class Board:
    switch: int
    port: int
    ip: str
    hostname: str

    def __str__(self) -> str:
        return f"sw{self.switch}/p{self.port} {self.hostname} {self.ip}"


def make_board(switch: int, port: int, pib_network: str) -> Board:
    return Board(
        switch=switch,
        port=port,
        ip=f"{pib_network}.{switch}.{port}",
        hostname=f"pi-sw{switch}-p{port}",
    )


def community_for(index: int) -> str:
    """The SNMP community for one switch, per-switch variable first."""
    community = os.environ.get(f"{COMMUNITY_ENV}_{index}") or os.environ.get(
        COMMUNITY_ENV
    )
    if not community:
        raise KeyError(
            f"no SNMP community for switch {index}: "
            f"set {COMMUNITY_ENV}_{index} or {COMMUNITY_ENV}"
        )
    return community


def protected_ports(spec) -> frozenset[int]:
    """Ports the watchdog must never cut: the trunks and the house uplink.

    Cutting the gateway trunk isolates the switch; cutting a downstream trunk
    takes the next switch (and every board on it) off the network. SyncSwitch
    raises ProtectedPortError on a write to any of these, which is a hard stop
    underneath the soft filter in occupied_boards.
    """
    return frozenset(
        {spec.gateway_trunk_port, spec.house_uplink_port, *spec.downstream_trunk_ports}
    )


def open_switch(spec, community: str) -> SyncSwitch:
    # One community serves both read and write on these switches, but
    # SyncSwitch raises CredentialError on any write unless the write
    # community is set explicitly, so pass it under both names.
    return SyncSwitch(
        get_model(spec.model),
        spec.mgmt_host,
        snmp_community=community,
        snmp_write_community=community,
        protected_ports=protected_ports(spec),
    )


def occupied_boards(
    sw: SyncSwitch, spec, excluded: frozenset[int], pib_network: str
) -> list[Board]:
    """Every access port that is delivering PoE, minus exclusions and trunks.

    Delivering alone means occupied: a board hung hard enough to stop
    transmitting ages out of the MAC table, and that is exactly the board this
    service exists to rescue.
    """
    skip = excluded | protected_ports(spec)
    return [
        make_board(spec.index, status.port, pib_network)
        for status in sw.get_poe()
        if status.detect is PoEDetect.DELIVERING
        and 1 <= status.port <= spec.access_ports
        and status.port not in skip
    ]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_watchdog_switches.py -v`

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_watchdog/switches.py tests/test_watchdog_switches.py
git commit -m "feat(watchdog): board identity and PoE-delivering port occupancy

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 3: Policy — health cycles, boot grace, first sweep

**Repo:** `fpgas.online-poe`

**Files:**
- Create: `src/fleet_watchdog/policy.py`
- Test: `tests/test_watchdog_policy_health.py`

**Interfaces:**
- Consumes: `Board` from Task 2, `WatchdogConfig` from Task 1.
- Produces:
  - `Observation` frozen dataclass: `board: Board`, `ok: bool`,
    `uptime_s: float | None`, `in_use: bool`, `error: str | None`.
  - `BoardState` mutable dataclass: `consecutive_failures: int = 0`,
    `last_cycle: float | None = None`.
  - `Reason` str-enum with members `UNREACHABLE = "unreachable"` and
    `UPTIME = "uptime"`.
  - `Decision` frozen dataclass: `cycles: tuple[tuple[Board, Reason], ...]`,
    `deferred: tuple[tuple[Board, str], ...]`, `breaker_tripped: bool`,
    `occupied: int`, `failed: int`, `in_use: int`.
  - `decide(observations, states, cfg, now, first_sweep) -> Decision`, which
    updates `states[board] .consecutive_failures` in place and never sets
    `last_cycle` (the caller does that after a cycle actually runs).

`Observation` is defined here rather than in `probe.py` so that `policy.py`
imports nothing that touches the network, which is what makes it testable with
no switch and no SSH.

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchdog_policy_health.py`:

```python
"""Health-driven cycles: the failure threshold, the boot grace, the breaker,
and the observe-only first sweep. No switch, no network, no real clock."""

import dataclasses

import pytest

from fleet_watchdog.config import WatchdogConfig
from fleet_watchdog.policy import BoardState, Observation, Reason, decide
from fleet_watchdog.switches import make_board

NOW = 1_000_000.0


def cfg(**overrides):
    base = WatchdogConfig(
        switches_config="/s", pib_network="10.21", ssh_key="/k", known_hosts="/kh"
    )
    return dataclasses.replace(base, **overrides)


def boards(n, switch=2):
    return [make_board(switch, port, "10.21") for port in range(1, n + 1)]


def ok(board, uptime_s=3600.0, in_use=False):
    return Observation(board=board, ok=True, uptime_s=uptime_s, in_use=in_use, error=None)


def dead(board, error="timed out"):
    return Observation(board=board, ok=False, uptime_s=None, in_use=False, error=error)


def states(bs):
    return {b: BoardState() for b in bs}


def test_one_failure_does_not_cycle():
    bs = boards(5)
    st = states(bs)
    d = decide([dead(bs[0])] + [ok(b) for b in bs[1:]], st, cfg(), NOW, first_sweep=False)
    assert d.cycles == ()
    assert st[bs[0]].consecutive_failures == 1


def test_two_consecutive_failures_cycle():
    bs = boards(5)
    st = states(bs)
    obs = [dead(bs[0])] + [ok(b) for b in bs[1:]]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    d = decide(obs, st, cfg(), NOW + 300, first_sweep=False)
    assert d.cycles == ((bs[0], Reason.UNREACHABLE),)
    assert st[bs[0]].consecutive_failures == 2


def test_a_success_resets_the_failure_count():
    bs = boards(5)
    st = states(bs)
    decide([dead(bs[0])] + [ok(b) for b in bs[1:]], st, cfg(), NOW, first_sweep=False)
    decide([ok(b) for b in bs], st, cfg(), NOW + 300, first_sweep=False)
    assert st[bs[0]].consecutive_failures == 0


def test_a_board_inside_its_boot_grace_is_not_cycled_again():
    bs = boards(5)
    st = states(bs)
    st[bs[0]].last_cycle = NOW - 100  # grace is 300 s
    obs = [dead(bs[0])] + [ok(b) for b in bs[1:]]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    d = decide(obs, st, cfg(), NOW, first_sweep=False)
    assert st[bs[0]].consecutive_failures == 2
    assert d.cycles == ()


def test_a_board_past_its_boot_grace_is_cycled_again():
    bs = boards(5)
    st = states(bs)
    st[bs[0]].last_cycle = NOW - 301
    obs = [dead(bs[0])] + [ok(b) for b in bs[1:]]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    d = decide(obs, st, cfg(), NOW, first_sweep=False)
    assert d.cycles == ((bs[0], Reason.UNREACHABLE),)


def test_the_first_sweep_cycles_nothing_but_still_counts():
    bs = boards(5)
    st = states(bs)
    obs = [dead(bs[0]), dead(bs[1])] + [ok(b) for b in bs[2:]]
    decide(obs, st, cfg(), NOW, first_sweep=True)
    d = decide(obs, st, cfg(), NOW + 300, first_sweep=True)
    assert d.cycles == ()
    assert st[bs[0]].consecutive_failures == 2


def test_health_cycles_are_not_capped_per_sweep():
    bs = boards(10)
    st = states(bs)
    obs = [dead(b) for b in bs[:4]] + [ok(b) for b in bs[4:]]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    d = decide(obs, st, cfg(), NOW + 300, first_sweep=False)
    assert len(d.cycles) == 4


def test_the_breaker_trips_when_most_of_the_fleet_fails():
    bs = boards(10)
    st = states(bs)
    obs = [dead(b) for b in bs[:6]] + [ok(b) for b in bs[6:]]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    d = decide(obs, st, cfg(), NOW + 300, first_sweep=False)
    assert d.breaker_tripped is True
    assert d.cycles == ()
    assert d.failed == 6
    assert d.occupied == 10


def test_the_breaker_does_not_trip_at_exactly_half():
    bs = boards(10)
    st = states(bs)
    obs = [dead(b) for b in bs[:5]] + [ok(b) for b in bs[5:]]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    d = decide(obs, st, cfg(), NOW + 300, first_sweep=False)
    assert d.breaker_tripped is False
    assert len(d.cycles) == 5


def test_the_count_floor_lets_a_small_site_recover_itself():
    """Two boards, both dead: the fraction test would trip, the floor of 3 saves it."""
    bs = boards(2)
    st = states(bs)
    obs = [dead(b) for b in bs]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    d = decide(obs, st, cfg(), NOW + 300, first_sweep=False)
    assert d.breaker_tripped is False
    assert len(d.cycles) == 2


def test_the_breaker_still_advances_failure_counts():
    bs = boards(10)
    st = states(bs)
    obs = [dead(b) for b in bs[:6]] + [ok(b) for b in bs[6:]]
    decide(obs, st, cfg(), NOW, first_sweep=False)
    assert st[bs[0]].consecutive_failures == 1


def test_a_board_with_no_prior_state_is_tracked():
    bs = boards(3)
    st = {}
    decide([dead(b) for b in bs], st, cfg(), NOW, first_sweep=False)
    assert set(st) == set(bs)


def test_the_decision_counts_reachable_and_in_use_boards():
    bs = boards(4)
    st = states(bs)
    obs = [ok(bs[0], in_use=True), ok(bs[1]), dead(bs[2]), dead(bs[3])]
    d = decide(obs, st, cfg(), NOW, first_sweep=False)
    assert (d.occupied, d.failed, d.in_use) == (4, 2, 1)


@pytest.mark.parametrize("threshold,expected", [(1, 1), (3, 0)])
def test_the_failure_threshold_is_configurable(threshold, expected):
    bs = boards(5)
    st = states(bs)
    obs = [dead(bs[0])] + [ok(b) for b in bs[1:]]
    d = decide(obs, st, cfg(fail_threshold=threshold), NOW, first_sweep=False)
    assert len(d.cycles) == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watchdog_policy_health.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'fleet_watchdog.policy'`.

- [ ] **Step 3: Write the implementation**

Create `src/fleet_watchdog/policy.py`:

```python
"""What to cycle, and why. Pure: no I/O, no clock, no sleeping.

Every rule here is a decision the service then carries out. Keeping the rules
free of I/O is what lets them be tested exhaustively with a fake clock, which
matters because the failure mode of getting them wrong is rebooting a fleet.
"""

from __future__ import annotations

import enum
import zlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping, MutableMapping

from .config import WatchdogConfig
from .switches import Board


@dataclass(frozen=True)
class Observation:
    board: Board
    ok: bool
    uptime_s: float | None
    in_use: bool
    error: str | None


@dataclass
class BoardState:
    consecutive_failures: int = 0
    last_cycle: float | None = None


class Reason(str, enum.Enum):
    UNREACHABLE = "unreachable"
    UPTIME = "uptime"


@dataclass(frozen=True)
class Decision:
    cycles: tuple[tuple[Board, Reason], ...] = ()
    deferred: tuple[tuple[Board, str], ...] = ()
    breaker_tripped: bool = False
    occupied: int = 0
    failed: int = 0
    in_use: int = 0


def jitter_seconds(switch: int, port: int, jitter_minutes: float) -> float:
    """A stable per-board offset added to the uptime threshold.

    crc32, not hash(): Python salts string hashes per process, so hash() would
    move every board's slot on each restart and defeat the staggering.
    """
    if jitter_minutes <= 0:
        return 0.0
    span = int(jitter_minutes * 60)
    return float(zlib.crc32(f"{switch}:{port}".encode()) % span)


def _in_boot_grace(state: BoardState, cfg: WatchdogConfig, now: float) -> bool:
    return state.last_cycle is not None and (now - state.last_cycle) < cfg.boot_grace


def decide(
    observations: Iterable[Observation],
    states: MutableMapping[Board, BoardState],
    cfg: WatchdogConfig,
    now: float,
    first_sweep: bool,
) -> Decision:
    """Fold one sweep's observations into per-board state and an action list.

    Updates each board's consecutive_failures in place. Never touches
    last_cycle: only a cycle that actually ran may start a boot grace, and this
    function does not run cycles.
    """
    observations = list(observations)
    for obs in observations:
        state = states.setdefault(obs.board, BoardState())
        state.consecutive_failures = 0 if obs.ok else state.consecutive_failures + 1

    failed = [o for o in observations if not o.ok]
    counts = {
        "occupied": len(observations),
        "failed": len(failed),
        "in_use": sum(1 for o in observations if o.ok and o.in_use),
    }

    # The watchdog is far likelier to be broken than most of the fleet is. A
    # wrong key, a wrong user, a routing fault or a changed NFS root host key
    # all look exactly like a dead fleet from here.
    if len(failed) >= cfg.breaker_min_failures and len(failed) > cfg.breaker_fraction * len(
        observations
    ):
        return Decision(breaker_tripped=True, **counts)

    if first_sweep:
        return Decision(**counts)

    cycles: list[tuple[Board, Reason]] = []
    deferred: list[tuple[Board, str]] = []

    for obs in failed:
        state = states[obs.board]
        if state.consecutive_failures < cfg.fail_threshold:
            continue
        # Without this, a board taking three minutes to boot would be cut again
        # mid-boot every sweep and never come up.
        if _in_boot_grace(state, cfg, now):
            continue
        cycles.append((obs.board, Reason.UNREACHABLE))

    cycles.extend(_scheduled(observations, states, cfg, now, deferred))
    return Decision(
        cycles=tuple(cycles), deferred=tuple(deferred), **counts
    )


def _scheduled(
    observations: list[Observation],
    states: MutableMapping[Board, BoardState],
    cfg: WatchdogConfig,
    now: float,
    deferred: list[tuple[Board, str]],
) -> list[tuple[Board, Reason]]:
    """Healthy boards that have been up too long, oldest first, capped."""
    hard_cap = cfg.hard_cap_hours * 3600
    candidates: list[tuple[float, Board]] = []
    for obs in observations:
        if not obs.ok or obs.uptime_s is None:
            continue
        if _in_boot_grace(states[obs.board], cfg, now):
            continue
        threshold = cfg.max_uptime_hours * 3600 + jitter_seconds(
            obs.board.switch, obs.board.port, cfg.uptime_jitter_minutes
        )
        if obs.uptime_s < threshold:
            continue
        if obs.in_use and obs.uptime_s < hard_cap:
            deferred.append(
                (obs.board, f"in use, up {obs.uptime_s / 3600:.1f} h, cap {cfg.hard_cap_hours} h")
            )
            continue
        candidates.append((obs.uptime_s, obs.board))

    candidates.sort(key=lambda c: c[0], reverse=True)
    chosen = candidates[: cfg.max_scheduled_cycles_per_sweep]
    return [(board, Reason.UPTIME) for _, board in chosen]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_watchdog_policy_health.py -v`

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_watchdog/policy.py tests/test_watchdog_policy_health.py
git commit -m "feat(watchdog): health cycle policy with boot grace and circuit breaker

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 4: Policy — scheduled uptime cycles

**Repo:** `fpgas.online-poe`

The implementation of `_scheduled` landed in Task 3 because `decide` calls it.
This task proves it behaves, and is a separate reviewer gate because the rules
are subtle and the failure mode is interrupting a visitor.

**Files:**
- Test: `tests/test_watchdog_policy_uptime.py`
- Modify (only if a test fails): `src/fleet_watchdog/policy.py`

**Interfaces:**
- Consumes: `decide`, `jitter_seconds`, `Reason` from Task 3.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchdog_policy_uptime.py`:

```python
"""The 8-hour rule: jitter, the in-use deferral, the 12-hour hard cap, and
the per-sweep stagger."""

import dataclasses

from fleet_watchdog.config import WatchdogConfig
from fleet_watchdog.policy import BoardState, Observation, Reason, decide, jitter_seconds
from fleet_watchdog.switches import make_board

NOW = 1_000_000.0
H = 3600.0


def cfg(**overrides):
    base = WatchdogConfig(
        switches_config="/s", pib_network="10.21", ssh_key="/k", known_hosts="/kh"
    )
    return dataclasses.replace(base, **overrides)


def obs(board, uptime_h, in_use=False):
    return Observation(
        board=board, ok=True, uptime_s=uptime_h * H, in_use=in_use, error=None
    )


def test_jitter_is_stable_for_a_board():
    assert jitter_seconds(2, 42, 60) == jitter_seconds(2, 42, 60)


def test_jitter_differs_between_boards():
    values = {jitter_seconds(2, p, 60) for p in range(1, 49)}
    assert len(values) > 40  # a handful of collisions is fine, a constant is not


def test_jitter_stays_inside_the_configured_window():
    for port in range(1, 49):
        assert 0 <= jitter_seconds(2, port, 60) < 3600


def test_jitter_of_zero_minutes_is_zero():
    assert jitter_seconds(2, 42, 0) == 0.0


def test_a_board_under_the_threshold_is_left_alone():
    b = make_board(2, 42, "10.21")
    d = decide([obs(b, 7.0)], {b: BoardState()}, cfg(uptime_jitter_minutes=0), NOW, False)
    assert d.cycles == ()


def test_a_board_past_the_threshold_is_cycled():
    b = make_board(2, 42, "10.21")
    d = decide([obs(b, 8.5)], {b: BoardState()}, cfg(uptime_jitter_minutes=0), NOW, False)
    assert d.cycles == ((b, Reason.UPTIME),)


def test_jitter_delays_a_board_past_the_bare_threshold():
    """With an hour of jitter, at least one board just past 8 h is still waiting."""
    bs = [make_board(2, p, "10.21") for p in range(1, 49)]
    st = {b: BoardState() for b in bs}
    d = decide([obs(b, 8.01) for b in bs], st, cfg(max_scheduled_cycles_per_sweep=99), NOW, False)
    assert len(d.cycles) < len(bs)


def test_an_in_use_board_is_deferred_not_cycled():
    b = make_board(2, 42, "10.21")
    d = decide(
        [obs(b, 9.0, in_use=True)], {b: BoardState()}, cfg(uptime_jitter_minutes=0), NOW, False
    )
    assert d.cycles == ()
    assert len(d.deferred) == 1
    assert d.deferred[0][0] == b
    assert "in use" in d.deferred[0][1]


def test_an_in_use_board_past_the_hard_cap_is_cycled_anyway():
    b = make_board(2, 42, "10.21")
    d = decide(
        [obs(b, 12.5, in_use=True)], {b: BoardState()}, cfg(uptime_jitter_minutes=0), NOW, False
    )
    assert d.cycles == ((b, Reason.UPTIME),)
    assert d.deferred == ()


def test_scheduled_cycles_are_capped_per_sweep():
    bs = [make_board(2, p, "10.21") for p in range(1, 11)]
    st = {b: BoardState() for b in bs}
    d = decide([obs(b, 20.0) for b in bs], st, cfg(uptime_jitter_minutes=0), NOW, False)
    assert len(d.cycles) == 2


def test_the_longest_running_boards_go_first():
    bs = [make_board(2, p, "10.21") for p in range(1, 6)]
    st = {b: BoardState() for b in bs}
    obs_list = [obs(b, 9.0 + i) for i, b in enumerate(bs)]
    d = decide(obs_list, st, cfg(uptime_jitter_minutes=0), NOW, False)
    assert [b for b, _ in d.cycles] == [bs[4], bs[3]]


def test_a_board_inside_its_boot_grace_is_not_scheduled():
    b = make_board(2, 42, "10.21")
    st = {b: BoardState(last_cycle=NOW - 10)}
    d = decide([obs(b, 20.0)], st, cfg(uptime_jitter_minutes=0), NOW, False)
    assert d.cycles == ()


def test_the_first_sweep_schedules_nothing():
    b = make_board(2, 42, "10.21")
    d = decide([obs(b, 20.0)], {b: BoardState()}, cfg(uptime_jitter_minutes=0), NOW, True)
    assert d.cycles == ()


def test_the_breaker_suppresses_scheduled_cycles_too():
    bs = [make_board(2, p, "10.21") for p in range(1, 11)]
    st = {b: BoardState() for b in bs}
    observations = [
        Observation(board=b, ok=False, uptime_s=None, in_use=False, error="x")
        for b in bs[:6]
    ] + [obs(b, 20.0) for b in bs[6:]]
    d = decide(observations, st, cfg(uptime_jitter_minutes=0), NOW, False)
    assert d.breaker_tripped is True
    assert d.cycles == ()


def test_thresholds_are_configurable():
    b = make_board(2, 42, "10.21")
    d = decide(
        [obs(b, 2.5)],
        {b: BoardState()},
        cfg(uptime_jitter_minutes=0, max_uptime_hours=2),
        NOW,
        False,
    )
    assert d.cycles == ((b, Reason.UPTIME),)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watchdog_policy_uptime.py -v`

Expected: PASS if Task 3's `_scheduled` is correct. If any test fails, fix
`src/fleet_watchdog/policy.py` until all pass. Do not weaken a test to make it
pass.

- [ ] **Step 3: Run the whole watchdog suite**

Run: `uv run pytest tests/ -v -k watchdog`

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_watchdog_policy_uptime.py src/fleet_watchdog/policy.py
git commit -m "test(watchdog): the 8-hour rule, jitter, deferral and the 12-hour cap

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 5: The SSH probe

**Repo:** `fpgas.online-poe`

**Files:**
- Create: `src/fleet_watchdog/probe.py`
- Test: `tests/test_watchdog_probe.py`

**Interfaces:**
- Consumes: `Board` (Task 2), `WatchdogConfig` (Task 1), `Observation` (Task 3).
- Produces:
  - `SshProbe(cfg: WatchdogConfig)` callable as `probe(board) -> Observation`.
  - `SshProbe.command(board) -> list[str]` so the argv is testable directly.
  - `probe_all(probe, boards, concurrency) -> list[Observation]`.
  - `REMOTE_COMMAND: str` = `"cat /proc/uptime; who"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchdog_probe.py`:

```python
"""The SSH health check, driven by a fake `ssh` on PATH."""

import dataclasses
import os
import stat
import textwrap

from fleet_watchdog.config import WatchdogConfig
from fleet_watchdog.probe import REMOTE_COMMAND, SshProbe, probe_all
from fleet_watchdog.switches import make_board

BOARD = make_board(2, 42, "10.21")


def cfg(**overrides):
    base = WatchdogConfig(
        switches_config="/s",
        pib_network="10.21",
        ssh_key="/var/lib/fleet-watchdog/id_ed25519",
        known_hosts="/var/lib/fleet-watchdog/known_hosts",
        ssh_timeout=2,
    )
    return dataclasses.replace(base, **overrides)


def fake_ssh(tmp_path, monkeypatch, body):
    """Put an `ssh` on PATH that behaves as `body` says."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    script = d / "ssh"
    script.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")


def test_the_command_pins_the_key_the_known_hosts_and_batch_mode():
    argv = SshProbe(cfg()).command(BOARD)
    assert argv[0] == "ssh"
    assert "-o" in argv and "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "UserKnownHostsFile=/var/lib/fleet-watchdog/known_hosts" in argv
    assert "ConnectTimeout=2" in argv
    assert "-i" in argv
    assert "/var/lib/fleet-watchdog/id_ed25519" in argv
    assert argv[-2] == "pi@10.21.2.42"
    assert argv[-1] == REMOTE_COMMAND


def test_the_command_never_allocates_a_pty():
    """A pty would write utmp and make the watchdog look like a logged-in user."""
    argv = SshProbe(cfg()).command(BOARD)
    assert "-n" in argv
    assert "-t" not in argv


def test_a_healthy_idle_board_reports_its_uptime(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, """
        print("12345.67 98765.43")
    """)
    o = SshProbe(cfg())(BOARD)
    assert o.ok is True
    assert o.uptime_s == 12345.67
    assert o.in_use is False
    assert o.error is None


def test_a_board_with_a_login_is_in_use(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, """
        print("500.0 900.0")
        print("pi       pts/0        2026-09-15 10:04 (10.21.0.1)")
    """)
    o = SshProbe(cfg())(BOARD)
    assert o.ok is True
    assert o.in_use is True


def test_a_nonzero_exit_is_a_failure(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, """
        import sys
        print("Permission denied (publickey).", file=sys.stderr)
        sys.exit(255)
    """)
    o = SshProbe(cfg())(BOARD)
    assert o.ok is False
    assert o.uptime_s is None
    assert "publickey" in o.error


def test_unparseable_output_is_a_failure(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, """
        print("cat: /proc/uptime: Input/output error")
    """)
    o = SshProbe(cfg())(BOARD)
    assert o.ok is False
    assert "uptime" in o.error


def test_empty_output_is_a_failure(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, """
        pass
    """)
    o = SshProbe(cfg())(BOARD)
    assert o.ok is False


def test_a_hanging_ssh_is_killed_and_reported(tmp_path, monkeypatch):
    """A board on stale NFS handles accepts the TCP connection and then never
    finishes the banner, so ConnectTimeout never fires. The hard timeout must."""
    fake_ssh(tmp_path, monkeypatch, """
        import time
        time.sleep(30)
    """)
    o = SshProbe(cfg(ssh_timeout=1))(BOARD)
    assert o.ok is False
    assert "timed out" in o.error


def test_a_host_key_mismatch_is_a_failure_not_a_crash(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, """
        import sys
        print("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", file=sys.stderr)
        sys.exit(255)
    """)
    o = SshProbe(cfg())(BOARD)
    assert o.ok is False
    assert "IDENTIFICATION HAS CHANGED" in o.error


def test_probe_all_returns_one_observation_per_board(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, """
        print("100.0 200.0")
    """)
    boards = [make_board(2, p, "10.21") for p in range(1, 6)]
    results = probe_all(SshProbe(cfg()), boards, concurrency=4)
    assert len(results) == 5
    assert {o.board for o in results} == set(boards)
    assert all(o.ok for o in results)


def test_probe_all_survives_a_probe_that_raises():
    def exploding(board):
        raise RuntimeError("boom")

    boards = [make_board(2, p, "10.21") for p in range(1, 4)]
    results = probe_all(exploding, boards, concurrency=2)
    assert len(results) == 3
    assert all(not o.ok for o in results)
    assert all("boom" in o.error for o in results)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watchdog_probe.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'fleet_watchdog.probe'`.

- [ ] **Step 3: Write the implementation**

Create `src/fleet_watchdog/probe.py`:

```python
"""Ask one board whether it is alive, and whether anyone is using it.

/proc/uptime rather than `uptime -s`: netbooted Pis have no RTC, so a boot
timestamp is only as trustworthy as NTP, while seconds-since-boot needs no
clock at all. Neither command needs sudo.

`who` covers both ways in: the web terminal is webssh sshing into the board as
pi, and direct ssh arrives through the gateway DNAT. Both land as sshd sessions
with a pty, so both appear in utmp. The Django site knows about neither, so the
board itself is the only place this information exists.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from .config import WatchdogConfig
from .policy import Observation
from .switches import Board

REMOTE_COMMAND = "cat /proc/uptime; who"


class SshProbe:
    def __init__(self, cfg: WatchdogConfig) -> None:
        self.cfg = cfg

    def command(self, board: Board) -> list[str]:
        cfg = self.cfg
        return [
            "ssh",
            # -n redirects stdin from /dev/null and, with a remote command,
            # allocates no pty. A pty would write a utmp record and the
            # watchdog would see itself as a logged-in user on every board.
            "-n",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={int(cfg.ssh_timeout)}",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={cfg.known_hosts}",
            "-o", "IdentitiesOnly=yes",
            "-i", cfg.ssh_key,
            f"{cfg.ssh_user}@{board.ip}",
            REMOTE_COMMAND,
        ]

    def __call__(self, board: Board) -> Observation:
        # The hard timeout is the one that matters: a board on stale NFS
        # handles completes the TCP handshake and then never finishes the SSH
        # banner, so ConnectTimeout alone never fires.
        try:
            proc = subprocess.run(
                self.command(board),
                capture_output=True,
                text=True,
                timeout=self.cfg.ssh_timeout + 10,
            )
        except subprocess.TimeoutExpired:
            return _failed(board, f"timed out after {self.cfg.ssh_timeout + 10:.0f}s")
        except OSError as exc:
            return _failed(board, f"could not run ssh: {exc}")

        if proc.returncode != 0:
            return _failed(board, _tidy(proc.stderr or proc.stdout) or f"exit {proc.returncode}")
        return parse_output(board, proc.stdout)


def parse_output(board: Board, stdout: str) -> Observation:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return _failed(board, "no output")
    try:
        uptime_s = float(lines[0].split()[0])
    except (ValueError, IndexError):
        return _failed(board, f"could not read uptime from {lines[0]!r}")
    return Observation(
        board=board,
        ok=True,
        uptime_s=uptime_s,
        # Anything `who` printed is a login session.
        in_use=len(lines) > 1,
        error=None,
    )


def _failed(board: Board, error: str) -> Observation:
    return Observation(board=board, ok=False, uptime_s=None, in_use=False, error=error)


def _tidy(text: str) -> str:
    return " ".join(text.split())[:200]


def probe_all(
    probe: Callable[[Board], Observation], boards: Iterable[Board], concurrency: int
) -> list[Observation]:
    """Probe every board in parallel. One board's explosion is that board's
    failure, never the sweep's."""
    boards = list(boards)
    if not boards:
        return []

    def guarded(board: Board) -> Observation:
        try:
            return probe(board)
        except Exception as exc:  # noqa: BLE001 - a probe must never kill the sweep
            return _failed(board, f"probe raised: {exc}")

    with ThreadPoolExecutor(max(1, min(concurrency, len(boards)))) as pool:
        return list(pool.map(guarded, boards))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_watchdog_probe.py -v`

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_watchdog/probe.py tests/test_watchdog_probe.py
git commit -m "feat(watchdog): SSH health probe reading /proc/uptime and who

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 6: The PoE cycle with a 30 second dwell

**Repo:** `fpgas.online-poe`

**Files:**
- Create: `src/fleet_watchdog/cycle.py`
- Test: `tests/test_watchdog_cycle.py`

**Interfaces:**
- Consumes: `SyncSwitch` from `open_switch` (Task 2).
- Produces:
  - `CycleError(Exception)`
  - `cycle_port(sw, port, off_seconds, *, sleep=time.sleep, clock=time.monotonic,
    off_timeout=30.0, on_timeout=60.0, poll=2.0) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchdog_cycle.py`:

```python
"""Turning a port off, leaving it off, and bringing it back.

SyncSwitch.cycle_poe cannot be used: its off_timeout is a deadline for
CONFIRMING the port went off, not a dwell, so it turns the port straight back
on. The board needs 30 seconds without power.
"""

import pytest
from netgear_switch.errors import ProtectedPortError
from netgear_switch.virtual.server import VirtualSwitch

from fleet_watchdog.cycle import CycleError, cycle_port
from fleet_watchdog.switches import open_switch

DELIVERING_PORT = 44


class Spec:
    index = 2
    model = "s3300"
    access_ports = 48
    gateway_trunk_port = 51
    downstream_trunk_ports = ()
    house_uplink_port = 52

    def __init__(self, mgmt_host):
        self.mgmt_host = mgmt_host


@pytest.fixture()
def sw():
    vs = VirtualSwitch("gsm7228ps")
    vs.start()
    yield open_switch(Spec(f"{vs.host}:{vs.port}"), vs.community)
    vs.stop()


def poe_detect(sw, port):
    return next(p for p in sw.get_poe() if p.port == port).detect.value


def test_the_port_ends_up_delivering_again(sw):
    slept = []
    cycle_port(sw, DELIVERING_PORT, off_seconds=30, sleep=slept.append)
    assert poe_detect(sw, DELIVERING_PORT) == "delivering"


def test_the_dwell_is_the_configured_length(sw):
    slept = []
    cycle_port(sw, DELIVERING_PORT, off_seconds=30, sleep=slept.append)
    assert 30 in slept


def test_the_dwell_happens_while_the_port_is_off(sw):
    seen = []

    def sleep(seconds):
        if seconds == 30:
            seen.append(poe_detect(sw, DELIVERING_PORT))

    cycle_port(sw, DELIVERING_PORT, off_seconds=30, sleep=sleep)
    # The virtual switch mirrors real coherence: admin off sets detect to 1,
    # which parse.DETECT_MAP reads as "disabled".
    assert seen == ["disabled"]


def test_a_protected_port_is_refused(sw):
    with pytest.raises(ProtectedPortError):
        cycle_port(sw, 52, off_seconds=30, sleep=lambda s: None)


def test_a_port_that_never_goes_off_raises_before_the_dwell(sw, monkeypatch):
    monkeypatch.setattr(sw, "set_poe", lambda *a, **k: None)  # writes do nothing
    slept = []
    # clock() is called once for the deadline (0, so deadline is 30), then once
    # per loop check: 1 is inside, 31 is past it.
    with pytest.raises(CycleError, match="did not turn off"):
        cycle_port(
            sw,
            DELIVERING_PORT,
            off_seconds=30,
            sleep=slept.append,
            clock=iter([0, 1, 31]).__next__,
        )
    assert 30 not in slept  # never dwelled, never turned anything back on


def test_a_port_that_never_comes_back_raises(sw, monkeypatch):
    real_set = sw.set_poe

    def set_poe(port, on, **kwargs):
        if on:
            return None  # the turn-on silently does nothing
        return real_set(port, on, **kwargs)

    monkeypatch.setattr(sw, "set_poe", set_poe)
    # Phase 1 succeeds immediately, so it consumes one clock() for its
    # deadline and never loops. Phase 2 then gets 1 (deadline 61), 2 (inside)
    # and 100 (past it).
    with pytest.raises(CycleError, match="did not come back"):
        cycle_port(
            sw,
            DELIVERING_PORT,
            off_seconds=30,
            sleep=lambda s: None,
            clock=iter([0, 1, 2, 100]).__next__,
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watchdog_cycle.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'fleet_watchdog.cycle'`.

- [ ] **Step 3: Write the implementation**

Create `src/fleet_watchdog/cycle.py`:

```python
"""One PoE cycle: off, confirmed, thirty seconds of nothing, on, confirmed.

SyncSwitch.cycle_poe is deliberately not used. Its PoeCycleTimeouts.off_timeout
is a deadline for confirming the port went off, not a dwell: _poe_rearm sets
the port off, polls until detect leaves DELIVERING and link drops, then sets it
straight back on. The board would lose power for a second or two. A board that
has hung needs longer than that to actually reset.
"""

from __future__ import annotations

import time
from typing import Callable

from netgear_switch import PoEDetect, SyncSwitch


class CycleError(Exception):
    """A cycle did not reach the state it was supposed to."""


def _detect(sw: SyncSwitch, port: int) -> PoEDetect | None:
    status = next((p for p in sw.get_poe() if p.port == port), None)
    return status.detect if status else None


def _wait_for(
    sw: SyncSwitch,
    port: int,
    predicate: Callable[[PoEDetect | None], bool],
    timeout: float,
    poll: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    message: str,
) -> None:
    deadline = clock() + timeout
    while not predicate(_detect(sw, port)):
        if clock() >= deadline:
            raise CycleError(f"{message} (detect={_detect(sw, port)})")
        sleep(poll)


def cycle_port(
    sw: SyncSwitch,
    port: int,
    off_seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    off_timeout: float = 30.0,
    on_timeout: float = 60.0,
    poll: float = 2.0,
) -> None:
    """Power-cycle one port. Raises CycleError if either transition fails.

    A failure to go off raises BEFORE the dwell, so the function never leaves a
    port off believing it turned it back on.
    """
    sw.set_poe(port, False)
    _wait_for(
        sw, port, lambda d: d is not PoEDetect.DELIVERING, off_timeout, poll,
        sleep, clock, f"PoE port {port} did not turn off within {off_timeout:.0f}s",
    )
    sleep(off_seconds)
    sw.set_poe(port, True)
    _wait_for(
        sw, port, lambda d: d is PoEDetect.DELIVERING, on_timeout, poll,
        sleep, clock, f"PoE port {port} did not come back within {on_timeout:.0f}s",
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_watchdog_cycle.py -v`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fleet_watchdog/cycle.py tests/test_watchdog_cycle.py
git commit -m "feat(watchdog): PoE cycle with a verified 30 second power-off dwell

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 7: The sweep loop, logging and CLI

**Repo:** `fpgas.online-poe`

**Files:**
- Create: `src/fleet_watchdog/service.py`
- Create: `src/fleet_watchdog/cli.py`
- Test: `tests/test_watchdog_service.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces:
  - `Watchdog(cfg, probe=None, sleep=time.sleep, clock=time.monotonic)`
  - `Watchdog.sweep(dry_run: bool = False) -> Decision`
  - `Watchdog.run() -> None`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_watchdog_service.py`:

```python
"""One whole sweep: enumerate, probe, decide, cycle, log."""

import logging
import textwrap

import pytest
from netgear_switch.virtual.server import VirtualSwitch

from fleet_watchdog.cli import main
from fleet_watchdog.config import load_config
from fleet_watchdog.policy import Observation
from fleet_watchdog.service import Watchdog

DELIVERING = {44, 48}


@pytest.fixture()
def virtual_switch():
    vs = VirtualSwitch("gsm7228ps")
    vs.start()
    yield vs
    vs.stop()


@pytest.fixture()
def config_path(virtual_switch, tmp_path, monkeypatch):
    switches = tmp_path / "switches.yml"
    switches.write_text(textwrap.dedent(f"""
        switches:
          - index: 2
            model: s3300
            mgmt_host: {virtual_switch.host}:{virtual_switch.port}
            access_ports: 48
            gateway_trunk_port: 51
            downstream_trunk_ports: []
            house_uplink_port: 52
    """))
    watchdog = tmp_path / "watchdog.yml"
    watchdog.write_text(textwrap.dedent(f"""
        switches_config: {switches}
        pib_network: "10.21"
        ssh_key: /k
        known_hosts: /kh
        uptime_jitter_minutes: 0
        poe_off_seconds: 30
    """))
    monkeypatch.setenv("FPGAS_SWITCH_COMMUNITY_2", virtual_switch.community)
    return str(watchdog)


def all_ok(uptime_s=3600.0, in_use=False):
    def probe(board):
        return Observation(board=board, ok=True, uptime_s=uptime_s, in_use=in_use, error=None)
    return probe


def all_dead(board_ports=None):
    def probe(board):
        if board_ports is None or board.port in board_ports:
            return Observation(board=board, ok=False, uptime_s=None, in_use=False, error="timed out")
        return Observation(board=board, ok=True, uptime_s=60.0, in_use=False, error=None)
    return probe


def watchdog(config_path, probe):
    return Watchdog(load_config(config_path), probe=probe, sleep=lambda s: None)


def poe_detect(wd, port):
    sw = wd.switch(2)
    return next(p for p in sw.get_poe() if p.port == port).detect.value


def test_a_sweep_finds_the_delivering_ports(config_path):
    wd = watchdog(config_path, all_ok())
    d = wd.sweep()
    assert d.occupied == len(DELIVERING)
    assert d.failed == 0


def test_the_first_sweep_cycles_nothing(config_path):
    wd = watchdog(config_path, all_dead())
    wd.sweep()
    assert poe_detect(wd, 44) == "delivering"


def test_a_board_failing_twice_is_cycled(config_path):
    """Two boards occupied, one dead: one failure is under the breaker's count
    floor of 3, so the breaker stays out of the way and the board is cycled.

    Sweep 1 is the observe-only first sweep and counts failure 1. Sweep 2
    counts failure 2, reaches the threshold and cycles.
    """
    wd = watchdog(config_path, all_dead({44}))
    wd.sweep()
    d = wd.sweep()
    assert 44 in [b.port for b, _ in d.cycles]
    assert poe_detect(wd, 44) == "delivering"  # off, dwell, back on


def test_a_dry_run_decides_but_changes_nothing(config_path, monkeypatch):
    wd = watchdog(config_path, all_dead({44}))
    wd.sweep()
    calls = []
    monkeypatch.setattr(wd, "cycle", lambda board, reason: calls.append(board))
    d = wd.sweep(dry_run=True)
    assert 44 in [b.port for b, _ in d.cycles]
    assert calls == []


def test_a_cycled_board_starts_its_boot_grace(config_path):
    wd = watchdog(config_path, all_dead({44}))
    wd.sweep()
    wd.sweep()
    board = next(b for b in wd.states if b.port == 44)
    assert wd.states[board].last_cycle is not None


def test_an_uptime_cycle_happens_for_a_long_running_board(config_path):
    wd = watchdog(config_path, all_ok(uptime_s=20 * 3600))
    wd.sweep()
    d = wd.sweep()
    assert all(r.value == "uptime" for _, r in d.cycles)
    assert len(d.cycles) == 2  # both occupied boards, cap is 2


def test_a_sweep_logs_a_summary(config_path, caplog):
    wd = watchdog(config_path, all_ok())
    with caplog.at_level(logging.INFO):
        wd.sweep()
    assert any("occupied=2" in r.message for r in caplog.records)


def test_a_cycle_is_logged_with_the_board_and_the_reason(config_path, caplog):
    wd = watchdog(config_path, all_ok(uptime_s=20 * 3600))
    wd.sweep()
    with caplog.at_level(logging.WARNING):
        wd.sweep()
    assert any("pi-sw2-p44" in r.message and "uptime" in r.message for r in caplog.records)


def test_a_switch_that_cannot_be_reached_does_not_kill_the_sweep(tmp_path, monkeypatch, caplog):
    switches = tmp_path / "switches.yml"
    switches.write_text(textwrap.dedent("""
        switches:
          - index: 9
            model: s3300
            mgmt_host: 127.0.0.1:1
            access_ports: 48
            gateway_trunk_port: 51
            downstream_trunk_ports: []
            house_uplink_port: 52
    """))
    watchdog_yml = tmp_path / "watchdog.yml"
    watchdog_yml.write_text(textwrap.dedent(f"""
        switches_config: {switches}
        pib_network: "10.21"
        ssh_key: /k
        known_hosts: /kh
    """))
    monkeypatch.setenv("FPGAS_SWITCH_COMMUNITY_9", "public")
    wd = Watchdog(load_config(str(watchdog_yml)), probe=all_ok(), sleep=lambda s: None)
    with caplog.at_level(logging.ERROR):
        d = wd.sweep()
    assert d.occupied == 0
    assert any("switch 9" in r.message for r in caplog.records)


def test_the_cli_runs_one_sweep_and_exits_zero(config_path, capsys):
    rc = main([
        "--config", config_path, "--once", "--dry-run",
        "--probe-command", "true",
    ])
    assert rc == 0


def test_the_cli_rejects_a_missing_config(tmp_path):
    with pytest.raises(SystemExit):
        main(["--config", str(tmp_path / "nope.yml"), "--once"])
```

Note on `--probe-command`: the CLI needs a way to run a sweep in tests without
real SSH. Implement it as a hidden flag that replaces `REMOTE_COMMAND`'s
transport with `subprocess` running the given command, or simpler, accept
`--probe-command true` meaning "treat every board as reachable with zero
uptime". Pick the simpler reading: `--probe-command true` installs a stub probe
returning `ok=True, uptime_s=0.0, in_use=False`. Document it in `--help` as
"testing only".

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_watchdog_service.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'fleet_watchdog.service'`.

- [ ] **Step 3: Write the implementation**

Create `src/fleet_watchdog/service.py`:

```python
"""The sweep loop: enumerate, probe, decide, act, log.

Reporting is the journal and nothing else, by design, so all state lives in
this object's memory. Nothing here needs to survive a restart: board uptime is
read from the board itself, and the first sweep after any start only observes,
so a restart costs at most one sweep of latency.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Mapping

from switch_setup.cli import load_specs

from .config import WatchdogConfig
from .cycle import cycle_port
from .policy import BoardState, Decision, Observation, Reason, decide
from .probe import SshProbe, probe_all
from .switches import Board, community_for, occupied_boards, open_switch

log = logging.getLogger("fleet_watchdog")


class Watchdog:
    def __init__(
        self,
        cfg: WatchdogConfig,
        probe: Callable[[Board], Observation] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.probe = probe or SshProbe(cfg)
        self.sleep = sleep
        self.clock = clock
        self.states: dict[Board, BoardState] = {}
        self.first_sweep = True
        self._switches: dict[int, object] = {}

    # -- switch access ----------------------------------------------------

    def specs(self):
        return load_specs(self.cfg.switches_config)

    def switch(self, index: int):
        if index not in self._switches:
            spec = next(s for s in self.specs() if s.index == index)
            self._switches[index] = open_switch(spec, community_for(index))
        return self._switches[index]

    def enumerate(self) -> list[Board]:
        boards: list[Board] = []
        for spec in self.specs():
            try:
                sw = self.switch(spec.index)
                boards.extend(
                    occupied_boards(
                        sw,
                        spec,
                        self.cfg.exclude.get(spec.index, frozenset()),
                        self.cfg.pib_network,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one switch must not end the sweep
                # Dropping a switch's boards from the sweep is safe: an absent
                # board is never cycled. It also feeds the breaker, because the
                # remaining switch's failures are measured against a smaller
                # fleet.
                log.error("switch %s unreachable, skipping its ports: %s", spec.index, exc)
        return boards

    # -- one sweep --------------------------------------------------------

    def sweep(self, dry_run: bool = False) -> Decision:
        boards = self.enumerate()
        observations = probe_all(self.probe, boards, self.cfg.probe_concurrency)
        for obs in observations:
            log.debug(
                "%s ok=%s uptime=%s in_use=%s %s",
                obs.board, obs.ok,
                f"{obs.uptime_s / 3600:.1f}h" if obs.uptime_s is not None else "-",
                obs.in_use, obs.error or "",
            )
            if not obs.ok and self.states.get(obs.board, BoardState()).consecutive_failures == 0:
                log.warning("%s first failed probe: %s", obs.board, obs.error)

        decision = decide(
            observations, self.states, self.cfg, self.clock(), self.first_sweep
        )
        self.first_sweep = False

        log.info(
            "sweep: occupied=%d ok=%d failed=%d in_use=%d cycling=%d deferred=%d%s",
            decision.occupied,
            decision.occupied - decision.failed,
            decision.failed,
            decision.in_use,
            len(decision.cycles),
            len(decision.deferred),
            " BREAKER" if decision.breaker_tripped else "",
        )
        if decision.breaker_tripped:
            log.error(
                "circuit breaker: %d of %d boards failed; cycling nothing. "
                "Suspect the watchdog, not the fleet: key, user, routing, switch "
                "or the NFS root host key.",
                decision.failed, decision.occupied,
            )
        for board, why in decision.deferred:
            log.info("%s deferring scheduled cycle: %s", board, why)

        if not dry_run:
            self.run_cycles(decision)
        return decision

    def run_cycles(self, decision: Decision) -> None:
        if not decision.cycles:
            return
        workers = max(1, min(self.cfg.cycle_concurrency, len(decision.cycles)))
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(lambda item: self.cycle(*item), decision.cycles))

    def cycle(self, board: Board, reason: Reason) -> None:
        log.warning("%s cycling PoE: %s", board, reason.value)
        try:
            cycle_port(
                self.switch(board.switch),
                board.port,
                self.cfg.poe_off_seconds,
                sleep=self.sleep,
            )
        except Exception as exc:  # noqa: BLE001 - includes CycleError
            log.error("%s cycle failed: %s", board, exc)
        finally:
            # The grace starts whether or not the cycle completed. A port that
            # failed to come back must not be hammered every sweep either.
            self.states.setdefault(board, BoardState()).last_cycle = self.clock()

    # -- the loop ---------------------------------------------------------

    def run(self) -> None:
        log.info(
            "fleet watchdog starting: interval=%.0fs threshold=%d off=%.0fs "
            "uptime=%.0fh cap=%.0fh; the first sweep only observes",
            self.cfg.interval, self.cfg.fail_threshold, self.cfg.poe_off_seconds,
            self.cfg.max_uptime_hours, self.cfg.hard_cap_hours,
        )
        while True:
            started = self.clock()
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 - the loop outlives any one sweep
                log.exception("sweep failed")
            # Sweeps never overlap. A sweep that overruns simply starts the
            # next one immediately rather than stacking.
            self.sleep(max(0.0, self.cfg.interval - (self.clock() - started)))
```

Create `src/fleet_watchdog/cli.py`:

```python
"""fpgas-fleet-watchdog: sweep the switch ports and keep the fleet alive."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .policy import Observation
from .service import Watchdog


def _stub_probe(board):
    return Observation(board=board, ok=True, uptime_s=0.0, in_use=False, error=None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="path to watchdog.yml")
    ap.add_argument("--once", action="store_true", help="run one sweep and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="decide and log, but cycle nothing")
    ap.add_argument("--verbose", action="store_true", help="log every probe result")
    ap.add_argument("--probe-command", choices=["true"], default=None,
                    help="testing only: treat every board as reachable")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    try:
        cfg = load_config(args.config)
    except OSError as exc:
        raise SystemExit(f"cannot read {args.config}: {exc}") from exc

    probe = _stub_probe if args.probe_command == "true" else None
    wd = Watchdog(cfg, probe=probe)
    if args.once:
        wd.sweep(dry_run=args.dry_run)
        return 0
    if args.dry_run:
        raise SystemExit("--dry-run needs --once")
    wd.run()
    return 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_watchdog_service.py -v`

Expected: 11 passed.

- [ ] **Step 5: Run the whole suite and lint**

Run:

```bash
uv run pytest -q
uvx ruff check src tests
```

Expected: all tests pass (the 14 pre-existing plus the new watchdog tests), ruff
clean.

`uvx ruff`, not `uv run ruff`: ruff is not a project dependency here, CI runs it
through `astral-sh/ruff-action`. The config in `ruff.toml` selects `E`, `F`, `W`
and `I`, so import order is enforced.

- [ ] **Step 6: Commit**

```bash
git add src/fleet_watchdog/service.py src/fleet_watchdog/cli.py \
        tests/test_watchdog_service.py
git commit -m "feat(watchdog): sweep loop, journald logging and the CLI

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 8: Document the watchdog in the poe README

**Repo:** `fpgas.online-poe`

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the CLI from Task 7.
- Produces: nothing code depends on.

- [ ] **Step 1: Add the section**

Append to `README.md`, after the `fpgas-switch-setup` section:

```markdown
## fpgas-fleet-watchdog

Sweeps every access port on every configured switch, checks the attached board
answers an SSH login, and PoE-cycles anything that does not. Also recycles
healthy boards that have been up longer than the configured maximum.

Deployed by the [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra)
`fleet-watchdog` role as a systemd service on the gateway. Configuration is
`/etc/fpgas/watchdog.yml`; SNMP write communities come from the environment as
`FPGAS_SWITCH_COMMUNITY_<index>`, the same variables the PoE web views use.

```bash
# one sweep, decide and log, change nothing
fpgas-fleet-watchdog --config /etc/fpgas/watchdog.yml --once --dry-run --verbose

# run the loop in the foreground
fpgas-fleet-watchdog --config /etc/fpgas/watchdog.yml
```

A port counts as occupied when it is delivering PoE, whatever the MAC table
says: a board hung hard enough to stop transmitting ages out of the MAC table,
and that is exactly the board worth rescuing.

Safety behaviour worth knowing before running it:

- The first sweep after any start observes only and cycles nothing.
- If at least `breaker_min_failures` boards fail and they are more than
  `breaker_fraction` of the occupied ports, it cycles nothing and logs an error.
  Most of the fleet failing at once usually means the watchdog is broken, not
  the fleet.
- Trunk and uplink ports are passed to the switch as protected, so a write to
  one raises rather than cutting the link to another switch.
```

- [ ] **Step 2: Verify the markdown renders**

Run: `uv run python -c "import pathlib; print(pathlib.Path('README.md').read_text().count('fpgas-fleet-watchdog'))"`

Expected: a number of at least 3.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: describe fpgas-fleet-watchdog and its safety behaviour

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin fleet-watchdog
gh pr create --repo fpgas-online/fpgas.online-poe \
  --title "fleet watchdog: sweep switch ports, SSH-check boards, PoE-cycle the dead" \
  --body "$(cat <<'BODY'
Adds `fleet_watchdog`, the logic behind the new infra `fleet-watchdog` role.

Every 5 minutes it lists the ports delivering PoE on each configured switch,
asks each board for `/proc/uptime` and `who` over SSH, and PoE-cycles (off,
30 s, on) any board that has failed two sweeps in a row or has been up past
8 hours.

Safety rails: the first sweep after a start only observes, a circuit breaker
refuses to cycle anything when most of the fleet fails at once, trunk and
uplink ports are protected at the library level, and a cycled board gets a
boot grace before it can be cycled again.

Design: `fpgas.online-infra` `docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV
BODY
)"
```

---

## Task 9: The infra role — user, key and NFS root authorisation

**Repo:** `fpgas.online-infra`

**Files:**
- Create: `ansible/roles/fleet-watchdog/defaults/main.yml`
- Create: `ansible/roles/fleet-watchdog/tasks/main.yml`
- Create: `ansible/roles/fleet-watchdog/templates/known_hosts.j2`

**Interfaces:**
- Consumes: inventory `switches`, `nfs_root`, `pib_network`.
- Produces: `/var/lib/fleet-watchdog/id_ed25519` and its `.pub`,
  `/var/lib/fleet-watchdog/known_hosts`, the `fleetwd` user.

- [ ] **Step 1: Write the defaults**

Create `ansible/roles/fleet-watchdog/defaults/main.yml`:

```yaml
---
# The fleet watchdog: sweeps every access port, SSH-checks the board, and
# PoE-cycles anything dead or up too long.
# Design: docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md

# OFF by default. The service's key only reaches the boards through an NFS root
# update and a fleet PoE cycle; until then every probe fails, the circuit
# breaker trips and it cycles nothing. Enable in host_vars once the key is in
# the root and verified (see the role README).
fleet_watchdog_enabled: false

fleet_watchdog_user: fleetwd
fleet_watchdog_home: /var/lib/fleet-watchdog
# Shared with roles/switch-vlans, which pip-installs fpgas-online-poe[cli]
# there on every converge. This role deliberately installs no software.
fleet_watchdog_venv: /opt/fpgas-switch/venv

fleet_watchdog_interval: 300
fleet_watchdog_fail_threshold: 2
fleet_watchdog_ssh_timeout: 20
fleet_watchdog_probe_concurrency: 8
fleet_watchdog_poe_off_seconds: 30
fleet_watchdog_boot_grace: 300
fleet_watchdog_max_uptime_hours: 8
fleet_watchdog_uptime_jitter_minutes: 60
fleet_watchdog_hard_cap_hours: 12
fleet_watchdog_max_scheduled_cycles_per_sweep: 2
fleet_watchdog_cycle_concurrency: 2
fleet_watchdog_breaker_fraction: 0.5
fleet_watchdog_breaker_min_failures: 3
fleet_watchdog_ssh_user: pi

# Ports the watchdog must never cycle, per switch index. Trunk and uplink
# ports need no entry: the role passes them to the switch as protected.
# Welland's real values live in host_vars; this default is empty so another
# site does not inherit welland's board layout.
fleet_watchdog_exclude: {}
```

- [ ] **Step 2: Write the tasks**

Create `ansible/roles/fleet-watchdog/tasks/main.yml`:

```yaml
---
# Runs in the nbp play AFTER pxe: this role writes into the Pi NFS root, and
# img (fresh image extract) and fixpi (authorized_keys) both run earlier in
# that play. Running before them would have the key wiped by the next extract.

- name: create the watchdog service account
  user:
    name: "{{ fleet_watchdog_user }}"
    home: "{{ fleet_watchdog_home }}"
    shell: /usr/sbin/nologin
    system: true
    create_home: true
  tags: [fleet-watchdog]

- name: lock down the watchdog home
  file:
    path: "{{ fleet_watchdog_home }}"
    state: directory
    owner: "{{ fleet_watchdog_user }}"
    group: "{{ fleet_watchdog_user }}"
    mode: "0700"
  tags: [fleet-watchdog]

# Idempotent: openssh_keypair regenerates only if the key is missing or the
# type changed. Never pass force=true here -- a new key would lock the service
# out until the next NFS root update and fleet cycle.
- name: generate the watchdog ssh key
  community.crypto.openssh_keypair:
    path: "{{ fleet_watchdog_home }}/id_ed25519"
    type: ed25519
    owner: "{{ fleet_watchdog_user }}"
    group: "{{ fleet_watchdog_user }}"
    mode: "0600"
  register: fleet_watchdog_key
  tags: [fleet-watchdog]

# Additive: authorized_key appends, so the videoteam and controller keys that
# fixpi installs survive. The Orange Pi boards on sw2 18-24 share this root,
# so this authorises the watchdog on them too.
- name: authorise the watchdog key for pi in the NFS root
  ansible.posix.authorized_key:
    user: pi
    state: present
    key: "{{ fleet_watchdog_key.public_key }}"
    path: "{{ nfs_root }}/root/home/pi/.ssh/authorized_keys"
  tags: [fleet-watchdog]

# fixpi generates the NFS root's host keys earlier in this play, so the file
# should exist. Check rather than assume: a missing key must degrade to "the
# watchdog cannot start" and not abort the whole converge, which would leave
# the server half-provisioned over a service that ships disabled anyway.
- name: check the NFS root has an ssh host key
  stat:
    path: "{{ nfs_root }}/root/etc/ssh/ssh_host_ed25519_key.pub"
  register: fleet_watchdog_host_key_stat
  tags: [fleet-watchdog]

- name: warn when the NFS root has no ssh host key
  debug:
    msg: >-
      {{ nfs_root }}/root/etc/ssh/ssh_host_ed25519_key.pub is missing, so no
      known_hosts can be pinned and the watchdog cannot verify any board.
      Leaving fleet_watchdog_enabled off until fixpi has generated it.
  when: not fleet_watchdog_host_key_stat.stat.exists
  tags: [fleet-watchdog]

- name: read the NFS root's ssh host key
  slurp:
    src: "{{ nfs_root }}/root/etc/ssh/ssh_host_ed25519_key.pub"
  register: fleet_watchdog_host_key
  when: fleet_watchdog_host_key_stat.stat.exists
  tags: [fleet-watchdog]

# Every netbooted board serves the NFS root's single host key, so one pinned
# entry covers the fleet. Pinning matters: with StrictHostKeyChecking=yes a
# changed root key makes every probe fail, which trips the circuit breaker
# instead of power-cycling 35 healthy boards.
- name: install the pinned known_hosts
  template:
    src: known_hosts.j2
    dest: "{{ fleet_watchdog_home }}/known_hosts"
    owner: "{{ fleet_watchdog_user }}"
    group: "{{ fleet_watchdog_user }}"
    mode: "0644"
  when: fleet_watchdog_host_key_stat.stat.exists
  tags: [fleet-watchdog]

- name: refuse to enable the watchdog without a pinned host key
  assert:
    that:
      - fleet_watchdog_host_key_stat.stat.exists
    fail_msg: >-
      fleet_watchdog_enabled is true but the NFS root has no ssh host key to
      pin. Every probe would fail. Run the pi provisioning first.
  when: fleet_watchdog_enabled
  tags: [fleet-watchdog]
```

- [ ] **Step 3: Write the known_hosts template**

Create `ansible/roles/fleet-watchdog/templates/known_hosts.j2`:

```jinja
# {{ ansible_managed }}
# Every netbooted board serves the NFS root's ssh_host_ed25519_key, so one key
# covers the whole fleet. One line per managed address, because
# StrictHostKeyChecking=yes needs an exact host match.
{% set hostkey = fleet_watchdog_host_key.content | b64decode | trim %}
{% for sw in switches %}
{% for port in range(1, sw.access_ports + 1) %}
{{ pib_network }}.{{ sw.index }}.{{ port }} {{ hostkey }}
{% endfor %}
{% endfor %}
```

- [ ] **Step 4: Lint what exists so far**

Neither linter is a project dependency: CI pip-installs them (see
`.github/workflows/lint.yml`), so run them with `uvx`. yamllint needs the
repo's config explicitly, and ansible-lint must run from `ansible/` where
`.ansible-lint` lives.

```bash
uvx yamllint -c .yamllint.yml ansible/roles/fleet-watchdog/
(cd ansible && uvx ansible-lint roles/fleet-watchdog/)
```

Expected: yamllint clean (it is blocking in CI). ansible-lint is advisory
(`continue-on-error: true`); fix anything it reports that is not already on the
skip list in `.ansible-lint`.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/fleet-watchdog/
git commit -m "feat(fleet-watchdog): service account, ssh key and pinned known_hosts

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 10: The infra role — config, unit and service state

**Repo:** `fpgas.online-infra`

**Files:**
- Create: `ansible/roles/fleet-watchdog/templates/watchdog.yml.j2`
- Create: `ansible/roles/fleet-watchdog/templates/watchdog.env.j2`
- Create: `ansible/roles/fleet-watchdog/templates/fleet-watchdog.service.j2`
- Create: `ansible/roles/fleet-watchdog/handlers/main.yml`
- Modify: `ansible/roles/fleet-watchdog/tasks/main.yml`

**Interfaces:**
- Consumes: everything from Task 9, plus the `switches` inventory block with
  its vaulted `snmp_rw_community` per switch.
- Produces: `/etc/fpgas/watchdog.yml`, `/etc/fpgas/watchdog.env`,
  `fleet-watchdog.service`.

- [ ] **Step 1: Write the config template**

Create `ansible/roles/fleet-watchdog/templates/watchdog.yml.j2`:

```jinja
# {{ ansible_managed }}
# Read by fpgas-fleet-watchdog (fpgas.online-poe, fleet_watchdog.config).
# No secrets here: the SNMP write communities are in watchdog.env (0600).
switches_config: /etc/fpgas/switches.yml
pib_network: "{{ pib_network }}"
ssh_key: {{ fleet_watchdog_home }}/id_ed25519
known_hosts: {{ fleet_watchdog_home }}/known_hosts
ssh_user: {{ fleet_watchdog_ssh_user }}

interval: {{ fleet_watchdog_interval }}
fail_threshold: {{ fleet_watchdog_fail_threshold }}
ssh_timeout: {{ fleet_watchdog_ssh_timeout }}
probe_concurrency: {{ fleet_watchdog_probe_concurrency }}
poe_off_seconds: {{ fleet_watchdog_poe_off_seconds }}
boot_grace: {{ fleet_watchdog_boot_grace }}
max_uptime_hours: {{ fleet_watchdog_max_uptime_hours }}
uptime_jitter_minutes: {{ fleet_watchdog_uptime_jitter_minutes }}
hard_cap_hours: {{ fleet_watchdog_hard_cap_hours }}
max_scheduled_cycles_per_sweep: {{ fleet_watchdog_max_scheduled_cycles_per_sweep }}
cycle_concurrency: {{ fleet_watchdog_cycle_concurrency }}
breaker_fraction: {{ fleet_watchdog_breaker_fraction }}
breaker_min_failures: {{ fleet_watchdog_breaker_min_failures }}

exclude:
{% for index, ports in (fleet_watchdog_exclude | dictsort) %}
  {{ index }}: {{ ports | list }}
{% endfor %}
```

- [ ] **Step 2: Write the environment template**

Create `ansible/roles/fleet-watchdog/templates/watchdog.env.j2`:

```jinja
# {{ ansible_managed }}
# systemd EnvironmentFile. Mode 0600, owned by the service account: these are
# the switches' SNMP write communities. Same pattern as the gunicorn PoE
# drop-in (roles/site/templates/gunicorn-poe.conf.j2).
FPGAS_SWITCHES_CONFIG=/etc/fpgas/switches.yml
{% for sw in switches %}
FPGAS_SWITCH_COMMUNITY_{{ sw.index }}={{ sw.snmp_rw_community }}
{% endfor %}
```

- [ ] **Step 3: Write the unit template**

Create `ansible/roles/fleet-watchdog/templates/fleet-watchdog.service.j2`:

```jinja
# {{ ansible_managed }}
[Unit]
Description=fpgas.online fleet watchdog
Documentation=https://github.com/fpgas-online/fpgas.online-poe
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User={{ fleet_watchdog_user }}
Group={{ fleet_watchdog_user }}
# ProtectHome hides /home, so ssh needs to be told where HOME is. It never
# writes there: the identity and known_hosts are both passed on the command
# line and StrictHostKeyChecking=yes adds no host keys.
Environment=HOME={{ fleet_watchdog_home }}
EnvironmentFile={{ fleet_watchdog_env_file }}
ExecStart={{ fleet_watchdog_venv }}/bin/fpgas-fleet-watchdog --config {{ fleet_watchdog_config_file }}
Restart=always
RestartSec=30
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={{ fleet_watchdog_home }}

[Install]
WantedBy=multi-user.target
```

Add to `defaults/main.yml`:

```yaml
fleet_watchdog_config_file: /etc/fpgas/watchdog.yml
fleet_watchdog_env_file: /etc/fpgas/watchdog.env
```

- [ ] **Step 4: Write the handler**

Create `ansible/roles/fleet-watchdog/handlers/main.yml`:

```yaml
---
- name: restart fleet-watchdog
  systemd:
    name: fleet-watchdog
    state: restarted
    daemon_reload: true
  when: fleet_watchdog_enabled
```

- [ ] **Step 5: Append the remaining tasks**

Append to `ansible/roles/fleet-watchdog/tasks/main.yml`:

```yaml
- name: create /etc/fpgas directory
  file:
    path: /etc/fpgas
    state: directory
    mode: "0755"
  tags: [fleet-watchdog]

- name: install the watchdog config
  template:
    src: watchdog.yml.j2
    dest: "{{ fleet_watchdog_config_file }}"
    mode: "0644"
  notify: restart fleet-watchdog
  tags: [fleet-watchdog]

- name: install the watchdog environment file
  template:
    src: watchdog.env.j2
    dest: "{{ fleet_watchdog_env_file }}"
    owner: "{{ fleet_watchdog_user }}"
    group: "{{ fleet_watchdog_user }}"
    mode: "0600"
  no_log: true
  notify: restart fleet-watchdog
  tags: [fleet-watchdog]

- name: install the watchdog systemd unit
  template:
    src: fleet-watchdog.service.j2
    dest: /etc/systemd/system/fleet-watchdog.service
    mode: "0644"
  notify: restart fleet-watchdog
  tags: [fleet-watchdog]

# Enabled state follows fleet_watchdog_enabled in both directions, so turning
# the flag off is a real off switch and not just "stop notifying".
- name: set the watchdog service state
  systemd:
    name: fleet-watchdog
    daemon_reload: true
    enabled: "{{ fleet_watchdog_enabled }}"
    state: "{{ 'started' if fleet_watchdog_enabled else 'stopped' }}"
  tags: [fleet-watchdog]
```

- [ ] **Step 6: Lint**

```bash
uvx yamllint -c .yamllint.yml ansible/roles/fleet-watchdog/
(cd ansible && uvx ansible-lint roles/fleet-watchdog/)
```

Expected: yamllint clean.

- [ ] **Step 7: Commit**

```bash
git add ansible/roles/fleet-watchdog/
git commit -m "feat(fleet-watchdog): config, environment file and systemd unit

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 11: Wire the role into the playbooks and inventory

**Repo:** `fpgas.online-infra`

**Files:**
- Modify: `ansible/site.yml`
- Modify: `ansible/inventory/host_vars/fpgas.online.yml`
- Create: `ansible/roles/fleet-watchdog/tasks/verify/main.yml`
- Modify: `ansible/verify-server.yml`

**Interfaces:**
- Consumes: the role from Tasks 9 and 10.
- Produces: the role running on hosts with `switches` defined.

- [ ] **Step 1: Add the role to the nbp play**

In `ansible/site.yml`, in the first `hosts: nbp` play, add after `pxe`:

```yaml
    - pxe
    # After pxe: this role writes the watchdog key into the Pi NFS root, so it
    # must run after img (fresh extract) and fixpi (authorized_keys).
    - {role: fleet-watchdog, when: switches is defined}
```

- [ ] **Step 2: Add welland's exclusions to host_vars**

In `ansible/inventory/host_vars/fpgas.online.yml`, after the `switches:` block
and its vault variables, add:

```yaml
# Fleet watchdog (roles/fleet-watchdog). Ports here are never cycled.
# Enumerated 2026-09-15; revisit when boards move.
fleet_watchdog_exclude:
  1:
    - 13    # rpiz-4, SD-booted, its own ssh host key
  2:
    - 11    # Pi 5 2c:cf:67:16:bc:30: draws 5.6 W, link never comes up,
            # a PoE cycle does not revive it. Needs on-site attention.
    - 12    # rpi5-netv2pcie-test, boots locally, rejects every key we hold
    - 27    # rpi5-new-13f56e, SD-booted
    - 30    # rpi5-new-13f59c, SD-booted; the EEPROM recovery host

# Turn on only after the NFS root carries the watchdog key and the fleet has
# been PoE-cycled; see roles/fleet-watchdog/README.md.
fleet_watchdog_enabled: false
```

The Orange Pi PC boards on sw2 18-24 are deliberately NOT excluded: they share
the Pis' NFS root, they hang most often, and a PoE cycle reliably revives them.

- [ ] **Step 3: Write the verify tasks**

Create `ansible/roles/fleet-watchdog/tasks/verify/main.yml`:

```yaml
---
- name: "verify: the watchdog key exists"
  stat:
    path: "{{ fleet_watchdog_home }}/id_ed25519"
  register: fleet_watchdog_verify_key

- name: "verify: the watchdog key is private to the service account"
  assert:
    that:
      - fleet_watchdog_verify_key.stat.exists
      - fleet_watchdog_verify_key.stat.pw_name == fleet_watchdog_user
      - fleet_watchdog_verify_key.stat.mode == "0600"
    fail_msg: "watchdog ssh key missing or world-readable"

- name: "verify: the watchdog key is authorised in the NFS root"
  command: >-
    grep -qF "{{ lookup('file', fleet_watchdog_home + '/id_ed25519.pub') | trim }}"
    {{ nfs_root }}/root/home/pi/.ssh/authorized_keys
  changed_when: false

- name: "verify: known_hosts pins one key per managed address"
  command: "wc -l < {{ fleet_watchdog_home }}/known_hosts"
  register: fleet_watchdog_verify_known_hosts
  changed_when: false

- name: "verify: known_hosts covers every access port"
  assert:
    that:
      - fleet_watchdog_verify_known_hosts.stdout | int
        >= (switches | map(attribute='access_ports') | sum)
    fail_msg: "known_hosts does not cover every access port"

- name: "verify: the environment file holds no world-readable secrets"
  stat:
    path: "{{ fleet_watchdog_env_file }}"
  register: fleet_watchdog_verify_env

- name: "verify: the environment file is 0600"
  assert:
    that:
      - fleet_watchdog_verify_env.stat.mode == "0600"
    fail_msg: "{{ fleet_watchdog_env_file }} must be 0600, it holds SNMP write communities"

- name: "verify: the console script is installed in the shared venv"
  stat:
    path: "{{ fleet_watchdog_venv }}/bin/fpgas-fleet-watchdog"
  register: fleet_watchdog_verify_script

- name: "verify: fpgas-fleet-watchdog is present"
  assert:
    that:
      - fleet_watchdog_verify_script.stat.exists
    fail_msg: >-
      fpgas-fleet-watchdog missing from {{ fleet_watchdog_venv }};
      roles/switch-vlans installs fpgas-online-poe there

- name: "verify: the config parses and one dry-run sweep decides something"
  command: >-
    {{ fleet_watchdog_venv }}/bin/fpgas-fleet-watchdog
    --config {{ fleet_watchdog_config_file }} --once --dry-run
  become: true
  become_user: "{{ fleet_watchdog_user }}"
  environment:
    HOME: "{{ fleet_watchdog_home }}"
  register: fleet_watchdog_verify_sweep
  changed_when: false
  when: fleet_watchdog_enabled

- name: "verify: the dry-run sweep reported a summary"
  assert:
    that:
      - "'sweep: occupied=' in fleet_watchdog_verify_sweep.stdout"
    fail_msg: "the dry-run sweep printed no summary line"
  when: fleet_watchdog_enabled
```

- [ ] **Step 4: Include the verify tasks**

In `ansible/verify-server.yml`, in the `Verify server roles (nbp)` play, add
immediately after the existing `verify pxe` task and before the
`VLAN-per-port scheme verification` comment block:

```yaml
    # Only on hosts with `switches:` defined (tweed). The legacy MAC-table
    # hosts (ps1.fpgas.online) run no watchdog.
    - name: verify fleet-watchdog
      include_role:
        name: fleet-watchdog
        tasks_from: verify/main.yml
      when: switches is defined
      tags: [verify, fleet-watchdog]
```

The surrounding tasks use exactly this shape: `include_role` with
`tasks_from: verify/main.yml` and a `tags: [verify, <role>]` pair, indented
four spaces under the play's `tasks:`.

- [ ] **Step 5: Syntax-check both playbooks**

Run:

```bash
uv run ansible-playbook --syntax-check ansible/site.yml
uv run ansible-playbook --syntax-check ansible/verify-server.yml
uvx yamllint -c .yamllint.yml ansible/
```

Expected: all clean. `ansible-playbook` is available through `uv run` because
`ansible-core` is a project dependency; the linters are not, hence `uvx`.

- [ ] **Step 6: Commit**

```bash
git add ansible/site.yml ansible/verify-server.yml \
        ansible/inventory/host_vars/fpgas.online.yml \
        ansible/roles/fleet-watchdog/
git commit -m "feat(fleet-watchdog): run in the nbp play, verify tasks, welland exclusions

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

---

## Task 12: Role README, spec status, and the PR

**Repo:** `fpgas.online-infra`

**Files:**
- Create: `ansible/roles/fleet-watchdog/README.md`
- Modify: `docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code depends on.

- [ ] **Step 1: Write the role README**

Create `ansible/roles/fleet-watchdog/README.md`:

```markdown
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

Never set `force: true` on the key generation task. A new key locks the service
out until the next NFS root update and fleet cycle.
```

- [ ] **Step 2: Mark the spec implemented**

In `docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md`, change the
Status line to:

```markdown
Date: 2026-09-15
Status: implemented 2026-09-15 (`roles/fleet-watchdog`, `fleet_watchdog` in
fpgas.online-poe). Plan: `docs/superpowers/plans/2026-09-15-fleet-watchdog.md`.
```

- [ ] **Step 3: Lint everything**

Run:

```bash
uvx yamllint -c .yamllint.yml ansible/
(cd ansible && uvx ansible-lint)
uv run ansible-playbook --syntax-check ansible/site.yml
```

Expected: yamllint and the syntax check clean; ansible-lint advisory.

- [ ] **Step 4: Commit**

```bash
git add ansible/roles/fleet-watchdog/README.md \
        docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md
git commit -m "docs(fleet-watchdog): role README and spec status

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV"
```

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin fleet-watchdog
gh pr create --repo fpgas-online/fpgas.online-infra \
  --title "fleet-watchdog: sweep switch ports, SSH-check boards, PoE-cycle the dead" \
  --body "$(cat <<'BODY'
Deploys the new `fpgas-fleet-watchdog` (fpgas.online-poe) as a systemd service
on the gateway, with its own service account, its own SSH key authorised in the
Pi NFS root, and the NFS root's host key pinned so a key change fails safe.

Ships **disabled**: the key only reaches the boards through an NFS root update
and a fleet PoE cycle, and until then every probe fails. See
`roles/fleet-watchdog/README.md` for the order to turn it on.

Depends on fpgas.online-poe #<N>.

Design: `docs/superpowers/specs/2026-09-15-fleet-watchdog-design.md`
Plan: `docs/superpowers/plans/2026-09-15-fleet-watchdog.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01XqcHkHcQHeq78MhTdqocnV
BODY
)"
```

Replace `#<N>` with the poe PR number from Task 8.

---

## Deployment checklist (after both PRs merge)

Not a code task. Run from ten64, following the standing NFS root procedure.

- [ ] Converge infra from merged `main`; confirm the key and `known_hosts` land.
- [ ] Update the NFS root: `site.yml --tags pi,fpgas-apt,onpi,cam`, detached
      (`setsid nohup`), from ten64.
- [ ] PoE-cycle the fleet so every board picks up the new root.
- [ ] Verify the key by hand against one known-good board.
- [ ] `--once --dry-run --verbose` and read every proposed action.
- [ ] Set `fleet_watchdog_enabled: true`, converge, watch one sweep.
- [ ] Re-check after 24 hours: confirm scheduled cycles are spread out rather
      than arriving in one clump, and that no board is stuck in a cycle loop.
