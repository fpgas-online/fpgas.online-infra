"""Render roles/pxe's per-port gateway configuration for tests.

ports.conf.j2 is dnsmasq's (DHCP for each switch port), radvd.conf.j2 is
radvd's (the router advertisements on each switch port).
"""

import importlib.util
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parent.parent
PXE = REPO / "ansible/roles/pxe"
TEMPLATE = PXE / "templates/ports.conf.j2"
RADVD_TEMPLATE = PXE / "templates/radvd.conf.j2"
# ports.conf.j2 of the commit this branch left main at: what a gateway has
# while pxe_board_ipv6 is off.
MAIN_TEMPLATE = REPO / "tests/fixtures/ports.conf.j2.main"
DEFAULTS = PXE / "defaults/main.yml"

# A site whose `switches` is one switch with one access port: switch 1
# port 1, as in the VM test's inventory (tests/inventory/host_vars/test-vm.yml).
SITE = {
    "ansible_managed": "rendered by tests/ports_conf_render.py",
    "switches": [{"index": 1, "model": "s3300", "mgmt_host": "127.0.0.1", "access_ports": 1,
                  "gateway_trunk_port": 49, "downstream_trunk_ports": [], "house_uplink_port": 52}],
    "pib_network": "10.21",
    "pib_network6_base": "2001:db8:a137:21",  # RFC 3849 documentation range
    "pib_domain": "test.fpgas.online",
    "eth_local": "eth-local",
}


def _port_vlan_map():
    spec = importlib.util.spec_from_file_location("port_vlans", REPO / "ansible/filter_plugins/port_vlans.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.port_vlan_map


def defaults() -> dict:
    return yaml.safe_load(DEFAULTS.read_text())


def switches(*access_ports: int) -> list[dict]:
    """Switches 1, 2, ... like SITE's, with these numbers of access ports, for `render(switches=...)`."""
    return [{**SITE["switches"][0], "index": index, "access_ports": count}
            for index, count in enumerate(access_ports, start=1)]


def _render(template: Path, overrides: dict) -> str:
    # trim_blocks: as Ansible's template module renders, so the bytes are a gateway's.
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True, trim_blocks=True)
    env.filters["port_vlan_map"] = _port_vlan_map()
    # Ansible's `bool` filter, for the values these templates give it.
    env.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    return env.from_string(template.read_text()).render(**{**defaults(), **SITE, **overrides})


def render(**overrides) -> str:
    """ports.conf for SITE with the role's defaults, and `overrides` on top."""
    return _render(TEMPLATE, overrides)


def render_as_on_main(**overrides) -> str:
    """ports.conf from the template as it was before boards had IPv6 (tests/fixtures), for the same site."""
    return _render(MAIN_TEMPLATE, overrides)


def render_radvd(**overrides) -> str:
    """radvd.conf for SITE with the role's defaults, and `overrides` on top."""
    return _render(RADVD_TEMPLATE, overrides)
