"""Every access port's ssh port is forwarded on IPv6 as well as IPv4 (part of #191).

The board pages print `ssh -p <switch><pp>22 pi@<site>`, and `<site>` has an
AAAA record: the gateway. Without an IPv6 forward a client that tries IPv6
first hangs for its connect timeout before it falls back to IPv4, and an
IPv6-only client never gets in. These tests render the firewall template
for a site and fail if:
  - an access port lacks its IPv4 or its IPv6 ssh forward, or the forward
    leads to another port's board,
  - an IPv6 forward lacks the uplink interface match (#204: a board must
    not reach another board's sshd through it) or stops being limited to
    the gateway's own addresses,
  - the port numbers stop following the inventory (they are generated from
    `switches`; a switch may have fewer access ports than another),
  - verify-server stops checking the loaded rules for the same things.

tests/test_firewall_dnat_netns.py loads the same rendering into a kernel and
makes the connections.
"""

import re

from tests.firewall_render import REPO, SITE, port_map, render

VERIFY_SERVER = REPO / "ansible/verify-server.yml"
VERIFY_PI = REPO / "ansible/verify-pi.yml"

# Two switches with different numbers of access ports, like a real site.
TWO_SWITCHES = {**SITE, "switches": [{"index": 1, "access_ports": 40}, {"index": 2, "access_ports": 48}]}


def _table(rendered: str, name: str) -> str:
    start = rendered.index(f"table {name} {{")
    end = rendered.find("\ntable ", start + 1)
    return rendered[start:end if end != -1 else len(rendered)]


def _dnat_rules(rendered: str, table: str) -> list[str]:
    return [line.strip() for line in _table(rendered, table).splitlines()
            if " dnat to " in line and not line.lstrip().startswith("#")]


def _ssh_port(entry: dict) -> str:
    return f"{entry['switch']}{entry['port']:02d}22"


def test_every_access_port_has_its_ssh_forward_on_both_families():
    rendered = render(TWO_SWITCHES)
    rules4 = _dnat_rules(rendered, "ip nat")
    rules6 = _dnat_rules(rendered, "ip6 nat")
    entries = port_map(TWO_SWITCHES)
    assert len(entries) == 88
    for e in entries:
        port = _ssh_port(e)
        assert f'iifname "eth-uplink" ip daddr 10.0.2.15 tcp dport {{ {port} }} dnat to {e["ip4"]}:22' in rules4
        assert f'iifname "eth-uplink" fib daddr type local tcp dport {{ {port} }} dnat to [{e["ip6"]}]:22' in rules6
    # One IPv6 rule per access port and nothing else: no aux port, no port
    # of a switch position that has no access port.
    assert len(rules6) == len(entries)


def test_the_port_numbers_follow_the_inventory():
    rendered = render(TWO_SWITCHES)
    ports6 = sorted(int(m) for rule in _dnat_rules(rendered, "ip6 nat") for m in re.findall(r"tcp dport \{ (\d+) \}", rule))
    assert ports6 == [10000 * s + 100 * p + 22 for s, n in ((1, 40), (2, 48)) for p in range(1, n + 1)]
    assert ports6[0] == 10122 and 14022 in ports6 and 14122 not in ports6 and ports6[-1] == 24822


def test_every_ipv6_dnat_rule_is_for_the_uplink_and_the_gateways_own_addresses():
    rules = _dnat_rules(render(TWO_SWITCHES), "ip6 nat")
    assert rules, "the per-port half of the firewall template has no IPv6 DNAT rules: has the template moved?"
    for rule in rules:
        assert rule.startswith('iifname "eth-uplink" fib daddr type local tcp dport '), rule


def test_ipv6_needs_no_source_translation():
    """The boards hold routed global addresses: only the destination is rewritten."""
    table = _table(render(TWO_SWITCHES), "ip6 nat")
    assert "masquerade" not in table and "snat" not in table


def test_the_forward_chain_accepts_dnat_for_both_families():
    """One `inet` chain forwards both families; it must stay drop-by-default."""
    filter_table = _table(render(TWO_SWITCHES), "inet filter")
    assert "type filter hook forward priority 0; policy drop;" in filter_table
    assert "ct status dnat counter accept;" in filter_table


def test_a_site_without_switches_gets_no_ipv6_nat():
    legacy = {k: v for k, v in SITE.items() if k != "switches"}
    legacy.update(eth_local_address="10.21.0.1", eth_local_netmask=24, switch={"nos": [{"port": 1}]})
    assert "table ip6 nat" not in render(legacy)


def test_verify_server_checks_the_loaded_rules_on_both_families():
    text = VERIFY_SERVER.read_text()
    assert "nft list chain ip6 nat prerouting" in text
    assert "Assert every per-board IPv6 DNAT rule is limited to the uplink interface" in text
    assert "Assert every access port's ssh port is forwarded to its board on IPv4 and IPv6" in text


def test_verify_pi_checks_the_address_the_ipv6_forward_leads_to():
    text = VERIFY_PI.read_text()
    assert "Assert per-port IPv6 address present" in text
    assert "The gateway reaches this Pi's sshd on its IPv6 address" in text
