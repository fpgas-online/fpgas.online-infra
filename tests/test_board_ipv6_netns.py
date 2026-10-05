"""Boards take their ports' IPv6 addresses and keep IPv6 working for as long as they are up (#222).

The VM test (verify-pi.yml) shows a netbooted virtual Pi holding its
address, once, minutes after it booted: the harness never reboots the Pi
and has one board on one port. This test runs what the VM test cannot: a
real dnsmasq and a real radvd with the per-port configuration roles/pxe
renders, and the real dhcpcd with the settings roles/onpi installs, in
network namespaces (tests/netns/board_ipv6_scenario.py). The gateway has
several port interfaces that share one /64 and one gateway address, as
every port of a switch does (roles/vlan_ports).

On one port, in turn:

  - a board gets <prefix><SS>::<P>, under a client identifier made from its
    MAC address, with IPv4 on the interface left exactly as it was;
  - the same board gets it again at once after a power cut that left it
    none of its state (its root is a tmpfs overlay): the gateway knows it
    by its MAC address;
  - another board plugged into the same port is refused while the lease of
    the board before it runs (the port's range holds one address), keeps
    asking, and gets the address when that lease has run out. That wait is
    why the lease is short (roles/pxe/defaults/main.yml); the test renders
    it at dnsmasq's minimum of two minutes, and takes about three.

On a few more ports, all that time, a board each that stays up: for several
router lifetimes (rendered short here). A board asks for a router
advertisement only when its link comes up, so after one lifetime its
default route, and with it every use of its address, depends on the
gateway advertising on that board's port by itself, periodically. dnsmasq
did not: it advertises once per prefix, on one of the interfaces that hold
it, and real boards lost their default route half an hour after booting
while the VM test, which looks sooner, passed.

Last, radvd is reloaded, restarted, and stopped for a while and started
again under the boards that stayed up: none of it may cost a board its
default route (a package upgrade or an operator does each of these).

The site is one switch with three ports unless BOARD_IPV6_NETNS_PORTS says
otherwise: `48,48` is two switches with 48 ports each, a real site's shape,
with the ports that get no board there as interfaces that only have to be
advertised on. tests/lab/RESULTS.md records such a run.

It needs to be root, in a network and a mount namespace of its own: it runs
as root, or through passwordless sudo (an unprivileged user namespace will
not do: dhcpcd drops privileges to its own user). Where it cannot run, or
one of the programs is missing, it is skipped, except with
BOARD_IPV6_NETNS_TEST=1 (set in CI, lint.yml's pytest job), where being
unable to run is a failure.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.ports_conf_render import render, render_radvd, switches

REPO = Path(__file__).resolve().parent.parent
SCENARIO = REPO / "tests/netns/board_ipv6_scenario.py"
BOARD_CONF = REPO / "ansible/roles/onpi/files/board-ipv6/fpgas-board-ipv6.conf"
REQUIRED = os.environ.get("BOARD_IPV6_NETNS_TEST") == "1"
# dnsmasq, radvd, dhcpcd, ip and sysctl live in sbin, which is not on every user's PATH.
ENV = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/sbin:/sbin"}
TOOLS = ("dnsmasq", "radvd", "dhcpcd", "ip", "unshare", "mount", "sysctl", "ping")

# The site: how many access ports each switch has.
PORTS = [int(count) for count in os.environ.get("BOARD_IPV6_NETNS_PORTS", "3").split(",")]
# What the scenario's site (tests/ports_conf_render.py) gives switch 1.
PREFIX6 = "2001:db8:a137:2101"
BOARD6 = f"{PREFIX6}::1"  # port 1, where boards come and go
STORY = "v2101"
MAC_A = "02:00:00:b6:00:0a"
MAC_B = "02:00:00:b6:00:0b"
REFUSED = "no addresses available"
BOOTS = ["first", "reboot", "swap"]
# The gateway's interfaces of the ports whose board stays up
# (tests/netns/board_ipv6_scenario.py chooses them), and of every port.
HELD = list(dict.fromkeys(
    ["v2102", f"v{2100 + PORTS[0]}"]
    + [f"v{2000 + 100 * switch + port}" for switch in range(2, len(PORTS) + 1) for port in (1, PORTS[switch - 1])]))
IFACES = [f"v{2000 + 100 * switch + port}"
          for switch, count in enumerate(PORTS, start=1) for port in range(1, count + 1)]
# The router advertisements' timing, in seconds, far shorter than a site's
# (roles/pxe/defaults/main.yml) so that the boards that stay up outlive the
# lifetime several times over. The interval is radvd's shortest for which
# its other defaults still hold.
RA_INTERVAL = 10
RA_LIFETIME = 30


def _root_command() -> list[str] | None:
    """The command prefix that starts a process as root in new network and mount namespaces."""
    if os.geteuid() == 0:
        return ["unshare", "--net", "--mount"]
    if subprocess.run(["sudo", "-n", "true"], stdin=subprocess.DEVNULL, capture_output=True).returncode == 0:
        return ["sudo", "env", f"PATH={ENV['PATH']}", "unshare", "--net", "--mount"]
    return None


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    missing = [tool for tool in TOOLS if not shutil.which(tool, path=ENV["PATH"])]
    prefix = None if missing else _root_command()
    if prefix is None:
        reason = f"missing {missing}" if missing else "not root and no passwordless sudo"
        if REQUIRED:
            pytest.fail(f"BOARD_IPV6_NETNS_TEST=1 but the test cannot run: {reason}")
        pytest.skip(reason)
    workdir = tmp_path_factory.mktemp("board-ipv6")
    site = {"switches": switches(*PORTS), "pxe_board_ipv6": True}
    ports_conf = workdir / "ports.conf"
    # dnsmasq's shortest lease: the swap waits for it to run out.
    ports_conf.write_text(render(**site, pxe_port_lease6="2m"))
    radvd_conf = workdir / "radvd.conf"
    radvd_conf.write_text(render_radvd(**site, pxe_ra_interval=RA_INTERVAL, pxe_ra_lifetime=RA_LIFETIME))
    proc = subprocess.run(
        [*prefix, sys.executable, str(SCENARIO), str(ports_conf), str(radvd_conf), str(BOARD_CONF), str(workdir),
         str(RA_LIFETIME), str(RA_INTERVAL), ",".join(map(str, PORTS))],
        env=ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1500)
    assert proc.returncode == 0, f"the scenario failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def boots(run):
    return run


def _story(run: dict, boot: str) -> str:
    """What to print when an assertion about one of port 1's boots fails."""
    return (f"{json.dumps(run[boot], indent=1)}\n--- dhcpcd\n{run['client_logs'][boot]}"
            f"\n--- dnsmasq\n{run['dnsmasq_log']}\n--- radvd\n{run['radvd_log']}")


def _held_story(run: dict, port: str) -> str:
    """What to print when an assertion about a board that stayed up fails."""
    return (f"{json.dumps(run['held'][port], indent=1)}\n--- its default route, once a second\n"
            f"{json.dumps(run['route_watch'][port])}\n--- router advertisements and solicitations, by interface\n"
            f"{json.dumps({iface: run['wire'].get(iface) for iface in HELD})}\n"
            f"--- dhcpcd\n{run['client_logs'][port]}"
            f"\n--- radvd\n{run['radvd_log']}")


# --- port 1: a first boot, a reboot, another board ----------------------------

def test_a_board_gets_its_ports_address(boots):
    first = boots["first"]
    assert first["addresses"] == [f"{BOARD6}/128"], _story(boots, "first")
    assert first["client_running"]


def test_the_gateway_knows_the_board_by_its_mac_address(boots):
    """DUID-LL: the same at every boot, different between boards; never from the shared machine-id."""
    assert boots["first"]["answers"], _story(boots, "first")
    assert all(answer == f"{BOARD6} 00:03:00:01:{MAC_A}" for answer in boots["first"]["answers"]), _story(boots, "first")


@pytest.mark.parametrize("boot", BOOTS)
def test_ipv4_on_the_interface_is_left_as_it_was(boots, boot):
    """The NFS root is mounted over it."""
    assert boots[boot]["ipv4_before"] == boots[boot]["ipv4_after"], _story(boots, boot)
    assert boots[boot]["ipv4_after"]["addresses"] == ["10.21.1.1/16"]
    assert any(route.startswith("default via 10.21.0.1 dev eth0") for route in boots[boot]["ipv4_after"]["routes"])


@pytest.mark.parametrize("boot", BOOTS)
def test_router_advertisements_stay_with_the_kernel(boots, boot):
    """The default route is the kernel's own, from the gateway's advertisement, not the client's."""
    assert boots[boot]["sysctls_after"]["accept_ra"] == "1", _story(boots, boot)
    assert " proto ra " in boots[boot]["default_route6"], _story(boots, boot)


@pytest.mark.parametrize("boot", BOOTS)
def test_the_client_changes_one_kernel_setting_of_the_interface(boots, boot):
    """addr_gen_mode, from 0 to 1, and it stays: the kernel makes no link-local address
    the next time the interface is brought up. The address it has is kept, and a
    netbooted board never takes the interface its root is mounted over down. dhcpcd
    does this on every interface it runs on, before it reads `noipv6rs`. Whether the
    kernel takes router advertisements and makes addresses from them is left alone.
    """
    assert boots[boot]["sysctls_before"] == {"accept_ra": "1", "autoconf": "1", "addr_gen_mode": "0"}, \
        _story(boots, boot)
    assert boots[boot]["sysctls_after"] == {"accept_ra": "1", "autoconf": "1", "addr_gen_mode": "1"}, \
        _story(boots, boot)


@pytest.mark.parametrize("boot", BOOTS)
def test_no_hook_rewrites_resolv_conf(boots, boot):
    assert boots[boot]["resolv_conf_untouched"], _story(boots, boot)


@pytest.mark.parametrize("boot", BOOTS)
def test_the_gateway_reaches_the_board_on_the_address(boots, boot):
    assert boots[boot]["gateway_reaches_board"], _story(boots, boot)


@pytest.mark.parametrize("boot", BOOTS)
def test_the_address_works_beyond_the_gateway_in_both_directions(boots, boot):
    """The swapped-in board has by then waited out the old lease: several router lifetimes."""
    assert boots[boot]["board_reaches_beyond"], _story(boots, boot)
    assert boots[boot]["beyond_reaches_board"], _story(boots, boot)


def test_the_same_board_gets_the_address_again_after_a_power_cut(boots):
    """With nothing left of its first boot, and without waiting for a lease to run out."""
    reboot = boots["reboot"]
    assert reboot["addresses"] == [f"{BOARD6}/128"], _story(boots, "reboot")
    assert reboot["answers"][len(boots["first"]["answers"]):], _story(boots, "reboot")
    assert not any(REFUSED in answer for answer in reboot["answers"]), _story(boots, "reboot")


def test_another_board_in_the_port_is_refused_while_the_old_lease_runs(boots):
    """The port's range holds one address: this is the wait the short lease bounds."""
    early = boots["swap"]["early"]
    assert early["addresses"] == [], _story(boots, "swap")
    assert early["answers"] and all(answer == f"00:03:00:01:{MAC_B} {REFUSED}" for answer in early["answers"]), \
        _story(boots, "swap")


def test_another_board_in_the_port_gets_the_address_once_the_old_lease_has_run_out(boots):
    """By itself: dhcpcd keeps soliciting."""
    swap = boots["swap"]
    assert swap["addresses"] == [f"{BOARD6}/128"], _story(boots, "swap")
    assert swap["answers"][-1] == f"{BOARD6} 00:03:00:01:{MAC_B}", _story(boots, "swap")


# --- ports 2 and 3: boards that stay up ---------------------------------------

@pytest.mark.parametrize("port", HELD)
def test_each_port_gives_its_own_address(run, port):
    """The ports share a /64 and the gateway's address; each board still gets its port's address."""
    boot = run["held"][port]["boot"]
    assert boot["addresses"] == [run["held"][port]["address"] + "/128"], _held_story(run, port)
    # v2SPP: 2001:db8:a137:210S::PP
    assert run["held"][port]["address"] == f"2001:db8:a137:210{port[2]}::{int(port[3:])}"
    assert boot["ipv4_before"] == boot["ipv4_after"], _held_story(run, port)
    assert boot["resolv_conf_untouched"], _held_story(run, port)


@pytest.mark.parametrize("port", HELD)
def test_a_board_keeps_its_default_route_past_the_router_lifetime(run, port):
    """Never without it, from the first advertisement to several lifetimes later."""
    later, watched = run["held"][port]["later"], run["route_watch"][port]
    assert later["uptime"] > 3 * RA_LIFETIME, _held_story(run, port)
    assert " proto ra " in later["default_route6"], _held_story(run, port)
    assert watched["first_seen"] is not None and watched["samples"] > RA_LIFETIME, _held_story(run, port)
    assert watched["missing"] == 0, _held_story(run, port)


@pytest.mark.parametrize("port", HELD)
def test_a_board_still_talks_ipv6_both_ways_past_the_router_lifetime(run, port):
    later = run["held"][port]["later"]
    assert later["board_reaches_beyond"], _held_story(run, port)
    assert later["beyond_reaches_board"], _held_story(run, port)
    assert later["gateway_reaches_board"], _held_story(run, port)


@pytest.mark.parametrize("port", HELD)
def test_a_board_that_stays_up_holds_its_one_address_and_nothing_else_changed(run, port):
    """No address made from the advertised prefix, IPv4 as at boot, the client still there."""
    boot, later = run["held"][port]["boot"], run["held"][port]["later"]
    assert later["addresses"] == [run["held"][port]["address"] + "/128"], _held_story(run, port)
    assert later["ipv4_after"] == boot["ipv4_before"], _held_story(run, port)
    assert later["sysctls_after"] == {"accept_ra": "1", "autoconf": "1", "addr_gen_mode": "1"}, _held_story(run, port)
    assert later["client_running"], _held_story(run, port)


@pytest.mark.parametrize("port", HELD)
def test_a_board_that_stays_up_renews_its_lease(run, port):
    """The lease (two minutes here) is renewed at half its time: the gateway answered again, with the same address."""
    boot, later = run["held"][port]["boot"], run["held"][port]["later"]
    renewals = later["answers"][len(boot["answers"]):]
    assert renewals, _held_story(run, port)
    assert all(answer == f"{run['held'][port]['address']} {run['held'][port]['duid']}"
               for answer in later["answers"]), _held_story(run, port)


@pytest.mark.parametrize("port", HELD)
def test_the_gateway_advertises_on_every_port_by_itself(run, port):
    """Periodic advertisements to all nodes of the port, long after the board last asked.

    The board's kernel solicits when its link comes up; a lifetime later
    only the gateway's own timer for this interface can be behind an
    advertisement here.
    """
    seen = run["wire"].get(port, {"to_all_nodes": []})
    after_one_lifetime = [at for at in seen["to_all_nodes"] if at > run["held"][port]["booted_at"] + RA_LIFETIME]
    assert len(after_one_lifetime) >= 3, _held_story(run, port)


def test_what_the_gateway_advertises(run):
    """On every port: a default router for the rendered lifetime; addresses by DHCPv6; the
    switch's prefix neither on-link nor for autoconfiguration; no MTU and no DNS server
    (roles/pxe/templates/radvd.conf.j2 says why not)."""
    wrong = {}
    for iface in IFACES:
        seen = run["wire"].get(iface, {})
        expected = {"router_lifetimes": [RA_LIFETIME], "managed": [True], "other": [True],
                    "prefixes": [[f"2001:db8:a137:210{iface[2]}::/64", "off-link", "not autonomous"]],
                    "mtu_or_dns_options": []}
        if not seen.get("to_all_nodes") or {key: seen.get(key) for key in expected} != expected:
            wrong[iface] = {key: seen.get(key) for key in expected}
    assert not wrong, json.dumps(wrong, indent=1)


def test_every_port_gets_its_periodic_advertisement_within_the_interval(run):
    """Every interface, not one per prefix: the ports with a board and the ports without.

    The port where boards come and go is left out: its interface is removed
    and made again with every board.
    """
    late = {}
    for iface in IFACES:
        if iface == STORY:
            continue
        sent = [at for at in run["wire"].get(iface, {}).get("to_all_nodes", []) if at <= run["wire_until"]]
        gaps = [after - before for before, after in zip(sent, sent[1:])]
        # A second for the clock that stamps them here.
        if len(sent) < 3 or max(gaps) > RA_INTERVAL + 1:
            late[iface] = {"advertisements": len(sent), "longest_gap": max(gaps, default=None)}
    assert not late, json.dumps(late, indent=1)


def test_radvd_advertises_on_the_interfaces_that_were_there_when_it_started(run):
    """Within its first interval; radvd spreads its first advertisements over up to 16 s."""
    slow = {iface: run["wire"].get(iface, {}).get("to_all_nodes", [None])[0] for iface in run["idle"]}
    slow = {iface: at for iface, at in slow.items() if at is None or at - run["radvd"]["started_at"] > 16 + 1}
    assert not slow, json.dumps(slow)
    assert sorted(run["idle"] + HELD + [STORY]) == sorted(IFACES)


# --- radvd reloaded, restarted, stopped and started under boards that are up ---

@pytest.mark.parametrize("what", ["reload", "restart", "stop_then_start"])
def test_no_board_loses_its_default_route_when_radvd_is(run, what):
    """A reload (the role's handler), a restart (a package upgrade) and a stop of a third
    of the router lifetime. radvd is told not to withdraw the router when it exits
    (RemoveAdvOnExit off): the gateway's kernel still forwards. With radvd's default the
    stop costs every board its route at once, for as long as radvd is down.
    """
    seen = run["interruptions"][what]
    assert sorted(seen["boards"]) == sorted(HELD)
    assert all(board["samples"] > 50 for board in seen["boards"].values()), json.dumps(seen)
    assert all(board["missing"] == 0 for board in seen["boards"].values()), json.dumps(seen)
    assert all(count == 0 for count in seen["withdrawals"].values()), json.dumps(seen)
