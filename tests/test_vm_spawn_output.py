"""Regression test: a QEMU child must never block on its own stdout/stderr.

VMManager used to start QEMU with stdout/stderr=subprocess.PIPE and never
read them. Once a VM had written a pipe buffer's worth (64 KiB) of
diagnostics, its next write blocked and the whole emulator froze: no guest
code, no serial output, no network. The virtual Pi went silent after a
near-constant amount of activity (~30 verify-pi tasks), which verify-pi saw
as "No route to host" (runs 33787903831 and later, see PR #63).
"""

import subprocess
import sys

from tests.vm.vm_manager import VMManager

MIB = 1024 * 1024


def test_child_writing_more_than_a_pipe_buffer_finishes(tmp_path):
    vm = VMManager("pi", tmp_path)
    vm.spawn([
        sys.executable, "-c",
        f"import sys; sys.stderr.write('x' * {MIB}); sys.stdout.write('y' * {MIB})",
    ])

    # With an unread PIPE the child blocks at 64 KiB and never exits.
    assert vm.process.wait(timeout=20) == 0
    assert vm.qemu_log.stat().st_size == 2 * MIB


def test_qemu_log_is_collected_with_the_serial_logs(tmp_path):
    # vm-test.yml uploads tests/vm/workdir/*-serial.log* as the post-mortem
    # artifact; the QEMU output must match that glob.
    vm = VMManager("pi", tmp_path)
    assert vm.qemu_log.match("*-serial.log*")
    assert vm.qemu_log.parent == tmp_path


def test_spawn_does_not_use_pipes(tmp_path):
    vm = VMManager("server", tmp_path)
    vm.spawn([sys.executable, "-c", "pass"])
    vm.process.wait(timeout=20)
    assert vm.process.stdout is None and vm.process.stderr is None
    assert subprocess.PIPE not in (vm.process.stdout, vm.process.stderr)
