"""The Pis' IPv6 (Tim's answer welland-1, 2026-10-07): a static address and route from the host name.

eth0 carries the NFS root, configured by the kernel over IPv4 (ip=dhcp). The board's
IPv6 address is the port_vlan_map formula, <base><switch, two digits>::<port>, and its
default route goes via the gateway's <base><switch, two digits>::ffff. These tests run
the script with stand-in `ip` and `hostname` commands, and fail if the address or the
routes it sets drift from the formula, if it acts on a host it should leave alone, or
if onpi, fixpi or verify-pi stop installing, feeding or checking it.

Not dhcpcd: dhcpcd 9.4.1 died at start on the VM test's 32-bit Pi (privilege
separation), and the gateway refreshes router advertisements on one interface per
switch prefix only (PR #251).
"""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ansible/roles/onpi/files/usr/local/sbin/fpgas-ipv6"
UNIT = REPO / "ansible/roles/onpi/files/etc/systemd/system/fpgas-ipv6.service"
TASKS = REPO / "ansible/roles/onpi/tasks/ipv6.yml"
ONPI_MAIN = REPO / "ansible/roles/onpi/tasks/main.yml"
FIXPI_SITE = REPO / "ansible/roles/fixpi/tasks/ipv6-site.yml"
FIXPI_MAIN = REPO / "ansible/roles/fixpi/tasks/main.yml"
VERIFY_PI = REPO / "ansible/verify-pi.yml"
BASE = "2404:e80:a137:21"


def _port_vlan_map_ip6(switch: int, port: int) -> str:
    spec = importlib.util.spec_from_file_location("pv", REPO / "ansible/filter_plugins/port_vlans.py")
    pv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pv)
    entries = pv.port_vlan_map([{"index": switch, "access_ports": port}], "10.21", BASE)
    return next(e["ip6"] for e in entries if e["port"] == port)


def _run(tmp_path: Path, hostname: str, base: str | None) -> tuple[str, list[str]]:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "ip.log"
    (stubs / "hostname").write_text(f"#!/bin/sh\necho {hostname}\n")
    (stubs / "ip").write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    for f in stubs.iterdir():
        f.chmod(0o755)
    base_file = tmp_path / "ipv6-base"
    if base is not None:
        base_file.write_text(base + "\n")
    env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", FPGAS_IPV6_BASE_FILE=str(base_file))
    out = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=True).stdout
    return out, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("switch,port", [(2, 43), (1, 7), (1, 40), (2, 3)])
def test_the_script_sets_the_port_vlan_map_address_and_the_gateway_route(tmp_path, switch, port):
    _, calls = _run(tmp_path, f"pi-sw{switch}-p{port}", BASE)
    address = _port_vlan_map_ip6(switch, port)
    gateway = f"{BASE}{switch:02d}::ffff"
    assert calls == [
        f"-6 addr replace {address}/128 dev eth0",
        f"-6 route replace {gateway}/128 dev eth0",
        f"-6 route replace default via {gateway} dev eth0 metric 512",
    ]


@pytest.mark.parametrize("hostname,base", [("pi-sw2-p43", None), ("raspberrypi", BASE), ("pi20", BASE)])
def test_the_script_leaves_other_hosts_alone(tmp_path, hostname, base):
    _, calls = _run(tmp_path, hostname, base)
    assert calls == []


def test_the_unit_is_a_oneshot_after_the_network_and_needs_the_base():
    unit = UNIT.read_text()
    assert "Type=oneshot" in unit and "RemainAfterExit=yes" in unit
    assert "After=network-online.target" in unit
    assert "ConditionPathExists=/etc/fpgas-online/ipv6-base" in unit
    assert "ExecStart=/usr/local/sbin/fpgas-ipv6" in unit
    assert "dhcpcd" not in TASKS.read_text()


def test_onpi_installs_it_and_fixpi_feeds_it_from_one_source():
    tasks = TASKS.read_text()
    assert "dest: /usr/local/sbin/fpgas-ipv6" in tasks
    assert "name: fpgas-ipv6.service" in tasks and "enabled: true" in tasks
    assert "include_tasks: ipv6.yml" in ONPI_MAIN.read_text()
    site = FIXPI_SITE.read_text()
    assert 'content: "{{ pib_network6_base }}\\n"' in site
    assert "include_tasks: ipv6-site.yml" in FIXPI_MAIN.read_text()


def test_verify_pi_checks_the_address_and_the_route():
    text = VERIFY_PI.read_text()
    assert '"ip6_default": run("ip", "-6", "route", "show", "default")' in text
    assert "Assert the per-port IPv6 address and an IPv6 default route" in text
