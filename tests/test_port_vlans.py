import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "port_vlans", Path("ansible/filter_plugins/port_vlans.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SWITCHES = [
    {"index": 1, "model": "s3300", "mgmt_host": "10.1.5.11",
     "access_ports": 48, "gateway_trunk_port": 49,
     "downstream_trunk_ports": [50], "house_uplink_port": 52},
    {"index": 2, "model": "gsm7252ps", "mgmt_host": "10.1.5.23",
     "access_ports": 48, "gateway_trunk_port": 49,
     "downstream_trunk_ports": [50], "house_uplink_port": 52},
]


def test_port_vlan_map():
    out = mod.port_vlan_map(SWITCHES, "10.21", "2404:e80:a137:21")
    assert len(out) == 96
    e = next(x for x in out if x["switch"] == 1 and x["port"] == 7)
    assert e == {"switch": 1, "port": 7, "vlan": 2107, "iface": "v2107",
                 "ip4": "10.21.1.7", "ip6": "2404:e80:a137:2101::7",
                 "hostname": "pi-sw1-p7"}
    e2 = next(x for x in out if x["switch"] == 2 and x["port"] == 48)
    assert e2["ip6"] == "2404:e80:a137:2102::48"
    assert e2["vlan"] == 2248 and e2["ip4"] == "10.21.2.48"
