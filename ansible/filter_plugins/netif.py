"""iface_by_mac: name of the physical interface carrying a MAC, from ansible_facts.

VLAN children inherit the parent's MAC, so prefer a non-VLAN device (no
'.' in the name, not a systemd-networkd vlan kind) and fall back to any
match. Returns '' when the MAC is not found (callers compare against the
desired name, so '' simply means "rename pending / unknown").
"""


def iface_by_mac(facts, mac):
    if not mac:
        return ""
    mac = str(mac).lower()
    candidates = []
    for name in facts.get("interfaces", []) or []:
        key = name.replace("-", "_")
        info = facts.get(key) or {}
        if str(info.get("macaddress", "")).lower() != mac:
            continue
        is_vlan = "." in name or info.get("type") == "vlan"
        candidates.append((is_vlan, name))
    if not candidates:
        return ""
    candidates.sort()  # (False, ...) physical first
    return candidates[0][1]


class FilterModule:
    def filters(self):
        return {"iface_by_mac": iface_by_mac}
