"""Loopback integration test for AccessPortSwitch's socket pump.

Connects two plain TCP sockets to the switch's trunk_port and access_port --
exactly what QEMU's `-netdev socket` / `-nic socket` do -- and pushes QEMU
length-prefixed ethernet frames through both directions, without booting any
VM. This exercises the actual accept()/pump threads in tests/vm/vswitch.py
(tests/test_vswitch.py only covers the pure tag_frame/untag_frame helpers).
"""

import socket
import struct

from tests.vm.vswitch import AccessPortSwitch

VLAN = 2101
DST = bytes(6)
SRC = bytes.fromhex("525400aabb02")
PAYLOAD = b"\x08\x00" + b"x" * 46  # ethertype IPv4 + body
FRAME = DST + SRC + PAYLOAD

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


def test_pump_tags_access_to_trunk_and_untags_trunk_to_access():
    switch = AccessPortSwitch(VLAN, accept_timeout=SOCK_TIMEOUT)
    switch.start()
    try:
        trunk = socket.create_connection(
            ("127.0.0.1", switch.trunk_port), timeout=SOCK_TIMEOUT)
        access = socket.create_connection(
            ("127.0.0.1", switch.access_port), timeout=SOCK_TIMEOUT)
        trunk.settimeout(SOCK_TIMEOUT)
        access.settimeout(SOCK_TIMEOUT)
        try:
            # access -> trunk: untagged frame in, 802.1Q-tagged frame out.
            _send_frame(access, FRAME)
            tagged = _recv_frame(trunk)
            assert tagged[:12] == FRAME[:12]
            assert tagged[12:14] == b"\x81\x00"
            assert int.from_bytes(tagged[14:16], "big") & 0x0FFF == VLAN
            assert tagged[16:] == FRAME[12:]

            # trunk -> access: VLAN-tagged frame in, untagged frame out.
            frame_in = FRAME[:12] + b"\x81\x00" + struct.pack("!H", VLAN) + FRAME[12:]
            _send_frame(trunk, frame_in)
            untagged = _recv_frame(access)
            assert untagged == FRAME
        finally:
            trunk.close()
            access.close()
    finally:
        switch.stop()
