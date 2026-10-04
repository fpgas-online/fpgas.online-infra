"""The per-port ssh forwards work, on IPv4 and IPv6, in the ruleset the firewall role renders.

The board pages print `ssh -p <switch><pp>22 pi@<site>`. `<site>` has an A
and an AAAA record, so the port has to lead to the board on both families,
for connections that arrive on the uplink and for no others (#204).

The VM test cannot show this: its server VM's uplink is QEMU user-mode
networking with IPv6 off (tests/vm/vm_manager.py says why), so nothing can
arrive on the uplink over IPv6 there. This test loads the rendered ruleset
with the real `nft` into a network namespace that stands for the gateway,
with a client namespace on its uplink and two board namespaces on per-port
interfaces (tests/netns/dnat_scenario.py), and makes real TCP connections.

It needs a network namespace it is root in: it runs as root, or in an
unprivileged user namespace where the kernel allows one, or through
passwordless sudo. Where none of those works it is skipped, except with
FIREWALL_NETNS_TEST=1 (set in CI, lint.yml's pytest job), where being
unable to run is a failure.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.firewall_render import SITE, render

SCENARIO = Path(__file__).resolve().parent / "netns" / "dnat_scenario.py"
REQUIRED = os.environ.get("FIREWALL_NETNS_TEST") == "1"
# nft, ip and nsenter live in sbin, which is not on every user's PATH.
ENV = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/sbin:/sbin"}
CLIENT4 = "10.0.2.2"
CLIENT6 = "2001:db8:ffff::1"


def _works(*argv: str) -> bool:
    return subprocess.run(argv, env=ENV, stdin=subprocess.DEVNULL, capture_output=True).returncode == 0


def _namespace_command() -> list[str] | None:
    """The command prefix that starts a process as root of a new network namespace."""
    if os.geteuid() == 0:
        return ["unshare", "--net"]
    unprivileged = ["unshare", "--user", "--map-root-user", "--net"]
    # Creating the namespace is not enough: some kernels allow that and then
    # refuse every privileged operation inside it.
    if _works(*unprivileged, "ip", "link", "add", "probe0", "type", "dummy"):
        return unprivileged
    if _works("sudo", "-n", "true"):
        return ["sudo", "env", f"PATH={ENV['PATH']}", "unshare", "--net"]
    return None


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    missing = [tool for tool in ("nft", "ip", "unshare", "nsenter") if not shutil.which(tool, path=ENV["PATH"])]
    prefix = None if missing else _namespace_command()
    if prefix is None:
        reason = (f"missing {missing}" if missing else
                  "no way to become root of a network namespace (not root, no user namespaces, no passwordless sudo)")
        if REQUIRED:
            pytest.fail(f"FIREWALL_NETNS_TEST=1 but the test cannot run: {reason}")
        pytest.skip(reason)
    ruleset = tmp_path_factory.mktemp("ruleset") / "nftables.conf"
    ruleset.write_text(render(SITE))
    proc = subprocess.run([*prefix, sys.executable, str(SCENARIO), str(ruleset)],
                          env=ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"the scenario failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_the_boards_answer_when_nothing_is_in_the_way(results):
    """Otherwise a refused connection below would prove nothing."""
    for board in ("board1", "board2"):
        for family in ("ip4", "ip6"):
            assert results[f"gateway_to_{board}_{family}"].get("board") == board, results[f"gateway_to_{board}_{family}"]


def test_ipv4_port_reaches_its_board_with_the_clients_address(results):
    assert results["v4_port1"] == {"board": "board1", "peer": CLIENT4}
    assert results["v4_port2"] == {"board": "board2", "peer": CLIENT4}


def test_ipv6_port_reaches_its_board_with_the_clients_address(results):
    """To the site's public address, which is on the internal trunk, not on the uplink."""
    assert results["v6_public_port1"] == {"board": "board1", "peer": CLIENT6}
    assert results["v6_public_port2"] == {"board": "board2", "peer": CLIENT6}


def test_ipv6_port_works_on_the_uplinks_own_address_too(results):
    assert results["v6_uplink_port1"] == {"board": "board1", "peer": CLIENT6}


def test_a_board_cannot_use_another_boards_port(results):
    """#204: the forwards are for connections that arrive on the uplink."""
    assert "error" in results["v4_board2_to_port1"], results["v4_board2_to_port1"]
    assert "error" in results["v6_board2_to_port1"], results["v6_board2_to_port1"]


def test_a_boards_own_ipv6_address_is_still_not_reachable_from_outside(results):
    """Only the per-port ports are opened; the board prefix stays closed."""
    assert "error" in results["v6_direct_board1"], results["v6_direct_board1"]


def test_the_aux_port_has_no_ipv6_forward(results):
    assert "error" in results["v6_public_aux1"], results["v6_public_aux1"]
