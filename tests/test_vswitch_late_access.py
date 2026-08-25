"""Regression test: the Pi (access side) connects long after the server.

In the real harness (tests/vm/run_tests.py) the switch is started once, via
the default AccessPortSwitch(VLAN). The server VM connects to the trunk port
within seconds, but the Pi VM only connects to the access port after the
entire server-provisioning phase -- tens of minutes under TCG. The switch
MUST still be accepting the access connection when the Pi finally appears,
and MUST relay frames afterwards.

Previously the switch used a fixed 120s accept() ceiling measured from
switch start, so its thread died long before the Pi booted, leaving the Pi
with no connectivity (DHCP/netboot stalled). The fix makes the default wait
unbounded (until stop()), polling for the peer. These tests guard that:

  * the default accept_timeout is None (unbounded) -- fails on the old 120.0;
  * a peer connecting several poll windows late still gets its frames relayed.
"""

import socket
import struct
import time

from tests.vm.vswitch import AccessPortSwitch

VLAN = 2101
DST = bytes(6)
SRC = bytes.fromhex("525400aabb02")
PAYLOAD = b"\x08\x00" + b"x" * 46
FRAME = DST + SRC + PAYLOAD

# Several _ACCEPT_POLL windows (1.0s each): proves the accept loop keeps
# waiting across iterations rather than accepting once and giving up.
LATE_DELAY = 3.5
SOCK_TIMEOUT = 5.0


def _send_frame(sock: socket.socket, frame: bytes) -> None:
    sock.sendall(struct.pack("!I", len(frame)) + frame)


def _recvall(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def _recv_frame(sock: socket.socket) -> bytes:
    (length,) = struct.unpack("!I", _recvall(sock, 4))
    return _recvall(sock, length)


def test_default_accept_timeout_is_unbounded():
    # The harness relies on this: a fixed ceiling would kill the switch before
    # the Pi (access side) boots after the long server phase.
    assert AccessPortSwitch(VLAN).accept_timeout is None


def test_access_connecting_late_still_relays_with_default():
    # Construct exactly as the harness does -- default (unbounded) timeout.
    switch = AccessPortSwitch(VLAN)
    switch.start()
    try:
        # Server (trunk) connects promptly, like the server VM.
        trunk = socket.create_connection(
            ("127.0.0.1", switch.trunk_port), timeout=SOCK_TIMEOUT)
        trunk.settimeout(SOCK_TIMEOUT)

        # Pi (access) only appears well after several accept-poll windows --
        # a compressed stand-in for the real tens-of-minutes server phase.
        time.sleep(LATE_DELAY)

        access = socket.create_connection(
            ("127.0.0.1", switch.access_port), timeout=SOCK_TIMEOUT)
        access.settimeout(SOCK_TIMEOUT)
        try:
            _send_frame(access, FRAME)
            tagged = _recv_frame(trunk)
            assert tagged[:12] == FRAME[:12]
            assert tagged[12:14] == b"\x81\x00"
            assert int.from_bytes(tagged[14:16], "big") & 0x0FFF == VLAN
            assert tagged[16:] == FRAME[12:]
        finally:
            trunk.close()
            access.close()
    finally:
        switch.stop()
