#!/usr/bin/env python3
"""Netns lab: verify dnsmasq per-interface-tag single-address ranges.

De-risks the VLAN-per-port network design (see
docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md): the real
gateway will carry ~150 VLAN sub-interfaces, all holding the *same* IPv4
gateway address 10.21.0.1/32, and dnsmasq must hand out one fixed IPv4 +
IPv6 address per interface using the "tag named after the receiving
interface" mechanism (dnsmasq sets a tag equal to the interface name on
every DHCP request).

Topology (all inside three netns, run as root):
  ns "gw":  veth gwv1 <-> ns "pi1": veth pi1v      (emulates access port 1)
            gwv1.2101 (vlan)         pi1v.2101 (vlan)
            veth gwv2 <-> ns "pi2": veth pi2v      (emulates access port 2)
            gwv2.2102 (vlan)         pi2v.2102 (vlan)

  gw runs a single dnsmasq bound to both gwv1.2101 and gwv2.2102, with
  tag-based single-address dhcp-ranges (v4 + v6) per interface, and RA.
  pi1/pi2 run busybox udhcpc (v4) and dhclient -6 (v6) on their vlan iface.

Run (as root): python3 tests/lab/dnsmasq_lab.py
   or:         sudo uv run tests/lab/dnsmasq_lab.py
Prints PASS/FAIL per check; exits non-zero if any FAIL.

WORKING CONFIGURATION (see tests/lab/RESULTS.md for the full writeup and
the deviations from the initial guess this script started from):
  - IPv4: interface keeps 10.21.0.1/32 (same on every VLAN sub-iface); the
    per-VLAN dhcp-range MUST use a netmask broader than /32 (255.255.0.0
    here) or dnsmasq refuses the request ("no address range available"),
    even though only one address is ever handed out (start==end).
  - IPv6: the interface's own address must be configured at the SAME
    prefix length as the dhcp-range (a /64 here, not /128) -- dnsmasq
    requires the range's prefix-len to be >= the interface's real prefix.
  - RA: dnsmasq's own `off-link` dhcp-range mode flag (2.90+) suppresses
    the on-link (L) bit in the advertised prefix -- no radvd needed.
"""

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---- lab addressing --------------------------------------------------
# Real design: every VLAN gateway sub-interface holds the SAME v4 address.
GW_V4 = "10.21.0.1"

VLANS = [
    # (vlan_id, gw_veth, pi_ns, pi_veth, client_v4, gw_v6, client_v6)
    (2101, "gwv1", "pi1", "pi1v", "10.21.1.1",
     "2404:e80:a137:2101::ffff", "2404:e80:a137:2101::1"),
    (2102, "gwv2", "pi2", "pi2v", "10.21.2.1",
     "2404:e80:a137:2102::ffff", "2404:e80:a137:2102::1"),
]

UDHCPC_SCRIPT = """#!/bin/sh
# Minimal busybox udhcpc script: apply the offered v4 address only.
# IMPORTANT: only flush IPv4 on deconfig -- flushing all families here
# also strips the kernel-formed IPv6 link-local address, which then
# breaks any subsequent DHCPv6 test on the same interface.
case "$1" in
  deconfig)
    ip -4 addr flush dev "$interface" 2>/dev/null
    ;;
  bound|renew)
    ip addr add "$ip"/32 dev "$interface" 2>/dev/null
    [ -n "$router" ] && ip route replace default via "$router" dev "$interface" 2>/dev/null
    ;;
esac
exit 0
"""


def sh(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    # cmd is always a fixed string from this file, never user input;
    # split to a list so no shell is involved.
    return subprocess.run(cmd.split(), check=check, text=True,
                           capture_output=True)


def ns(name: str, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return sh(f"ip netns exec {name} {cmd}", check=check)


def setup(workdir: Path) -> Path:
    sh("ip netns add gw")
    sh("ip netns add pi1")
    sh("ip netns add pi2")
    sh("ip link add gwv1 netns gw type veth peer name pi1v netns pi1")
    sh("ip link add gwv2 netns gw type veth peer name pi2v netns pi2")

    for n in ("gw", "pi1", "pi2"):
        ns(n, "ip link set lo up")

    conf_lines = [
        "port=0",
        "bind-interfaces",
        "log-dhcp",
        f"dhcp-leasefile={workdir}/leases",
    ]
    for vlan_id, gw_veth, pi_ns, pi_veth, v4, gw_v6, v6 in VLANS:
        gw_iface = f"{gw_veth}.{vlan_id}"
        pi_iface = f"{pi_veth}.{vlan_id}"
        ns("gw", f"ip link set {gw_veth} up")
        ns("gw", f"ip link add link {gw_veth} name {gw_iface} type vlan id {vlan_id}")
        ns("gw", f"ip link set {gw_iface} up")
        ns(pi_ns, f"ip link set {pi_veth} up")
        ns(pi_ns, f"ip link add link {pi_veth} name {pi_iface} type vlan id {vlan_id}")
        ns(pi_ns, f"ip link set {pi_iface} up")
        # THE CONFIG UNDER TEST: /32 v4 gw address, identical across every
        # VLAN; /64 v6 gw address (must match the dhcp-range prefix len).
        ns("gw", f"ip addr add {GW_V4}/32 dev {gw_iface}")
        ns("gw", f"ip addr add {gw_v6}/64 dev {gw_iface}")
        conf_lines.append(f"interface={gw_iface}")
        conf_lines.append(f"dhcp-range=tag:{gw_iface},{v4},{v4},255.255.0.0,12h")
        conf_lines.append(
            f"dhcp-range=tag:{gw_iface},{v6},{v6},off-link,64,12h")
    conf_lines.append("enable-ra")

    conf = workdir / "lab.conf"
    conf.write_text("\n".join(conf_lines) + "\n")

    script = workdir / "udhcpc-script.sh"
    script.write_text(UDHCPC_SCRIPT)
    script.chmod(0o755)

    return conf


def start_dnsmasq(conf: Path, workdir: Path) -> None:
    ns("gw", f"dnsmasq --conf-file={conf} --pid-file={workdir}/pid "
              f"--log-facility={workdir}/log")
    time.sleep(1)


def teardown(workdir: Path | None) -> None:
    if workdir is not None:
        pidfile = workdir / "pid"
        if pidfile.exists():
            pid = pidfile.read_text().strip()
            if pid:
                sh(f"kill {pid}", check=False)
    # dhclient -6 -1 still daemonizes instead of exiting after the lease is
    # bound, so kill it explicitly via the pid files v6_check() wrote --
    # `ip netns del` alone leaves these orphaned holding the netns open.
    for tag in ("pi1", "pi2"):
        pidf = Path(f"/run/dhclient6-{tag}.pid")
        if pidf.exists():
            pid = pidf.read_text().strip()
            if pid:
                sh(f"kill {pid}", check=False)
    for n in ("gw", "pi1", "pi2"):
        sh(f"ip netns del {n}", check=False)


def v4_check(pi_ns: str, pi_iface: str, script: Path, expect: str,
             other: str) -> tuple[bool, str]:
    ns(pi_ns, f"busybox udhcpc -i {pi_iface} -n -q -f -t 3 -T 2 -s {script}",
       check=False)
    got = ns(pi_ns, f"ip -4 addr show {pi_iface}", check=False).stdout
    ok = expect in got and other not in got
    return ok, got


def v6_check(pi_ns: str, pi_iface: str, expect: str, other: str,
             tag: str) -> tuple[bool, str]:
    leases = f"/var/lib/dhcp/dhclient6-{tag}.leases"
    pidf = f"/run/dhclient6-{tag}.pid"
    sh(f"rm -f {leases} {pidf}", check=False)
    time.sleep(2)  # let DAD form the link-local before dhclient needs it
    ns(pi_ns, f"timeout 8 dhclient -6 -v -1 -pf {pidf} -lf {leases} {pi_iface}",
       check=False)
    got = ns(pi_ns, f"ip -6 addr show {pi_iface}", check=False).stdout
    ok = expect in got and other not in got
    return ok, got


def ra_checks(pi_ns: str, pi_iface: str) -> tuple[bool, bool, str]:
    ra = ns(pi_ns, f"rdisc6 -1 -w 3000 {pi_iface}", check=False).stdout
    received = "from" in ra
    m = re.search(r"On-link\s*:\s*(Yes|No)", ra)
    no_onlink = bool(m and m.group(1) == "No")
    return received, no_onlink, ra


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="lab-", dir="tmp")).resolve()
    teardown(None)  # best-effort in case a previous run left netns behind
    results: dict[str, bool] = {}
    try:
        conf = setup(workdir)
        start_dnsmasq(conf, workdir)
        script = workdir / "udhcpc-script.sh"

        vlan1 = VLANS[0]
        vlan2 = VLANS[1]
        pi1_iface = f"{vlan1[3]}.{vlan1[0]}"
        pi2_iface = f"{vlan2[3]}.{vlan2[0]}"

        ok, _ = v4_check("pi1", pi1_iface, script, vlan1[4], vlan2[4])
        results["v4-single-range-by-tag"] = ok

        ok, _ = v6_check("pi1", pi1_iface, vlan1[6], vlan2[6], "pi1")
        results["v6-single-range-by-tag"] = ok

        received, no_onlink, _ = ra_checks("pi1", pi1_iface)
        results["ra-received"] = received
        results["ra-no-onlink-prefix"] = no_onlink

        # Second VLAN + cross-isolation: each client gets ITS OWN
        # interface's address, never the other interface's.
        ok, _ = v4_check("pi2", pi2_iface, script, vlan2[4], vlan1[4])
        results["v4-isolation-vlan2"] = ok

        ok, _ = v6_check("pi2", pi2_iface, vlan2[6], vlan1[6], "pi2")
        results["v6-isolation-vlan2"] = ok

        log = (workdir / "log").read_text() if (workdir / "log").exists() else ""
        print(log[-3000:])
    finally:
        teardown(workdir)
        sh(f"rm -rf {workdir}", check=False)

    ok = True
    for name, passed in results.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    Path("tmp").mkdir(exist_ok=True)
    sys.exit(main())
