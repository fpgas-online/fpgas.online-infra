"""Netns lab: a real dnsmasq serving the real ports.conf.j2, real DHCP clients, and fpgas-release-port-lease
releasing one port's leases with dnsmasq's own tools (coord-56).

Topology (network namespaces; needs sudo, ip, dnsmasq-base, dnsmasq-utils, udhcpc and dhclient):

  ns "gw": v2101 <-> ns "pa": eth0      port 1 of switch 1
           v2102 <-> ns "pb": eth0      port 2 of switch 1

The gateway side is named like production's per-port interfaces (v<vlan>), holds 10.21.0.1/32 and the switch's
<base>01::ffff/64 on each (as roles/vlan_ports does), and runs
one dnsmasq with bind-dynamic, the rendered ports.conf and a lease file in a test directory. The release runs the
script's own release_port() with every dnsmasq-utils command started inside "gw" through sudo.

Runs in CI (lint.yml's pytest job sets LEASE_LAB_TEST=1 and installs the tools) and is skipped elsewhere.
"""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import jinja2
import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("LEASE_LAB_TEST") != "1",
    reason="needs sudo, dnsmasq, dnsmasq-utils, udhcpc and dhclient (set LEASE_LAB_TEST=1; runs in CI)")
TOOLS = ("/usr/sbin/dnsmasq", "/usr/bin/dhcp_release", "/usr/bin/dhcp_release6", "/usr/sbin/dhclient")

sys.path.insert(0, str(REPO / "ansible/filter_plugins"))
from port_vlans import port_vlan_map  # noqa: E402


def load_release():
    path = REPO / "ansible/roles/pxe/files/fpgas-release-port-lease"
    loader = importlib.machinery.SourceFileLoader("fpgas_release_port_lease", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


release = load_release()

PORTS = {"v2101": ("pa", "10.21.1.1", "2001:db8:a137:2101::1"),
         "v2102": ("pb", "10.21.1.2", "2001:db8:a137:2101::2")}
UDHCPC_SCRIPT = """#!/bin/sh
case "$1" in
  bound|renew) ip addr add "$ip"/32 dev "$interface" ;;
esac
exit 0
"""


def sudo(*argv, check=True, timeout=30):
    return subprocess.run(["sudo", *argv], capture_output=True, text=True, check=check, timeout=timeout)


def ns(name, *argv, check=True, timeout=30):
    return sudo("ip", "netns", "exec", name, *argv, check=check, timeout=timeout)


def render_ports_conf():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(REPO / "ansible/roles/pxe/templates"),
                             undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    env.filters["port_vlan_map"] = port_vlan_map
    return env.get_template("ports.conf.j2").render(
        ansible_managed="lab", switches=[{"index": 1, "access_ports": 2}], pib_network="10.21",
        pib_network6_base="2001:db8:a137:21", pib_domain="lab.example", eth_local="trunk0")


@pytest.fixture()
def lab(tmp_path):
    # asked for (LEASE_LAB_TEST=1) means every tool must be there: a missing one fails, it does not skip
    missing = [tool for tool in TOOLS if not Path(tool).exists()] + ([] if shutil.which("udhcpc") else ["udhcpc"])
    assert not missing, f"LEASE_LAB_TEST=1 but missing: {missing}"
    work = tmp_path / "lab"
    work.mkdir()
    (work / "ports.conf").write_text(render_ports_conf())
    (work / "lab.conf").write_text(f"port=0\nbind-dynamic\nlog-dhcp\ndhcp-leasefile={work}/leases\n"
                                   f"conf-file={work}/ports.conf\n")
    script = work / "udhcpc.sh"
    script.write_text(UDHCPC_SCRIPT)
    script.chmod(0o755)
    for name in ("gw", "pa", "pb"):
        sudo("ip", "netns", "del", name, check=False)
        sudo("ip", "netns", "add", name)
        ns(name, "ip", "link", "set", "lo", "up")
    for iface, (peer, _, _) in PORTS.items():
        sudo("ip", "link", "add", iface, "netns", "gw", "type", "veth", "peer", "name", "eth0", "netns", peer)
        ns("gw", "ip", "addr", "add", "10.21.0.1/32", "dev", iface)
        # as roles/vlan_ports' vlan.network.j2: every port interface of a switch has <base><switch>::ffff/64
        ns("gw", "ip", "addr", "add", "2001:db8:a137:2101::ffff/64", "dev", iface)
        ns("gw", "ip", "link", "set", iface, "up")
        ns(peer, "ip", "link", "set", "eth0", "up")
    ns("gw", "dnsmasq", f"--conf-file={work}/lab.conf", f"--pid-file={work}/pid", f"--log-facility={work}/log",
       "--user=root")
    time.sleep(2)  # DAD, so the link-local addresses DHCPv6 needs exist
    yield work, script
    if (work / "pid").exists():
        sudo("kill", (work / "pid").read_text().strip(), check=False)
    for name in ("pa", "pb"):
        pid = work / f"dhclient-{name}.pid"
        if pid.exists():
            sudo("kill", pid.read_text().strip(), check=False)
    for name in ("gw", "pa", "pb"):
        sudo("ip", "netns", "del", name, check=False)
    if (work / "log").exists():
        print((work / "log").read_text()[-4000:])


def take_leases(work, script, peer):
    ns(peer, "udhcpc", "-i", "eth0", "-n", "-q", "-f", "-t", "3", "-T", "2", "-s", str(script), check=False)
    ns(peer, "timeout", "10", "dhclient", "-6", "-1", "-pf", f"{work}/dhclient-{peer}.pid",
       "-lf", f"{work}/dhclient6-{peer}.leases", "eth0", check=False)


def leased(work):
    _, leases = release.parse_leases((work / "leases").read_text())
    return {str(x["address"]) for x in leases}


def test_one_ports_v4_and_v6_leases_are_released_and_the_other_ports_kept(lab):
    work, script = lab
    for peer in ("pa", "pb"):
        take_leases(work, script, peer)
    before = leased(work)
    want = {address for _, *addresses in PORTS.values() for address in addresses}
    assert want <= before, f"the clients did not get every lease: {before}"

    published, logged = [], []

    def run(argv, **kwargs):
        return subprocess.run(["sudo", "ip", "netns", "exec", "gw", *argv], **kwargs)

    released = release.release_port("v2101", "link-down", ports_conf=work / "ports.conf", lease_file=work / "leases",
                                    run=run, publish=published.append, log=lambda p, m: logged.append(m))
    assert released == 2
    after = leased(work)
    assert not ({"10.21.1.1", "2001:db8:a137:2101::1"} & after), after
    assert {"10.21.1.2", "2001:db8:a137:2101::2"} <= after
    assert [r["family"] for r in published] == [4, 6]
    assert "DHCPRELEASE" in (work / "log").read_text()


def test_the_command_line_refuses_an_interface_that_is_not_a_port(lab):
    done = sudo(sys.executable, "-I", str(REPO / "ansible/roles/pxe/files/fpgas-release-port-lease"),
                "eth0", "--cause", "link-down", check=False)
    assert done.returncode == 2, done.stderr
