"""Regression test: the Pi boot wait must not depend on a serial "login:".

The netbooted image never prints a getty "login:" prompt on the captured
serial console, so the old wait_for_pi_boot() -- which only returned success
on "login:" -- burned its whole 3600 s timeout on every VM test run and then
fell through to the SSH wait, which answered at once (runs 33787903831 and
35942577696: exactly 60.0 min between the server phase and "Pi did not reach
login prompt within timeout"). The boot wait now ends at the kernel handoff;
readiness of userland is the SSH wait's job.
"""

import time
from pathlib import Path

from tests.vm.run_tests import wait_for_pi_boot

UBOOT = (
    "U-Boot 2024.01\n"
    "BOOTP broadcast 1\n"
    "DHCP client bound to address 10.21.1.1\n"
    "Using genet device\n"
    "our IP address is 10.21.1.1\n"
    "Loading kernel...\n"
)
KERNEL = (
    "Booting Linux on physical CPU 0x0000000000 [0x410fd083]\n"
    "[   65.215721] rc.local[493]: My IP address is 10.21.1.1\n"
)


class FakePi:
    def __init__(self, tmp_path: Path, alive: bool = True):
        self.serial_log = tmp_path / "pi-serial.log"
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def test_kernel_handoff_without_login_prompt_is_booted(tmp_path):
    pi = FakePi(tmp_path)
    Path(str(pi.serial_log) + ".uboot").write_text(UBOOT)
    pi.serial_log.write_text(KERNEL)

    start = time.monotonic()
    booted, ip = wait_for_pi_boot(pi, timeout=30)

    assert booted
    assert ip == "10.21.1.1"
    assert time.monotonic() - start < 5


def test_no_kernel_handoff_times_out(tmp_path):
    pi = FakePi(tmp_path)
    Path(str(pi.serial_log) + ".uboot").write_text(UBOOT)

    booted, ip = wait_for_pi_boot(pi, timeout=1)

    assert not booted
    assert ip == "10.21.1.1"


def test_dead_qemu_is_not_booted(tmp_path):
    pi = FakePi(tmp_path, alive=False)

    booted, _ = wait_for_pi_boot(pi, timeout=30)

    assert not booted
