"""A board takes its port's IPv6 address, again after a reboot, and so does a board swapped in (#222).

The VM test (verify-pi.yml) shows a netbooted virtual Pi holding its
address, once: the harness never reboots the Pi and has one board per port.
This test runs what the VM test cannot: a real dnsmasq with the per-port
configuration roles/pxe renders, and the real dhcpcd with the settings
roles/onpi installs, in network namespaces (tests/netns/board_ipv6_scenario.py):

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

It needs to be root, in a network and a mount namespace of its own: it runs
as root, or through passwordless sudo (an unprivileged user namespace will
not do: dhcpcd drops privileges to its own user). Where it cannot run, or
dnsmasq or dhcpcd is missing, it is skipped, except with
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

from tests.ports_conf_render import render

REPO = Path(__file__).resolve().parent.parent
SCENARIO = REPO / "tests/netns/board_ipv6_scenario.py"
BOARD_CONF = REPO / "ansible/roles/onpi/files/board-ipv6/fpgas-board-ipv6.conf"
REQUIRED = os.environ.get("BOARD_IPV6_NETNS_TEST") == "1"
# dnsmasq, dhcpcd, ip and sysctl live in sbin, which is not on every user's PATH.
ENV = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/sbin:/sbin"}
TOOLS = ("dnsmasq", "dhcpcd", "ip", "unshare", "mount", "sysctl", "ping")

# What the scenario's site (tests/ports_conf_render.py) gives switch 1 port 1.
BOARD6 = "2001:db8:a137:2101::1"
MAC_A = "02:00:00:b6:00:0a"
MAC_B = "02:00:00:b6:00:0b"
REFUSED = "no addresses available"


def _root_command() -> list[str] | None:
    """The command prefix that starts a process as root in new network and mount namespaces."""
    if os.geteuid() == 0:
        return ["unshare", "--net", "--mount"]
    if subprocess.run(["sudo", "-n", "true"], stdin=subprocess.DEVNULL, capture_output=True).returncode == 0:
        return ["sudo", "env", f"PATH={ENV['PATH']}", "unshare", "--net", "--mount"]
    return None


@pytest.fixture(scope="module")
def boots(tmp_path_factory):
    missing = [tool for tool in TOOLS if not shutil.which(tool, path=ENV["PATH"])]
    prefix = None if missing else _root_command()
    if prefix is None:
        reason = f"missing {missing}" if missing else "not root and no passwordless sudo"
        if REQUIRED:
            pytest.fail(f"BOARD_IPV6_NETNS_TEST=1 but the test cannot run: {reason}")
        pytest.skip(reason)
    workdir = tmp_path_factory.mktemp("board-ipv6")
    ports_conf = workdir / "ports.conf"
    # dnsmasq's shortest lease: the swap waits for it to run out.
    ports_conf.write_text(render(pxe_port_lease6="2m"))
    proc = subprocess.run([*prefix, sys.executable, str(SCENARIO), str(ports_conf), str(BOARD_CONF), str(workdir)],
                          env=ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, f"the scenario failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def _story(boots: dict, boot: str) -> str:
    """What to print when an assertion about a boot fails."""
    return (f"{json.dumps(boots[boot], indent=1)}\n--- dhcpcd\n{boots['client_logs'][boot]}"
            f"\n--- dnsmasq\n{boots['dnsmasq_log']}")


def test_a_board_gets_its_ports_address(boots):
    first = boots["first"]
    assert first["addresses"] == [f"{BOARD6}/128"], _story(boots, "first")
    assert first["client_running"]


def test_the_gateway_knows_the_board_by_its_mac_address(boots):
    """DUID-LL: the same at every boot, different between boards; never from the shared machine-id."""
    assert boots["first"]["answers"], _story(boots, "first")
    assert all(answer == f"{BOARD6} 00:03:00:01:{MAC_A}" for answer in boots["first"]["answers"]), _story(boots, "first")


@pytest.mark.parametrize("boot", ["first", "reboot", "swap"])
def test_ipv4_on_the_interface_is_left_as_it_was(boots, boot):
    """The NFS root is mounted over it."""
    assert boots[boot]["ipv4_before"] == boots[boot]["ipv4_after"], _story(boots, boot)
    assert boots[boot]["ipv4_after"]["addresses"] == ["10.21.1.1/16"]
    assert any(route.startswith("default via 10.21.0.1 dev eth0") for route in boots[boot]["ipv4_after"]["routes"])


@pytest.mark.parametrize("boot", ["first", "reboot", "swap"])
def test_router_advertisements_stay_with_the_kernel(boots, boot):
    """dhcpcd adds the address and nothing else: the default route is the kernel's own."""
    assert boots[boot]["accept_ra"] == "1", _story(boots, boot)
    assert " proto ra " in boots[boot]["default_route6"], _story(boots, boot)


@pytest.mark.parametrize("boot", ["first", "reboot", "swap"])
def test_no_hook_rewrites_resolv_conf(boots, boot):
    assert boots[boot]["resolv_conf_untouched"], _story(boots, boot)


@pytest.mark.parametrize("boot", ["first", "reboot", "swap"])
def test_the_gateway_reaches_the_board_on_the_address(boots, boot):
    assert boots[boot]["gateway_reaches_board"], _story(boots, boot)


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
