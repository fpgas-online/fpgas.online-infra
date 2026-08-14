# VLAN-per-port Network Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-port VLAN isolation and port-determined IPv4/IPv6 addressing for the fpgas.online welland network, prototyped on tweed + the s2 (GSM7252PS) and s3300 switches.

**Architecture:** Each Pi-facing switch port becomes an untagged access port in its own VLAN (2101–2348); trunks carry them tagged to tweed's `eth-local`, where per-VLAN interfaces + dnsmasq interface-tags assign the port's fixed IPv4/IPv6, and nftables default-denies Pi↔Pi. A declarative CLI (`fpgas-switch-setup`, in fpgas.online-poe) converges switch state via `python-netgear-switch-library`.

**Tech Stack:** Python 3.11+/uv, python-netgear-switch-library (SNMP), Ansible (systemd-networkd, dnsmasq 2.90, nftables), pytest, QEMU VM harness.

**Spec:** `docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md` (in fpgas.online-infra)

## Global Constraints

- Formulas (verbatim from spec): VLAN = `2000 + 100*s + p`; IPv4 = `10.21.s.p`; IPv6 = `2404:e80:a137:21<s:02d>::<p>` (digits used literally, e.g. port 48 → `::48`); hostname = `pi-sw<s>-p<p>`; tweed iface = `v<VLAN>`.
- Access ports are members of ONLY their own VLAN — explicitly excluded from VLAN 1. Trunk rule (daisy-chain): gateway trunk of switch s carries blocks of switches ≥ s; downstream trunks carry blocks of switches > s. The house-uplink port and any VLAN outside 2101–2348 (except owned ports' VLAN-1 membership) are NEVER modified.
- All Python via `uv` (`uv run`, `uv pip`). Never bare `python`/`pip`.
- No files in `/tmp`; use a project-local `tmp/` and clean up.
- Small discrete commits; yamllint must pass in fpgas.online-infra (blocking); never force-push.
- Repos/branches: fpgas.online-infra → branch `vlan-per-port-network` (exists, contains the spec); fpgas.online-poe → create branch `switch-setup`. Local checkouts: `/home/tim/github/fpgas-online/<repo>`.
- The library's local clone (reference only): `/home/tim/github/fpgas-online/python-netgear-switch-library`. PyPI name `python-netgear-switch-library`, import `netgear_switch`.
- ps1 site and production cutover are OUT OF SCOPE — this plan ends at a working hardware prototype. Cutover gets its own plan afterwards.

---

## Phase 0 — Risk verification labs (spec: "Risks to verify FIRST")

### Task 1: dnsmasq netns lab (risks 2 + 3)

Proves, on any Linux box (no switch hardware): (a) IPv4 single-address `dhcp-range` selected by interface tag when the interface's only address is `10.21.0.1/32`; (b) DHCPv6 single-address range the same way; (c) RA without on-link prefix. Result adjusts Task 8/9 templates.

**Files:**
- Create: `tests/lab/dnsmasq_lab.py` (fpgas.online-infra)
- Create: `tests/lab/RESULTS.md`

**Interfaces:**
- Produces: `tests/lab/RESULTS.md` — one `## <check>: PASS/FAIL — <detail>` section per check; Tasks 8/9 read it before finalising templates.

- [ ] **Step 1: Write the lab script**

```python
#!/usr/bin/env python3
"""Netns lab: verify dnsmasq per-interface-tag single-address ranges.

Topology (all inside two netns, run as root):
  ns "gw":  veth gw0 <---> ns "pi": veth pi0
            gw0.2101 (vlan)         pi0.2101 (vlan)   <- emulates access port
  gw runs dnsmasq bound to gw0.2101 with tag-based single-address ranges.
  pi runs busybox udhcpc (v4) and udhcpc6 (v6) on pi0.2101.

Run: sudo uv run tests/lab/dnsmasq_lab.py
Prints PASS/FAIL per check; exits non-zero if any FAIL.
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

V4 = "10.21.1.1"
V6 = "2404:e80:a137:2101::1"


def sh(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    # cmd is always a fixed string from this file, never user input;
    # split to a list so no shell is involved.
    return subprocess.run(cmd.split(), check=check, text=True,
                          capture_output=True)


def ns(name: str, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return sh(f"ip netns exec {name} {cmd}", check=check)


def setup() -> Path:
    sh("ip netns add gw")
    sh("ip netns add pi")
    sh("ip link add gw0 netns gw type veth peer name pi0 netns pi")
    for n, dev in (("gw", "gw0"), ("pi", "pi0")):
        ns(n, "ip link set lo up")
        ns(n, f"ip link set {dev} up")
        ns(n, f"ip link add link {dev} name {dev}.2101 type vlan id 2101")
        ns(n, f"ip link set {dev}.2101 up")
    # THE CONFIG UNDER TEST: /32 gateway address on the vlan iface.
    ns("gw", "ip addr add 10.21.0.1/32 dev gw0.2101")
    ns("gw", f"ip addr add {V6.replace('::1', '::ffff')}/128 dev gw0.2101")
    ns("gw", "ip route add 10.21.1.1/32 dev gw0.2101")
    conf = Path(tempfile.mkdtemp(prefix="dnsmasq-lab-", dir="tmp")) / "lab.conf"
    conf.write_text(f"""
port=0
interface=gw0.2101
bind-interfaces
log-dhcp
dhcp-range=tag:gw0.2101,{V4},{V4},255.255.0.0,12h
dhcp-range=tag:gw0.2101,{V6},{V6},64,12h
enable-ra
""")
    return conf


def teardown() -> None:
    sh("pkill -f dnsmasq-lab", check=False)
    sh("ip netns del gw", check=False)
    sh("ip netns del pi", check=False)


def main() -> int:
    Path("tmp").mkdir(exist_ok=True)
    teardown()
    conf = setup()
    ns("gw", f"dnsmasq --conf-file={conf} --pid-file={conf.parent}/pid "
             f"--log-facility={conf.parent}/log")
    time.sleep(1)
    results: dict[str, bool] = {}

    r = ns("pi", "busybox udhcpc -i pi0.2101 -n -q -f -t 3 -T 2", check=False)
    got4 = ns("pi", "ip -4 addr show pi0.2101", check=False).stdout
    results["v4-single-range-by-tag"] = V4 in got4

    r6 = ns("pi", "busybox udhcpc6 -i pi0.2101 -n -q -f -t 3 -T 2", check=False)
    got6 = ns("pi", "ip -6 addr show pi0.2101", check=False).stdout
    results["v6-single-range-by-tag"] = V6 in got6

    ra = ns("pi", "rdisc6 -1 pi0.2101", check=False).stdout
    results["ra-received"] = "from" in ra
    results["ra-no-onlink-prefix"] = "On-link" not in ra or "Yes" not in ra

    log = (conf.parent / "log").read_text() if (conf.parent / "log").exists() else ""
    print(log[-2000:])
    ok = True
    for name, passed in results.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
        ok = ok and passed
    teardown()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it on tweed (has dnsmasq 2.90) or desktop.buddy**

Run: `ssh root@tweed.welland.mithis.com` then in a checkout: `apt-get install -y busybox ndisc6 && uv run tests/lab/dnsmasq_lab.py` (as root, no sudo needed there). Alternatively copy the single file over with scp and run `python3 dnsmasq_lab.py` directly — the script has no third-party imports.
Expected: each check prints PASS or FAIL. Any outcome is a valid lab result.

- [ ] **Step 3: Record results and remediations in `tests/lab/RESULTS.md`**

For each FAIL, iterate the lab (e.g. if the v4 range is not selected with a `/32`, retry with `10.21.0.1/16 noprefixroute`; if dnsmasq's RA advertises on-link, note "use radvd with `AdvOnLink off`") and record the WORKING configuration — that exact configuration is what Tasks 8/9 template.

- [ ] **Step 4: Commit**

```bash
cd /home/tim/github/fpgas-online/fpgas.online-infra
git add tests/lab/ && git commit -m "test: dnsmasq netns lab for per-interface tag ranges"
```

### Task 2: switch VLAN capacity probe (risks 1 + 4)

**Files:**
- Create: `scripts/vlan_capacity_probe.py` (fpgas.online-poe)

**Interfaces:**
- Consumes: `netgear_switch` — `SyncSwitch(get_model(m), host, snmp_community=c)`, `.create_vlan(vid, name)`, `.get_vlans()`, `.delete_vlan(vid)`.
- Produces: measured VLAN capacity numbers for s2/s3300, recorded in `tests/lab/RESULTS.md` (infra repo).

- [ ] **Step 1: Create branch + write probe**

```bash
cd /home/tim/github/fpgas-online/fpgas.online-poe && git checkout -b switch-setup
```

```python
#!/usr/bin/env python3
"""Probe how many VLANs a switch accepts. Uses IDs 3000+ (never the
production 2101-2348 block, never house VLANs) and deletes everything
it created, even on failure.

Usage: uv run scripts/vlan_capacity_probe.py --host 10.1.5.23 \
           --model gsm7252ps --community <rw-community> --count 150
"""

import argparse
import sys

from netgear_switch import SyncSwitch, get_model

BASE = 3000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--community", required=True)
    ap.add_argument("--count", type=int, default=150)
    args = ap.parse_args()

    sw = SyncSwitch(get_model(args.model), args.host,
                    snmp_community=args.community)
    created: list[int] = []
    try:
        for i in range(args.count):
            vid = BASE + i
            try:
                sw.create_vlan(vid, f"probe{i}")
                created.append(vid)
            except Exception as e:  # noqa: BLE001 - report and stop at capacity
                print(f"create_vlan({vid}) failed after "
                      f"{len(created)} creations: {e}")
                break
        present = {v.vlan_id for v in sw.get_vlans()}
        verified = [v for v in created if v in present]
        print(f"created={len(created)} verified_present={len(verified)}")
    finally:
        for vid in created:
            try:
                sw.delete_vlan(vid, force=True)
            except Exception as e:  # noqa: BLE001
                print(f"cleanup delete_vlan({vid}) failed: {e}")
    return 0 if len(created) >= args.count else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test against the library's mock (no hardware, no creds)**

Run (two shells, or background the first):
`cd /home/tim/github/fpgas-online/python-netgear-switch-library && uv run ngsw serve --model gsm7252ps --port 40161`
`cd /home/tim/github/fpgas-online/fpgas.online-poe && uv run scripts/vlan_capacity_probe.py --host 127.0.0.1:40161 --model gsm7252ps --community public --count 150`
Expected: `created=150 verified_present=150`, exit 0. (Note: Task 3 adds the library dependency; if running Task 2 first, use `uv run --with python-netgear-switch-library scripts/vlan_capacity_probe.py ...`.)

- [ ] **Step 3: Run against real s2 and s3300 (needs RW SNMP community from Tim)**

Run: `uv run scripts/vlan_capacity_probe.py --host 10.1.5.23 --model gsm7252ps --community $COMMUNITY --count 150` and again with `--host 10.1.5.11 --model s3300`.
Expected: 150/150 on both. If the S3300 stops short, record its real ceiling in `tests/lab/RESULTS.md` — below ~146 forces a design amendment (fewer access ports per switch); flag to Tim before continuing.

- [ ] **Step 4: Commit**

```bash
git add scripts/vlan_capacity_probe.py && git commit -m "feat: add VLAN capacity probe script"
```

---

## Phase 1 — `fpgas-switch-setup` CLI (fpgas.online-poe)

### Task 3: packaging + derivation module (`plan.py`)

**Files:**
- Modify: `pyproject.toml`
- Create: `src/switch_setup/__init__.py` (empty), `src/switch_setup/plan.py`
- Test: `tests/__init__.py` (empty), `tests/test_plan.py`

**Interfaces:**
- Produces: `SwitchSpec(index, model, mgmt_host, access_ports, gateway_trunk_port, downstream_trunk_ports, house_uplink_port)`; `vlan_id(s: int, p: int) -> int`; `hostname(s, p) -> str`; `DesiredState(vlans: dict[int, str], untagged: dict[int, int], tagged: dict[int, frozenset[int]], pvids: dict[int, int], vlan1_excluded: frozenset[int])`; `desired_state(specs: list[SwitchSpec], index: int) -> DesiredState`; `OWN_RANGE = range(2101, 2349)`.

- [ ] **Step 1: Update packaging**

In `pyproject.toml`: `requires-python = ">=3.11"`; add to `dependencies`: `'python-netgear-switch-library'`, `'pyyaml'`; add `[project.scripts] fpgas-switch-setup = "switch_setup.cli:main"`; change wheel packages to `["src/snmp_switch", "src/switch_setup"]`; add:

```toml
[dependency-groups]
dev = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.uv]
package = true
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_plan.py
from switch_setup.plan import (
    OWN_RANGE, DesiredState, SwitchSpec, desired_state, hostname, vlan_id,
)

SPECS = [
    SwitchSpec(index=1, model="s3300", mgmt_host="10.1.5.11",
               access_ports=48, gateway_trunk_port=49,
               downstream_trunk_ports=(50,), house_uplink_port=52),
    SwitchSpec(index=2, model="gsm7252ps", mgmt_host="10.1.5.23",
               access_ports=48, gateway_trunk_port=49,
               downstream_trunk_ports=(50,), house_uplink_port=52),
]


def test_formulas():
    assert vlan_id(1, 7) == 2107
    assert vlan_id(3, 48) == 2348
    assert hostname(2, 7) == "pi-sw2-p7"
    assert OWN_RANGE == range(2101, 2349)


def test_desired_state_switch1():
    d = desired_state(SPECS, 1)
    assert d.vlans[2107] == "pi-sw1-p7"
    assert set(d.vlans) == set(range(2101, 2149))
    assert d.untagged[7] == 2107 and d.pvids[7] == 2107
    # gateway trunk carries own block AND switch 2's block
    assert d.tagged[49] == frozenset(range(2101, 2149)) | frozenset(range(2201, 2249))
    # downstream trunk carries only blocks BEHIND it
    assert d.tagged[50] == frozenset(range(2201, 2249))
    assert d.vlan1_excluded == frozenset(range(1, 49))
    # house uplink is never in any map
    assert 52 not in d.untagged and 52 not in d.tagged and 52 not in d.pvids


def test_desired_state_switch2_trunks():
    d = desired_state(SPECS, 2)
    assert d.tagged[49] == frozenset(range(2201, 2249))
    assert d.tagged[50] == frozenset()  # no switch 3 configured
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_plan.py -v`
Expected: FAIL (ModuleNotFoundError: switch_setup.plan)

- [ ] **Step 4: Implement `plan.py`**

```python
"""Pure derivation of desired switch state from the fpgas.online formulas.

Spec: fpgas.online-infra docs/superpowers/specs/
2026-08-14-vlan-per-port-network-design.md
"""

from dataclasses import dataclass

OWN_RANGE = range(2101, 2349)  # the only VLAN IDs this tool may create/delete


def vlan_id(s: int, p: int) -> int:
    return 2000 + 100 * s + p


def hostname(s: int, p: int) -> str:
    return f"pi-sw{s}-p{p}"


@dataclass(frozen=True)
class SwitchSpec:
    index: int
    model: str
    mgmt_host: str
    access_ports: int
    gateway_trunk_port: int
    downstream_trunk_ports: tuple[int, ...]
    house_uplink_port: int


def _block(spec: SwitchSpec) -> frozenset[int]:
    return frozenset(vlan_id(spec.index, p) for p in range(1, spec.access_ports + 1))


@dataclass(frozen=True)
class DesiredState:
    vlans: dict[int, str]
    untagged: dict[int, int]          # access port -> its vlan
    tagged: dict[int, frozenset[int]]  # trunk port -> vlan set
    pvids: dict[int, int]
    vlan1_excluded: frozenset[int]     # access ports removed from VLAN 1


def desired_state(specs: list[SwitchSpec], index: int) -> DesiredState:
    spec = next(s for s in specs if s.index == index)
    behind = frozenset().union(
        *[_block(s) for s in specs if s.index > index] or [frozenset()])
    vlans = {vlan_id(index, p): hostname(index, p)
             for p in range(1, spec.access_ports + 1)}
    tagged = {spec.gateway_trunk_port: _block(spec) | behind}
    for port in spec.downstream_trunk_ports:
        tagged[port] = behind
    return DesiredState(
        vlans=vlans,
        untagged={p: vlan_id(index, p) for p in range(1, spec.access_ports + 1)},
        tagged=tagged,
        pvids={p: vlan_id(index, p) for p in range(1, spec.access_ports + 1)},
        vlan1_excluded=frozenset(range(1, spec.access_ports + 1)),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_plan.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/switch_setup tests/
git commit -m "feat: switch_setup derivation module with VLAN/hostname formulas"
```

### Task 4: diff engine

**Files:**
- Modify: `src/switch_setup/plan.py` (append)
- Test: `tests/test_plan.py` (append)

**Interfaces:**
- Consumes: `netgear_switch.models.VLANInfo` (fields `vlan_id, name, member_ports, tagged_ports, untagged_ports`), `netgear_switch.models.VlanMode` (`UNTAGGED/TAGGED/EXCLUDED`).
- Produces: `Action = tuple` — one of `("create_vlan", vid, name)`, `("membership", vid, port, VlanMode)`, `("pvid", port, vid)`, `("delete_vlan", vid)`; `diff(current_vlans: list[VLANInfo], current_pvids: dict[int, int], desired: DesiredState) -> list[Action]` in safe apply order.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_plan.py
from netgear_switch.models import VLANInfo, VlanMode

from switch_setup.plan import diff


def vinfo(vid, name="", member=(), tagged=(), untagged=()):
    return VLANInfo(vlan_id=vid, name=name, member_ports=frozenset(member),
                    tagged_ports=frozenset(tagged),
                    untagged_ports=frozenset(untagged))


def test_diff_from_factory_default():
    d = desired_state(SPECS, 2)
    # factory-ish: only VLAN 1, all ports untagged members, all PVID 1
    current = [vinfo(1, "default", member=range(1, 53), untagged=range(1, 53))]
    pvids = {p: 1 for p in range(1, 53)}
    actions = diff(current, pvids, d)
    assert ("create_vlan", 2207, "pi-sw2-p7") in actions
    assert ("membership", 2207, 7, VlanMode.UNTAGGED) in actions
    assert ("pvid", 7, 2207) in actions
    assert ("membership", 1, 7, VlanMode.EXCLUDED) in actions
    # order: create < trunk tag < access untag < pvid < vlan1 exclusion
    assert (actions.index(("membership", 2207, 7, VlanMode.UNTAGGED))
            < actions.index(("pvid", 7, 2207))
            < actions.index(("membership", 1, 7, VlanMode.EXCLUDED)))
    # house uplink (52) untouched: no action mentions port 52
    assert not [a for a in actions if a[0] == "membership" and a[2] == 52]


def test_diff_idempotent_and_prunes_stale():
    d = desired_state(SPECS, 2)
    current = [vinfo(1, "default", member=(49, 50, 52), untagged=(49, 50, 52))]
    current += [vinfo(v, d.vlans[v], member={p, 49}, untagged={p}, tagged={49})
                for p, v in enumerate(sorted(d.vlans), start=1)]
    current.append(vinfo(2199, "stale"))       # in OWN_RANGE -> delete
    current.append(vinfo(5, "net-house"))      # outside -> NEVER touched
    pvids = {p: 2200 + p for p in range(1, 49)} | {49: 1, 50: 1, 52: 1}
    actions = diff(current, pvids, d)
    assert ("delete_vlan", 2199) in actions
    assert not [a for a in actions if a[1] == 5]
    # second run over converged state = no actions
    # (trunk memberships asserted separately in the apply test)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plan.py -v -k diff`
Expected: FAIL (ImportError: cannot import name 'diff')

- [ ] **Step 3: Implement `diff` (append to plan.py)**

```python
from netgear_switch.models import VLANInfo, VlanMode

Action = tuple


def diff(current_vlans: list[VLANInfo], current_pvids: dict[int, int],
         desired: DesiredState) -> list[Action]:
    """Actions to converge, in an order that never strands a port:
    1. create missing VLANs
    2. trunk tagged memberships (path to gateway exists first)
    3. access untagged memberships
    4. access PVIDs (port must already be a member)
    5. access-port VLAN 1 exclusions (only after PVID moved off 1)
    6. delete stale VLANs in OWN_RANGE
    Only VLANs in OWN_RANGE (plus owned ports' VLAN 1 membership) are
    ever written; everything else on the switch is invisible to us.
    """
    by_id = {v.vlan_id: v for v in current_vlans}
    creates: list[Action] = []
    trunk: list[Action] = []
    access: list[Action] = []
    pvids: list[Action] = []
    vlan1: list[Action] = []
    deletes: list[Action] = []

    for vid, name in sorted(desired.vlans.items()):
        if vid not in by_id:
            creates.append(("create_vlan", vid, name))

    for port, vids in sorted(desired.tagged.items()):
        for vid in sorted(vids):
            cur = by_id.get(vid)
            if cur is None or port not in cur.tagged_ports:
                trunk.append(("membership", vid, port, VlanMode.TAGGED))

    for port, vid in sorted(desired.untagged.items()):
        cur = by_id.get(vid)
        if cur is None or port not in cur.untagged_ports:
            access.append(("membership", vid, port, VlanMode.UNTAGGED))

    for port, vid in sorted(desired.pvids.items()):
        if current_pvids.get(port) != vid:
            pvids.append(("pvid", port, vid))

    v1 = by_id.get(1)
    for port in sorted(desired.vlan1_excluded):
        if v1 is not None and port in v1.member_ports:
            vlan1.append(("membership", 1, port, VlanMode.EXCLUDED))

    for vid in sorted(by_id):
        if vid in OWN_RANGE and vid not in desired.vlans:
            deletes.append(("delete_vlan", vid))

    return creates + trunk + access + pvids + vlan1 + deletes
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/switch_setup/plan.py tests/test_plan.py
git commit -m "feat: switch_setup diff engine with safe apply ordering"
```

### Task 5: apply module + mock-switch integration test

**Files:**
- Create: `src/switch_setup/apply.py`
- Test: `tests/test_apply.py`
- Modify: `.github/workflows/build.yml` (add a pytest job installing `snmp` apt package)

**Interfaces:**
- Consumes: Task 3/4 (`desired_state`, `diff`, `Action`); `netgear_switch`: `SyncSwitch`, `get_model`, `NetsnmpCliClient` (from `netgear_switch.transport.sync.snmp_netsnmp_cli`), `VirtualSwitch` (from `netgear_switch.virtual.server`).
- Produces: `converge(sw: SyncSwitch, specs: list[SwitchSpec], index: int, *, apply: bool) -> list[Action]` — returns pending actions; executes them only when `apply=True`.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_apply.py
"""Integration tests against the library's in-process virtual switch.

Requires the net-snmp CLI tools (apt: snmp) for NetsnmpCliClient.
"""

import pytest
from netgear_switch import SyncSwitch, get_model
from netgear_switch.transport.sync.snmp_netsnmp_cli import NetsnmpCliClient
from netgear_switch.virtual.server import VirtualSwitch

from switch_setup.apply import converge
from switch_setup.plan import SwitchSpec

SPECS = [SwitchSpec(index=2, model="gsm7252ps", mgmt_host="unused",
                    access_ports=8, gateway_trunk_port=49,
                    downstream_trunk_ports=(50,), house_uplink_port=52)]


@pytest.fixture()
def mock_switch():
    vs = VirtualSwitch("gsm7252ps")
    vs.start()
    client = NetsnmpCliClient(f"{vs.host}:{vs.port}", vs.community)
    sw = SyncSwitch(get_model("gsm7252ps"), vs.host,
                    snmp_community=vs.community,
                    snmp_client=client, snmp_write_client=client)
    yield sw
    vs.stop()


def test_converge_then_idempotent(mock_switch):
    actions = converge(mock_switch, SPECS, 2, apply=True)
    assert actions  # first run had work to do
    vlans = {v.vlan_id: v for v in mock_switch.get_vlans()}
    assert vlans[2203].untagged_ports == frozenset({3})
    assert 49 in vlans[2203].tagged_ports
    assert dict(mock_switch.get_pvids())[3] == 2203
    assert 3 not in vlans[1].member_ports
    # second run: nothing left to do
    assert converge(mock_switch, SPECS, 2, apply=True) == []


def test_check_mode_writes_nothing(mock_switch):
    before = mock_switch.get_vlans()
    actions = converge(mock_switch, SPECS, 2, apply=False)
    assert actions
    assert mock_switch.get_vlans() == before
```

- [ ] **Step 2: Run to verify failure**

Run: `sudo apt-get install -y snmp` (once), then `uv run pytest tests/test_apply.py -v`
Expected: FAIL (No module named 'switch_setup.apply')

- [ ] **Step 3: Implement `apply.py`**

```python
"""Read switch state, diff against desired, optionally apply."""

from netgear_switch import SyncSwitch
from netgear_switch.models import VlanMode

from .plan import Action, SwitchSpec, desired_state, diff


def converge(sw: SyncSwitch, specs: list[SwitchSpec], index: int,
             *, apply: bool) -> list[Action]:
    desired = desired_state(specs, index)
    actions = diff(sw.get_vlans(), dict(sw.get_pvids()), desired)
    if not apply:
        return actions
    for act in actions:
        match act:
            case ("create_vlan", vid, name):
                sw.create_vlan(vid, name)
            case ("membership", vid, port, mode):
                sw.set_vlan_membership(vid, port, mode, force=True)
            case ("pvid", port, vid):
                sw.set_pvid(port, vid, force=True)
            case ("delete_vlan", vid):
                sw.delete_vlan(vid, force=True)
    return actions
```

Note: `force=True` because the library's safety rails guard interactive misuse; here the diff engine is the guard, and access ports intentionally leave VLAN 1.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -v`
Expected: all pass

- [ ] **Step 5: Add CI test job**

In `.github/workflows/build.yml` add a job (mirror the existing job's checkout/uv setup steps):

```yaml
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8.1.0
      - run: sudo apt-get update && sudo apt-get install -y snmp
      - run: uv sync --dev
      - run: uv run pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add src/switch_setup/apply.py tests/test_apply.py .github/workflows/build.yml
git commit -m "feat: switch_setup converge with mock-switch integration tests"
```

### Task 6: CLI entry point

**Files:**
- Create: `src/switch_setup/cli.py`
- Test: `tests/test_cli.py`
- Modify: `README.md` (usage section)

**Interfaces:**
- Consumes: Task 5 `converge`; YAML config matching the infra host_vars `switches:` schema (keys: `index, model, mgmt_host, access_ports, gateway_trunk_port, downstream_trunk_ports, house_uplink_port`).
- Produces: console script `fpgas-switch-setup --config FILE --switch N [--apply] [--community STR] [--host HOST]`. Exit codes: 0 = in sync / applied cleanly, 2 = drift found in check mode (Ansible `changed_when: rc == 2`), 1 = error. Community defaults to env `FPGAS_SWITCH_COMMUNITY`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import subprocess
import textwrap

from netgear_switch.virtual.server import VirtualSwitch


def run_cli(*args, env_extra=None):
    import os
    env = os.environ | (env_extra or {})
    return subprocess.run(["uv", "run", "fpgas-switch-setup", *args],
                          capture_output=True, text=True, env=env)


def test_check_then_apply_then_clean(tmp_path):
    vs = VirtualSwitch("gsm7252ps")
    vs.start()
    try:
        cfg = tmp_path / "switches.yml"
        cfg.write_text(textwrap.dedent(f"""
            switches:
              - index: 2
                model: gsm7252ps
                mgmt_host: {vs.host}:{vs.port}
                access_ports: 4
                gateway_trunk_port: 49
                downstream_trunk_ports: [50]
                house_uplink_port: 52
        """))
        base = ["--config", str(cfg), "--switch", "2"]
        env = {"FPGAS_SWITCH_COMMUNITY": vs.community}
        r = run_cli(*base, env_extra=env)
        assert r.returncode == 2, r.stderr          # drift in check mode
        r = run_cli(*base, "--apply", env_extra=env)
        assert r.returncode == 0, r.stderr
        r = run_cli(*base, env_extra=env)
        assert r.returncode == 0, r.stderr          # now in sync
    finally:
        vs.stop()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL (command errors — cli module missing)

- [ ] **Step 3: Implement `cli.py`**

```python
"""fpgas-switch-setup: converge Netgear switch VLAN config for fpgas.online.

Check mode (default) prints the pending actions and exits 2 if there are
any; --apply executes them. Only VLANs 2101-2348 and the owned ports'
VLAN 1 membership are ever written (see plan.diff).
"""

import argparse
import os
import sys

import yaml
from netgear_switch import SyncSwitch, get_model

from .apply import converge
from .plan import SwitchSpec


def load_specs(path: str) -> list[SwitchSpec]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return [SwitchSpec(
        index=s["index"], model=s["model"], mgmt_host=s["mgmt_host"],
        access_ports=s["access_ports"],
        gateway_trunk_port=s["gateway_trunk_port"],
        downstream_trunk_ports=tuple(s["downstream_trunk_ports"]),
        house_uplink_port=s["house_uplink_port"],
    ) for s in data["switches"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--switch", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--host", help="override mgmt_host from config")
    ap.add_argument("--community",
                    default=os.environ.get("FPGAS_SWITCH_COMMUNITY"))
    args = ap.parse_args()
    if not args.community:
        print("no SNMP community (--community or FPGAS_SWITCH_COMMUNITY)",
              file=sys.stderr)
        return 1
    specs = load_specs(args.config)
    spec = next(s for s in specs if s.index == args.switch)
    sw = SyncSwitch(get_model(spec.model), args.host or spec.mgmt_host,
                    snmp_community=args.community)
    actions = converge(sw, specs, args.switch, apply=args.apply)
    for act in actions:
        print(" ".join(str(a) for a in act))
    if actions and not args.apply:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: all pass

- [ ] **Step 5: Add a README section**

Append to `README.md`: a `## fpgas-switch-setup` section with the CLI synopsis from the Interfaces block above, the exit-code contract, and one example invocation against `ngsw serve`.

- [ ] **Step 6: Commit + push branch**

```bash
git add src/switch_setup/cli.py tests/test_cli.py README.md
git commit -m "feat: fpgas-switch-setup CLI with check/apply modes"
git push -u origin switch-setup
```

---

## Phase 2 — Gateway roles (fpgas.online-infra, branch `vlan-per-port-network`)

### Task 7: port-map filter plugin + host_vars schema

**Files:**
- Create: `ansible/filter_plugins/port_vlans.py`
- Test: `tests/test_port_vlans.py`
- Modify: `ansible/inventory/host_vars/fpgas.online.yml`, `pyproject.toml` (dev group: pytest)

**Interfaces:**
- Produces: Jinja filter `switches | port_vlan_map(pib_network, pib_network6_base)` → `list[dict]` with keys `switch, port, vlan, iface, ip4, ip6, hostname` — consumed by Tasks 8, 9, 10. host_vars gain: `pib_network: "10.21"`, `pib_network6_base: "2404:e80:a137:21"`, `pib_domain: fpgas.welland.mithis.com`, and the `switches:` list (spec schema).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_port_vlans.py
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "port_vlans", Path("ansible/filter_plugins/port_vlans.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SWITCHES = [
    {"index": 1, "model": "s3300", "mgmt_host": "10.1.5.11",
     "access_ports": 48, "gateway_trunk_port": 49,
     "downstream_trunk_ports": [50], "house_uplink_port": 52},
    {"index": 2, "model": "gsm7252ps", "mgmt_host": "10.1.5.23",
     "access_ports": 48, "gateway_trunk_port": 49,
     "downstream_trunk_ports": [50], "house_uplink_port": 52},
]


def test_port_vlan_map():
    out = mod.port_vlan_map(SWITCHES, "10.21", "2404:e80:a137:21")
    assert len(out) == 96
    e = next(x for x in out if x["switch"] == 1 and x["port"] == 7)
    assert e == {"switch": 1, "port": 7, "vlan": 2107, "iface": "v2107",
                 "ip4": "10.21.1.7", "ip6": "2404:e80:a137:2101::7",
                 "hostname": "pi-sw1-p7"}
    e2 = next(x for x in out if x["switch"] == 2 and x["port"] == 48)
    assert e2["ip6"] == "2404:e80:a137:2102::48"
    assert e2["vlan"] == 2248 and e2["ip4"] == "10.21.2.48"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_port_vlans.py -v` (add `pytest` to a `[dependency-groups] dev` in pyproject.toml first, `uv sync --dev`)
Expected: FAIL (file not found)

- [ ] **Step 3: Implement the filter**

```python
# ansible/filter_plugins/port_vlans.py
"""Derive the per-port VLAN/IP/hostname map from the switches list.

Single source of truth for the formulas on the Ansible side (the
fpgas-switch-setup CLI holds the same formulas for the switch side).
Spec: docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md
"""


def port_vlan_map(switches, pib_network, pib_network6_base):
    out = []
    for sw in switches:
        s = sw["index"]
        for p in range(1, sw["access_ports"] + 1):
            vlan = 2000 + 100 * s + p
            out.append({
                "switch": s,
                "port": p,
                "vlan": vlan,
                "iface": f"v{vlan}",
                "ip4": f"{pib_network}.{s}.{p}",
                "ip6": f"{pib_network6_base}{s:02d}::{p}",
                "hostname": f"pi-sw{s}-p{p}",
            })
    return out


class FilterModule:
    def filters(self):
        return {"port_vlan_map": port_vlan_map}
```

- [ ] **Step 4: Run test — expect pass. Then update host_vars**

In `ansible/inventory/host_vars/fpgas.online.yml`: change `pib_network` from `10.21.0` to `10.21` (Task 9/10 update its consumers — until they land, `site.yml` is red; that is fine mid-branch); add `pib_network6_base`, `pib_domain`, and the `switches:` list (switch 1 = s3300 @ 10.1.5.11, switch 2 = gsm7252ps @ 10.1.5.23, both `access_ports: 48, gateway_trunk_port: 49, downstream_trunk_ports: [50], house_uplink_port: 52` — Tim corrects real trunk cabling at prototype time). Keep the existing `switch:` block (PoE SNMP creds) but delete its `nos:` list only in Task 10 when the last consumer goes.

- [ ] **Step 5: Lint + commit**

Run: `uv run yamllint ansible/` — expect clean.

```bash
git add ansible/filter_plugins/ tests/test_port_vlans.py pyproject.toml ansible/inventory/host_vars/fpgas.online.yml
git commit -m "feat: port_vlan_map filter plugin and switches host_vars schema"
```

### Task 8: `vlan-ports` role (networkd)

**Files:**
- Create: `ansible/roles/vlan-ports/tasks/main.yml`, `ansible/roles/vlan-ports/templates/vlan.netdev.j2`, `ansible/roles/vlan-ports/templates/vlan.network.j2`, `ansible/roles/vlan-ports/templates/eth-local.network.j2`, `ansible/roles/vlan-ports/handlers/main.yml`
- Modify: `ansible/site.yml` (add `vlan-ports` to the `nbp` role list, after `firewall`)

**Interfaces:**
- Consumes: Task 7 filter + host_vars; Task 1 `tests/lab/RESULTS.md` (**read it first** — if the lab needed `10.21.0.1/16 noprefixroute` instead of `/32`, or radvd, template that instead).
- Produces: interfaces `v<VLAN>` live on tweed; `eth-local` = `10.21.0.1/24`; handler `reload networkd`.

- [ ] **Step 1: Write templates**

`vlan.netdev.j2`:

```ini
# {{ ansible_managed }}
[NetDev]
Name={{ item.iface }}
Kind=vlan

[VLAN]
Id={{ item.vlan }}
```

`vlan.network.j2` (adjust per lab RESULTS.md):

```ini
# {{ ansible_managed }}
# {{ item.hostname }}: switch {{ item.switch }} port {{ item.port }}
[Match]
Name={{ item.iface }}

[Network]
IPv4ProxyARP=true
IPv6AcceptRA=no
ConfigureWithoutCarrier=yes

[Address]
Address=10.21.0.1/32

[Address]
Address={{ pib_network6_base }}00::1/128

[Route]
Destination={{ item.ip4 }}/32

[Route]
Destination={{ item.ip6 }}/128
```

`eth-local.network.j2`:

```ini
# {{ ansible_managed }}
[Match]
Name=eth-local
Type=ether

[Link]
RequiredForOnline=yes

[Network]
Description=FPGA per-port VLAN trunk (untagged = VLAN 1 quarantine)
{% for e in switches | port_vlan_map(pib_network, pib_network6_base) %}
VLAN={{ e.iface }}
{% endfor %}

[Address]
Address=10.21.0.1/24
```

- [ ] **Step 2: Write tasks + handler**

`tasks/main.yml`:

```yaml
---
- name: compute port vlan map
  set_fact:
    port_map: "{{ switches | port_vlan_map(pib_network, pib_network6_base) }}"
  tags: [vlan-ports]

- name: create vlan netdevs
  template:
    src: vlan.netdev.j2
    dest: "/etc/systemd/network/40-{{ item.iface }}.netdev"
  loop: "{{ port_map }}"
  loop_control: {label: "{{ item.iface }}"}
  notify: reload networkd
  tags: [vlan-ports]

- name: create vlan networks
  template:
    src: vlan.network.j2
    dest: "/etc/systemd/network/40-{{ item.iface }}.network"
  loop: "{{ port_map }}"
  loop_control: {label: "{{ item.iface }}"}
  notify: reload networkd
  tags: [vlan-ports]

- name: configure eth-local trunk
  template:
    src: eth-local.network.j2
    dest: /etc/systemd/network/30-eth-local.network
  notify: reload networkd
  tags: [vlan-ports]

- name: find stale vlan config files
  find:
    paths: /etc/systemd/network
    patterns: "40-v*"
  register: vlan_files
  tags: [vlan-ports]

- name: remove stale vlan config files
  file:
    path: "{{ item.path }}"
    state: absent
  loop: "{{ vlan_files.files }}"
  loop_control: {label: "{{ item.path }}"}
  when: >-
    (item.path | basename | regex_replace('^40-(v\\d+)\\..*$', '\\1'))
    not in (port_map | map(attribute='iface') | list)
  notify: reload networkd
  tags: [vlan-ports]
```

`handlers/main.yml`:

```yaml
---
- name: reload networkd
  command: networkctl reload
```

- [ ] **Step 3: Syntax check + lint**

Run: `uv run ansible-playbook ansible/site.yml --syntax-check && uv run yamllint ansible/`
Expected: both clean

- [ ] **Step 4: Commit**

```bash
git add ansible/roles/vlan-ports ansible/site.yml
git commit -m "feat: vlan-ports role generating per-port networkd config"
```

### Task 9: pxe role rework (dnsmasq)

**Files:**
- Create: `ansible/roles/pxe/templates/ports.conf.j2`
- Modify: `ansible/roles/pxe/tasks/main.yml`, `ansible/roles/pxe/templates/dnsmasq-base.conf.j2`
- Delete: `ansible/roles/pxe/templates/pibs.conf.j2`, `ansible/roles/pxe/templates/switch.conf.j2`

**Interfaces:**
- Consumes: Task 7 filter + host_vars; Task 1 `RESULTS.md` (RA/DHCPv6 exact syntax).
- Produces: `/etc/dnsmasq.d/ports.conf` on tweed; removal of `/etc/dnsmasq.d/pibs.conf` and `/etc/dnsmasq.d/switch.conf`.

- [ ] **Step 1: Write `ports.conf.j2`**

```jinja
# {{ ansible_managed }}
# Per-port addressing: the dhcp-range tag is the receiving interface name
# (dnsmasq sets a tag named after the interface a request arrived on).
# No MAC addresses appear anywhere in this file by design.
{% for e in switches | port_vlan_map(pib_network, pib_network6_base) %}
interface={{ e.iface }}
dhcp-range=tag:{{ e.iface }},{{ e.ip4 }},{{ e.ip4 }},255.255.0.0,12h
dhcp-range=tag:{{ e.iface }},{{ e.ip6 }},{{ e.ip6 }},64,12h
host-record={{ e.hostname }},{{ e.hostname }}.{{ pib_domain }},{{ e.ip4 }},{{ e.ip6 }}
{% endfor %}

# VLAN 1 quarantine on the untagged trunk: unconfigured ports land here,
# visibly, instead of joining a network.
dhcp-range=tag:eth-local,10.21.0.128,10.21.0.150,255.255.255.0,1h

enable-ra
```

- [ ] **Step 2: Update `dnsmasq-base.conf.j2` and tasks**

In `dnsmasq-base.conf.j2`: keep `interface={{ eth_local }}` and `bind-interfaces`; DELETE the bare `dhcp-range={{ dhcp_range }}` line (ranges now live in ports.conf); keep TFTP + logging + DNS lines.

In `tasks/main.yml`: replace the `pibs.conf` and `switch.conf` template tasks with:

```yaml
- name: create dnsmasq.d ports.conf
  template:
    src: templates/ports.conf.j2
    dest: /etc/dnsmasq.d/ports.conf
  notify: restart dnsmasq
  tags: [pibs]

- name: remove legacy MAC-table configs
  file:
    path: "/etc/dnsmasq.d/{{ item }}"
    state: absent
  loop: [pibs.conf, switch.conf]
  notify: restart dnsmasq
```

Then `git rm ansible/roles/pxe/templates/pibs.conf.j2 ansible/roles/pxe/templates/switch.conf.j2`. Also delete the now-unused `dhcp_range` var from `ansible/inventory/host_vars/fpgas.online.yml`.

- [ ] **Step 3: Render check**

Run: `uv run ansible-playbook ansible/site.yml --syntax-check && uv run yamllint ansible/`
Expected: clean. Also render the template locally against the test in Task 13's inventory once it exists; for now eyeball one stanza via `uv run ansible -m template ...` is NOT needed — the VM test covers it.

- [ ] **Step 4: Commit**

```bash
git add -A ansible/roles/pxe ansible/inventory/host_vars/fpgas.online.yml
git commit -m "feat: pxe role emits per-port dnsmasq config, drops MAC tables"
```

### Task 10: firewall role rework (nftables)

**Files:**
- Modify: `ansible/roles/firewall/templates/nftables.conf.j2`, `ansible/inventory/host_vars/fpgas.online.yml` (drop `switch.nos`)

**Interfaces:**
- Consumes: Task 7 filter. External DNAT port pattern: SSH `<s><pp>22 -> ip4:22`, aux `<s><pp>44 -> ip4:4444` (sw1 p7 → 10722/10744, preserving today's switch-1 numbers; sw2 p7 → 20722).
- Produces: forward chain default-drop with Pi↔Pi denied; NAT for `v*`.

- [ ] **Step 1: Rewrite the affected template sections**

Forward chain (replace the existing `chain forward`):

```
  chain forward {
    type filter hook forward priority 0; policy drop;
    ct state established,related counter accept;
    ct status dnat counter accept;
    iifname "v*" oifname "eth-uplink" counter accept;
    iifname "eth-local" oifname "eth-uplink" counter accept;
    # v* <-> v* (Pi to Pi) intentionally falls through to policy drop.
  }
```

Masquerade (replace the `iifname "eth-local"` rule):

```
        iifname {"eth-local", "v*"} ip saddr {{ pib_network }}.0.0/16 oifname "eth-uplink" counter masquerade;
```

`internal_networks` chain: change to `ip saddr {{ pib_network }}.0.0/16 counter accept;` (pib_network is now `10.21`).

Prerouting: delete the two `.200` switch-management DNATs (management is on the house net now) and replace the `switch.nos` loop with:

```jinja
{% for e in switches | port_vlan_map(pib_network, pib_network6_base) %}
            ip daddr {{ eth_uplink_static_address }} tcp dport { {{ e.switch }}{{ '%02d' | format(e.port) }}22 } dnat to {{ e.ip4 }}:22
            ip daddr {{ eth_uplink_static_address }} tcp dport { {{ e.switch }}{{ '%02d' | format(e.port) }}44 } dnat to {{ e.ip4 }}:4444
{% endfor %}
```

Then delete the `nos:` list (and `mpi_port`/`mpi_ip`) from `switch:` in `ansible/inventory/host_vars/fpgas.online.yml`, keeping host/oid/SNMP creds for PoE control.

- [ ] **Step 2: Validate rendered ruleset**

Run: `uv run ansible-playbook ansible/site.yml --syntax-check`; then on tweed after first deploy: `nft -c -f /etc/nftables.conf` (checked in Task 14 runbook — record here that `-c` is the validation gate before reload).
Expected: syntax check clean.

- [ ] **Step 3: Commit**

```bash
git add ansible/roles/firewall ansible/inventory/host_vars/fpgas.online.yml
git commit -m "feat: firewall isolates per-port VLANs, port-forwards by switch/port"
```

### Task 11: `switch-vlans` role + verify-server additions

**Files:**
- Create: `ansible/roles/switch-vlans/tasks/main.yml`, `ansible/roles/switch-vlans/templates/switches.yml.j2`
- Modify: `ansible/site.yml` (add role to `nbp`, gated), `ansible/verify-server.yml`

**Interfaces:**
- Consumes: Task 6 CLI contract (exit 0/2, `--config --switch --apply`, env `FPGAS_SWITCH_COMMUNITY`); host_vars `switches:`; new vault var `switch_snmp_rw_community` (Tim adds the real value with `ansible-vault`).
- Produces: `/etc/fpgas/switches.yml` on the server; converged switches on each `site.yml` run; `switches_manage: false` skips the role (used by the VM test).

- [ ] **Step 1: Write the role**

`templates/switches.yml.j2`:

```jinja
# {{ ansible_managed }}
switches:
{% for sw in switches %}
  - index: {{ sw.index }}
    model: {{ sw.model }}
    mgmt_host: {{ sw.mgmt_host }}
    access_ports: {{ sw.access_ports }}
    gateway_trunk_port: {{ sw.gateway_trunk_port }}
    downstream_trunk_ports: {{ sw.downstream_trunk_ports }}
    house_uplink_port: {{ sw.house_uplink_port }}
{% endfor %}
```

`tasks/main.yml`:

```yaml
---
- name: install switches config
  template:
    src: switches.yml.j2
    dest: /etc/fpgas/switches.yml
  tags: [switch-vlans]

- name: converge switch VLAN config
  command: >-
    fpgas-switch-setup --config /etc/fpgas/switches.yml
    --switch {{ item.index }} --apply
  loop: "{{ switches }}"
  loop_control: {label: "switch {{ item.index }} ({{ item.mgmt_host }})"}
  environment:
    FPGAS_SWITCH_COMMUNITY: "{{ switch_snmp_rw_community }}"
  register: converge_result
  changed_when: converge_result.stdout | length > 0
  tags: [switch-vlans]
```

Also add a `file: {path: /etc/fpgas, state: directory}` task before the template. In `site.yml` add to the `nbp` play roles: `- {role: switch-vlans, when: switches_manage | default(true)}`.

- [ ] **Step 2: verify-server.yml additions**

Append assertions: `networkctl list` output contains `v2101`; `dnsmasq --test` exits 0; `nft list chain inet filter forward` contains `policy drop`; `/etc/dnsmasq.d/pibs.conf` absent. Model each as `command:` + `assert:`, matching the existing style in `verify-server.yml` (read it first and copy its idiom).

- [ ] **Step 3: Lint + syntax check + commit**

Run: `uv run yamllint ansible/ && uv run ansible-playbook ansible/site.yml --syntax-check && uv run ansible-playbook ansible/verify-server.yml --syntax-check`

```bash
git add ansible/roles/switch-vlans ansible/site.yml ansible/verify-server.yml
git commit -m "feat: switch-vlans role converges switches on deploy"
```

---

## Phase 3 — VM end-to-end test (fpgas.online-infra)

### Task 12: userspace access-port switch for QEMU

The VM harness links VMs with QEMU `-netdev socket` (4-byte big-endian length-prefixed ethernet frames over TCP). A small pump emulates one access port: tags untagged Pi frames into VLAN 2101 toward the server, untags 2101 toward the Pi. This is harness code, not an Ansible workaround — the Ansible roles stay identical to production.

**Files:**
- Create: `tests/vm/vswitch.py`
- Test: `tests/test_vswitch.py`

**Interfaces:**
- Produces: `AccessPortSwitch(vlan: int, host="127.0.0.1")` with `.start() -> None` (binds two listening TCP ports, readable as `.trunk_port`/`.access_port` attributes), `.stop()`; pure helpers `tag_frame(frame: bytes, vlan: int) -> bytes`, `untag_frame(frame: bytes, vlan: int) -> bytes | None`.

- [ ] **Step 1: Write the failing unit tests**

```python
# tests/test_vswitch.py
from tests.vm.vswitch import tag_frame, untag_frame

DST = bytes(6)
SRC = bytes.fromhex("525400aabb02")
PAYLOAD = b"\x08\x00" + b"x" * 46  # ethertype IPv4 + body


def test_tag_inserts_8021q_after_src_mac():
    tagged = tag_frame(DST + SRC + PAYLOAD, 2101)
    assert tagged[:12] == DST + SRC
    assert tagged[12:14] == b"\x81\x00"
    assert int.from_bytes(tagged[14:16], "big") & 0x0FFF == 2101
    assert tagged[16:] == PAYLOAD


def test_untag_roundtrip_and_foreign_vlan_dropped():
    frame = DST + SRC + PAYLOAD
    assert untag_frame(tag_frame(frame, 2101), 2101) == frame
    assert untag_frame(tag_frame(frame, 2102), 2101) is None
    assert untag_frame(frame, 2101) is None  # untagged on trunk -> drop
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_vswitch.py -v`
Expected: FAIL (no module tests.vm.vswitch)

- [ ] **Step 3: Implement `vswitch.py`**

```python
"""Emulate one switch access port between two QEMU socket netdevs.

QEMU 'socket' netdev framing: 4-byte big-endian length + raw ethernet
frame. The Pi VM connects to .access_port (untagged), the server VM to
.trunk_port (tagged). Frames crossing access->trunk gain an 802.1Q tag;
trunk->access frames are untagged if the VID matches, dropped otherwise
-- exactly what a hardware access port does.
"""

import socket
import struct
import threading

TPID = b"\x81\x00"


def tag_frame(frame: bytes, vlan: int) -> bytes:
    return frame[:12] + TPID + struct.pack("!H", vlan & 0x0FFF) + frame[12:]


def untag_frame(frame: bytes, vlan: int) -> bytes | None:
    if frame[12:14] != TPID:
        return None
    if int.from_bytes(frame[14:16], "big") & 0x0FFF != vlan:
        return None
    return frame[:12] + frame[16:]


class AccessPortSwitch:
    def __init__(self, vlan: int, host: str = "127.0.0.1"):
        self.vlan = vlan
        self.host = host
        self._socks: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.trunk_port = 0
        self.access_port = 0

    def _listen(self) -> socket.socket:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, 0))
        s.listen(1)
        self._socks.append(s)
        return s

    def start(self) -> None:
        trunk_l, access_l = self._listen(), self._listen()
        self.trunk_port = trunk_l.getsockname()[1]
        self.access_port = access_l.getsockname()[1]
        t = threading.Thread(target=self._run, args=(trunk_l, access_l),
                             daemon=True)
        t.start()
        self._threads.append(t)

    def _run(self, trunk_l, access_l) -> None:
        trunk, _ = trunk_l.accept()
        access, _ = access_l.accept()
        self._socks += [trunk, access]

        def pump(src, dst, xform):
            try:
                while not self._stop.is_set():
                    hdr = self._recvall(src, 4)
                    frame = self._recvall(src, struct.unpack("!I", hdr)[0])
                    out = xform(frame)
                    if out is not None:
                        dst.sendall(struct.pack("!I", len(out)) + out)
            except (OSError, ConnectionError):
                pass

        a = threading.Thread(
            target=pump, args=(access, trunk, lambda f: tag_frame(f, self.vlan)),
            daemon=True)
        b = threading.Thread(
            target=pump, args=(trunk, access, lambda f: untag_frame(f, self.vlan)),
            daemon=True)
        a.start(); b.start()
        self._threads += [a, b]

    @staticmethod
    def _recvall(sock, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("peer closed")
            buf += chunk
        return buf

    def stop(self) -> None:
        self._stop.set()
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
```

- [ ] **Step 4: Run tests — expect pass. Commit**

```bash
git add tests/vm/vswitch.py tests/test_vswitch.py
git commit -m "test: userspace access-port switch for QEMU socket netdevs"
```

### Task 13: wire vswitch into the VM harness + update assertions

**Files:**
- Modify: `tests/vm/vm_manager.py` (server: `-netdev socket,id=net1,connect=:{trunk_port}`; Pi: `-nic socket,connect=:{access_port}`), `tests/vm/run_tests.py` (start `AccessPortSwitch(2101)` before VMs; drop the old `wait_for_socket_listen(VLAN_PORT)` sequencing — the vswitch listens before either VM starts), `tests/inventory/host_vars/test-vm.yml` and `tests/inventory/group_vars/all/*.yml` (add `pib_network: "10.21"`, `pib_network6_base`, `pib_domain`, `switches:` with one switch `index: 1, model: s3300, mgmt_host: 127.0.0.1, access_ports: 1, gateway_trunk_port: 49, downstream_trunk_ports: [], house_uplink_port: 52`, and `switches_manage: false`), `ansible/verify-pi.yml` (expect IP `10.21.1.1` and hostname `pi-sw1-p1` instead of `10.21.0.x`)

**Interfaces:**
- Consumes: Task 12 `AccessPortSwitch`; Tasks 8/9/10/11 roles via the unchanged `site.yml`.
- Produces: green end-to-end VM test proving DHCP-by-port, PXE boot over the tagged path, and the new addressing.

- [ ] **Step 1: Make the wiring changes** (read each file first; keep changes minimal and mirror existing style)

- [ ] **Step 2: Run the server phase**

Run: `uv run tests/vm/run_tests.py --phase server --keep-vm`
Expected: `site.yml` + `verify-server.yml` green — proves the roles converge and dnsmasq/networkd/nftables validate inside the VM.

- [ ] **Step 3: Run the full test**

Run: `uv run tests/vm/run_tests.py --phase all`
Expected: virtual Pi PXE-boots through the tagged path, `verify-pi.yml` passes with `10.21.1.1`/`pi-sw1-p1`. This is the plan's main pre-hardware gate; debug failures with the serial logs the harness saves.

- [ ] **Step 4: Commit + push, watch CI**

```bash
git add tests/ ansible/verify-pi.yml
git commit -m "test: VM harness boots virtual Pi through emulated access port"
git push -u origin vlan-per-port-network
```

CI `vm-test.yml` runs on the push (workflows run on all branches). Expected: green.

---

## Phase 4 — Hardware prototype

### Task 14: prototype runbook + execution

**Files:**
- Create: `docs/superpowers/specs/2026-08-14-vlan-per-port-prototype-runbook.md` (fpgas.online-infra)

**Interfaces:**
- Consumes: everything above; Tim for cabling and the RW SNMP community.

- [ ] **Step 1: Write the runbook** with these exact stages (each stage: command + expected output + rollback note):

1. **Preflight**: from tweed, `ping 10.1.5.23 && ping 10.1.5.11`; `snmpget` sysDescr on both (proves reachability + creds). Physical: cable s3300 (switch 1) trunk port 49 → tweed `eth-local`; s2 (switch 2) port 49 → s3300 port 50. Tim confirms/corrects actual trunk port numbers in host_vars.
2. **Capacity probes** (Task 2) against both switches if not already run.
3. **Switch converge**: `fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 1` (check mode; review action list), then `--apply`, then check again → exit 0. Repeat for switch 2. Rollback: the tool only owns 2101–2348, so `delete_vlan` of that range restores pre-state; VLAN 5/house config was never writable.
4. **Gateway deploy**: `uv run ansible-playbook ansible/site.yml --limit fpgas.online --tags vlan-ports,pxe,switch-vlans` then the firewall role; `nft -c -f` gate before reload; `uv run ansible-playbook ansible/verify-server.yml`.
5. **Boot test**: one Pi on s3300 port 1 (expect `10.21.1.1` / `pi-sw1-p1` / `2404:e80:a137:2101::1`), one on s2 port 1 (expect `10.21.2.1` / `pi-sw2-p1`). Check `dnsmasq.leases` and PXE/NFS boot to login.
6. **Isolation matrix** (from a Pi): ping other Pi v4+v6 → FAIL; ping/ssh tweed → OK; ping 8.8.8.8 → OK; `tcpdump` on the second Pi shows no direct traffic. From tweed: both Pis reachable.
7. **Port-identity test**: move a Pi from port 1 to port 3, renew DHCP (or reboot) → it becomes `pi-sw1-p3`/`10.21.1.3`.
8. **Quarantine test**: plug a laptop into an unconfigured port → lease in 10.21.0.128–150 appears in `dnsmasq.leases`.
9. Record all results (incl. any deviations) in the runbook file itself; deviations feed spec/plan amendments.

- [ ] **Step 2: Execute stages 1–4** (stop and report to Tim at any unexpected output)

- [ ] **Step 3: Execute stages 5–8 with Tim** (physical access needed)

- [ ] **Step 4: Commit the completed runbook with results**

```bash
git add docs/superpowers/specs/2026-08-14-vlan-per-port-prototype-runbook.md
git commit -m "docs: hardware prototype runbook with recorded results"
```

---

## Self-review notes

- Spec coverage: addressing/VLAN plan → Tasks 3/7; switch provisioning → 3–6, 11; gateway networkd → 8; DHCP/DNS/PXE + IPv6 → 9; firewall/isolation → 10; risks-first → 1/2; VM testing → 12/13; hardware prototype → 14. Production cutover + ps1: explicitly out of scope (spec Rollout steps 3–4 get a follow-up plan).
- The `pib_network` semantic change (`10.21.0` → `10.21`) breaks `site.yml` renders between Tasks 7 and 10 on the branch; Tasks 9/10 fix all consumers before anything is deployed. Search for remaining `pib_network` consumers (`grep -rn pib_network ansible/`) at the end of Task 10 — `switch.conf.j2` (deleted Task 9) and the nginx/site roles if any hit must be updated in the same commit.
