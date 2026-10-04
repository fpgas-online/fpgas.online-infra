#!/usr/bin/env python3
"""The prebuilt LitePCIe module packages for the kernels in this root that a Raspberry Pi 5 can boot.

fpgas.online-test-designs builds litepcie.ko (the Acorn PCIe SoC's driver) once per Raspberry Pi kernel, as
`fpgas-online-acorn-litepcie-modules-<kernel>`, with the kernel package's architecture. A Pi 5 or CM5 boots
only 64-bit kernels, the `rpi-v8` and `rpi-2712` flavours, and those are the ones that have a module package.
The root is 32-bit with arm64 as a foreign architecture, so each name is printed with its architecture, as
apt wants it:

    fpgas-online-acorn-litepcie-modules-6.12.109+rpt-rpi-v8:arm64

Module packages exist from kernel 6.12 on (MIN_KERNEL: the floor fpgas.online-test-designs builds from,
packaging/acorn-litepcie/kernels.toml `min_kernel`). An older Pi 5 kernel still in the root, such as the base
image's 6.6.31+rpt-rpi-v8 in a root built from scratch, has none, and asking apt for one fails the image
build. Such a kernel is left out and named (`--left-out`); a Pi that boots it has no driver, which
verify-pi's litepcie assert reports on that Pi.

Run in the root (it reads the root's own dpkg database). One apt package per line; nothing when the root has
no such kernel. `--kernels` prints the kernel versions instead, `--left-out` the Pi 5 kernels below the floor.

Only real kernels count: `linux-image-rpi-v8` is the meta package that follows the newest one, and has no
modules of its own.
"""

import argparse
import re
import subprocess
import sys

PREFIX = "fpgas-online-acorn-litepcie-modules-"
FLAVOURS = ("rpi-v8", "rpi-2712")
# linux-image-6.12.109+rpt-rpi-v8, linux-image-6.1.0-rpi8-rpi-2712: a version, then the flavour
IMAGE_RE = re.compile(r"linux-image-(\d\S*-(?:{}))".format("|".join(re.escape(f) for f in FLAVOURS)))
# The first kernel series that has module packages: keep it equal to min_kernel in fpgas.online-test-designs's
# packaging/acorn-litepcie/kernels.toml.
MIN_KERNEL = (6, 12)
SERIES_RE = re.compile(r"(\d+)\.(\d+)")
QUERY = ("dpkg-query", "-W", "-f=${Package}\\t${Architecture}\\t${db:Status-Status}\\n", "linux-image-*")


def series(kver):
    """(major, minor) of a kernel version: (6, 12) for 6.12.109+rpt-rpi-v8, (6, 1) for 6.1.0-rpi8-rpi-2712."""
    m = SERIES_RE.match(kver)
    if not m:
        raise ValueError(f"no kernel series in {kver!r}")
    return int(m.group(1)), int(m.group(2))


def installed(dpkg_lines):
    """[(kernel version, architecture)] of every installed kernel a Pi 5 can boot, whatever its series."""
    found = set()
    for line in dpkg_lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 3 or parts[2] != "installed":
            continue
        m = IMAGE_RE.fullmatch(parts[0])
        if m:
            found.add((m.group(1), parts[1]))
    return sorted(found)


def kernels(dpkg_lines):
    """[(kernel version, architecture)] of the installed Pi 5 kernels that have a module package: those from
    MIN_KERNEL on, in version order."""
    return [(kver, arch) for kver, arch in installed(dpkg_lines) if series(kver) >= MIN_KERNEL]


def left_out(dpkg_lines):
    """The installed Pi 5 kernels below MIN_KERNEL: no module package exists for them."""
    return [kver for kver, _ in installed(dpkg_lines) if series(kver) < MIN_KERNEL]


def packages(dpkg_lines):
    return [f"{PREFIX}{kver}:{arch}" for kver, arch in kernels(dpkg_lines)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    what = parser.add_mutually_exclusive_group()
    what.add_argument("--kernels", action="store_true", help="print the kernel versions, not the packages")
    what.add_argument("--left-out", action="store_true", help="print the Pi 5 kernels below the packages' floor")
    args = parser.parse_args(argv)
    run = subprocess.run(QUERY, capture_output=True, text=True, check=False)
    if run.returncode not in (0, 1):  # 1: no package matches the pattern
        sys.exit(f"error: dpkg-query: {run.stderr.strip()}")
    lines = run.stdout.splitlines()
    if args.left_out:
        items = left_out(lines)
    elif args.kernels:
        items = [k for k, _ in kernels(lines)]
    else:
        items = packages(lines)
    for item in items:
        print(item)


if __name__ == "__main__":
    main()
