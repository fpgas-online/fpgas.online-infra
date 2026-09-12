#!/usr/bin/env python3
"""Phase 1 spike for the CI-built NFS root (issue #34, design doc
docs/superpowers/specs/2026-08-31-ci-nfsroot-build-design.md).

Runs on a GitHub ubuntu-24.04-arm runner and answers the design's open
runner questions with real measurements:

  1. Does the RasPiOS armhf userland execute natively (AArch32 EL0), or
     does the runner need qemu-user-static?  (decision D-2)
  2. Do sudo losetup / mount / chroot work on the runner (base extraction)?
  3. How long does a chroot apt-get install take?
  4. How big is the imported OCI image and how long does the GHCR push take?

Every stage is timed; the results land in the job log and, when running
under Actions, in $GITHUB_STEP_SUMMARY as a markdown table.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

IMG_URL = (
    "http://downloads.raspberrypi.org/raspios_lite_armhf/images/"
    "raspios_lite_armhf-2024-07-04/2024-07-04-raspios-bookworm-armhf-lite.img.xz"
)
WORK = Path.home() / "nfsroot-spike"
NFSROOT = WORK / "nfsroot"
ROOT = NFSROOT / "root"
IMAGE_TAG = (
    f"ghcr.io/fpgas-online/nfsroot-spike:run-{os.environ.get('GITHUB_RUN_ID', 'local')}"
)
CHROOT_BINDS = ["proc", "sys", "dev", "dev/pts"]

timings: list[tuple[str, float, str]] = []


def run(cmd, **kwargs):
    cmd = [str(c) for c in cmd]
    print("+", " ".join(cmd), flush=True)
    check = kwargs.pop("check", True)
    return subprocess.run(cmd, check=check, **kwargs)


def stage(name):
    """Decorator: time a stage, keep its one-line note for the summary."""

    def wrap(fn):
        def inner():
            t0 = time.monotonic()
            note = fn() or ""
            dt = time.monotonic() - t0
            timings.append((name, dt, note))
            print(f"=== {name}: {dt:.1f}s  {note}", flush=True)

        return inner

    return wrap


@stage("download image")
def download():
    WORK.mkdir(parents=True, exist_ok=True)
    run(["curl", "-fsSLo", WORK / "rpi.img.xz", IMG_URL])
    return f"{(WORK / 'rpi.img.xz').stat().st_size / 1e6:.0f} MB compressed"


@stage("extract to nfsroot tree")
def extract():
    run(["xz", "-d", "-T0", WORK / "rpi.img.xz"])
    img = WORK / "rpi.img"
    loop = run(
        ["sudo", "losetup", "--find", "--show", "--partscan", img],
        capture_output=True,
        text=True,
    ).stdout.strip()
    time.sleep(1)  # let the partition device nodes appear
    mnt_boot = WORK / "mnt-boot"
    mnt_root = WORK / "mnt-root"
    for d in (mnt_boot, mnt_root, NFSROOT / "boot", ROOT):
        d.mkdir(parents=True, exist_ok=True)
    run(["sudo", "mount", f"{loop}p1", mnt_boot])
    run(["sudo", "mount", f"{loop}p2", mnt_root])
    run(["sudo", "rsync", "-a", f"{mnt_boot}/", NFSROOT / "boot/"])
    run(["sudo", "rsync", "-a", f"{mnt_root}/", f"{ROOT}/"])
    run(["sudo", "umount", mnt_boot])
    run(["sudo", "umount", mnt_root])
    run(["sudo", "losetup", "-d", loop])
    img.unlink()  # reclaim ~2.7 GB before the docker import doubles usage
    du = run(
        ["sudo", "du", "-sh", NFSROOT], capture_output=True, text=True
    ).stdout.split()[0]
    return f"tree size {du}"


@stage("AArch32 probe (D-2)")
def probe_aarch32():
    native = subprocess.run(
        ["sudo", "chroot", str(ROOT), "/bin/uname", "-m"],
        capture_output=True,
        text=True,
        check=False,
    )
    if native.returncode == 0:
        return f"NATIVE: armhf runs directly (uname -m = {native.stdout.strip()})"
    err = (native.stderr.strip() or f"rc={native.returncode}")[:120]
    run(["sudo", "apt-get", "-qq", "install", "-y", "qemu-user-static"])
    emulated = run(
        ["sudo", "chroot", str(ROOT), "/bin/uname", "-m"],
        capture_output=True,
        text=True,
    )
    return (
        f"QEMU FALLBACK: native exec failed ({err}); "
        f"qemu-user-static works (uname -m = {emulated.stdout.strip()})"
    )


@stage("chroot apt-get install lldpd")
def chroot_apt():
    for d in CHROOT_BINDS:
        run(["sudo", "mount", "--bind", f"/{d}", ROOT / d])
    run(["sudo", "tee", ROOT / "usr/sbin/policy-rc.d"], input="#!/bin/sh\nexit 101\n",
        text=True, stdout=subprocess.DEVNULL)
    run(["sudo", "chmod", "755", ROOT / "usr/sbin/policy-rc.d"])
    run(["sudo", "rm", "-f", ROOT / "etc/resolv.conf"])
    run(["sudo", "cp", "--dereference", "/etc/resolv.conf", ROOT / "etc/resolv.conf"])
    try:
        for apt_cmd in (
            ["apt-get", "-qq", "update"],
            ["apt-get", "-qq", "install", "-y", "--no-install-recommends", "lldpd"],
        ):
            run(
                ["sudo", "chroot", ROOT, "env", "DEBIAN_FRONTEND=noninteractive"]
                + apt_cmd
            )
    finally:
        for d in reversed(CHROOT_BINDS):
            run(["sudo", "umount", ROOT / d])
    return "apt update + install lldpd inside the armhf root"


@stage("docker import")
def docker_import():
    tar = subprocess.Popen(
        ["sudo", "tar", "-C", str(NFSROOT), "--numeric-owner", "--xattrs",
         "-c", "boot", "root"],
        stdout=subprocess.PIPE,
    )
    run(["docker", "import", "-", IMAGE_TAG], stdin=tar.stdout)
    tar.stdout.close()
    if tar.wait() != 0:
        raise RuntimeError("tar of the nfsroot tree failed")
    size = run(
        ["docker", "image", "inspect", "--format", "{{.Size}}", IMAGE_TAG],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return f"image size {int(size) / 1e9:.2f} GB uncompressed"


@stage("push to GHCR")
def push():
    run(["docker", "push", IMAGE_TAG])
    return IMAGE_TAG


def summary():
    lines = [
        "## nfsroot spike results",
        "",
        f"Runner: `{run(['uname', '-a'], capture_output=True, text=True).stdout.strip()}`",
        "",
        "| stage | seconds | notes |",
        "|---|---:|---|",
    ]
    lines += [f"| {n} | {dt:.1f} | {note} |" for n, dt, note in timings]
    lines.append(f"| **total** | **{sum(dt for _, dt, _ in timings):.1f}** | |")
    text = "\n".join(lines)
    print(text, flush=True)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write(text + "\n")


def main():
    run(["df", "-h", "/"])
    for step in (download, extract, probe_aarch32, chroot_apt, docker_import, push):
        step()
    run(["df", "-h", "/"])
    summary()


if __name__ == "__main__":
    sys.exit(main())
