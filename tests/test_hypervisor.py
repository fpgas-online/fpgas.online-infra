"""Unit tests rendering the hypervisor role's networkd templates
(ansible/roles/hypervisor/templates) with representative tweed-hv vars."""
import pathlib

import jinja2

TPL = pathlib.Path(__file__).resolve().parents[1] / "ansible" / "roles" / "hypervisor" / "templates"

# tweed-hv's real values (ansible/inventory/host_vars/tweed-hv.yml + role defaults)
FPGAS_MAC = "0c:c4:7a:16:3b:4a"
UPLINK_MAC = "0c:c4:7a:16:3b:4b"


def render(name, **vars):
    # trim_blocks matches Ansible's template defaults (lstrip_blocks off)
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True,
                             keep_trailing_newline=True)
    tpl = env.from_string((TPL / name).read_text())
    return tpl.render(ansible_managed="test-managed", **vars)


def test_link_mac_match_lowercased():
    out = render("link.j2", item={"name": "eth-local", "mac": FPGAS_MAC.upper()})
    assert f"MACAddress={FPGAS_MAC}" in out
    assert "Type=ether" in out
    assert "Name=eth-local" in out


def test_bridge_netdev_fpgas():
    out = render("bridge.netdev.j2", item={"name": "br-fpgas", "mtu": 1504})
    assert "Name=br-fpgas" in out
    assert "Kind=bridge" in out
    assert "MTUBytes=1504" in out
    assert "STP=no" in out
    assert "VLANFiltering=no" in out


def test_bridge_netdev_uplink_has_no_mtu_override():
    out = render("bridge.netdev.j2", item={"name": "br-uplink"})
    assert "Name=br-uplink" in out
    assert "MTUBytes" not in out
    assert "STP=no" in out and "VLANFiltering=no" in out


def test_bridge_port_enslaves_nic():
    out = render("bridge-port.network.j2",
                 item={"name": "eth-local", "bridge": "br-fpgas", "mtu": 1504})
    assert "Name=eth-local" in out
    assert "Bridge=br-fpgas" in out
    assert "MTUBytes=1504" in out
    assert "RequiredForOnline=no" in out


def test_br_fpgas_network_no_address_by_default():
    out = render("br-fpgas.network.j2", hypervisor_fpgas_mtu=1504)
    assert "MTUBytes=1504" in out
    # The host stays silent on the FPGA net: no address at all (the gw VM
    # owns 10.21.0.1), not even link-local.
    assert "[Address]" not in out
    assert "Address=" not in out
    assert "LinkLocalAddressing=no" in out


def test_br_fpgas_network_optional_host_address():
    out = render("br-fpgas.network.j2", hypervisor_fpgas_mtu=1504,
                 hypervisor_fpgas_host_address="10.21.255.1/24")
    assert "Address=10.21.255.1/24" in out
    assert "LinkLocalAddressing=no" not in out


def test_br_uplink_network_addresses_and_gateways():
    out = render("br-uplink.network.j2",
                 hypervisor_uplink_address="10.99.21.2/30",
                 hypervisor_uplink_gateway="10.99.21.1",
                 hypervisor_uplink_address6="2404:e80:a137:9921::2/126",
                 hypervisor_uplink_gateway6="2404:e80:a137:9921::1",
                 hypervisor_uplink_dns_server="10.99.21.1")
    assert "Address=10.99.21.2/30" in out
    assert "Gateway=10.99.21.1\n" in out
    assert "Address=2404:e80:a137:9921::2/126" in out
    assert "Gateway=2404:e80:a137:9921::1" in out
    assert "DNS=10.99.21.1" in out


def test_br_uplink_network_v4_only():
    out = render("br-uplink.network.j2",
                 hypervisor_uplink_address="10.99.21.2/30",
                 hypervisor_uplink_gateway="10.99.21.1")
    assert "Address=10.99.21.2/30" in out
    assert "2404:" not in out
    assert "DNS=" not in out
