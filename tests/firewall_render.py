"""Render roles/firewall's nftables.conf.j2 the way Ansible does, for the tests."""

import importlib.util
from pathlib import Path

import jinja2

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "ansible/roles/firewall/templates/nftables.conf.j2"

_spec = importlib.util.spec_from_file_location("port_vlans", REPO / "ansible/filter_plugins/port_vlans.py")
port_vlans = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(port_vlans)

# A site with one switch and two access ports: the gateway of
# tests/netns/dnat_scenario.py.
SITE = {
    "ansible_managed": "Ansible managed",
    "eth_uplink": "eth-uplink",
    "eth_local": "eth-local",
    "eth_uplink_static_address": "10.0.2.15",
    "pib_network": "10.21",
    "pib_network6_base": "2001:db8:a137:21",
    "switches": [{"index": 1, "access_ports": 2}],
}


def port_map(site: dict) -> list[dict]:
    return port_vlans.port_vlan_map(site["switches"], site["pib_network"], site["pib_network6_base"])


def render(site: dict) -> str:
    # trim_blocks as Ansible's template module sets it; StrictUndefined so a
    # variable the tests forgot fails here instead of rendering as nothing.
    env = jinja2.Environment(trim_blocks=True, undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    env.filters["port_vlan_map"] = port_vlans.port_vlan_map
    return env.from_string(TEMPLATE.read_text()).render(**site)
