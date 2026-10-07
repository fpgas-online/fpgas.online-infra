"""Per-board DNAT applies only to connections that arrive on the uplink (issue #204).

With one VLAN per port, boards must not reach each other: the forward chain
drops board-to-board traffic. It accepts DNATed connections first, though
(`ct status dnat`), so a per-board DNAT rule without an interface match lets
a board connect to the gateway's uplink address on another board's port and
get through. These tests fail if:
  - any DNAT rule in the per-port (switches) half of the firewall template
    lacks `iifname "{{ eth_uplink }}"`,
  - the forward chain stops dropping by default, or stops being the place
    where `ct status dnat` is accepted (the reason the match is needed),
  - verify-server stops checking the loaded rules for the same thing.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ansible/roles/firewall/templates/nftables.conf.j2"
VERIFY_SERVER = REPO / "ansible/verify-server.yml"
UPLINK_MATCH = 'iifname "{{ eth_uplink }}"'


def _per_port_half() -> str:
    text = TEMPLATE.read_text()
    start = text.index("{% if switches is defined %}")
    end = text.index("{% else %}", start)
    return text[start:end]


def test_every_per_board_dnat_rule_matches_the_uplink_interface():
    rules = [line.strip() for line in _per_port_half().splitlines() if " dnat to " in line and not line.lstrip().startswith("#")]
    assert rules, "the per-port half of the firewall template has no DNAT rules: has the template moved?"
    for rule in rules:
        assert rule.startswith(UPLINK_MATCH), f"DNAT rule without the uplink interface match: {rule}"


def test_the_forward_chain_still_drops_by_default_and_accepts_dnat():
    half = _per_port_half()
    assert "type filter hook forward priority 0; policy drop;" in half
    assert "ct status dnat counter accept;" in half


def test_verify_server_checks_the_loaded_dnat_rules():
    text = VERIFY_SERVER.read_text()
    assert "nft list chain ip nat prerouting" in text
    assert "Assert every per-board DNAT rule is limited to the uplink interface" in text


def test_the_per_board_ports_are_forwarded_over_ipv6_too():
    # Board ssh over IPv6 (Tim's answer welland-1, 2026-10-07): the same ports on
    # the gateway's own global address, to the board's address in its switch's /64.
    half = _per_port_half()
    assert "table ip6 nat {" in half
    assert "ip6 daddr {{ pib_network6_base }}00::1" in half
    assert "dnat to [{{ e.ip6 }}]:22" in half
    assert "dnat to [{{ e.ip6 }}]:4444" in half


def test_verify_server_checks_the_loaded_ipv6_dnat_rules():
    text = VERIFY_SERVER.read_text()
    assert "nft list chain ip6 nat prerouting" in text
    assert "Assert the IPv6 per-board DNAT rules are there and limited to the uplink interface" in text
