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
import subprocess
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


def _generate(tmp_path: Path, route: str | None, base: str | None = BASE, cmdline: str = CMDLINE,
              check: bool = True) -> tuple[str, str | None]:
    """Run the generator with a stub `ip` answering `-o -4 route get` with `route` (None: no route)."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
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
               FPGAS_UPLINK_CMDLINE=str(cmdline_file), FPGAS_UPLINK_NETWORK_DIR=str(out_dir))
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
def test_the_file_matches_the_interface_that_routes_to_the_nfs_server(tmp_path, dev):
    out, network = _generate(tmp_path, _route(dev, "10.21.2.43"))
    assert _sections(network)["Match"] == [f"Name={dev}"]
    assert out.startswith(f"{dev}: routes to the NFS server 10.21.0.1 from 10.21.2.43"), out


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
    # eth-fpga: the USB adapter an FPGA's Ethernet test configures itself
    r = _run_hook(tmp_path, "eth-fpga", "[Match]\nName=eth0\n\n[Network]\nDHCP=ipv4\n")
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r


def test_the_hook_does_nothing_without_the_generated_file(tmp_path):
    env = dict(os.environ, IFACE="eth0", STATE="routable", FPGAS_UPLINK_NETWORK=str(tmp_path / "absent"))
    r = subprocess.run(["python3", str(HOOK)], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "", r


def test_the_hook_reads_the_uplink_from_the_match_section_only(tmp_path):
    hook = _hook()
    network_file = tmp_path / "10-fpgas-uplink.network"
    # a Name= in another section (as [Link] Name= renames a link) is not the match
    network_file.write_text("# routes to the NFS server\n[Link]\nName=renamed0\n\n[Match]\nName=eth-uplink\n\n"
                            "[Network]\nDHCP=ipv4\n")
    assert hook.uplink_name(str(network_file)) == "eth-uplink"
    network_file.write_text("[Link]\nName=renamed0\n")
    assert hook.uplink_name(str(network_file)) is None
    assert hook.uplink_name(str(tmp_path / "absent")) is None


def test_the_hook_is_bounded_and_publishes_on_the_fleet_event_topic():
    text = HOOK.read_text()
    assert "TIMEOUT_S = 30" in text and "signal.alarm(TIMEOUT_S)" in text
    assert 'fleet_agent.topics(cfg["site"], doc["serial"])["event"]' in text
    assert 'STAGE = "network-state"' in text
    assert "wait_for_publish(timeout=10)" in text


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
                 "Assert the gateway acknowledged this Pi within the lease time",
                 "Assert the site holds a network-state record from this boot",
                 "Assert no other network manager is installed",
                 "Assert the per-port IPv6 address and an IPv6 default route"):
        assert f"- name: {name}\n" in text, name
    assert '"systemd-networkd.service", "fpgas-uplink-network.service"' in text
    assert "fpgas-ipv6" not in text
    # the gateway half runs on the gateway
    ack = text.split("- name: Read the gateway's recent DHCPACKs for this Pi")[1].split("- name:")[0]
    assert 'delegate_to: "{{ verify_pi_server }}"' in ack


class _Info:
    def __init__(self, ok):
        self.ok = ok

    def wait_for_publish(self, timeout):
        pass

    def is_published(self):
        return self.ok


class _Client:
    """A stand-in paho client: acknowledges the first `acks` publishes, or refuses to connect."""

    def __init__(self, acks=None, refuse=False):
        self.acks, self.refuse, self.sent = acks, refuse, []

    def connect(self, broker, port):
        if self.refuse:
            raise OSError(113, "No route to host")

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos):
        self.sent.append((topic, json.loads(payload)))
        return _Info(self.acks is None or len(self.sent) <= self.acks)


def test_records_publish_in_order_and_stop_at_the_first_unacknowledged(tmp_path):
    hook = _hook()
    payloads = [{"ts": f"2026-10-14T06:4{i}:00+00:00", "n": i} for i in range(3)]
    client = _Client(acks=1)
    sent = hook.publish(payloads, "10.21.0.1", 1883, "fpgas/welland/pi/s/event", None, lambda _mqtt: client)
    assert sent == 1
    assert [p["n"] for _, p in client.sent] == [0, 1]


def test_undelivered_records_are_spooled_oldest_first_and_bounded(tmp_path):
    hook = _hook()
    for minute in (3, 1, 2):
        assert hook.spool({"ts": f"2026-10-14T06:4{minute}:00+00:00"}, str(tmp_path), keep=2) == (1 if minute == 2 else 0)
    kept = [json.loads(Path(p).read_text())["ts"] for p in hook.spooled(str(tmp_path))]
    assert kept == ["2026-10-14T06:42:00+00:00", "2026-10-14T06:43:00+00:00"]
    assert hook.spooled(str(tmp_path / "absent")) == []
