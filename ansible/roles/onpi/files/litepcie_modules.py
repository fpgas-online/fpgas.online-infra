#!/usr/bin/env python3
"""The prebuilt LitePCIe module packages for the kernels in this root that a Raspberry Pi 5 can boot.

fpgas.online-test-designs builds litepcie.ko (the Acorn PCIe SoC's driver) once per Raspberry Pi kernel, as
`fpgas-online-acorn-litepcie-modules-<kernel>`, with the kernel package's architecture. A Pi 5 or CM5 boots
only 64-bit kernels, the `rpi-v8` and `rpi-2712` flavours, and those are the ones that have a module package.
The root is 32-bit with arm64 as a foreign architecture, so each name is printed with its architecture, as
apt wants it:

    fpgas-online-acorn-litepcie-modules-6.12.109+rpt-rpi-v8:arm64

Run in the root (it reads the root's own dpkg database). One apt package per line; nothing when the root has
no such kernel. `--kernels` prints the kernel versions instead.

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
QUERY = ("dpkg-query", "-W", "-f=${Package}\\t${Architecture}\\t${db:Status-Status}\\n", "linux-image-*")


def kernels(dpkg_lines):
    """[(kernel version, architecture)] of the installed kernels a Pi 5 can boot, in version order."""
    found = set()
    for line in dpkg_lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 3 or parts[2] != "installed":
            continue
        m = IMAGE_RE.fullmatch(parts[0])
        if m:
            found.add((m.group(1), parts[1]))
    return sorted(found)


def packages(dpkg_lines):
    return [f"{PREFIX}{kver}:{arch}" for kver, arch in kernels(dpkg_lines)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kernels", action="store_true", help="print the kernel versions, not the packages")
    args = parser.parse_args(argv)
    run = subprocess.run(QUERY, capture_output=True, text=True, check=False)
    if run.returncode not in (0, 1):  # 1: no package matches the pattern
        sys.exit(f"error: dpkg-query: {run.stderr.strip()}")
    lines = run.stdout.splitlines()
    for item in [k for k, _ in kernels(lines)] if args.kernels else packages(lines):
        print(item)


if __name__ == "__main__":
    main()
