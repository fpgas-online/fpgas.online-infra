"""The Pis' IPv6 (Tim's answer welland-1, 2026-10-07): a static address and route from the host name.

One interface carries the NFS root, configured by the kernel over IPv4 (ip=dhcp);
its name varies (eth0, end0, ...: 7 of 20 live welland Pis had no eth0 on
2026-10-08), so the script uses the one carrying the port's IPv4 address, <net>.S.P. The board's
IPv6 address is the port_vlan_map formula, <base><switch, two digits>::<port>, and its
default route goes via the gateway's <base><switch, two digits>::ffff. These tests run
the script with stand-in `ip` and `hostname` commands, and fail if the address or the
routes it sets drift from the formula or go to any interface but that one, if it
guesses when there is not exactly one, if it acts on a host it should leave alone, or
if onpi, fixpi or verify-pi stop installing, feeding or checking it (on every per-port
Pi, whatever its interface is called).

Not dhcpcd: dhcpcd 9.4.1 died at start on the VM test's 32-bit Pi (privilege
separation), and the gateway refreshes router advertisements on one interface per
switch prefix only (PR #251).
"""

import importlib.util
import os
import re
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


def _ipv4(*pairs: tuple[str, str]) -> str:
    """`ip -4 -o addr show` output: one line per (interface, address)."""
    return "".join(f"2: {d}    inet {a}/16 brd 10.21.255.255 scope global {d}\\       valid_lft forever\n"
                   for d, a in pairs)


LO = ("lo", "127.0.0.1")


def _run(tmp_path: Path, hostname: str, base: str | None, ipv4: str | None = None,
         check: bool = True) -> tuple[str, list[str]]:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "ip.log"
    answer = tmp_path / "ipv4.txt"
    if ipv4 is None:  # the port's address on end0, as the kernel's ip=dhcp leaves it
        m = re.fullmatch(r"pi-sw(\d+)-p(\d+)", hostname)
        ipv4 = _ipv4(LO, ("end0", f"10.21.{int(m[1])}.{int(m[2])}" if m else "10.21.0.99"))
    answer.write_text(ipv4)
    (stubs / "hostname").write_text(f"#!/bin/sh\necho {hostname}\n")
    # The address query is answered and not logged: the log holds what the script sets.
    (stubs / "ip").write_text(
        "#!/bin/sh\n"
        f'if [ "$*" = "-4 -o addr show" ]; then cat {answer}; exit 0; fi\n'
        f'echo "$*" >> {log}\n')
    for f in stubs.iterdir():
        f.chmod(0o755)
    base_file = tmp_path / "ipv6-base"
    if base is not None:
        base_file.write_text(base + "\n")
    env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", FPGAS_IPV6_BASE_FILE=str(base_file))
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=check)
    return r.stdout + r.stderr, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("switch,port", [(2, 43), (1, 7), (1, 40), (2, 3)])
def test_the_script_sets_the_port_vlan_map_address_and_the_gateway_route(tmp_path, switch, port):
    _, calls = _run(tmp_path, f"pi-sw{switch}-p{port}", BASE)
    address = _port_vlan_map_ip6(switch, port)
    gateway = f"{BASE}{switch:02d}::ffff"
    assert calls == [
        f"-6 addr replace {address}/128 dev end0",
        f"-6 route replace {gateway}/128 dev end0",
        f"-6 route replace default via {gateway} dev end0 metric 512",
    ]


# eth-uplink: a Pi 3B+'s NIC as setup-pi's .link files name it (pi-sw1-p10, read 2026-10-08);
# eth0: a Pi 5's (pi-sw2-p43). Other interfaces with addresses of their own are ignored.
@pytest.mark.parametrize("dev", ["eth-uplink", "eth0", "enxb827eb123456"])
def test_the_script_uses_the_interface_with_the_ports_ipv4_address(tmp_path, dev):
    ipv4 = _ipv4(LO, ("usb0", "192.168.7.2"), (dev, "10.21.1.10"), ("wlan0", "10.21.9.10"))
    out, calls = _run(tmp_path, "pi-sw1-p10", BASE, ipv4)
    assert len(calls) == 3 and all(f"dev {dev}" in c for c in calls), calls
    assert out.startswith(f"{dev}: ")


@pytest.mark.parametrize("ipv4,names", [
    (_ipv4(LO), ["none"]),
    (_ipv4(("eth0", "10.21.2.10")), ["none"]),  # another port's address
    (_ipv4(("eth0", "10.21.1.10"), ("eth-uplink", "10.21.1.10")), ["eth0", "eth-uplink"]),
])
def test_the_script_refuses_to_guess_the_interface(tmp_path, ipv4, names):
    out, calls = _run(tmp_path, "pi-sw1-p10", BASE, ipv4, check=False)
    assert "expected one interface carrying the port's IPv4 address *.1.10, found: " in out, out
    found = out.split("found: ", 1)[1].split()
    assert sorted(found) == sorted(names), out
    assert calls == []


@pytest.mark.parametrize("hostname,base", [("pi-sw2-p43", None), ("raspberrypi", BASE), ("pi20", BASE)])
def test_the_script_leaves_other_hosts_alone(tmp_path, hostname, base):
    _, calls = _run(tmp_path, hostname, base)
    assert calls == []


def test_the_unit_is_a_oneshot_after_the_network_and_needs_the_base():
    unit = UNIT.read_text()
    assert "Type=oneshot" in unit and "RemainAfterExit=yes" in unit
    assert "After=network-online.target" in unit
    assert "ConditionPathExists=/etc/fpgas-online/ipv6-base" in unit
    assert "eth0" not in unit, "the unit must not depend on an interface name"
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
    # every per-port Pi is checked, whatever its interface is called
    assert "eth0" not in text.split("- name: Derive the expected per-port IPv6 address from hostname")[1].split("- name:")[1]
    assert "verify_pi_eth0" not in text
