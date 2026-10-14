"""The Pis' network under systemd-networkd (#230, Tim's answer coord-57, 14 Oct 2026).

The kernel's ip=dhcp configures the NFS root's interface before userspace;
fpgas-uplink-network then writes, before networkd starts, the .network that
hands that interface (and only it) to networkd: DHCPv4 that renews but never
drops the address (KeepConfiguration=yes, IgnoreCarrierLoss=yes), and the
port's static IPv6 address and default route (Tim's answer welland-1, the
port_vlan_map formula <base><switch, two digits>::<port>, gateway ::ffff).
Its name varies (eth0, eth-uplink, end0), so the script takes the interface
that routes to the NFS server on the kernel command line.

fpgas-network-state, run by networkd-dispatcher at every state change,
publishes that interface's state to the site on the fleet event topic.

These tests run both scripts against stand-ins (a stub `ip`, sample `ip -j`
output, a sample lease file) and fail if the generated file loses a setting
the NFS root depends on, if the IPv6 address drifts from port_vlan_map, if
either script guesses or acts on another interface, or if onpi, fixpi or
verify-pi stop installing, feeding or checking them.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ONPI = REPO / "ansible/roles/onpi"
GENERATOR = ONPI / "files/usr/local/sbin/fpgas-uplink-network"
GENERATOR_UNIT = ONPI / "files/etc/systemd/system/fpgas-uplink-network.service"
HOOK = ONPI / "files/usr/local/sbin/fpgas-network-state"
DISPATCHER_DROPIN = ONPI / "files/etc/systemd/system/networkd-dispatcher.service.d/fpgas-after-fleet-agent.conf"
TASKS = ONPI / "tasks/networkd.yml"
ONPI_MAIN = ONPI / "tasks/main.yml"
ONPI_DEFAULTS = ONPI / "defaults/main.yml"
FIXPI = REPO / "ansible/roles/fixpi"
VERIFY_PI = REPO / "ansible/verify-pi.yml"
BASE = "2404:e80:a137:21"
# fixpi's cmdline.txt.j2, as a Pi boots it
CMDLINE = ("root=/dev/nfs nfsroot=10.21.0.1:/srv/nfs/rpi/bookworm/root,nfsvers=3,tcp ro ip=dhcp rootwait "
           "overlayroot=tmpfs console=tty1\n")


def _port_vlan_map(switch: int, port: int) -> dict:
    spec = importlib.util.spec_from_file_location("pv", REPO / "ansible/filter_plugins/port_vlans.py")
    pv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pv)
    entries = pv.port_vlan_map([{"index": switch, "access_ports": port}], "10.21", BASE)
    return next(e for e in entries if e["port"] == port)


MAC = "88:a2:9e:45:dd:be"


def _generate(tmp_path: Path, route: str | None, base: str | None = BASE, cmdline: str = CMDLINE,
              check: bool = True, mac: str = MAC.upper()) -> tuple[str, str | None]:
    """Run the generator with a stub `ip` answering `-o -4 route get` with `route` (None: no route), and a
    stand-in /sys/class/net where every interface has the address `mac`."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    sysfs = tmp_path / "sys-class-net"
    for dev in ("eth0", "eth-uplink", "end0"):
        (sysfs / dev).mkdir(parents=True)
        (sysfs / dev / "address").write_text(mac + "\n")
        (sysfs / dev / "carrier_down_count").write_text("1\n")
        (sysfs / dev / "carrier_up_count").write_text("2\n")
        (sysfs / dev / "operstate").write_text("up\n")
    answer = tmp_path / "route.txt"
    answer.write_text(route or "")
    (stubs / "ip").write_text(
        "#!/bin/sh\n"
        '[ "$1 $2 $3 $4" = "-o -4 route get" ] || { echo "unexpected ip $*" >&2; exit 2; }\n'
        + (f"cat {answer}\n" if route is not None else 'echo "RTNETLINK answers: Network is unreachable" >&2; exit 2\n'))
    (stubs / "ip").chmod(0o755)
    base_file = tmp_path / "ipv6-base"
    if base is not None:
        base_file.write_text(base + "\n")
    cmdline_file = tmp_path / "cmdline"
    cmdline_file.write_text(cmdline)
    out_dir = tmp_path / "network"
    env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", FPGAS_IPV6_BASE_FILE=str(base_file),
               FPGAS_UPLINK_CMDLINE=str(cmdline_file), FPGAS_UPLINK_NETWORK_DIR=str(out_dir),
               FPGAS_UPLINK_SYSFS_NET=str(sysfs), FPGAS_UPLINK_CARRIER_FILE=str(tmp_path / "carrier"))
    r = subprocess.run(["bash", str(GENERATOR)], env=env, capture_output=True, text=True, check=check)
    written = out_dir / "10-fpgas-uplink.network"
    return r.stdout + r.stderr, written.read_text() if written.exists() else None


def _route(dev: str, src: str) -> str:
    return f"10.21.0.1 dev {dev} src {src} uid 0 \\    cache \n"


def _sections(network: str) -> dict[str, list[str]]:
    """{section: [key=value lines]}, in order; a repeated section gets a numbered key."""
    out, current = {}, None
    for line in network.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = line.strip("[]")
            n = 1
            while (current if n == 1 else f"{current}{n}") in out:
                n += 1
            current = current if n == 1 else f"{current}{n}"
            out[current] = []
        else:
            out[current].append(line)
    return out


# eth0: a Pi 4 or 5; eth-uplink: a Pi 3B+ as setup-pi's .link files name it; end0: a predictable name.
@pytest.mark.parametrize("dev", ["eth0", "eth-uplink", "end0"])
def test_the_file_matches_the_interface_that_routes_to_the_nfs_server_by_its_mac(tmp_path, dev):
    out, network = _generate(tmp_path, _route(dev, "10.21.2.43"))
    # by MAC (lower case), not by name: a .link rename after the generator ran would orphan a Name= match
    assert _sections(network)["Match"] == [f"MACAddress={MAC}", "Type=ether"]
    assert f": {dev} at boot." in network
    assert out.startswith(f"{dev} ({MAC}): routes to the NFS server 10.21.0.1 from 10.21.2.43"), out


@pytest.mark.parametrize("mac", ["00:00:00:00:00:00", "", "not-a-mac"])
def test_an_interface_without_a_mac_is_an_error(tmp_path, mac):
    out, network = _generate(tmp_path, _route("eth0", "10.21.2.43"), mac=mac, check=False)
    assert network is None
    assert "eth0 has no MAC address" in out, out


@pytest.fixture
def network(tmp_path):
    return _sections(_generate(tmp_path, _route("eth0", "10.21.2.43"))[1])


def test_the_network_section_never_drops_the_nfs_roots_address(network):
    assert network["Network"] == [
        "DHCP=ipv4",
        "KeepConfiguration=yes",
        "IgnoreCarrierLoss=yes",
        "IPv6AcceptRA=no",
        "LinkLocalAddressing=ipv6",
    ]
    assert network["Link"] == ["RequiredForOnline=routable"]


def test_the_dhcp_client_identifies_by_mac_and_takes_hostname_and_ntp(network):
    assert network["DHCPv4"] == [
        "ClientIdentifier=mac",
        "UseHostname=yes",
        "UseNTP=yes",
        "UseDomains=no",
        "SendRelease=no",
    ]


@pytest.mark.parametrize("switch,port", [(2, 43), (1, 7), (1, 40), (2, 3)])
def test_the_ipv6_address_and_route_are_port_vlan_maps(tmp_path, switch, port):
    entry = _port_vlan_map(switch, port)
    _, text = _generate(tmp_path, _route("eth0", entry["ip4"]))
    network = _sections(text)
    gateway = f"{BASE}{switch:02d}::ffff"
    assert network["Address"] == [f"Address={entry['ip6']}/128"]
    assert network["Route"] == [f"Gateway={gateway}", "GatewayOnLink=yes", "Metric=512"]


@pytest.mark.parametrize("src,base,why", [
    ("10.21.2.43", None, "no IPv6"),               # a site with no per-port IPv6
    ("10.21.0.130", BASE, "not a per-port address"),  # the quarantine range on switch 0
    ("192.168.1.10", BASE, "not a per-port address"),
])
def test_no_ipv6_without_a_base_or_off_a_per_port_address(tmp_path, src, base, why):
    out, text = _generate(tmp_path, _route("eth0", src), base=base)
    network = _sections(text)
    assert "Address" not in network and "Route" not in network
    assert network["Network"][0] == "DHCP=ipv4"
    assert why in out, out


def test_no_route_to_the_server_is_an_error_not_a_guess(tmp_path):
    out, network = _generate(tmp_path, None, check=False)
    assert network is None
    assert "Network is unreachable" in out, out


def test_no_nfs_server_on_the_command_line_is_an_error(tmp_path):
    out, network = _generate(tmp_path, _route("eth0", "10.21.2.43"), cmdline="console=tty1 root=/dev/mmcblk0p2\n",
                             check=False)
    assert network is None
    assert "no nfsroot=<IPv4 server>:<path>" in out, out


def test_the_generator_runs_before_networkd_at_every_boot():
    unit = GENERATOR_UNIT.read_text()
    assert "Type=oneshot" in unit and "DefaultDependencies=no" in unit
    assert "Before=systemd-networkd.service" in unit
    assert "WantedBy=systemd-networkd.service" in unit
    assert "ExecStart=/usr/local/sbin/fpgas-uplink-network" in unit
    assert "eth0" not in unit, "the unit must not depend on an interface name"


# --- fpgas-network-state ---------------------------------------------------------------------------------------

def _hook():
    loader = importlib.machinery.SourceFileLoader("fpgas_network_state", str(HOOK))
    spec = importlib.util.spec_from_loader("fpgas_network_state", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# `ip -j addr show dev eth0` and `ip -j -4/-6 route show default dev eth0` on pi-sw2-p43 (shape as iproute2 6.1
# prints it; the addresses are those read on 14 Oct 2026)
ADDR_JSON = [{"ifindex": 2, "ifname": "eth0", "addr_info": [
    {"family": "inet", "local": "10.21.2.43", "prefixlen": 16, "scope": "global",
     "valid_life_time": 4294967295, "preferred_life_time": 4294967295},
    {"family": "inet6", "local": "2404:e80:a137:2102::43", "prefixlen": 128, "scope": "global",
     "valid_life_time": 4294967295, "preferred_life_time": 4294967295},
    {"family": "inet6", "local": "fe80::8aa2:9eff:fe45:ddbe", "prefixlen": 64, "scope": "link",
     "valid_life_time": 290, "preferred_life_time": 140},
]}]
ROUTE4_JSON = [{"dst": "default", "gateway": "10.21.0.1", "dev": "eth0", "flags": []},
               {"dst": "default", "gateway": "10.21.0.1", "dev": "eth0", "protocol": "dhcp", "metric": 1024,
                "flags": []}]
ROUTE6_JSON = [{"dst": "default", "gateway": "2404:e80:a137:2102::ffff", "dev": "eth0", "protocol": "static",
                "metric": 512, "flags": ["onlink"], "pref": "medium"}]
KEYS = ["schema", "serial", "mac", "hostname", "switch", "port", "at", "interface", "trigger",
        "operational_state", "administrative_state", "addresses", "default_routes", "lease"]
LEASE_KEYS = ["address", "server", "lifetime_s", "acked_at", "expires"]


def _document(tmp_path, lease_text=None, addr=ADDR_JSON, cpuinfo="Serial\t\t: 10000000ce8e3593\n"):
    """The document for eth0 (ifindex 2); no lease file when lease_text is None."""
    lease_file = tmp_path / "2"
    if lease_text is not None:
        lease_file.write_text(lease_text)
        os.utime(lease_file, (1_791_960_000, 1_791_960_000))
    env = {"STATE": "routable", "OperationalState": "routable", "AdministrativeState": "configured"}
    return _hook().document("eth0", "routable", env, addr, ROUTE4_JSON, ROUTE6_JSON, str(lease_file), CMDLINE,
                            cpuinfo, "88:a2:9e:45:dd:be", "pi-sw2-p43", 1_791_960_100)


def test_the_document_has_the_same_keys_every_time(tmp_path):
    with_lease = _document(tmp_path, "# This is private data. Do not parse.\nADDRESS=10.21.2.43\n"
                                     "SERVER_ADDRESS=10.21.0.1\nT1=150\nT2=262\nLIFETIME=300\n")
    no_lease_dir = tmp_path / "no-lease"
    no_lease_dir.mkdir()
    without = _document(no_lease_dir)
    for doc in (with_lease, without):
        assert list(doc) == KEYS
        assert list(doc["lease"]) == LEASE_KEYS
        json.dumps(doc)  # plain JSON, as published
    assert without["lease"] == dict.fromkeys(LEASE_KEYS)


def test_the_document_reads_the_state_addresses_routes_and_lease(tmp_path):
    doc = _document(tmp_path, "ADDRESS=10.21.2.43\nSERVER_ADDRESS=10.21.0.1\nLIFETIME=300\n")
    assert doc["schema"] == 1
    assert (doc["serial"], doc["mac"], doc["hostname"], doc["interface"]) == (
        "10000000ce8e3593", "88:a2:9e:45:dd:be", "pi-sw2-p43", "eth0")
    assert (doc["switch"], doc["port"]) == (2, 43)
    assert (doc["trigger"], doc["operational_state"], doc["administrative_state"]) == (
        "routable", "routable", "configured")
    assert doc["addresses"] == [
        {"family": 4, "address": "10.21.2.43", "prefixlen": 16, "scope": "global",
         "valid_lifetime_s": None, "preferred_lifetime_s": None},
        {"family": 6, "address": "2404:e80:a137:2102::43", "prefixlen": 128, "scope": "global",
         "valid_lifetime_s": None, "preferred_lifetime_s": None},
        {"family": 6, "address": "fe80::8aa2:9eff:fe45:ddbe", "prefixlen": 64, "scope": "link",
         "valid_lifetime_s": 290, "preferred_lifetime_s": 140},
    ]
    assert doc["default_routes"] == [
        {"family": 4, "gateway": "10.21.0.1", "metric": 0, "protocol": "boot"},
        {"family": 4, "gateway": "10.21.0.1", "metric": 1024, "protocol": "dhcp"},
        {"family": 6, "gateway": "2404:e80:a137:2102::ffff", "metric": 512, "protocol": "static"},
    ]
    assert doc["lease"] == {"address": "10.21.2.43", "server": "10.21.0.1", "lifetime_s": 300,
                            "acked_at": "2026-10-14T06:40:00+00:00", "expires": "2026-10-14T06:45:00+00:00"}
    assert doc["at"] == "2026-10-14T06:41:40+00:00"


def test_no_switch_and_port_off_a_per_port_address(tmp_path):
    addr = [{"addr_info": [{"family": "inet", "local": "10.21.0.130", "prefixlen": 24, "scope": "global"}]}]
    doc = _document(tmp_path, addr=addr)
    assert (doc["switch"], doc["port"]) == (None, None)


def _run_hook(tmp_path, iface, network_text):
    network_file = tmp_path / "10-fpgas-uplink.network"
    network_file.write_text(network_text)
    env = dict(os.environ, IFACE=iface, STATE="routable", FPGAS_UPLINK_NETWORK=str(network_file))
    return subprocess.run(["python3", str(HOOK)], env=env, capture_output=True, text=True)


def test_the_hook_does_nothing_for_another_interface(tmp_path):
    # eth-fpga: the USB adapter an FPGA's Ethernet test configures itself (absent on this machine, so its MAC
    # reads as none); lo: present everywhere, with MAC 00:00:00:00:00:00
    for iface in ("eth-fpga", "lo"):
        r = _run_hook(tmp_path, iface, f"[Match]\nMACAddress={MAC}\nType=ether\n\n[Network]\nDHCP=ipv4\n")
        assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r


def test_the_hook_refuses_arguments_it_does_not_know(tmp_path):
    r = subprocess.run(["python3", str(HOOK), "--flsuh"], capture_output=True, text=True)
    assert r.returncode == 2 and "usage: fpgas-network-state [--flush]" in r.stderr, r


def test_the_hook_does_nothing_without_the_generated_file(tmp_path):
    env = dict(os.environ, IFACE="eth0", STATE="routable", FPGAS_UPLINK_NETWORK=str(tmp_path / "absent"))
    r = subprocess.run(["python3", str(HOOK)], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "", r


def test_the_hook_reads_the_uplink_mac_from_the_match_section_only(tmp_path):
    hook = _hook()
    network_file = tmp_path / "10-fpgas-uplink.network"
    # a MACAddress= in another section ([Link] MACAddress= sets one) is not the match
    network_file.write_text("# routes to the NFS server\n[Link]\nMACAddress=02:00:00:00:00:01\n\n[Match]\n"
                            "MACAddress=88:A2:9E:45:DD:BE\nType=ether\n\n[Network]\nDHCP=ipv4\n")
    assert hook.uplink_mac(str(network_file)) == MAC
    network_file.write_text("[Link]\nMACAddress=02:00:00:00:00:01\n")
    assert hook.uplink_mac(str(network_file)) is None
    assert hook.uplink_mac(str(tmp_path / "absent")) is None




# --- the role, fixpi and verify-pi ----------------------------------------------------------------------------

def test_onpi_installs_networkd_and_the_dispatcher_and_removes_the_rest():
    tasks = TASKS.read_text()
    for package in ("network-manager", "ifupdown", "isc-dhcp-client"):
        assert f"      - {package}\n" in tasks.split("- name: Remove NetworkManager, ifupdown and dhclient")[1] \
            .split("- name:")[0]
    for unit in ("fpgas-uplink-network.service", "systemd-networkd.service", "networkd-dispatcher.service"):
        assert f"name: {unit}\n    enabled: true" in tasks, unit
    assert "name: networkd-dispatcher\n    state: present" in tasks
    for retired in ("/etc/systemd/system/fpgas-ipv6.service", "/usr/local/sbin/fpgas-ipv6",
                    "/etc/systemd/system/multi-user.target.wants/fpgas-ipv6.service"):
        assert f"    - {retired}\n" in tasks
    assert "include_tasks: networkd.yml" in ONPI_MAIN.read_text()
    assert "ipv6.yml" not in ONPI_MAIN.read_text()


def test_the_hook_is_linked_into_every_dispatcher_state():
    # networkd-dispatcher 2.2.3's ADMIN_STATES and OPER_STATES
    admin = ["configured", "configuring", "failed", "pending", "unmanaged", "linger", "initialized"]
    oper = ["carrier", "degraded", "degraded-carrier", "dormant", "enslaved", "missing", "no-carrier", "off",
            "routable"]
    # Loaded as Ansible loads it (YAML 1.1): a bare `off` would be the boolean false, not the state.
    listed = yaml.safe_load(ONPI_DEFAULTS.read_text())["onpi_networkd_states"]
    assert sorted(listed) == sorted(admin + oper)
    tasks = TASKS.read_text()
    assert 'dest: "/etc/networkd-dispatcher/{{ item }}.d/50-fpgas-network-state"' in tasks
    assert 'loop: "{{ onpi_networkd_states }}"' in tasks
    assert "After=fpgas-fleet-agent.service" in DISPATCHER_DROPIN.read_text()


def test_fixpi_feeds_the_ipv6_base_and_drops_the_dead_network_config():
    assert 'content: "{{ pib_network6_base }}\\n"' in (FIXPI / "tasks/ipv6-site.yml").read_text()
    assert "include_tasks: ipv6-site.yml" in (FIXPI / "tasks/main.yml").read_text()
    tweeks = (FIXPI / "tasks/tweeks.yml").read_text()
    assert "src: /dev/null" not in tweeks, "ifupdown is purged, not masked"
    for gone in ("etc/systemd/system/networking.service", "etc/systemd/system/ifupdown-pre.service",
                 "etc/network/interfaces.d/eth1.conf"):
        assert f"    - {gone}\n" in tweeks
    assert not (FIXPI / "files/etc/network").exists()
    assert not (FIXPI / "templates/resolve.conf.j2").exists()


def test_timesyncd_takes_ntp_from_dhcp_with_the_gateway_as_fallback():
    netboot = (FIXPI / "tasks/netboot.yml").read_text()
    assert "FallbackNTP={{ eth_local_address | default('10.21.0.1') }}" in netboot
    assert not re.search(r"^\s*NTP=", netboot, re.M), "no fixed NTP=: DHCP is the one source"
    # the same variable ci-nfsroot.yml writes into resolv.conf
    assert "nameserver {{ eth_local_address | default('10.21.0.1') }}" in (REPO / "ansible/ci-nfsroot.yml").read_text()


def test_verify_pi_checks_networkd_the_renewals_ntp_and_the_sites_record():
    text = VERIFY_PI.read_text()
    for name in ("Assert systemd-networkd manages the uplink and renews its lease",
                 "Assert timesyncd takes its server from DHCP",
                 "Assert the gateway acknowledged a renewal by this Pi",
                 "Assert the site holds a network-state record from this boot",
                 "Assert no other network manager is installed",
                 "Assert the per-port IPv6 address and an IPv6 default route"):
        assert f"- name: {name}\n" in text, name
    assert '"systemd-networkd.service", "fpgas-uplink-network.service"' in text
    assert "fpgas-ipv6" not in text
    # the gateway half runs on the gateway
    ack = text.split("- name: Wait for the gateway to acknowledge a renewal by this Pi")[1].split("- name:")[0]
    assert 'delegate_to: "{{ verify_pi_server }}"' in ack
    # a renewal, not the boot's own exchanges: only ACKs from 30 s after networkd's first acquisition count
    assert '"--since=@{{ verify_pi_network.acquired_epoch | int + 30 }}"' in ack
    record = text.split("- name: Assert the site holds a network-state record from this boot")[1].split("- name:")[0]
    assert "== 'routable'" in record


def test_the_spool_is_retried_every_minute_only_while_it_holds_records():
    service = (ONPI / "files/etc/systemd/system/fpgas-network-state-flush.service").read_text()
    timer = (ONPI / "files/etc/systemd/system/fpgas-network-state-flush.timer").read_text()
    assert "ExecStart=/usr/local/sbin/fpgas-network-state --flush" in service
    assert "ConditionDirectoryNotEmpty=/run/fpgas-network-state" in service
    assert "OnActiveSec=1min" in timer and "OnUnitActiveSec=1min" in timer
    # started by the publisher when records stay, not at boot
    assert "[Install]" not in timer
    assert "fpgas-network-state-flush.timer\n    enabled" not in TASKS.read_text()


def _purge_extra(simulate_stdout: str) -> list:
    """The purge guard's own expression from networkd.yml, rendered by Ansible as the play does."""
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar, trust_as_template

    tasks = yaml.safe_load(TASKS.read_text())
    guard = next(t for t in tasks if t.get("name") == "Assert the removal takes nothing else")
    # ansible-core 2.19 renders only templates it trusts, as it trusts a playbook's own
    expression = {name: trust_as_template(value) for name, value in guard["vars"].items()}
    templar = Templar(loader=DataLoader(), variables={
        "onpi_network_purge_sim": {"stdout_lines": simulate_stdout.splitlines()}, **expression})
    return templar.template(trust_as_template("{{ onpi_network_purge_extra }}"))


# `apt-get --simulate purge network-manager ifupdown isc-dhcp-client` output, in the shape apt prints
SIMULATE = """Reading package lists...
Building dependency tree...
The following packages will be REMOVED:
  ifupdown* isc-dhcp-client* network-manager* raspberrypi-net-mods*
0 upgraded, 0 newly installed, 4 to remove and 0 not upgraded.
Purg raspberrypi-net-mods [20230626]
Purg network-manager [1.42.4-1+rpt1+deb12u1]
Purg ifupdown [0.8.41]
Purg isc-dhcp-client [4.4.3-P1-2]
"""


def test_the_purge_guard_passes_the_expected_four_and_names_anything_else():
    tasks = TASKS.read_text()
    assert tasks.index("argv: [apt-get, --simulate, purge, network-manager, ifupdown, isc-dhcp-client]") \
        < tasks.index("- name: Assert the removal takes nothing else") \
        < tasks.index("- name: Remove NetworkManager, ifupdown and dhclient")
    assert _purge_extra(SIMULATE) == []
    assert _purge_extra(SIMULATE + "Purg rpi-connect-lite [2.1.0]\n") == ["rpi-connect-lite"]
    assert _purge_extra("Package 'network-manager' is not installed, so not removed\n") == []


class _Info:
    def __init__(self, ok):
        self.ok = ok

    def wait_for_publish(self, timeout):
        pass

    def is_published(self):
        return self.ok


class _Client:
    """A stand-in paho client: acknowledges the first `acks` publishes, refuses to connect, raises the alarm's
    TimeoutError at publish `alarm_at`, or raises paho's RuntimeError (a dropped connection) at `drop_at`."""

    def __init__(self, acks=None, refuse=False, alarm_at=None, drop_at=None):
        self.acks, self.refuse, self.alarm_at, self.drop_at, self.sent = acks, refuse, alarm_at, drop_at, []

    def connect(self, broker, port):
        if self.refuse:
            raise OSError(113, "No route to host")
        self.connected = (broker, port)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos):
        assert qos == 1
        self.sent.append((topic, json.loads(payload)))
        if self.alarm_at is not None and len(self.sent) == self.alarm_at:
            raise TimeoutError("network-state took over 30 s")  # as the hook's SIGALRM handler raises it
        if self.drop_at is not None and len(self.sent) == self.drop_at:
            raise RuntimeError("Message publish failed: The client is not currently connected.")
        return _Info(self.acks is None or len(self.sent) <= self.acks)


CFG = {"site": "welland", "broker": "10.21.0.1", "port": 1883}
TOPIC = "fpgas/welland/pi/10000000ce8e3593/event"


def _spool_four(hook, spool_dir):
    for minute in (1, 2, 3, 4):
        hook.spool({"ts": f"2026-10-14T06:4{minute}:00+00:00", "n": minute}, str(spool_dir))


def _send(tmp_path, client):
    hook = _hook()
    return hook.send_spool(CFG, TOPIC, None, lambda _mqtt: client, str(tmp_path / "spool"), str(tmp_path / "bad")), hook


def _left(hook, tmp_path):
    return [json.loads(Path(p).read_text())["n"] for p in hook.spooled(str(tmp_path / "spool"))]


def test_the_spool_goes_out_oldest_first_on_the_fleet_topic(tmp_path):
    _spool_four(_hook(), tmp_path / "spool")
    client = _Client()
    (sent, queued), hook = _send(tmp_path, client)
    assert (sent, queued) == (4, 0)
    assert client.connected == ("10.21.0.1", 1883)
    assert [p["n"] for _, p in client.sent] == [1, 2, 3, 4]
    assert {topic for topic, _ in client.sent} == {TOPIC}
    assert _left(hook, tmp_path) == []


def test_each_record_leaves_the_spool_as_it_is_acknowledged(tmp_path):
    _spool_four(_hook(), tmp_path / "spool")
    (sent, queued), hook = _send(tmp_path, _Client(acks=2))
    assert (sent, queued) == (2, 2)
    assert _left(hook, tmp_path) == [3, 4]


@pytest.mark.parametrize("client,error", [
    (_Client(alarm_at=3), "TimeoutError"),  # the 30 s alarm, mid-publish
    (_Client(drop_at=3), "RuntimeError"),   # paho: the connection dropped (the cable was pulled)
])
def test_an_error_mid_publish_keeps_what_was_acknowledged_and_the_rest(tmp_path, capsys, client, error):
    _spool_four(_hook(), tmp_path / "spool")
    (sent, queued), hook = _send(tmp_path, client)
    assert (sent, queued) == (2, 2)
    assert _left(hook, tmp_path) == [3, 4]  # nothing lost, nothing resent from the start
    assert error in capsys.readouterr().err


def test_an_unreachable_broker_keeps_the_spool(tmp_path, capsys):
    _spool_four(_hook(), tmp_path / "spool")
    (sent, queued), hook = _send(tmp_path, _Client(refuse=True))
    assert (sent, queued) == (0, 4)
    assert _left(hook, tmp_path) == [1, 2, 3, 4]
    assert "No route to host" in capsys.readouterr().err


def test_an_empty_spool_connects_to_nothing(tmp_path):
    client = _Client(refuse=True)  # would raise if connected
    (sent, queued), _ = _send(tmp_path, client)
    assert (sent, queued) == (0, 0) and client.sent == []


def test_a_spool_file_that_is_not_json_is_moved_aside_not_resent_for_ever(tmp_path, capsys):
    hook = _hook()
    _spool_four(hook, tmp_path / "spool")
    broken = Path(hook.spooled(str(tmp_path / "spool"))[1])
    broken.write_text("{not json")
    client = _Client()
    (sent, queued), _ = _send(tmp_path, client)
    assert (sent, queued) == (3, 0)
    assert [p["n"] for _, p in client.sent] == [1, 3, 4]
    assert (tmp_path / "bad" / broken.name).exists()
    assert f"moved to {tmp_path / 'bad'}" in capsys.readouterr().err


# --- main(): the dispatcher's run and the timer's, with fake fleet_agent and paho modules -----------------

FAKE_FLEET_AGENT = '''
import json, os
CLIENT = None
def load_config(path):
    with open(path) as f:
        return json.load(f)
def topics(site, serial):
    return {"event": f"fpgas/{site}/pi/{serial}/event"}
def mqtt_client(mqtt):
    return mqtt.Client()
'''
FAKE_PAHO = '''
import json, os
class _Info:
    def __init__(self, ok): self.ok = ok
    def wait_for_publish(self, timeout): pass
    def is_published(self): return self.ok
class Client:
    def connect(self, broker, port):
        if os.environ.get("FAKE_BROKER") == "down":
            raise OSError(113, "No route to host")
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass
    def publish(self, topic, payload, qos):
        with open(os.environ["FAKE_SENT"], "a") as f:
            f.write(json.dumps({"topic": topic, "payload": json.loads(payload)}) + "\\n")
        return _Info(True)
'''


@pytest.fixture
def pi(tmp_path, monkeypatch):
    """A stand-in Pi for main(): the generated file naming the uplink's MAC, sysfs, cpuinfo, boot id, a fleet
    config, fake fleet_agent and paho, and a systemctl that only records its calls."""
    hook = _hook()
    lib = tmp_path / "fleet-lib"
    (lib / "paho" / "mqtt").mkdir(parents=True)
    (lib / "fleet_agent.py").write_text(FAKE_FLEET_AGENT)
    (lib / "paho" / "__init__.py").write_text("")
    (lib / "paho" / "mqtt" / "__init__.py").write_text("")
    (lib / "paho" / "mqtt" / "client.py").write_text(FAKE_PAHO)
    sysfs = tmp_path / "sys-class-net"
    (sysfs / "eth0").mkdir(parents=True)
    (sysfs / "eth0" / "address").write_text(MAC + "\n")
    (sysfs / "eth-fpga").mkdir()
    (sysfs / "eth-fpga" / "address").write_text("00:05:1b:b0:47:9d\n")
    network = tmp_path / "10-fpgas-uplink.network"
    network.write_text(f"[Match]\nMACAddress={MAC}\nType=ether\n")
    (tmp_path / "cpuinfo").write_text("Serial\t\t: 10000000ce8e3593\n")
    (tmp_path / "boot_id").write_text("d3adb33f\n")
    (tmp_path / "fleet.toml").write_text(json.dumps(CFG))
    systemctl = tmp_path / "systemctl.log"
    for name, value in {"FLEET_LIB": str(lib), "FLEET_CONFIG": str(tmp_path / "fleet.toml"),
                        "SYSFS_NET": str(sysfs), "CPUINFO": str(tmp_path / "cpuinfo"),
                        "BOOT_ID": str(tmp_path / "boot_id"), "SPOOL_DIR": str(tmp_path / "spool"),
                        "BAD_DIR": str(tmp_path / "bad"), "LOCK_FILE": str(tmp_path / "lock"),
                        "SYSTEMCTL": ["sh", "-c", f'echo "$@" >> {systemctl}', "systemctl"]}.items():
        monkeypatch.setattr(hook, name, value)
    # send_spool's defaults were bound at definition: point them at the test's spool too
    monkeypatch.setattr(hook.send_spool, "__defaults__", (str(tmp_path / "spool"), str(tmp_path / "bad")))
    monkeypatch.setattr(hook.spool, "__defaults__", (str(tmp_path / "spool"), hook.SPOOL_MAX, None))
    monkeypatch.setattr(hook.spooled, "__defaults__", (str(tmp_path / "spool"),))
    doc = {"at": "2026-10-14T06:41:40+00:00", "trigger": "routable", "operational_state": "routable",
           "administrative_state": "configured"}
    monkeypatch.setattr(hook, "collect", lambda iface, env, mac: dict(doc, interface=iface, mac=mac))
    sent = tmp_path / "sent.jsonl"
    monkeypatch.setenv("FAKE_SENT", str(sent))
    monkeypatch.setenv("FPGAS_UPLINK_NETWORK", str(network))
    monkeypatch.setenv("IFACE", "eth0")
    monkeypatch.setenv("STATE", "routable")
    monkeypatch.delenv("FAKE_BROKER", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))  # _fleet() inserts FLEET_LIB
    for module in ("fleet_agent", "paho", "paho.mqtt", "paho.mqtt.client"):
        monkeypatch.delitem(sys.modules, module, raising=False)

    class Pi:
        pass

    p = Pi()
    p.hook, p.tmp, p.sent, p.systemctl = hook, tmp_path, sent, systemctl
    p.records = lambda: [json.loads(line) for line in sent.read_text().splitlines()] if sent.exists() else []
    p.calls = lambda: systemctl.read_text().splitlines() if systemctl.exists() else []
    return p


def test_main_sends_the_uplinks_record_to_the_site(pi):
    assert pi.hook.main(["fpgas-network-state"]) == 0
    [record] = pi.records()
    assert record["topic"] == "fpgas/welland/pi/10000000ce8e3593/event"
    assert record["payload"]["stage"] == "network-state" and record["payload"]["boot_id"] == "d3adb33f"
    assert record["payload"]["ts"] == record["payload"]["detail"]["at"]
    assert record["payload"]["detail"]["interface"] == "eth0" and record["payload"]["detail"]["mac"] == MAC
    assert pi.hook.spooled() == [] and pi.calls() == []


def test_main_ignores_another_interface(pi, monkeypatch):
    monkeypatch.setenv("IFACE", "eth-fpga")
    assert pi.hook.main(["fpgas-network-state"]) == 0
    assert pi.records() == [] and pi.hook.spooled() == []


def test_main_keeps_the_record_and_starts_the_retry_timer_when_the_broker_is_down(pi, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BROKER", "down")
    assert pi.hook.main(["fpgas-network-state"]) == 0
    assert len(pi.hook.spooled()) == 1
    assert pi.calls() == ["start --no-block fpgas-network-state-flush.timer"]
    assert "1 queued" in capsys.readouterr().out
    # the timer's flush, once the broker is back, sends it and stops the timer
    monkeypatch.setenv("FAKE_BROKER", "up")
    assert pi.hook.main(["fpgas-network-state", "--flush"]) == 0
    assert [r["payload"]["detail"]["trigger"] for r in pi.records()] == ["routable"]
    assert pi.hook.spooled() == []
    assert pi.calls()[-1] == "stop --no-block fpgas-network-state-flush.timer"


def test_a_flush_that_cannot_send_fails_so_the_unit_shows_it(pi, monkeypatch):
    monkeypatch.setenv("FAKE_BROKER", "down")
    pi.hook.main(["fpgas-network-state"])
    assert pi.hook.main(["fpgas-network-state", "--flush"]) == 1
    assert len(pi.hook.spooled()) == 1


def test_an_alarm_before_the_send_leaves_the_record_spooled(pi, monkeypatch, capsys):
    def stuck(*_):
        raise TimeoutError("network-state took over 30 s")  # as the alarm raises it, waiting for the lock

    monkeypatch.setattr(pi.hook.fcntl, "flock", stuck)
    assert pi.hook.main(["fpgas-network-state"]) == 1
    assert len(pi.hook.spooled()) == 1 and pi.records() == []
    assert pi.calls() == ["start --no-block fpgas-network-state-flush.timer"]
    assert "the record is in the spool" in capsys.readouterr().err


def test_main_refuses_a_machine_without_a_serial(pi, capsys):
    (pi.tmp / "cpuinfo").write_text("processor\t: 0\n")
    assert pi.hook.main(["fpgas-network-state"]) == 1
    assert pi.records() == [] and pi.hook.spooled() == []
    assert "no Serial in" in capsys.readouterr().err


def test_undelivered_records_are_spooled_oldest_first_and_bounded(tmp_path):
    hook = _hook()
    for minute in (3, 1, 2):
        assert hook.spool({"ts": f"2026-10-14T06:4{minute}:00+00:00"}, str(tmp_path), keep=2) == (1 if minute == 2 else 0)
    kept = [json.loads(Path(p).read_text())["ts"] for p in hook.spooled(str(tmp_path))]
    assert kept == ["2026-10-14T06:42:00+00:00", "2026-10-14T06:43:00+00:00"]
    assert hook.spooled(str(tmp_path / "absent")) == []


def test_a_dead_runs_temporary_file_is_cleared_and_never_sent(tmp_path):
    hook = _hook()
    stale, fresh = tmp_path / ".old.json", tmp_path / ".new.json"
    stale.write_text("{}")
    fresh.write_text("{}")
    os.utime(stale, (1_000, 1_000))
    hook.spool({"ts": "2026-10-14T06:41:00+00:00"}, str(tmp_path), now=1_000 + hook.STALE_TMP_S + 1)
    assert not stale.exists() and fresh.exists()
    assert [Path(p).name.startswith(".") for p in hook.spooled(str(tmp_path))] == [False]


def test_main_leaves_no_alarm_behind(pi, monkeypatch):
    # A pending SIGALRM would fire into whatever runs next in the process (it did, into another test).
    for env in ({"FAKE_BROKER": "up"}, {"FAKE_BROKER": "down"}):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        pi.hook.main(["fpgas-network-state"])
        assert signal.alarm(0) == 0


def test_the_gateway_offers_a_ports_address_without_pinging_it_first():
    # A board keeps its address past its lease; a ping-before-offer would find the board itself and refuse it
    # its own address after any DHCP outage longer than the lease (the VM test saw "no address available").
    base = (REPO / "ansible/roles/pxe/templates/dnsmasq-base.conf.j2").read_text()
    assert re.search(r"^no-ping$", base, re.M)


def test_the_generator_records_the_uplinks_carrier_counters_before_networkd(tmp_path):
    _generate(tmp_path, _route("eth-uplink", "10.21.2.43"))
    # verify-pi compares these with the live counters: a link that went down after networkd took it has a higher
    # carrier_down_count (at ps1, networkd taking the kernel's link down killed the NFS mount)
    assert (tmp_path / "carrier").read_text() == (
        "interface=eth-uplink\ncarrier_down_count=1\ncarrier_up_count=2\noperstate=up\n")


def test_the_network_file_sets_nothing_that_takes_the_link_down(network):
    # networkd 252 takes a link down only for: a [Link] MACAddress= change, CAN settings, joining a bond,
    # ActivationPolicy=down/always-down, BindCarrier= (networkd-setlink.c:491,502,538,1088; networkd-link.c:693,1676).
    # The .network sets none of them: [Link] carries only RequiredForOnline, MACAddress= is in [Match] (it matches;
    # it sets nothing), and no MTU, bond, bridge, activation policy or bound carrier is named.
    assert network["Link"] == ["RequiredForOnline=routable"]
    everything = [line.split("=", 1)[0] for lines in network.values() for line in lines]
    for key in ("ActivationPolicy", "BindCarrier", "Bond", "Bridge", "VRF", "MTUBytes", "MACAddressPolicy",
                "WakeOnLan", "Unmanaged", "ConfigureWithoutCarrier"):
        assert key not in everything, key
    assert "MACAddress" not in [line.split("=", 1)[0] for line in network["Network"] + network["Link"]]


# Every file of the conversion that runs on the Pi
PI_FILES = [ONPI / "files/usr/local/sbin/fpgas-uplink-network", HOOK, GENERATOR_UNIT, DISPATCHER_DROPIN,
            ONPI / "files/etc/systemd/system/fpgas-network-state-flush.service",
            ONPI / "files/etc/systemd/system/fpgas-network-state-flush.timer"]


@pytest.mark.parametrize("path", PI_FILES, ids=lambda p: p.name)
def test_nothing_on_the_pi_reconfigures_the_link_or_the_root(path):
    text = path.read_text()
    # no link down/up, no networkd reconfigure or restart, no install or enable at run time (#271: the root is
    # read-only under a tmpfs overlay; anything installed or enabled on the Pi is gone at the next boot)
    for forbidden in ("networkctl", "ip link set", "ifdown", "ifup", "restart systemd-networkd", "systemctl enable",
                      "apt-get", "apt install", "dpkg -i"):
        assert forbidden not in text, (path.name, forbidden)


def test_the_pi_side_writes_only_under_run():
    hook = _hook()
    for path in (hook.SPOOL_DIR, hook.BAD_DIR, hook.LOCK_FILE):
        assert path.startswith("/run/"), path
    generator = GENERATOR.read_text()
    assert "out_dir=${FPGAS_UPLINK_NETWORK_DIR:-/run/systemd/network}" in generator
    assert "carrier_file=${FPGAS_UPLINK_CARRIER_FILE:-/run/fpgas-uplink-network.carrier}" in generator
    # the only redirections in the generator go to those two files
    assert sorted(set(re.findall(r'> "(\$[a-z_]+)', generator))) == ["$carrier_file", "$out_dir"]


def test_only_our_network_file_can_configure_the_uplink():
    # systemd 252's systemd-networkd.service has Also=systemd-network-generator.service, so enabling networkd
    # enables the generator, whose 71-default.network (DHCP from ip=dhcp, no KeepConfiguration) is what killed the
    # NFS root at ps1 (help-carl report1.md 3.2-3.4). It is masked, and networkd needs our generator to have run.
    tasks = TASKS.read_text()
    assert "name: systemd-network-generator.service\n    masked: true" in tasks
    assert tasks.index("name: systemd-networkd.service\n    enabled: true") < tasks.index(
        "name: systemd-network-generator.service\n    masked: true"), "mask after the enable that turns it on"
    dropin = (ONPI / "files/etc/systemd/system/systemd-networkd.service.d/fpgas-uplink.conf").read_text()
    assert "Requires=fpgas-uplink-network.service" in dropin and "After=fpgas-uplink-network.service" in dropin
    text = VERIFY_PI.read_text()
    assert "'Network File: /run/systemd/network/10-fpgas-uplink.network' in verify_pi_network.status.stdout" in text
    assert "verify_pi_network.generator.stdout == 'masked'" in text


def test_keep_configuration_says_why_it_must_be_explicit(network):
    # networkd's own "yes on a network root" default checks / with statfs (systemd 252 path_is_network_fs,
    # src/basic/stat-util.c), which sees the tmpfs overlay, not NFS.
    generator = GENERATOR.read_text()
    assert "/ is the tmpfs overlay (overlayroot=tmpfs), not NFS" in generator
    assert "KeepConfiguration=yes" in network["Network"]
