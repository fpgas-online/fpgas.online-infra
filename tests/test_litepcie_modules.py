"""roles/onpi/files/litepcie_modules.py: which prebuilt LitePCIe module packages a root needs.

The Pi root is armhf with arm64 as a foreign architecture, and carries a kernel per Pi family. Only the
64-bit flavours a Pi 5 boots (rpi-v8, rpi-2712) have a module package, named for the kernel and installed
with the kernel's architecture.
"""

import importlib.util
from pathlib import Path

import pytest

from tests.test_verify_pi_hw_detection import _run, _task

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ansible/roles/onpi/files/litepcie_modules.py"

spec = importlib.util.spec_from_file_location("litepcie_modules", SCRIPT)
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)

# pi-sw2-p48's root on 2026-10-02, as dpkg-query prints it
WELLAND = """\
linux-image-6.1.0-50-armmp\tarmhf\tinstalled
linux-image-6.12.109+rpt-rpi-v6\tarmhf\tinstalled
linux-image-6.12.109+rpt-rpi-v7\tarmhf\tinstalled
linux-image-6.12.109+rpt-rpi-v7l\tarmhf\tinstalled
linux-image-6.12.109+rpt-rpi-v8\tarm64\tinstalled
linux-image-armmp\tarmhf\tinstalled
linux-image-rpi-v6\tarmhf\tinstalled
linux-image-rpi-v7\tarmhf\tinstalled
linux-image-rpi-v7l\tarmhf\tinstalled
linux-image-rpi-v8\tarm64\tinstalled
""".splitlines()


def test_the_welland_root_needs_the_one_v8_kernels_modules_as_arm64():
    assert lm.packages(WELLAND) == ["fpgas-online-acorn-litepcie-modules-6.12.109+rpt-rpi-v8:arm64"]
    assert lm.kernels(WELLAND) == [("6.12.109+rpt-rpi-v8", "arm64")]


def test_the_meta_package_and_the_32_bit_flavours_have_no_modules():
    names = " ".join(lm.packages(WELLAND))
    assert "modules-rpi-v8" not in names  # linux-image-rpi-v8 follows the newest kernel: not a kernel itself
    for flavour in ("rpi-v6", "rpi-v7", "rpi-v7l", "armmp"):
        assert not names.endswith(flavour) and f"{flavour}:" not in names


def test_every_installed_pi5_kernel_gets_its_modules_both_flavours():
    lines = [
        "linux-image-6.12.96+rpt-rpi-v8\tarm64\tinstalled",
        "linux-image-6.12.109+rpt-rpi-v8\tarm64\tinstalled",
        "linux-image-6.12.109+rpt-rpi-2712\tarm64\tinstalled",
        "linux-image-6.1.0-rpi8-rpi-2712\tarm64\tinstalled",  # the 6.1 kernels are named like this
    ]
    assert lm.packages(lines) == [
        "fpgas-online-acorn-litepcie-modules-6.1.0-rpi8-rpi-2712:arm64",
        "fpgas-online-acorn-litepcie-modules-6.12.109+rpt-rpi-2712:arm64",
        "fpgas-online-acorn-litepcie-modules-6.12.109+rpt-rpi-v8:arm64",
        "fpgas-online-acorn-litepcie-modules-6.12.96+rpt-rpi-v8:arm64",
    ]


def test_a_kernel_that_is_removed_but_not_purged_is_not_counted():
    lines = ["linux-image-6.12.96+rpt-rpi-v8\tarm64\tconfig-files", "linux-image-6.12.109+rpt-rpi-v8\tarm64\tinstalled"]
    assert lm.kernels(lines) == [("6.12.109+rpt-rpi-v8", "arm64")]


def test_a_root_with_no_pi5_kernel_needs_nothing():
    assert lm.packages(["linux-image-6.12.109+rpt-rpi-v7l\tarmhf\tinstalled", "garbage"]) == []


def test_the_real_time_flavour_is_not_built_so_it_is_not_asked_for():
    assert lm.packages(["linux-image-6.12.109+rpt-rpi-v8-rt\tarm64\tinstalled"]) == []


def test_an_arm64_root_gets_the_same_package_with_its_own_architecture_named():
    """A 64-bit root: the kernel package is arm64 there too, and naming the architecture is still right."""
    assert lm.packages(["linux-image-6.18.50+rpt-rpi-2712\tarm64\tinstalled"]) == [
        "fpgas-online-acorn-litepcie-modules-6.18.50+rpt-rpi-2712:arm64"
    ]


# -- verify-pi: litepcie.ko for the running kernel, where the root carries the driver ---------------------

COMMON = "fpgas-online-acorn-litepcie-common install ok installed"
FOUND = {"rc": 0, "stdout": "/lib/modules/6.12.109+rpt-rpi-v8/updates/fpgas-online/litepcie.ko", "stderr": ""}
NOT_FOUND = {"rc": 1, "stdout": "", "stderr": "modinfo: ERROR: Module litepcie not found."}


@pytest.mark.parametrize(("packages", "kernel", "modinfo", "passes"), [
    pytest.param([COMMON], "6.12.109+rpt-rpi-v8", FOUND, True, id="driver in the root, module for this kernel: pass"),
    pytest.param([COMMON], "6.12.109+rpt-rpi-v8", NOT_FOUND, False, id="driver in the root, no module for this kernel: fail"),
    pytest.param([COMMON], "6.12.109+rpt-rpi-2712", NOT_FOUND, False, id="the 2712 kernel counts too: fail"),
    pytest.param([COMMON], "6.12.109+rpt-rpi-v7l", NOT_FOUND, True, id="a 32-bit kernel has no module to find: pass"),
    pytest.param([], "6.12.109+rpt-rpi-v8", NOT_FOUND, True, id="a root built without the driver: pass"),
])  # fmt: skip
def test_verify_pi_wants_the_module_only_where_the_root_carries_the_driver(tmp_path, packages, kernel, modinfo, passes):
    facts = {
        "verify_pi_litepcie": {"packages": {"stdout_lines": packages}, "modinfo": modinfo},
        "ansible_kernel": kernel,  # verify-pi sets it from the collector; verify_pi_state itself is gone by here
    }
    rc, output = _run(tmp_path, [_task("Assert litepcie.ko is there for this kernel, where the root carries the driver")],
                      facts)  # fmt: skip
    assert (rc == 0) == passes, output
