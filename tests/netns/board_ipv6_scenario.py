#!/usr/bin/env python3
"""Boot boards on a site's switch ports against the real gateway daemons and watch their IPv6.

Run by tests/test_board_ipv6_netns.py, never by hand on a real host: it
must start as root in a network and a mount namespace of its own:

    sudo unshare --net --mount python3 board_ipv6_scenario.py \\
        PORTS_CONF RADVD_CONF BOARD_CONF WORKDIR ROUTER_LIFETIME RA_INTERVAL PORTS

PORTS is the number of access ports of each switch, by commas: `3` is one
switch with three ports, `48,48` two switches with 48 ports each.

The namespace it starts in becomes the gateway: one per-port interface for
every port, as roles/vlan_ports configures them (every interface of a
switch with the SAME gateway address in the SAME /64, and a host route to
its port's board), a real dnsmasq that reads PORTS_CONF (roles/pxe's
ports.conf.j2) and a real radvd that reads RADVD_CONF (roles/pxe's
radvd.conf.j2), both rendered for that site. Behind the gateway is one more
namespace, "beyond", for an address a board can only reach through its
default route.

A board is a network namespace on the other end of a port's interface, with
IPv4 already configured as the kernel's ip=dhcp leaves it, running the real
dhcpcd with BOARD_CONF (roles/onpi's fpgas-board-ipv6.conf) the way
fpgas-board-ipv6.sh starts it. dhcpcd's state directories are a fresh tmpfs
at every boot, as on a board, whose root is a tmpfs overlay.

Which ports get what:

  - Switch 1 port 1 is where boards come and go (below).
  - A few ports get a board that stays up for the whole run: port 2 and
    the last port of switch 1, the first and the last port of every other
    switch. Longer than several router lifetimes (ROUTER_LIFETIME, the
    seconds RADVD_CONF was rendered with), which is what a board's default
    route has to survive: a board asks for a router advertisement only
    when its link comes up, and after that its route lives on the
    gateway's periodic ones.
  - Every other port has a link and nothing that speaks on it. These
    interfaces exist before radvd starts, as a gateway's do; the others
    appear after it.

Switch 1 port 1 boots, in turn:

    first      board A, for the first time
    reboot     board A again after a power cut: nothing is left of the first
               boot but what the gateway remembers
    swap       board B (another MAC address) in the same port, straight
               after board A was unplugged

Last, with the boards that stayed up still there, radvd is reloaded, then
restarted, then stopped for a third of the router lifetime and started
again, and each time the boards' default route is read five times a second.

It prints one JSON object with what each boot saw, what the boards that
stayed up hold at the end, every router advertisement and solicitation that
crossed a port interface, radvd's start and size, and what each
interruption of radvd did to the boards' routes.

Standard library only: it runs under the system python3, as root.
"""

import json
import os
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

TRUNK = "eth-local"  # tests/ports_conf_render.py's eth_local
GATEWAY4 = "10.21.0.1"
BASE6 = "2001:db8:a137:21"  # tests/ports_conf_render.py's pib_network6_base
# Beyond the gateway: its uplink, and a host there.
UPLINK = "eth-uplink"
UPLINK6 = "2001:db8:ffff::1"
BEYOND = "beyond"
BEYOND6 = "2001:db8:ffff::2"
# Where the far ends of the ports nothing is plugged into are kept.
IDLE = "idle"
MAC_A = "02:00:00:b6:00:0a"
MAC_B = "02:00:00:b6:00:0b"
# How long a board that is the port's known client may take to hold the
# address (DHCPv6's first solicit waits up to a second; then duplicate
# address detection).
PROMPT = 30
# How long the swapped-in board may take: the lease of the board before it
# has to run out (PORTS_CONF's lease time, dnsmasq's minimum of two minutes
# in the test), and then the board's next solicit has to come round; they
# are sent at doubling intervals.
PATIENT = 330
# The boards that stay up are looked at once they have been up for this many
# router lifetimes.
LIFETIMES_HELD = 3
# The kernel settings of the board's interface that the test reads before
# the client starts and afterwards.
SYSCTLS = ("accept_ra", "autoconf", "addr_gen_mode")


def run(*argv: str, check: bool = True) -> str:
    proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{argv} failed ({proc.returncode}): {proc.stdout} {proc.stderr}")
    return proc.stdout


def reaches(*argv: str) -> bool:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True).returncode == 0


class Port:
    """A switch port: the gateway's interface for it, and the board plugged into it."""

    def __init__(self, switch: int, number: int) -> None:
        self.switch = switch
        self.number = number
        # ansible/filter_plugins/port_vlans.py's formulas.
        self.iface = f"v{2000 + 100 * switch + number}"
        self.netns = f"board-{self.iface}"
        self.board4 = f"10.21.{switch}.{number}"
        self.prefix6 = f"{BASE6}{switch:02d}"
        self.board6 = f"{self.prefix6}::{number}"
        # A board of this port that stays up: its own MAC address.
        self.mac = f"02:00:00:b6:{switch:02x}:{number:02x}"

    def board(self, *argv: str, check: bool = True) -> str:
        return run("ip", "netns", "exec", self.netns, *argv, check=check)


def duid_ll(mac: str) -> str:
    """DUID-LL of an Ethernet interface (RFC 8415 section 11.4), as dnsmasq logs it."""
    return "00:03:00:01:" + mac


class Wire(threading.Thread):
    """Every router advertisement the gateway sends and every solicitation it hears, by interface."""

    def __init__(self, started: float) -> None:
        super().__init__(daemon=True)
        self.started = started
        self.stopping = threading.Event()
        self.seen: dict = {}
        # Packets the gateway sends are shown only to a socket that asks
        # for every protocol.
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(3))
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)

    def run(self) -> None:
        while not self.stopping.is_set():
            if not select.select([self.sock], [], [], 0.2)[0]:
                continue
            packet, (iface, protocol, pkttype, _hatype, _addr) = self.sock.recvfrom(2048)
            # IPv6 (0x86DD), next header ICMPv6 (58), no extension headers.
            if protocol != 0x86DD or len(packet) < 48 or packet[6] != 58:
                continue
            outgoing = pkttype == 4  # PACKET_OUTGOING
            kind = packet[40]
            now = round(time.monotonic() - self.started, 1)
            seen = self.seen.setdefault(iface, {
                "to_all_nodes": [], "unicast": [], "solicitations": [], "withdrawals": [],
                "router_lifetimes": set(), "managed": set(), "other": set(), "prefixes": set(),
                "mtu_or_dns_options": set()})
            if kind == 133 and not outgoing:
                seen["solicitations"].append(now)
            elif kind == 134 and outgoing and len(packet) >= 56:
                lifetime = struct.unpack("!H", packet[46:48])[0]
                if lifetime == 0:
                    # "This router is gone": what a stopping radvd sends.
                    seen["withdrawals"].append(now)
                    continue
                destination = socket.inet_ntop(socket.AF_INET6, packet[24:40])
                seen["to_all_nodes" if destination == "ff02::1" else "unicast"].append(now)
                seen["router_lifetimes"].add(lifetime)
                seen["managed"].add(bool(packet[45] & 0x80))
                seen["other"].add(bool(packet[45] & 0x40))
                options = packet[56:]
                while len(options) >= 2 and options[1]:
                    if options[0] == 3 and len(options) >= 32:  # prefix information
                        seen["prefixes"].add((
                            f"{socket.inet_ntop(socket.AF_INET6, options[16:32])}/{options[2]}",
                            "on-link" if options[3] & 0x80 else "off-link",
                            "autonomous" if options[3] & 0x40 else "not autonomous"))
                    elif options[0] in (5, 25):  # MTU, recursive DNS server
                        seen["mtu_or_dns_options"].add({5: "mtu", 25: "rdnss"}[options[0]])
                    options = options[options[1] * 8:]

    def snapshot(self) -> dict:
        """What has been seen so far."""
        return {iface: {key: sorted(value) if isinstance(value, set) else list(value)
                        for key, value in dict(seen).items()}
                for iface, seen in sorted(dict(self.seen).items())}

    def stop(self) -> None:
        self.stopping.set()
        self.join(timeout=5)


class RouteWatch(threading.Thread):
    """Once a second: does each board that stays up have its IPv6 default route?"""

    def __init__(self, ports: list, started: float) -> None:
        super().__init__(daemon=True)
        self.ports = ports
        self.started = started
        self.stopping = threading.Event()
        # missing: how many samples found no route after the first that
        # found one, and when the first few of them were.
        self.seen = {port.iface: {"samples": 0, "first_seen": None, "missing": 0, "missing_from": [],
                                  "least_expires": None}
                     for port in ports}

    def run(self) -> None:
        while not self.stopping.wait(1):
            for port in self.ports:
                route = port.board("ip", "-6", "route", "show", "default", check=False).strip()
                now = round(time.monotonic() - self.started, 1)
                seen = self.seen[port.iface]
                seen["samples"] += 1
                if route:
                    if seen["first_seen"] is None:
                        seen["first_seen"] = now
                    expires = re.search(r"expires (\d+)sec", route)
                    if expires and (seen["least_expires"] is None or int(expires.group(1)) < seen["least_expires"]):
                        seen["least_expires"] = int(expires.group(1))
                elif seen["first_seen"] is not None:
                    seen["missing"] += 1
                    if len(seen["missing_from"]) < 5:
                        seen["missing_from"].append(now)

    def result(self) -> dict:
        self.stopping.set()
        self.join(timeout=10)
        return self.seen


class Gateway:
    def __init__(self, ports_conf: str, radvd_conf: str, workdir: str, idle: list, started: float) -> None:
        self.log = os.path.join(workdir, "dnsmasq.log")
        self.leases = os.path.join(workdir, "dnsmasq.leases")
        self.radvd_conf = radvd_conf
        self.radvd_log = os.path.join(workdir, "radvd.log")
        self.started = started
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
        # The uplink, and a host beyond it that knows the site's prefixes
        # are behind this gateway.
        run("ip", "netns", "add", BEYOND)
        run("ip", "link", "add", UPLINK, "type", "veth", "peer", "name", "eth0", "netns", BEYOND)
        run("ip", "addr", "add", f"{UPLINK6}/64", "dev", UPLINK, "nodad")
        run("ip", "link", "set", UPLINK, "up")
        run("ip", "netns", "exec", BEYOND, "ip", "link", "set", "lo", "up")
        run("ip", "netns", "exec", BEYOND, "ip", "addr", "add", f"{BEYOND6}/64", "dev", "eth0", "nodad")
        run("ip", "netns", "exec", BEYOND, "ip", "link", "set", "eth0", "up")
        run("ip", "netns", "exec", BEYOND, "ip", "route", "add", f"{BASE6}00::/56", "via", UPLINK6)
        # The ports nothing is plugged into: there before the daemons start.
        run("ip", "netns", "add", IDLE)
        for port in idle:
            self.interface(port, port.iface, IDLE, port.mac)
            run("ip", "netns", "exec", IDLE, "ip", "link", "set", port.iface, "up")
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
        self.radvd = None
        self.radvd_started_at = self.start_radvd()
        deadline = time.monotonic() + 10
        while not os.path.exists(self.log):
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"dnsmasq did not start (exit status {self.proc.poll()})")
            time.sleep(0.1)
        self.check()

    def start_radvd(self) -> float:
        """radvd as its Debian unit starts it, but in the foreground. When it was started."""
        with open(self.radvd_log, "a") as log:
            self.radvd = subprocess.Popen(
                ["radvd", "--nodaemon", "--logmethod", "stderr_clean", "--config", self.radvd_conf,
                 "--pidfile", "/run/radvd.pid"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        return round(time.monotonic() - self.started, 1)

    def stop_radvd(self) -> None:
        """As `systemctl stop radvd`: SIGTERM, and wait for it to go."""
        self.radvd.terminate()
        self.radvd.wait(timeout=20)

    def radvd_memory_kb(self) -> int:
        """Resident memory of radvd's processes (it runs as two)."""
        total = 0
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/status") as f:
                    status = dict(line.split(":\t", 1) for line in f.read().splitlines() if ":\t" in line)
            except OSError:
                continue
            if int(pid) == self.radvd.pid or int(status.get("PPid", "0")) == self.radvd.pid:
                total += int(status.get("VmRSS", "0 kB").split()[0])
        return total

    def check(self) -> None:
        if self.proc.poll() is not None:
            raise RuntimeError(f"dnsmasq exited ({self.proc.poll()})")
        if self.radvd.poll() is not None:
            with open(self.radvd_log) as f:
                raise RuntimeError(f"radvd exited ({self.radvd.poll()}): {f.read()}")

    def interface(self, port: Port, peer: str, netns: str, mac: str) -> None:
        """The gateway's interface for a port, its far end in `netns`."""
        run("ip", "link", "add", port.iface, "type", "veth", "peer", "name", peer, "address", mac, "netns", netns)
        # roles/vlan_ports, vlan.network.j2: the gateway's own addresses,
        # the same on every port of the switch, and a host route to the
        # board on each family.
        run("ip", "addr", "add", f"{GATEWAY4}/32", "dev", port.iface)
        run("ip", "addr", "add", f"{port.prefix6}::ffff/64", "dev", port.iface, "nodad")
        run("ip", "link", "set", port.iface, "up")
        run("ip", "route", "add", f"{port.board4}/32", "dev", port.iface)
        run("ip", "route", "add", f"{port.board6}/128", "dev", port.iface)

    def plug(self, port: Port, mac: str) -> None:
        """A board on the port: the far end of the gateway's per-port interface."""
        run("ip", "netns", "add", port.netns)
        self.interface(port, "eth0", port.netns, mac)

    def unplug(self, port: Port, client: subprocess.Popen | None) -> None:
        """A power cut: every process of the board dies where it stands."""
        # dhcpcd and the helpers it forks are one process group (start_client());
        # then whatever else is still in the board's namespace.
        if client is not None:
            try:
                os.killpg(client.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pid in run("ip", "netns", "pids", port.netns).split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        run("ip", "netns", "del", port.netns)  # takes both ends of the veth with it
        deadline = time.monotonic() + 10
        while run("ip", "-o", "link", "show", "dev", port.iface, check=False) and time.monotonic() < deadline:
            time.sleep(0.1)

    def answers(self, port: Port, duid: str) -> list[str]:
        """What dnsmasq answered this client, oldest first."""
        with open(self.log) as f:
            return [m.group(1) for m in re.finditer(
                rf"DHCP(?:ADVERTISE|REPLY)\({port.iface}\) (.*?)\s*$", f.read(), re.MULTILINE) if duid in m.group(1)]

    def stop(self) -> None:
        for proc in (self.proc, self.radvd):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=20)


RESOLV_CONF = "nameserver 10.21.0.1\n"  # what the image carries (ansible/ci-nfsroot.yml)


def prepare(port: Port) -> None:
    """The board as it is when fpgas-board-ipv6.service starts."""
    port.board("sysctl", "-q", "-w", "net.ipv6.conf.all.accept_ra=1", "net.ipv6.conf.default.accept_ra=1")
    port.board("ip", "link", "set", "lo", "up")
    # What the kernel's ip=dhcp leaves: the address and a default route.
    port.board("ip", "addr", "add", f"{port.board4}/16", "dev", "eth0")
    port.board("ip", "link", "set", "eth0", "up")
    port.board("ip", "route", "add", "default", "via", GATEWAY4, "dev", "eth0")


def start_client(port: Port, board_conf: str, resolv_conf: str) -> subprocess.Popen:
    """The client fpgas-board-ipv6.service runs."""
    with open(resolv_conf, "w") as f:
        f.write(RESOLV_CONF)
    # /run and /var/lib/dhcpcd are where dhcpcd keeps its pid, its DUID and
    # its lease: empty at every boot, and never the test host's own. Its
    # hooks must not run; if a change to BOARD_CONF lets them, what they
    # rewrite is the board's resolv.conf and hostname, not the test host's.
    # The board's resolv.conf goes over the file /etc/resolv.conf names: the
    # file itself, or where it is a link into /run (systemd-resolved's), a
    # file made for it on the board's own /run.
    target = os.path.realpath("/etc/resolv.conf")
    if target.startswith("/run/"):
        place = 'mkdir -p "$(dirname "$2")" && : > "$2" && '
    elif os.path.isfile(target):
        place = ""
    else:
        raise RuntimeError(f"/etc/resolv.conf is {target}, not a file: cannot stand the board's in for it")
    return subprocess.Popen(
        ["ip", "netns", "exec", port.netns, "unshare", "--mount", "--uts", "sh", "-c",
         'mount -t tmpfs tmpfs /run && mount -t tmpfs tmpfs /var/lib/dhcpcd && ' + place +
         'mount --bind "$1" "$2" && exec dhcpcd -B -f "$0" eth0',
         board_conf, resolv_conf, target],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True)


def ipv4_state(port: Port) -> dict:
    return {"addresses": re.findall(r"inet (\S+)", port.board("ip", "-4", "-o", "addr", "show", "dev", "eth0")),
            "routes": sorted(port.board("ip", "-4", "route", "show").splitlines())}


def sysctls(port: Port) -> dict:
    return {name: port.board("cat", f"/proc/sys/net/ipv6/conf/eth0/{name}").strip() for name in SYSCTLS}


def global6(port: Port) -> list[str]:
    """The board's usable global IPv6 addresses (not still in duplicate address detection)."""
    out = port.board("ip", "-6", "-o", "addr", "show", "dev", "eth0", "scope", "global", "-tentative")
    return re.findall(r"inet6 (\S+)", out)


def holds(port: Port, client: subprocess.Popen) -> dict:
    """What a board holds now, and whether its address works in both directions."""
    return {
        "addresses": global6(port),
        "ipv4_after": ipv4_state(port),
        "default_route6": port.board("ip", "-6", "route", "show", "default").strip(),
        "sysctls_after": sysctls(port),
        "gateway_reaches_board": reaches("ping", "-6", "-c", "1", "-W", "3", port.board6),
        # Beyond the gateway and back: the board's side of this is its
        # default route, the port's prefix being off-link.
        "board_reaches_beyond": reaches("ip", "netns", "exec", port.netns, "ping", "-6", "-c", "1", "-W", "3", BEYOND6),
        "beyond_reaches_board": reaches("ip", "netns", "exec", BEYOND, "ping", "-6", "-c", "1", "-W", "3", port.board6),
        "client_running": client.poll() is None,
    }


def watch(gateway: Gateway, port: Port, mac: str, board_conf: str, workdir: str,
          patience: int) -> tuple[dict, subprocess.Popen]:
    """Boot the board that is plugged in; report what it holds once it has an address, or at the deadline."""
    started = time.monotonic()
    resolv_conf = os.path.join(workdir, f"{port.netns}-resolv.conf")
    prepare(port)
    ipv4_before = ipv4_state(port)
    sysctls_before = sysctls(port)
    client = start_client(port, board_conf, resolv_conf)
    early = None  # what the board held, and was told, 15 s into the boot
    while time.monotonic() - started < patience:
        addresses = global6(port)
        if early is None and (addresses or time.monotonic() - started > 15):
            early = {"addresses": addresses, "answers": gateway.answers(port, duid_ll(mac))}
        if addresses:
            break
        if client.poll() is not None:
            raise RuntimeError(f"the board's client exited ({client.returncode}): {client.communicate()[0]}")
        gateway.check()
        time.sleep(0.5)
    seconds = round(time.monotonic() - started, 1)
    # The default route is the kernel's, from the router advertisement that
    # answers its solicitation: on a known board the address can be there first.
    deadline = time.monotonic() + 15
    while not port.board("ip", "-6", "route", "show", "default").strip() and time.monotonic() < deadline:
        time.sleep(0.5)
    result = {
        "seconds": seconds,
        "early": early,
        "answers": gateway.answers(port, duid_ll(mac)),
        "ipv4_before": ipv4_before,
        "sysctls_before": sysctls_before,
        "resolv_conf_untouched": open(resolv_conf).read() == RESOLV_CONF,
        **holds(port, client),
    }
    return result, client


def routes_during(action, held: list, window: float) -> dict:
    """Do `action` to radvd and read each held board's default route five times a second for `window` seconds.

    Per board: how many reads found no route, when the first of them was
    and when the route was back (seconds after the action began), so how
    long the board was without a default route.
    """
    began = time.monotonic()
    seen = {port.iface: {"samples": 0, "missing": 0, "lost_at": None, "back_at": None} for port in held}
    worker = threading.Thread(target=action, daemon=True)
    worker.start()
    while time.monotonic() - began < window:
        for port in held:
            route = port.board("ip", "-6", "route", "show", "default", check=False).strip()
            now = round(time.monotonic() - began, 1)
            one = seen[port.iface]
            one["samples"] += 1
            if not route:
                one["missing"] += 1
                if one["lost_at"] is None:
                    one["lost_at"] = now
                one["back_at"] = None
            elif one["lost_at"] is not None and one["back_at"] is None:
                one["back_at"] = now
        time.sleep(0.2)
    worker.join(timeout=30)
    for one in seen.values():
        one["without_route_s"] = (0 if one["lost_at"] is None
                                  else None if one["back_at"] is None
                                  else round(one["back_at"] - one["lost_at"], 1))
    return seen


def interruptions(gateway: Gateway, wire: Wire, held: list, lifetime: int, interval: int) -> dict:
    """What a reload, a restart and a stop of radvd each cost the boards that are up."""
    results = {}

    def reload() -> None:
        gateway.radvd.send_signal(signal.SIGHUP)  # the unit's ExecReload

    def restart() -> None:
        gateway.stop_radvd()
        gateway.start_radvd()

    def stop_then_start() -> None:
        gateway.stop_radvd()
        time.sleep(lifetime / 3)
        gateway.start_radvd()

    # Long enough for the stop, the pause, and radvd's first advertisements
    # after a start, which it spreads over up to 16 s.
    window = lifetime / 3 + 16 + 2 * interval
    for name, action in (("reload", reload), ("restart", restart), ("stop_then_start", stop_then_start)):
        before = wire.snapshot()
        began = round(time.monotonic() - gateway.started, 1)
        boards = routes_during(action, held, window)
        gateway.check()
        after = wire.snapshot()
        results[name] = {
            "began_at": began,
            "window_s": window,
            "boards": boards,
            # Advertisements with a router lifetime of zero sent meanwhile.
            "withdrawals": {port.iface: len(after.get(port.iface, {}).get("withdrawals", []))
                            - len(before.get(port.iface, {}).get("withdrawals", [])) for port in held},
        }
        # Every board has its route again before the next one.
        deadline = time.monotonic() + lifetime
        while time.monotonic() < deadline and not all(
                port.board("ip", "-6", "route", "show", "default", check=False).strip() for port in held):
            time.sleep(0.5)
    return results


def main() -> int:
    ports_conf, radvd_conf, board_conf, workdir, router_lifetime, ra_interval, port_counts = sys.argv[1:8]
    lifetime, interval = int(router_lifetime), int(ra_interval)
    if os.geteuid() != 0:
        sys.exit("must be root, in a network and mount namespace of its own (see the docstring)")
    # `ip netns add` leaves a file in /run/netns: keep it, too, out of the
    # host's /run. The mount namespace is ours (unshare --mount).
    run("mount", "-t", "tmpfs", "tmpfs", "/run")
    started = time.monotonic()
    counts = [int(count) for count in port_counts.split(",")]
    ports = {(switch, number): Port(switch, number)
             for switch, count in enumerate(counts, start=1) for number in range(1, count + 1)}
    story = ports[1, 1]
    held = [ports[key] for key in dict.fromkeys(
        [(1, 2), (1, counts[0])] + [key for switch in range(2, len(counts) + 1)
                                    for key in ((switch, 1), (switch, counts[switch - 1]))])]
    idle = [port for port in ports.values() if port is not story and port not in held]
    gateway = Gateway(ports_conf, radvd_conf, workdir, idle, started)
    wire = Wire(started)
    wire.start()
    routes = RouteWatch(held, started)
    results: dict = {"story": story.iface, "held": {}, "idle": [port.iface for port in idle],
                     "radvd": {"started_at": gateway.radvd_started_at}}
    logs: dict = {}
    plugged: dict = {}  # port -> its board's client, for the boards there now
    try:
        # The boards that stay up.
        booted = {}
        for port in held:
            gateway.plug(port, port.mac)
            plugged[port] = None
            booted[port] = time.monotonic()
            boot, plugged[port] = watch(gateway, port, port.mac, board_conf, workdir, PROMPT)
            results["held"][port.iface] = {"booted_at": round(booted[port] - started, 1), "address": port.board6,
                                           "duid": duid_ll(port.mac), "boot": boot}
        routes.start()

        # Switch 1 port 1's three boots.
        for name, mac, patience in (("first", MAC_A, PROMPT), ("reboot", MAC_A, PROMPT), ("swap", MAC_B, PATIENT)):
            gateway.plug(story, mac)
            plugged[story] = None
            results[name], plugged[story] = watch(gateway, story, mac, board_conf, workdir, patience)
            client = plugged.pop(story)
            gateway.unplug(story, client)
            logs[name] = client.communicate()[0]

        # The boards that stayed up, several router lifetimes after they came up.
        for port in held:
            remaining = booted[port] + LIFETIMES_HELD * lifetime + 1 - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            gateway.check()
            results["held"][port.iface]["later"] = {
                "uptime": round(time.monotonic() - booted[port], 1),
                "answers": gateway.answers(port, duid_ll(port.mac)),
                **holds(port, plugged[port])}
        results["route_watch"] = routes.result()
        results["wire"] = wire.snapshot()
        results["wire_until"] = round(time.monotonic() - started, 1)
        results["radvd"]["memory_kb"] = gateway.radvd_memory_kb()

        results["interruptions"] = interruptions(gateway, wire, held, lifetime, interval)
    finally:
        routes.stopping.set()
        wire.stop()
        for port, client in list(plugged.items()):
            gateway.unplug(port, client)
            if client is not None:
                logs[port.iface] = client.communicate()[0]
        gateway.stop()
    results["client_logs"] = logs
    with open(gateway.log) as f:
        results["dnsmasq_log"] = f.read()
    with open(gateway.radvd_log) as f:
        results["radvd_log"] = f.read()
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
