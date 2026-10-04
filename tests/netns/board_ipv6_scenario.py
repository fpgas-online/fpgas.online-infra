#!/usr/bin/env python3
"""Boot boards on one switch port against a real dnsmasq and watch the port's IPv6 address.

Run by tests/test_board_ipv6_netns.py, never by hand on a real host: it
must start as root in a network and a mount namespace of its own:

    sudo unshare --net --mount python3 board_ipv6_scenario.py PORTS_CONF BOARD_CONF WORKDIR

The namespace it starts in becomes the gateway, with the per-port interface
of switch 1 port 1 as roles/vlan_ports configures it and a real dnsmasq
that reads PORTS_CONF (roles/pxe's ports.conf.j2, rendered for that site).
A board is a second network namespace on the other end of that interface,
with IPv4 already configured as the kernel's ip=dhcp leaves it, running the
real dhcpcd with BOARD_CONF (roles/onpi's fpgas-board-ipv6.conf) the way
fpgas-board-ipv6.sh starts it. dhcpcd's state directories are a fresh tmpfs
at every boot, as on a board, whose root is a tmpfs overlay.

It boots, in turn:

    first      board A, for the first time
    reboot     board A again after a power cut: nothing is left of the first
               boot but what the gateway remembers
    swap       board B (another MAC address) in the same port, straight
               after board A was unplugged

and prints one JSON object with what each boot saw.

Standard library only: it runs under the system python3, as root.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

IFACE = "v2101"  # the gateway's interface for switch 1 port 1
TRUNK = "eth-local"  # tests/ports_conf_render.py's eth_local
GATEWAY4 = "10.21.0.1"
BOARD4 = "10.21.1.1"
PREFIX6 = "2001:db8:a137:2101"  # tests/test_board_ipv6_netns.py renders PORTS_CONF for it
BOARD6 = f"{PREFIX6}::1"
MAC_A = "02:00:00:b6:00:0a"
MAC_B = "02:00:00:b6:00:0b"
NETNS = "board"
# How long a board that is the port's known client may take to hold the
# address (DHCPv6's first solicit waits up to a second; then duplicate
# address detection).
PROMPT = 30
# How long the swapped-in board may take: the lease of the board before it
# has to run out (PORTS_CONF's lease time, dnsmasq's minimum of two minutes
# in the test), and then the board's next solicit has to come round; they
# are sent at doubling intervals.
PATIENT = 330


def run(*argv: str, check: bool = True) -> str:
    proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{argv} failed ({proc.returncode}): {proc.stdout} {proc.stderr}")
    return proc.stdout


def board(*argv: str, check: bool = True) -> str:
    return run("ip", "netns", "exec", NETNS, *argv, check=check)


def duid_ll(mac: str) -> str:
    """DUID-LL of an Ethernet interface (RFC 8415 section 11.4), as dnsmasq logs it."""
    return "00:03:00:01:" + mac


class Gateway:
    def __init__(self, ports_conf: str, workdir: str) -> None:
        self.log = os.path.join(workdir, "dnsmasq.log")
        self.leases = os.path.join(workdir, "dnsmasq.leases")
        # The gateway forwards (roles/firewall), so its neighbour
        # advertisements say "router" and a board keeps the default route it
        # took from the router advertisement.
        run("sysctl", "-q", "-w", "net.ipv6.conf.all.forwarding=1")
        run("ip", "link", "set", "lo", "up")
        # The trunk the per-port interfaces sit on (PORTS_CONF names it).
        # dnsmasq also needs one interface with a hardware address when it
        # starts, to make its own DHCPv6 server identifier from.
        run("ip", "link", "add", TRUNK, "type", "dummy")
        run("ip", "link", "set", TRUNK, "up")
        # A copy of the binary: where dnsmasq is confined by an AppArmor
        # profile attached to its path, the profile allows it neither this
        # lease file nor this log.
        binary = os.path.join(workdir, "dnsmasq")
        shutil.copy(shutil.which("dnsmasq"), binary)
        os.chmod(binary, 0o755)
        self.proc = subprocess.Popen(
            [binary, "--keep-in-foreground", "--conf-file=/dev/null", f"--conf-file={ports_conf}",
             "--bind-dynamic", "--port=0", "--log-dhcp", f"--log-facility={self.log}",
             f"--dhcp-leasefile={self.leases}", "--pid-file=", "--user=root"],
            stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while not os.path.exists(self.log):
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"dnsmasq did not start (exit status {self.proc.poll()})")
            time.sleep(0.1)

    def plug(self, mac: str) -> None:
        """A board on the port: the far end of the gateway's per-port interface."""
        run("ip", "netns", "add", NETNS)
        run("ip", "link", "add", IFACE, "type", "veth", "peer", "name", "eth0", "address", mac, "netns", NETNS)
        # roles/vlan_ports, vlan.network.j2: the gateway's own addresses and
        # a host route to the board on each family.
        run("ip", "addr", "add", f"{GATEWAY4}/32", "dev", IFACE)
        run("ip", "addr", "add", f"{PREFIX6}::ffff/64", "dev", IFACE, "nodad")
        run("ip", "link", "set", IFACE, "up")
        run("ip", "route", "add", f"{BOARD4}/32", "dev", IFACE)
        run("ip", "route", "add", f"{BOARD6}/128", "dev", IFACE)

    def unplug(self, client: subprocess.Popen) -> None:
        """A power cut: every process of the board dies where it stands."""
        # dhcpcd and the helpers it forks are one process group (boot());
        # then whatever else is still in the board's namespace.
        try:
            os.killpg(client.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for pid in run("ip", "netns", "pids", NETNS).split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        run("ip", "netns", "del", NETNS)  # takes both ends of the veth with it
        deadline = time.monotonic() + 10
        while run("ip", "-o", "link", "show", "dev", IFACE, check=False) and time.monotonic() < deadline:
            time.sleep(0.1)

    def answers(self, duid: str) -> list[str]:
        """What dnsmasq answered this client, oldest first."""
        with open(self.log) as f:
            return [m.group(1) for m in re.finditer(
                rf"DHCP(?:ADVERTISE|REPLY)\({IFACE}\) (.*?)\s*$", f.read(), re.MULTILINE) if duid in m.group(1)]

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)


RESOLV_CONF = "nameserver 10.21.0.1\n"  # what the image carries (ansible/ci-nfsroot.yml)


def boot(board_conf: str, resolv_conf: str) -> subprocess.Popen:
    """The board as it is when fpgas-board-ipv6.service starts, and the client the service runs."""
    with open(resolv_conf, "w") as f:
        f.write(RESOLV_CONF)
    board("sysctl", "-q", "-w", "net.ipv6.conf.all.accept_ra=1", "net.ipv6.conf.default.accept_ra=1")
    board("ip", "link", "set", "lo", "up")
    # What the kernel's ip=dhcp leaves: the address and a default route.
    board("ip", "addr", "add", f"{BOARD4}/16", "dev", "eth0")
    board("ip", "link", "set", "eth0", "up")
    board("ip", "route", "add", "default", "via", GATEWAY4, "dev", "eth0")
    # /run and /var/lib/dhcpcd are where dhcpcd keeps its pid, its DUID and
    # its lease: empty at every boot, and never the test host's own. Its
    # hooks must not run; if a change to BOARD_CONF lets them, what they
    # rewrite is the board's resolv.conf and hostname, not the test host's.
    return subprocess.Popen(
        ["ip", "netns", "exec", NETNS, "unshare", "--mount", "--uts", "sh", "-c",
         'mount -t tmpfs tmpfs /run && mount -t tmpfs tmpfs /var/lib/dhcpcd'
         ' && mount --bind "$1" /etc/resolv.conf && exec dhcpcd -B -f "$0" eth0',
         board_conf, resolv_conf],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True)


def ipv4_state() -> dict:
    return {"addresses": re.findall(r"inet (\S+)", board("ip", "-4", "-o", "addr", "show", "dev", "eth0")),
            "routes": sorted(board("ip", "-4", "route", "show").splitlines())}


def global6() -> list[str]:
    """The board's usable global IPv6 addresses (not still in duplicate address detection)."""
    out = board("ip", "-6", "-o", "addr", "show", "dev", "eth0", "scope", "global", "-tentative")
    return re.findall(r"inet6 (\S+)", out)


def watch(gateway: Gateway, mac: str, board_conf: str, workdir: str, patience: int) -> tuple[dict, subprocess.Popen]:
    """Boot the board that is plugged in; report what it holds once it has an address, or at the deadline."""
    started = time.monotonic()
    resolv_conf = os.path.join(workdir, "board-resolv.conf")
    client = boot(board_conf, resolv_conf)
    ipv4_before = ipv4_state()
    early = None  # what the board held, and was told, 15 s into the boot
    while time.monotonic() - started < patience:
        addresses = global6()
        if early is None and (addresses or time.monotonic() - started > 15):
            early = {"addresses": addresses, "answers": gateway.answers(duid_ll(mac))}
        if addresses:
            break
        if client.poll() is not None:
            break
        if gateway.proc.poll() is not None:
            raise RuntimeError(f"dnsmasq exited ({gateway.proc.poll()})")
        time.sleep(0.5)
    seconds = round(time.monotonic() - started, 1)
    # The default route is the kernel's, from the router advertisement that
    # answers its solicitation: on a known board the address can be there first.
    deadline = time.monotonic() + 15
    while not board("ip", "-6", "route", "show", "default").strip() and time.monotonic() < deadline:
        time.sleep(0.5)
    result = {
        "seconds": seconds,
        "addresses": global6() if client.poll() is None else [],
        "early": early,
        "answers": gateway.answers(duid_ll(mac)),
        "ipv4_before": ipv4_before,
        "ipv4_after": ipv4_state(),
        "resolv_conf_untouched": open(resolv_conf).read() == RESOLV_CONF,
        "default_route6": board("ip", "-6", "route", "show", "default").strip(),
        "accept_ra": board("cat", "/proc/sys/net/ipv6/conf/eth0/accept_ra").strip(),
        "gateway_reaches_board": subprocess.run(
            ["ping", "-6", "-c", "1", "-W", "3", BOARD6], stdin=subprocess.DEVNULL, capture_output=True).returncode == 0,
        "client_running": client.poll() is None,
    }
    return result, client


def main() -> int:
    ports_conf, board_conf, workdir = sys.argv[1:4]
    if os.geteuid() != 0:
        sys.exit("must be root, in a network and mount namespace of its own (see the docstring)")
    # `ip netns add` leaves a file in /run/netns: keep it, too, out of the
    # host's /run. The mount namespace is ours (unshare --mount).
    run("mount", "-t", "tmpfs", "tmpfs", "/run")
    gateway = Gateway(ports_conf, workdir)
    results: dict = {}
    logs: dict = {}
    try:
        gateway.plug(MAC_A)
        results["first"], client = watch(gateway, MAC_A, board_conf, workdir, PROMPT)
        gateway.unplug(client)
        logs["first"] = client.communicate()[0]

        gateway.plug(MAC_A)
        results["reboot"], client = watch(gateway, MAC_A, board_conf, workdir, PROMPT)
        gateway.unplug(client)
        logs["reboot"] = client.communicate()[0]

        gateway.plug(MAC_B)
        results["swap"], client = watch(gateway, MAC_B, board_conf, workdir, PATIENT)
        gateway.unplug(client)
        logs["swap"] = client.communicate()[0]
    finally:
        gateway.stop()
    results["client_logs"] = logs
    with open(gateway.log) as f:
        results["dnsmasq_log"] = f.read()
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
