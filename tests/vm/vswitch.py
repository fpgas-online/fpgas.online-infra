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
    def __init__(self, vlan: int, host: str = "127.0.0.1",
                 accept_timeout: float = 120.0):
        self.vlan = vlan
        self.host = host
        # Bounds how long _run() will block in accept() waiting for a VM
        # that never connects, so a hung/misconfigured caller doesn't wedge
        # the switch thread (and thus stop()/join()) forever.
        self.accept_timeout = accept_timeout
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
        trunk_l.settimeout(self.accept_timeout)
        access_l.settimeout(self.accept_timeout)
        try:
            trunk, _ = trunk_l.accept()
        except OSError:
            return  # timed out, or closed by stop() before a peer connected
        # Register the trunk socket for cleanup *before* the second accept()
        # blocks -- otherwise a stop() call during that wait can't close it,
        # leaking the fd and leaving the accept() stranded past its timeout.
        self._socks.append(trunk)
        try:
            access, _ = access_l.accept()
        except OSError:
            return
        self._socks.append(access)

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

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
        # Bounded join so callers get clean teardown instead of leaving the
        # run/pump threads to finish on their own time (they're daemon
        # threads, so this is a courtesy, not a correctness requirement).
        for t in list(self._threads):
            if t is not threading.current_thread():
                t.join(timeout=join_timeout)
