#!/usr/bin/env python3
"""Build U-Boot for Raspberry Pi 4B targeting QEMU raspi4b.

Downloads U-Boot source, cross-compiles for aarch64 rpi_4_defconfig,
and outputs u-boot.bin ready for QEMU -kernel.

The RPi 4B has native Gigabit Ethernet (Broadcom GENET) which QEMU's
raspi4b machine emulates, avoiding the USB ethernet issues of raspi3b.
rpi_4_defconfig enables CONFIG_BCMGENET=y.

Requirements: gcc-aarch64-linux-gnu, make, bison, flex, libssl-dev, libgnutls28-dev
"""

import os
import subprocess
import sys
from pathlib import Path

UBOOT_VERSION = "v2025.01"
UBOOT_URL = f"https://github.com/u-boot/u-boot/archive/refs/tags/{UBOOT_VERSION}.tar.gz"
BUILD_DIR = Path(__file__).parent / "images"
UBOOT_SRC = BUILD_DIR / f"u-boot-{UBOOT_VERSION.lstrip('v')}"
UBOOT_BIN = BUILD_DIR / "u-boot-rpi4b.bin"


def check_deps():
    """Check build dependencies are installed."""
    missing = []
    for cmd in ["aarch64-linux-gnu-gcc", "make", "bison", "flex"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            missing.append(cmd)
    if missing:
        print(f"Missing build tools: {', '.join(missing)}")
        print("Install: sudo apt install gcc-aarch64-linux-gnu make bison flex libssl-dev")
        sys.exit(1)


def download_source():
    """Download and extract U-Boot source."""
    tarball = BUILD_DIR / f"u-boot-{UBOOT_VERSION}.tar.gz"
    if UBOOT_SRC.exists():
        print(f"U-Boot source already at {UBOOT_SRC}")
        return
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading U-Boot {UBOOT_VERSION}...")
    subprocess.run(
        ["wget", "-q", "--show-progress", "-O", str(tarball), UBOOT_URL],
        check=True,
    )
    print("Extracting...")
    subprocess.run(
        ["tar", "xf", str(tarball), "-C", str(BUILD_DIR)],
        check=True,
    )
    tarball.unlink()


def build():
    """Cross-compile U-Boot for RPi 3B."""
    if UBOOT_BIN.exists():
        print(f"U-Boot already built at {UBOOT_BIN}")
        return UBOOT_BIN

    env = os.environ.copy()
    env["CROSS_COMPILE"] = "aarch64-linux-gnu-"

    print("Configuring U-Boot for rpi_4_defconfig...")
    subprocess.run(
        ["make", "rpi_4_defconfig"],
        cwd=UBOOT_SRC,
        env=env,
        check=True,
        capture_output=True,
    )

    print("Building U-Boot (this takes ~1 minute)...")
    subprocess.run(
        ["make", f"-j{os.cpu_count() or 2}"],
        cwd=UBOOT_SRC,
        env=env,
        check=True,
        capture_output=True,
    )

    # Copy the binary to a known location
    src = UBOOT_SRC / "u-boot.bin"
    if not src.exists():
        print(f"ERROR: {src} not found after build")
        sys.exit(1)

    import shutil
    shutil.copy2(src, UBOOT_BIN)
    print(f"U-Boot binary: {UBOOT_BIN} ({UBOOT_BIN.stat().st_size} bytes)")
    return UBOOT_BIN


def main():
    check_deps()
    download_source()
    result = build()
    print(f"\nDone: {result}")


if __name__ == "__main__":
    main()
