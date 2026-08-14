"""Emulate one switch access port between two QEMU socket netdevs.

QEMU 'socket' netdev framing: 4-byte big-endian length + raw ethernet
frame. The Pi VM connects to .access_port (untagged), the server VM to
.trunk_port (tagged). Frames crossing access->trunk gain an 802.1Q tag;
trunk->access frames are untagged if the VID matches, dropped otherwise
-- exactly what a hardware access port does.
"""

import socket
import struct
import threading

TPID = b"\x81\x00"


def tag_frame(frame: bytes, vlan: int) -> bytes:
    return frame[:12] + TPID + struct.pack("!H", vlan & 0x0FFF) + frame[12:]


def untag_frame(frame: bytes, vlan: int) -> bytes | None:
    if frame[12:14] != TPID:
        return None
    if int.from_bytes(frame[14:16], "big") & 0x0FFF != vlan:
        return None
    return frame[:12] + frame[16:]


class AccessPortSwitch:
    def __init__(self, vlan: int, host: str = "127.0.0.1"):
        self.vlan = vlan
        self.host = host
        self._socks: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.trunk_port = 0
        self.access_port = 0

    def _listen(self) -> socket.socket:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, 0))
        s.listen(1)
        self._socks.append(s)
        return s

    def start(self) -> None:
        trunk_l, access_l = self._listen(), self._listen()
        self.trunk_port = trunk_l.getsockname()[1]
        self.access_port = access_l.getsockname()[1]
        t = threading.Thread(target=self._run, args=(trunk_l, access_l),
                             daemon=True)
        t.start()
        self._threads.append(t)

    def _run(self, trunk_l, access_l) -> None:
        trunk, _ = trunk_l.accept()
        access, _ = access_l.accept()
        self._socks += [trunk, access]

        def pump(src, dst, xform):
            try:
                while not self._stop.is_set():
                    hdr = self._recvall(src, 4)
                    frame = self._recvall(src, struct.unpack("!I", hdr)[0])
                    out = xform(frame)
                    if out is not None:
                        dst.sendall(struct.pack("!I", len(out)) + out)
            except (OSError, ConnectionError):
                pass

        a = threading.Thread(
            target=pump, args=(access, trunk, lambda f: tag_frame(f, self.vlan)),
            daemon=True)
        b = threading.Thread(
            target=pump, args=(trunk, access, lambda f: untag_frame(f, self.vlan)),
            daemon=True)
        a.start(); b.start()
        self._threads += [a, b]

    @staticmethod
    def _recvall(sock, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("peer closed")
            buf += chunk
        return buf

    def stop(self) -> None:
        self._stop.set()
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
