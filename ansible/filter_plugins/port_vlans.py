"""Derive the per-port VLAN/IP/hostname map from the switches list.

Single source of truth for the formulas on the Ansible side (the
fpgas-switch-setup CLI holds the same formulas for the switch side).
Spec: docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md
"""


def port_vlan_map(switches, pib_network, pib_network6_base):
    out = []
    for sw in switches:
        s = sw["index"]
        for p in range(1, sw["access_ports"] + 1):
            vlan = 2000 + 100 * s + p
            out.append({
                "switch": s,
                "port": p,
                "vlan": vlan,
                "iface": f"v{vlan}",
                "ip4": f"{pib_network}.{s}.{p}",
                "ip6": f"{pib_network6_base}{s:02d}::{p}",
                "hostname": f"pi-sw{s}-p{p}",
            })
    return out


class FilterModule:
    def filters(self):
        return {"port_vlan_map": port_vlan_map}
