#!/usr/bin/env python3
"""Load a rendered gateway ruleset in a network namespace and try the per-port ssh forwards.

Run by tests/test_firewall_dnat_netns.py, never by hand on a real host: it
must start in a network namespace of its own, as root of that namespace:

    unshare --user --map-root-user --net python3 dnat_scenario.py RULESET
    sudo unshare --net python3 dnat_scenario.py RULESET

The namespace it starts in becomes the gateway. It builds three more:

    client --- eth-uplink [gateway] v2101 --- board 1 (switch 1 port 1)
                                    v2102 --- board 2 (switch 1 port 2)

with the addresses the roles give a site whose `switches` is one switch
with two access ports (roles/vlan_ports, roles/netif): the gateway's
transit address on the uplink, its public IPv6 address on the internal
trunk, one address per board. It loads RULESET (roles/firewall's
nftables.conf.j2, rendered for that site) with the real `nft`, starts a
listener on port 22 of each board, makes the connections in ATTEMPTS and
prints one JSON object: for each attempt, which board answered and the
client address that board saw, or the error.

Standard library only: it runs under the system python3, as root.
"""

import json
import os
import socket
import subprocess
import sys
import time

GATEWAY4 = "10.0.2.15"  # the transit address (eth_uplink_static_address)
GATEWAY6_UPLINK = "2001:db8:ffff::2"
GATEWAY6_PUBLIC = "2001:db8:a137:2100::1"  # <pib_network6_base>00::1, on the trunk
CLIENT4 = "10.0.2.2"
CLIENT6 = "2001:db8:ffff::1"
BOARDS = {
    "board1": {"iface": "v2101", "ip4": "10.21.1.1", "ip6": "2001:db8:a137:2101::1"},
    "board2": {"iface": "v2102", "ip4": "10.21.1.2", "ip6": "2001:db8:a137:2101::2"},
}
# name: (namespace the connection starts in, destination address, port)
ATTEMPTS = {
    # From outside, to the port of switch 1 port 1 and of switch 1 port 2.
    "v4_port1": ("client", GATEWAY4, 10122),
    "v4_port2": ("client", GATEWAY4, 10222),
    "v6_public_port1": ("client", GATEWAY6_PUBLIC, 10122),
    "v6_public_port2": ("client", GATEWAY6_PUBLIC, 10222),
    "v6_uplink_port1": ("client", GATEWAY6_UPLINK, 10122),
    # From a board, to another board's port: must not get through (#204).
    "v4_board2_to_port1": ("board2", GATEWAY4, 10122),
    "v6_board2_to_port1": ("board2", GATEWAY6_PUBLIC, 10122),
    # From outside, straight to a board's own address: still not forwarded.
    "v6_direct_board1": ("client", BOARDS["board1"]["ip6"], 22),
    # The aux port has no IPv6 forward.
    "v6_public_aux1": ("client", GATEWAY6_PUBLIC, 10144),
}
CONNECT_TIMEOUT = 3


def sh(*argv: str) -> None:
    subprocess.run(argv, check=True)


def listen(name: str) -> None:
    """Answer every connection to port 22 with this board's name and the peer's address."""
    server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    server.bind(("::", 22))
    server.listen(16)
    print("listening", flush=True)
    while True:
        conn, peer = server.accept()
        conn.sendall(f"{name} {peer[0].removeprefix('::ffff:')}\n".encode())
        conn.close()


def connect(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as conn:
            board, _, peer = conn.makefile().readline().strip().partition(" ")
            print(json.dumps({"board": board, "peer": peer}))
    except OSError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))


class Namespace:
    """A network namespace, held open by a sleeping process."""

    def __init__(self, name: str):
        self.name = name
        self.holder = subprocess.Popen(["unshare", "--net", "sleep", "600"])
        own = os.readlink("/proc/self/ns/net")
        deadline = time.monotonic() + 10
        while os.readlink(f"/proc/{self.holder.pid}/ns/net") == own:
            if time.monotonic() > deadline:
                raise RuntimeError(f"namespace {name} was not created")
            time.sleep(0.05)

    def argv(self, *argv: str) -> list[str]:
        return ["nsenter", "--target", str(self.holder.pid), "--net", *argv]

    def sh(self, *argv: str) -> None:
        sh(*self.argv(*argv))


def sysctl(path: str, value: str) -> None:
    with open(f"/proc/sys/{path}", "w") as f:
        f.write(value)


def main(ruleset: str) -> None:
    # A new network namespace holds a loopback interface and nothing else.
    # Anything more is a real machine, whose firewall this would replace.
    interfaces = [name for _index, name in socket.if_nameindex()]
    if interfaces != ["lo"]:
        sys.exit(f"refusing to run: not a fresh network namespace (interfaces: {interfaces})")
    me = [sys.executable, os.path.abspath(__file__)]
    spaces = {name: Namespace(name) for name in ("client", *BOARDS)}
    listeners = []
    try:
        # The gateway: this namespace.
        sh("ip", "link", "set", "lo", "up")
        sh("ip", "link", "add", "eth-uplink", "type", "veth", "peer", "name", "eth0",
           "netns", str(spaces["client"].holder.pid))
        sh("ip", "addr", "add", f"{GATEWAY4}/24", "dev", "eth-uplink")
        sh("ip", "addr", "add", f"{GATEWAY6_UPLINK}/64", "dev", "eth-uplink", "nodad")
        sh("ip", "link", "set", "eth-uplink", "up")
        sh("ip", "link", "add", "eth-local", "type", "dummy")
        sh("ip", "addr", "add", "10.21.0.1/24", "dev", "eth-local")
        sh("ip", "addr", "add", f"{GATEWAY6_PUBLIC}/56", "dev", "eth-local", "nodad")
        sh("ip", "link", "set", "eth-local", "up")
        for name, board in BOARDS.items():
            pid = str(spaces[name].holder.pid)
            sh("ip", "link", "add", board["iface"], "type", "veth", "peer", "name", "eth0", "netns", pid)
            # As roles/vlan_ports/templates/vlan.network.j2: the same gateway
            # addresses on every port, and a host route to the port's board.
            sh("ip", "addr", "add", "10.21.0.1/32", "dev", board["iface"])
            sh("ip", "addr", "add", "2001:db8:a137:2101::ffff/64", "dev", board["iface"], "nodad")
            sh("ip", "link", "set", board["iface"], "up")
            sh("ip", "route", "add", f"{board['ip4']}/32", "dev", board["iface"])
            sh("ip", "-6", "route", "add", f"{board['ip6']}/128", "dev", board["iface"])
        sysctl("net/ipv4/ip_forward", "1")
        sysctl("net/ipv6/conf/all/forwarding", "1")

        client = spaces["client"]
        client.sh("ip", "link", "set", "lo", "up")
        client.sh("ip", "addr", "add", f"{CLIENT4}/24", "dev", "eth0")
        client.sh("ip", "addr", "add", f"{CLIENT6}/64", "dev", "eth0", "nodad")
        client.sh("ip", "link", "set", "eth0", "up")
        client.sh("ip", "route", "add", "default", "via", GATEWAY4)
        client.sh("ip", "-6", "route", "add", "default", "via", GATEWAY6_UPLINK)

        for name, board in BOARDS.items():
            space = spaces[name]
            space.sh("ip", "link", "set", "lo", "up")
            space.sh("ip", "addr", "add", f"{board['ip4']}/32", "dev", "eth0")
            space.sh("ip", "addr", "add", f"{board['ip6']}/128", "dev", "eth0", "nodad")
            space.sh("ip", "link", "set", "eth0", "up")
            space.sh("ip", "route", "add", "10.21.0.1/32", "dev", "eth0")
            space.sh("ip", "route", "add", "default", "via", "10.21.0.1")
            space.sh("ip", "-6", "route", "add", "2001:db8:a137:2101::ffff/128", "dev", "eth0")
            space.sh("ip", "-6", "route", "add", "default", "via", "2001:db8:a137:2101::ffff")
            listener = subprocess.Popen(space.argv(*me, "listen", name), stdout=subprocess.PIPE, text=True)
            listeners.append(listener)
            if listener.stdout.readline().strip() != "listening":
                raise RuntimeError(f"the listener on {name} did not start")

        sh("nft", "-f", ruleset)

        # Without the firewall in the way the boards do answer, so a refused
        # attempt below is the ruleset's doing: the gateway itself reaches
        # each board's port 22 on both families.
        results = {}
        for name, board in BOARDS.items():
            for family in ("ip4", "ip6"):
                out = subprocess.run([*me, "connect", board[family], "22"],
                                     check=True, capture_output=True, text=True).stdout
                results[f"gateway_to_{name}_{family}"] = json.loads(out)
        for name, (source, host, port) in ATTEMPTS.items():
            out = subprocess.run(spaces[source].argv(*me, "connect", host, str(port)),
                                 check=True, capture_output=True, text=True).stdout
            results[name] = json.loads(out)
        results["ruleset"] = subprocess.run(["nft", "list", "ruleset"], check=True,
                                            capture_output=True, text=True).stdout
        print(json.dumps(results))
    finally:
        for proc in [*listeners, *(space.holder for space in spaces.values())]:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    if sys.argv[1] == "listen":
        listen(sys.argv[2])
    elif sys.argv[1] == "connect":
        connect(sys.argv[2], int(sys.argv[3]))
    else:
        main(sys.argv[1])
