"""The gateway's lease scripts (roles/pxe/files): fpgas-release-port-lease and fpgas-lease-event.

Loaded from their files as they are installed (no .py suffix). The release script's paths are constants; these
tests call its functions with test files, which its command line cannot do."""

import importlib.machinery
import importlib.util
import ipaddress
import types
from pathlib import Path

import pytest

FILES = Path(__file__).resolve().parents[1] / "ansible/roles/pxe/files"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(FILES / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


release = load("fpgas-release-port-lease")
event = load("fpgas-lease-event")

# what roles/pxe/templates/ports.conf.j2 renders, two ports and the quarantine range
PORTS_CONF = """\
interface=v2101
dhcp-range=tag:v2101,10.21.1.1,10.21.1.1,255.255.0.0,5m
dhcp-range=tag:v2101,2404:e80:a137:2101::1,2404:e80:a137:2101::1,off-link,64,5m
interface=v2248
dhcp-range=tag:v2248,10.21.2.48,10.21.2.48,255.255.0.0,5m
dhcp-range=tag:v2248,2404:e80:a137:2102::48,2404:e80:a137:2102::48,off-link,64,5m
interface=eth-local
dhcp-range=tag:eth-local,10.21.0.128,10.21.0.150,255.255.255.0,1h
"""
SERVER_DUID = "00:01:00:01:2e:4c:7a:10:52:54:00:12:34:56"
CLIENT_DUID = "00:04:a1:b2:c3:d4:e5:f6:07:18:29:3a:4b:5c:6d:7e:8f:90"
LEASES = f"""\
1791941144 b8:27:eb:e3:e7:e4 10.21.2.48 pi-sw2-p48 *
1791965048 98:fe:54:13:f5:9c 10.21.1.1 pi-sw1-p1 01:98:fe:54:13:f5:9c
duid {SERVER_DUID}
1791959703 318764444 2404:e80:a137:2102::48 pi-sw2-p48 {CLIENT_DUID}
"""


# --- the arguments: the boundary ------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    [],
    ["v2248"],
    ["v2248", "--cause"],
    ["v2248", "--cause", "link-down", "extra"],
    ["v2248", "--why", "link-down"],
    ["eth0", "--cause", "link-down"],
    ["eth-local", "--cause", "link-down"],
    ["v2248;id", "--cause", "link-down"],
    ["../v2248", "--cause", "link-down"],
    ["v22488", "--cause", "link-down"],
    ["", "--cause", "link-down"],
    ["v2248", "--cause", "because"],
    ["v2248", "--cause", "link-down;id"],
])
def test_anything_but_a_port_interface_and_a_known_cause_is_refused(argv):
    with pytest.raises(release.Refused):
        release.parse_args(argv)


def test_a_port_interface_and_a_cause_are_accepted():
    assert release.parse_args(["v2248", "--cause", "power-cycle"]) == ("v2248", "power-cycle")


def test_an_interface_ports_conf_does_not_name_is_refused():
    with pytest.raises(release.Refused, match="not an interface"):
        release.port_addresses(PORTS_CONF, "v2249")


def test_the_ports_own_two_addresses_come_from_ports_conf():
    assert release.port_addresses(PORTS_CONF, "v2248") == [
        ipaddress.ip_address("10.21.2.48"), ipaddress.ip_address("2404:e80:a137:2102::48")]


def test_main_refuses_with_exit_2_before_reading_anything(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("read or ran something")
    monkeypatch.setattr(release, "release_port", refuse)
    monkeypatch.setattr(release.syslog, "syslog", lambda *a: None)
    assert release.main(["eth0", "--cause", "link-down"]) == 2


# --- the lease file -------------------------------------------------------------------------------------------


def test_the_lease_file_is_read_with_its_server_duid():
    duid, leases = release.parse_leases(LEASES)
    assert duid == SERVER_DUID
    assert [(str(x["address"]), x.get("mac"), x.get("iaid"), x["client_id"]) for x in leases] == [
        ("10.21.2.48", "b8:27:eb:e3:e7:e4", None, None),
        ("10.21.1.1", "98:fe:54:13:f5:9c", None, "01:98:fe:54:13:f5:9c"),
        ("2404:e80:a137:2102::48", None, "318764444", CLIENT_DUID),
    ]


@pytest.mark.parametrize("bad", [
    "1791941144 b8:27:eb:e3:e7:e4 10.21.2.48 pi-sw2-p48",
    "1791941144 b8:27:eb:e3:e7:e4 10.21.2.48 $(id) *",
    "1791941144 b8:27:eb:e3:e7:e4 10.21.2.48 pi -x *",
    "1791941144 --iface 10.21.2.48 pi-sw2-p48 *",
    "1791959703 --server-id 2404:e80:a137:2102::48 pi-sw2-p48 00:04",
])
def test_a_line_not_in_dnsmasqs_shape_refuses_the_file(bad):
    with pytest.raises(release.Failed):
        release.parse_leases(LEASES + bad + "\n")


def test_v6_leases_without_the_duid_line_are_a_half_written_file():
    with pytest.raises(release.Failed, match="duid"):
        release.parse_leases(LEASES.replace(f"duid {SERVER_DUID}\n", ""))


# --- the release ----------------------------------------------------------------------------------------------


def write(tmp_path, leases):
    (tmp_path / "ports.conf").write_text(PORTS_CONF)
    (tmp_path / "leases").write_text(leases)
    return tmp_path / "ports.conf", tmp_path / "leases"


class Gateway:
    """dnsmasq answering the tools: each release removes that lease from the file (or does not, if told)."""

    def __init__(self, leases_path, honour=True):
        self.path, self.honour, self.ran, self.published, self.logged = leases_path, honour, [], [], []

    def run(self, argv, **kwargs):
        assert kwargs["env"] == release.CHILD_ENV and kwargs["timeout"] == release.TOOL_TIMEOUT
        self.ran.append(argv)
        if self.honour:
            address = argv[2] if argv[0] == release.DHCP_RELEASE else argv[argv.index("--ip") + 1]
            lines = [line for line in self.path.read_text().splitlines() if f" {address} " not in line]
            self.path.write_text("\n".join(lines) + "\n")
        return types.SimpleNamespace(returncode=0, stderr="")

    def call(self, iface, cause, ports_conf):
        clock = iter(range(0, 100))
        return release.release_port(iface, cause, ports_conf=ports_conf, lease_file=self.path, run=self.run,
                                    publish=self.published.append, log=lambda p, m: self.logged.append(m),
                                    sleep=lambda s: None, clock=lambda: next(clock))


def test_both_leases_of_the_port_are_released_with_dnsmasqs_tools_and_logged(tmp_path):
    ports_conf, leases = write(tmp_path, LEASES)
    gw = Gateway(leases)
    assert gw.call("v2248", "link-down", ports_conf) == 2
    assert gw.ran == [
        ["/usr/bin/dhcp_release", "v2248", "10.21.2.48", "b8:27:eb:e3:e7:e4"],
        ["/usr/bin/dhcp_release6", "--iface", "v2248", "--ip", "2404:e80:a137:2102::48", "--client-id", CLIENT_DUID,
         "--iaid", "318764444", "--server-id", SERVER_DUID],
    ]
    assert gw.logged == ["released v4 10.21.2.48 b8:27:eb:e3:e7:e4 on v2248 (cause: link-down)",
                         "released v6 2404:e80:a137:2102::48 IAID 318764444 on v2248 (cause: link-down)"]
    assert [(r["event"], r["switch"], r["port"], r["hostname"], r["family"], r["cause"]) for r in gw.published] == [
        ("released", 2, 48, "pi-sw2-p48", 4, "link-down"), ("released", 2, 48, "pi-sw2-p48", 6, "link-down")]
    # another port's lease is untouched
    assert " 10.21.1.1 " in leases.read_text()


def test_a_client_id_is_passed_exactly_as_the_file_has_it(tmp_path):
    ports_conf, leases = write(tmp_path, LEASES)
    gw = Gateway(leases)
    gw.call("v2101", "power-off", ports_conf)
    assert gw.ran == [["/usr/bin/dhcp_release", "v2101", "10.21.1.1", "98:fe:54:13:f5:9c", "01:98:fe:54:13:f5:9c"]]


def test_a_port_with_no_lease_releases_nothing(tmp_path):
    ports_conf, leases = write(tmp_path, LEASES)
    gw = Gateway(leases)
    (tmp_path / "ports.conf").write_text(PORTS_CONF + "interface=v2203\ndhcp-range=tag:v2203,10.21.2.3,10.21.2.3,"
                                                      "255.255.0.0,5m\n")
    assert gw.call("v2203", "down-port", ports_conf) == 0
    assert gw.ran == [] and gw.published == []


def test_a_lease_still_there_after_the_release_is_a_failure(tmp_path):
    ports_conf, leases = write(tmp_path, LEASES)
    gw = Gateway(leases, honour=False)
    with pytest.raises(release.Failed, match="still leased"):
        gw.call("v2248", "link-down", ports_conf)
    assert gw.published == []


def test_a_tool_that_fails_is_a_failure(tmp_path):
    ports_conf, leases = write(tmp_path, LEASES)
    gw = Gateway(leases)
    gw.run = lambda argv, **k: types.SimpleNamespace(returncode=255, stderr="bind failed")
    with pytest.raises(release.Failed, match="exited 255"):
        gw.call("v2248", "link-down", ports_conf)


# --- the lease event script -----------------------------------------------------------------------------------

NOW = 1_791_941_000.0
ENV = {"DNSMASQ_INTERFACE": "v2248", "DNSMASQ_LEASE_EXPIRES": "1791941300", "DNSMASQ_TIME_REMAINING": "300"}


def test_a_new_v4_lease_is_a_record_with_the_shared_names():
    doc = event.record(["add", "b8:27:eb:e3:e7:e4", "10.21.2.48", "pi-sw2-p48"], ENV, NOW)
    assert doc == {"schema": 1, "switch": 2, "port": 48, "hostname": "pi-sw2-p48", "at": "2026-10-14T01:23:20+00:00",
                   "event": "add", "family": 4, "address": "10.21.2.48", "mac": "b8:27:eb:e3:e7:e4", "server": None,
                   "lifetime_s": 300, "acked_at": "2026-10-14T01:23:20+00:00", "expires": 1791941300,
                   "interface": "v2248"}


def test_a_renewal_is_an_ack_and_dnsmasqs_restart_reread_is_not():
    renewal = event.record(["old", "b8:27:eb:e3:e7:e4", "10.21.2.48"], ENV, NOW)
    reread = event.record(["old", "b8:27:eb:e3:e7:e4", "10.21.2.48"], {**ENV, "DNSMASQ_DATA_MISSING": "1"}, NOW)
    assert renewal["acked_at"] is not None and reread["acked_at"] is None


def test_a_v6_lease_takes_its_mac_from_the_environment():
    doc = event.record(["add", CLIENT_DUID, "2404:e80:a137:2102::48"], {**ENV, "DNSMASQ_MAC": "b8:27:eb:e3:e7:e4"},
                       NOW)
    assert (doc["family"], doc["mac"]) == (6, "b8:27:eb:e3:e7:e4")


@pytest.mark.parametrize("args, env", [
    (["add", "b8:27:eb:e3:e7:e4", "10.21.0.130"], {"DNSMASQ_INTERFACE": "eth-local"}),  # the quarantine pool
    (["tftp", "52624", "10.21.2.48", "/srv/tftp/bootcode.bin"], ENV),
    (["init"], {}),
    (["arp-add", "b8:27:eb:e3:e7:e4", "10.21.2.48"], ENV),
    (["add", "b8:27:eb:e3:e7:e4", "not-an-address"], ENV),
])
def test_anything_but_a_per_port_lease_event_is_not_published(args, env):
    assert event.record(args, env, NOW) is None


def test_the_event_script_always_exits_0_and_says_when_it_could_not_publish(monkeypatch, capsys):
    monkeypatch.setattr(event, "read_config", lambda path: ("welland", "10.21.0.1"))
    monkeypatch.setattr(event.subprocess, "run",
                        lambda argv, **k: types.SimpleNamespace(returncode=5, stderr="Connection refused"))
    assert event.main(["add", "b8:27:eb:e3:e7:e4", "10.21.2.48"], ENV, NOW) == 0
    assert "not published" in capsys.readouterr().err


def test_the_event_script_publishes_to_the_sites_gw_lease_topic(monkeypatch):
    ran = []
    monkeypatch.setattr(event, "read_config", lambda path: ("welland", "10.21.0.1"))
    monkeypatch.setattr(event.subprocess, "run",
                        lambda argv, **k: ran.append((argv, k["timeout"])) or types.SimpleNamespace(returncode=0))
    event.main(["add", "b8:27:eb:e3:e7:e4", "10.21.2.48"], ENV, NOW)
    (argv, timeout), = ran
    assert argv[:7] == ["/usr/bin/mosquitto_pub", "-h", "10.21.0.1", "-q", "1", "-t", "fpgas/welland/gw/lease"]
    assert timeout == event.TIMEOUT


@pytest.mark.parametrize("text", ["site=welland\n", "broker=10.21.0.1\n", "site=Wel land\nbroker=10.21.0.1\n",
                                  "site=welland\nbroker=gateway\n"])
def test_a_config_without_a_site_and_a_broker_address_is_refused(tmp_path, text):
    path = tmp_path / "lease-events.conf"
    path.write_text(text)
    for module in (release, event):
        with pytest.raises(ValueError):
            module.read_config(path)
