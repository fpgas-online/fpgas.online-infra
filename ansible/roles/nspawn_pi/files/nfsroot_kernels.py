#!/usr/bin/env python3
"""Keep the Pi NFS root's kernel set small and its initramfs images fresh.

Runs on the SERVER (native, unemulated) against the NFS root tree, which
holds ``boot/`` (the TFTP-served payload) and ``root/`` (the Pi rootfs).

Why this exists
---------------
The NFS root is long-lived and incrementally upgraded through an armhf
chroot under qemu-user-static, where everything costs 50-100x native.  Two
properties of Raspbian's initramfs-tools turn that into hours:

* ``/etc/initramfs-tools/update-initramfs.conf`` ships ``update_initramfs=all``,
  so a *bare* ``update-initramfs -u`` -- which raspi-firmware's postinst runs
  on every one of its own upgrades -- rebuilds an initramfs for **every**
  installed kernel, whether or not anything about it changed.
* Nothing prunes superseded kernels, so "every installed kernel" grows.

On tweed 2026-09-23 that made ``cam/pi : apt update/upgrade`` take 6232 s:
eight full initramfs builds (four 6.12.96 flavours, three 6.12.109 flavours
and the 164 MB Debian armmp tree the Orange Pi H3 boards boot) triggered by a
raspi-firmware point upgrade.

The roles suppress the blanket rebuild for the duration of the chroot session
(see roles/nspawn_pi/tasks/chroot-prep.yml); this script does the rest:

  protect            apt-mark manual the kernels the fleet currently boots
  plan / prune       purge kernel packages that are neither served nor newest
  refresh-initramfs  rebuild only the initramfs images that are actually stale
  check              fail the run if the kernel set or /boot drifts back

What is never removed
---------------------
* Any kernel whose ``vmlinuz`` is byte-identical to a kernel image the TFTP
  payload currently serves (``boot/kernel*.img``, ``boot/sunxi/vmlinuz``).
  That is the fleet's running kernel, identified by content rather than by a
  name or a date, so pruning cannot move the fleet onto a different kernel.
* The newest kernel of each flavour -- what the next ``netboot`` payload sync
  would publish.
* Anything matching ``--keep`` (inventory: ``nfsroot_kernel_keep``).
* Module directories themselves.  Only dpkg-installed kernel *packages* are
  purged; leftover trees such as the 6.6.31 husks (index files only, the
  modules already gone) are left exactly where they are.

A purge is simulated first and aborted if apt would remove anything outside
the computed set, so a dependency of a meta-package can never be dragged out.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# "6.12.96+rpt-rpi-v7l" -> ((6, 12, 96), "+rpt-rpi-v7l")
# "6.1.0-50-armmp"      -> ((6, 1, 0, 50), "-armmp")
_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:-(\d+))?(.*)$")

# What the Pi firmware loads over TFTP: kernel.img / kernel7.img /
# kernel7l.img / kernel8.img, one per board family, written by
# raspi-firmware's kernel hook and published by fixpi's `netboot` sync.
KERNEL_IMAGE_GLOB = "kernel*.img"

# Trees whose contents end up inside every initramfs.  If any of them is newer
# than an initramfs image, that image is stale and must be rebuilt; if none is,
# a rebuild would reproduce the same content and is pure emulated waste.
INITRAMFS_INPUT_DIRS = (
    "etc/initramfs-tools",
    "usr/share/initramfs-tools",
    "etc/modprobe.d",
    "etc/crypttab",
)


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for a kernel version string, newest last.

    Compares only the numeric part, so 6.12.109 > 6.12.96 (a plain string
    sort gets that backwards) and the ABI number breaks ties for Debian
    kernels (6.1.0-50-armmp > 6.1.0-9-armmp).
    """
    match = _VERSION_RE.match(version)
    if not match:
        return (0,)
    numbers = [int(part) for part in match.group(1).split(".")]
    if match.group(2):
        numbers.append(int(match.group(2)))
    return tuple(numbers)


def flavour(version: str) -> str:
    """The kernel flavour a version belongs to.

    Flavours are the unit of "one kernel per board family": v6/v7/v7l/v8 for
    the Pis and -armmp for the Orange Pi H3 boards.  Versions in the same
    flavour compete; versions in different flavours never do.
    """
    match = _VERSION_RE.match(version)
    return match.group(3) if match else version


def select_keep(
    versions: list[str],
    served: set[str],
    keep_globs: list[str] | None = None,
) -> set[str]:
    """Kernel versions that must stay installed.

    ``served`` are the versions the TFTP payload boots right now (matched by
    content, see ``served_versions``).  On top of those, the newest version of
    each flavour is kept, because that is what the next ``netboot`` sync would
    publish -- so an upgrade run never leaves the server unable to move the
    fleet forward.
    """
    keep = set(served)
    keep.update(pattern_matches(versions, keep_globs or []))
    newest: dict[str, str] = {}
    for version in versions:
        group = flavour(version)
        if group not in newest or version_key(version) > version_key(newest[group]):
            newest[group] = version
    keep.update(newest.values())
    return keep


def pattern_matches(versions: list[str], globs: list[str]) -> set[str]:
    return {v for v in versions for g in globs if fnmatch.fnmatch(v, g)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rootfs_kernels(nfs_root: Path) -> dict[str, Path]:
    """version -> vmlinuz path, for every kernel installed in the rootfs."""
    boot = nfs_root / "root" / "boot"
    return {
        path.name[len("vmlinuz-"):]: path
        for path in sorted(boot.glob("vmlinuz-*"))
        if path.is_file()
    }


def served_images(nfs_root: Path, tftp_root: Path | None = None) -> list[Path]:
    """The kernel images the boot server hands out over TFTP.

    ``tftp_root`` defaults to ``<nfs-root>/boot``; the per-port-VLAN sites
    serve straight out of the NFS root's boot dir, the others from /srv/tftp
    (inventory: ``tftp_root``).  The sunxi payload is the Orange Pi H3 boards'
    Debian armmp kernel, published by fixpi under ``sunxi/``.
    """
    images = sorted((nfs_root / "boot").glob(KERNEL_IMAGE_GLOB))
    roots = {nfs_root / "boot"}
    if tftp_root is not None:
        roots.add(Path(tftp_root))
    for root in sorted(roots):
        sunxi = root / "sunxi" / "vmlinuz"
        if sunxi.is_file():
            images.append(sunxi)
    return images


def resolve_served(
    nfs_root: Path, tftp_root: Path | None = None
) -> tuple[dict[str, str], list[Path]]:
    """Map each served kernel image to the installed version it is.

    Matched by sha256 rather than by filename or mtime: ``boot/kernel8.img``
    carries no version in its name, and its mtime is the date of the last
    ``netboot`` sync, not of the kernel.  On tweed that mtime reads 26 August
    while the bytes are 6.12.96 -- exactly the kind of mismatch that makes a
    date-based rule delete a running kernel.

    Returns ``({image path: version}, [images matching nothing])``.
    """
    by_hash: dict[str, str] = {}
    for version, path in rootfs_kernels(nfs_root).items():
        by_hash.setdefault(sha256(path), version)
    matched: dict[str, str] = {}
    orphans: list[Path] = []
    for image in served_images(nfs_root, tftp_root):
        version = by_hash.get(sha256(image))
        if version:
            matched[str(image)] = version
        else:
            orphans.append(image)
    return matched, orphans


def served_versions(nfs_root: Path, tftp_root: Path | None = None) -> set[str]:
    """The installed kernel versions the TFTP payload currently boots."""
    matched, _orphans = resolve_served(nfs_root, tftp_root)
    return set(matched.values())


def module_trees(nfs_root: Path) -> list[str]:
    """Kernel versions with a real module tree under the rootfs.

    A directory holding only depmod's index files (``modules.dep`` and
    friends) is what dpkg leaves behind after a kernel package is removed.
    It costs nothing and is not a kernel, so it is not counted or touched.
    """
    modules = nfs_root / "root" / "lib" / "modules"
    if not modules.is_dir():
        return []
    return sorted(d.name for d in modules.iterdir() if (d / "kernel").is_dir())


def dpkg_kernel_packages(nfs_root: Path) -> dict[str, list[tuple[str, str]]]:
    """version -> [(package, arch)] for installed linux-image/-headers."""
    proc = subprocess.run(
        [
            "dpkg-query",
            f"--root={nfs_root / 'root'}",
            "-W",
            "-f=${Package}\\t${Architecture}\\t${db:Status-Status}\\n",
            "linux-image-*",
            "linux-headers-*",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    found: dict[str, list[tuple[str, str]]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[2] != "installed":
            continue
        package, arch, _ = parts
        for prefix in ("linux-image-", "linux-headers-"):
            if package.startswith(prefix):
                found.setdefault(package[len(prefix):], []).append((package, arch))
    return found


def purge_plan(
    packages: dict[str, list[tuple[str, str]]],
    installed_kernels: list[str],
    keep: set[str],
) -> list[str]:
    """apt package specs (``name:arch``) for the kernels that may go.

    Only versions that are a real kernel in this root (they have a
    ``/boot/vmlinuz-<version>``) are candidates.  ``linux-image-rpi-v7`` and
    ``linux-headers-6.12.96+rpt-common-rpi`` are meta/common packages whose
    "version" is not a kernel version; apt's own autoremove takes those out
    once the last real kernel that needs them is gone.
    """
    specs = []
    for version in sorted(set(installed_kernels) - keep):
        specs.extend(
            sorted(f"{package}:{arch}" for package, arch in packages.get(version, []))
        )
    return specs


def chroot_apt(nfs_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    return subprocess.run(
        ["chroot", str(nfs_root / "root"), "apt-get", "-y", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def simulated_removals(output: str) -> set[str]:
    """Package names apt says it would remove, from ``apt-get -s`` output."""
    return {
        line.split()[1].split(":")[0]
        for line in output.splitlines()
        if line.startswith(("Remv ", "Purg "))
    }


def newest_mtime(root: Path, relatives: list[str]) -> float:
    newest = 0.0
    for relative in relatives:
        path = root / relative
        if not path.exists():
            continue
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
            continue
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in [dirpath, *(os.path.join(dirpath, f) for f in filenames)]:
                try:
                    newest = max(newest, os.stat(name).st_mtime)
                except OSError:
                    pass
    return newest


def stale_initramfs(nfs_root: Path, versions: list[str]) -> list[str]:
    """Versions whose initramfs is missing or older than its inputs.

    This is the staleness test ``update-initramfs -u`` does not do: it rebuilds
    unconditionally, which is why a raspi-firmware point release cost tweed 104
    minutes of emulated work that produced byte-identical images.
    """
    root = nfs_root / "root"
    shared = newest_mtime(root, list(INITRAMFS_INPUT_DIRS))
    stale = []
    for version in versions:
        initrd = root / "boot" / f"initrd.img-{version}"
        if not initrd.is_file():
            stale.append(version)
            continue
        inputs = max(shared, newest_mtime(root, [f"lib/modules/{version}"]))
        # One second of slack: dpkg and the postinst hook that built the image
        # routinely land in the same second.
        if inputs > initrd.stat().st_mtime + 1:
            stale.append(version)
    return stale


def load_plan(args: argparse.Namespace) -> dict:
    nfs_root = Path(args.nfs_root)
    tftp_root = Path(args.tftp_root) if args.tftp_root else None
    installed = sorted(rootfs_kernels(nfs_root))
    served = served_versions(nfs_root, tftp_root)
    keep = select_keep(installed, served, args.keep)
    return {
        "installed": installed,
        "served": sorted(served),
        "keep": sorted(keep),
        "purge": purge_plan(dpkg_kernel_packages(nfs_root), installed, keep),
        "module_trees": module_trees(nfs_root),
    }


def cmd_protect(args: argparse.Namespace) -> int:
    """Mark the kernels the fleet boots as manually installed.

    Every kernel in this root arrives as an automatic dependency of a meta
    package (linux-image-rpi-v7 and friends).  When the meta package moves to
    a newer version, `apt-get autoremove` -- which roles/onpi/tasks/apt.yml
    runs on every pass -- considers the old one orphaned and deletes it,
    modules and all, while the TFTP payload is still handing that exact
    kernel to ~20 netbooted boards.  That is what emptied the 6.6.31 module
    trees on tweed on 2026-09-23 (apt history: "Remove:
    linux-image-6.6.31+rpt-rpi-v6 ... v7 ... v7l ... v8").

    Marking them manual makes autoremove leave them alone.  Moving the fleet
    to a different kernel stays the `netboot` tag's job: publish the payload
    first, and the kernel this protects moves with it.
    """
    nfs_root = Path(args.nfs_root)
    tftp_root = Path(args.tftp_root) if args.tftp_root else None
    packages = dpkg_kernel_packages(nfs_root)
    served = served_versions(nfs_root, tftp_root)
    specs = sorted(
        f"{package}:{arch}"
        for version in served
        for package, arch in packages.get(version, [])
    )
    if not specs:
        print("nfsroot-kernels: no served kernel package to protect")
        return 0
    result = subprocess.run(
        ["chroot", str(nfs_root / "root"), "apt-mark", "manual", *specs],
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"),
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    # apt-mark prints "set to manually installed" only for packages it
    # changed, so that string is the honest changed/unchanged signal.
    print("nfsroot-kernels: protected " + " ".join(specs))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    print(json.dumps(load_plan(args), indent=2))
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    nfs_root = Path(args.nfs_root)
    plan = load_plan(args)
    if not plan["purge"]:
        print("nfsroot-kernels: nothing to purge; keeping " + " ".join(plan["keep"]))
        return 0
    if not plan["served"]:
        # No served image matched an installed kernel: the payload predates
        # every kernel now in the root.  "What the fleet boots" is then not
        # knowable from the tree, so purging could take out the only copy of a
        # running kernel's modules.  Keep everything instead and say why.
        print(
            "nfsroot-kernels: no served kernel image matches an installed "
            "kernel -- keeping all "
            + str(len(plan["installed"]))
            + " kernels rather than guessing.  Publish a payload with the "
            "`netboot` tag (a separate, deliberate change to what the fleet "
            "boots), or name the kernels to keep in nfsroot_kernel_keep.",
            file=sys.stderr,
        )
        return 0

    expected = {spec.split(":")[0] for spec in plan["purge"]}
    simulation = chroot_apt(nfs_root, ["-s", "purge", *plan["purge"]])
    if simulation.returncode != 0:
        print(simulation.stdout, file=sys.stderr)
        print(simulation.stderr, file=sys.stderr)
        return simulation.returncode
    collateral = simulated_removals(simulation.stdout) - expected
    if collateral:
        print(
            "nfsroot-kernels: ERROR purging "
            + " ".join(sorted(expected))
            + " would also remove "
            + " ".join(sorted(collateral))
            + " -- refusing.",
            file=sys.stderr,
        )
        return 1
    if args.dry_run:
        print("nfsroot-kernels: would purge " + " ".join(plan["purge"]))
        return 0

    result = chroot_apt(nfs_root, ["purge", *plan["purge"]])
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode

    # The kernels that were kept must still be there, with their modules.  A
    # purge that took out a served kernel would strand every booted board on a
    # root with no modules for its running kernel.
    remaining = set(module_trees(nfs_root))
    lost = {v for v in plan["served"] if v not in remaining}
    if lost:
        print(
            "nfsroot-kernels: ERROR the purge removed the module tree of "
            "served kernel(s) " + " ".join(sorted(lost)),
            file=sys.stderr,
        )
        return 1
    print("nfsroot-kernels: purged " + " ".join(plan["purge"]))
    return 0


def cmd_refresh_initramfs(args: argparse.Namespace) -> int:
    nfs_root = Path(args.nfs_root)
    installed = sorted(rootfs_kernels(nfs_root))
    stale = stale_initramfs(nfs_root, installed)
    if not stale:
        print("nfsroot-kernels: all initramfs images current")
        return 0
    if args.dry_run:
        print("nfsroot-kernels: would rebuild " + " ".join(stale))
        return 0
    # One kernel per core: each update-initramfs -k <version> writes only
    # that version's image and works in its own temporary directory, and
    # mkinitramfs is single-threaded (a CI build rebuilt five one after
    # another, ~65 s on a 4-core runner).
    results = run_initramfs_builds(nfs_root, stale)
    failed = [version for version, rc in results if rc != 0]
    if failed:
        print(
            "nfsroot-kernels: ERROR update-initramfs failed for " + " ".join(failed),
            file=sys.stderr,
        )
        return next(rc for _version, rc in results if rc != 0)
    print("nfsroot-kernels: rebuilt " + " ".join(stale))
    return 0


def run_initramfs_builds(nfs_root: Path, versions: list[str]) -> list[tuple[str, int]]:
    """Rebuild each version's initramfs, in parallel; (version, rc) in order."""

    def build(version: str) -> tuple[str, int, str]:
        initrd = nfs_root / "root" / "boot" / f"initrd.img-{version}"
        mode = "-u" if initrd.is_file() else "-c"
        result = subprocess.run(
            ["chroot", str(nfs_root / "root"), "update-initramfs", mode, "-k", version],
            check=False,
            capture_output=True,
            text=True,
            env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"),
        )
        return version, result.returncode, f"({mode}) {result.stdout}{result.stderr}"

    workers = max(1, min(len(versions), os.cpu_count() or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(build, versions))
    for version, rc, output in done:
        print(f"nfsroot-kernels: rebuilt initramfs for {version} {output}".rstrip())
    return [(version, rc) for version, rc, _output in done]


def cmd_check(args: argparse.Namespace) -> int:
    nfs_root = Path(args.nfs_root)
    problems = []

    trees = module_trees(nfs_root)
    if len(trees) > args.max_trees:
        problems.append(
            f"{len(trees)} kernel module trees exceed the limit of "
            f"{args.max_trees}: {' '.join(trees)}.  Every one of them is a "
            "full initramfs build under qemu emulation whenever anything "
            "triggers a rebuild -- prune, or raise nfsroot_max_kernel_trees "
            "deliberately."
        )

    for version, vmlinuz in sorted(rootfs_kernels(nfs_root).items()):
        initrd = vmlinuz.parent / f"initrd.img-{version}"
        if not initrd.is_file():
            problems.append(f"{version} has a vmlinuz but no initramfs")

    conf = nfs_root / "root" / "etc" / "initramfs-tools" / "update-initramfs.conf"
    if conf.is_file() and "update_initramfs=no" in conf.read_text():
        problems.append(
            f"{conf} still says update_initramfs=no -- the chroot build's "
            "suppression was not lifted, so this root will never refresh an "
            "initramfs again"
        )

    # What the fleet boots, reported rather than asserted: a payload older
    # than the root's kernels is the NORMAL state between `netboot` runs --
    # tweed serves 6.12.96 while the chroot has moved on to 6.12.109, and
    # moving the boards forward is a separate, separately-authorised change.
    # --strict-payload turns it into a failure for callers that have just
    # published a payload and expect it to match.
    tftp_root = Path(args.tftp_root) if args.tftp_root else None
    matched, orphans = resolve_served(nfs_root, tftp_root)
    served = set(matched.values())
    if orphans:
        message = (
            "the TFTP payload "
            + " ".join(str(o) for o in orphans)
            + " matches no kernel installed in the root; boards booting it "
            "find no modules for it"
        )
        if args.strict_payload:
            problems.append(message)
        else:
            print(f"nfsroot-kernels: NOTE {message}", file=sys.stderr)

    for problem in problems:
        print(f"nfsroot-kernels: FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        f"nfsroot-kernels: OK {len(trees)} kernel trees "
        f"(limit {args.max_trees}), serving {' '.join(sorted(served)) or 'nothing'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nfs-root",
        required=True,
        help="the tree holding boot/ and root/, e.g. /srv/nfs/rpi/bookworm",
    )
    parser.add_argument(
        "--tftp-root",
        default=None,
        help="TFTP root, when it is not <nfs-root>/boot (the sunxi payload)",
    )
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="GLOB",
        help="kernel version glob to keep regardless (repeatable)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "protect", help="apt-mark manual the kernels the TFTP payload serves"
    ).set_defaults(func=cmd_protect)

    sub.add_parser("plan", help="print the keep/purge plan as JSON").set_defaults(
        func=cmd_plan
    )

    prune = sub.add_parser("prune", help="purge superseded kernel packages")
    prune.add_argument("--dry-run", action="store_true")
    prune.set_defaults(func=cmd_prune)

    refresh = sub.add_parser(
        "refresh-initramfs", help="rebuild only the stale initramfs images"
    )
    refresh.add_argument("--dry-run", action="store_true")
    refresh.set_defaults(func=cmd_refresh_initramfs)

    check = sub.add_parser("check", help="assert the root has not drifted back")
    check.add_argument("--max-trees", type=int, default=10)
    check.add_argument(
        "--strict-payload",
        action="store_true",
        help="also fail when a served kernel image matches no installed kernel",
    )
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
