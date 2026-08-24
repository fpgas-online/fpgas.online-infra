"""Unit tests for the netif role's iface_by_mac filter (ansible/filter_plugins/netif.py)."""
import importlib.util
import pathlib

MOD = pathlib.Path(__file__).resolve().parents[1] / "ansible" / "filter_plugins" / "netif.py"
spec = importlib.util.spec_from_file_location("netif_filter", MOD)
netif = importlib.util.module_from_spec(spec)
spec.loader.exec_module(netif)
iface_by_mac = netif.iface_by_mac


def facts(**ifaces):
    # ansible_facts keys use '_' for '-'; the interfaces list keeps the real name
    f = {"interfaces": list(ifaces)}
    for name, (mac, typ) in ifaces.items():
        f[name.replace("-", "_")] = {"device": name, "macaddress": mac, "type": typ}
    return f


def test_fresh_install_names():
    f = facts(lo=("00:00:00:00:00:00", "loopback"),
              enp2s0=("0c:c4:7a:16:3b:4b", "ether"),
              enp3s0=("0c:c4:7a:16:3b:4a", "ether"))
    assert iface_by_mac(f, "0C:C4:7A:16:3B:4B") == "enp2s0"
    assert iface_by_mac(f, "0c:c4:7a:16:3b:4a") == "enp3s0"


def test_renamed_host_with_vlan_children_prefers_parent():
    f = facts(**{"eth-local": ("0c:c4:7a:16:3b:4a", "ether"),
                 "v2101": ("0c:c4:7a:16:3b:4a", "vlan"),
                 "eth-local.21": ("0c:c4:7a:16:3b:4a", "ether")})
    assert iface_by_mac(f, "0c:c4:7a:16:3b:4a") == "eth-local"


def test_unknown_or_empty_mac():
    f = facts(enp2s0=("0c:c4:7a:16:3b:4b", "ether"))
    assert iface_by_mac(f, "de:ad:be:ef:00:01") == ""
    assert iface_by_mac(f, "") == ""
    assert iface_by_mac({}, "0c:c4:7a:16:3b:4b") == ""


def test_filter_module_registers():
    assert "iface_by_mac" in netif.FilterModule().filters()
