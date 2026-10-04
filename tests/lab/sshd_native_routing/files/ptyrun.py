#!/usr/bin/env python3
"""Run a command on a real terminal (a pty), type scripted input, record it.

Runs inside the lab's client container, where the only Python is the
distribution's python3 (standard library only).

    ptyrun.py SPEC.json     (or the spec on stdin with "-")

Spec: {"argv": [...], "env": {...}, "rows": 24, "cols": 80,
       "steps": [{"expect": "regex", "send": "text", "resize": [rows, cols],
                  "timeout": 10, "pause": 0.3}],
       "final_timeout": 10}
Each step waits for `expect` in the output that arrived since the previous
step matched, then resizes and/or sends. Result on stdout as JSON:
{"output": str, "matched": [bool...], "exit": int|null, "timed_out": bool}
"""

import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time


def set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def main() -> int:
    spec = json.load(sys.stdin if sys.argv[1] == "-" else open(sys.argv[1]))
    env = dict(os.environ)
    env.update(spec.get("env", {}))
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(spec["argv"][0], spec["argv"], env)
    set_size(fd, spec.get("rows", 24), spec.get("cols", 80))
    out = b""
    pos = 0
    matched = []
    eof = False

    def pump(deadline: float, pattern: re.Pattern | None) -> bool:
        nonlocal out, eof, pos
        while True:
            if pattern is not None:
                m = pattern.search(out, pos)
                if m:
                    pos = m.end()
                    return True
            left = deadline - time.monotonic()
            if left <= 0 or eof:
                return False
            ready, _, _ = select.select([fd], [], [], min(left, 0.2))
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    eof = True
                else:
                    out += data

    for step in spec.get("steps", []):
        pattern = re.compile(step["expect"].encode()) if step.get("expect") else None
        ok = pump(time.monotonic() + step.get("timeout", 10), pattern) if pattern else True
        matched.append(ok)
        if not ok:
            break
        if step.get("pause"):
            pump(time.monotonic() + step["pause"], None)
        if step.get("resize"):
            set_size(fd, *step["resize"])
        if step.get("send") is not None:
            os.write(fd, step["send"].encode())
    pump(time.monotonic() + spec.get("final_timeout", 10), None)
    timed_out = not eof
    if timed_out:
        os.kill(pid, signal.SIGKILL)
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    json.dump({"output": out.decode(errors="replace"), "matched": matched,
               "exit": code, "timed_out": timed_out}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
