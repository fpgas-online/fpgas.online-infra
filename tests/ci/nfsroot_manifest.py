#!/usr/bin/env python3
"""Write a comparable manifest of a Pi NFS root tree (issue #34).

Usage: nfsroot_manifest.py <nfsroot_dir> <out_dir>

<nfsroot_dir> holds boot/ and root/ (e.g. /srv/nfs/rpi/bookworm). Run as
root so permission-restricted paths are readable. The same script runs in
the CI build (uploaded as a workflow artifact) and on a production server,
so the two trees can be diffed file-by-file and package-by-package.

Outputs in <out_dir>:
  packages.tsv   dpkg name/version/arch from the root's dpkg database
  boot-files.tsv relpath, size, sha256 of every file under boot/
  root-files.tsv relpath, type, size, uid:gid, mode under root/ (no hashes;
                 pseudo-filesystems and cache/log noise excluded)
  configs.txt    verbatim dump of the role-managed config files
  services.txt   enabled units (multi-user.target.wants) + ssh/.ssh listings
                 (names and sizes only -- never key material)
"""

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

# Never descend into these under root/: API mounts may be live on a server
# (nspawn binds), and caches/logs/lists are pure noise between two builds.
ROOT_EXCLUDES = {
    "proc", "sys", "dev", "run", "tmp",
    "var/cache", "var/log", "var/backups", "var/lib/apt/lists",
    "var/lib/dpkg/info",
}

CONFIG_FILES = [
    "boot/cmdline.txt",
    "boot/config.txt",
    "boot/userconf.txt",
    "root/etc/fstab",
    "root/etc/environment",
    "root/etc/resolv.conf",
    "root/etc/resolve.conf",
    "root/etc/hostname",
    "root/etc/systemd/timesyncd.conf.d/fpgas.conf",
    "root/etc/fpgas-online/tt-boards.yaml",
    "root/usr/sbin/policy-rc.d",
]
CONFIG_GLOBS = [
    "root/etc/apt/sources.list.d/*",
    "root/etc/sudoers.d/*",
]
LIST_ONLY_DIRS = [
    "root/etc/ssh",
    "root/etc/ssh/sshd_config.d",
    "root/home/pi/.ssh",
    "root/root/.ssh",
    "root/etc/systemd/system/multi-user.target.wants",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def walk(top: Path, excludes=()):
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames.sort()
        dirnames[:] = [
            d for d in dirnames
            if os.path.relpath(os.path.join(dirpath, d), top) not in excludes
        ]
        for name in sorted(filenames + dirnames):
            full = Path(dirpath) / name
            yield full, os.path.relpath(full, top)


def main():
    nfsroot = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)

    admindir = nfsroot / "root/var/lib/dpkg"
    pkgs = subprocess.run(
        ["dpkg-query", f"--admindir={admindir}", "-W",
         "-f", "${Package}\t${Version}\t${Architecture}\n"],
        capture_output=True, text=True, check=True,
    ).stdout
    (out / "packages.tsv").write_text("".join(sorted(pkgs.splitlines(True))))

    with open(out / "boot-files.tsv", "w") as f:
        for path, rel in walk(nfsroot / "boot"):
            if path.is_file() and not path.is_symlink():
                f.write(f"{rel}\t{path.stat().st_size}\t{sha256(path)}\n")

    with open(out / "root-files.tsv", "w") as f:
        for path, rel in walk(nfsroot / "root", ROOT_EXCLUDES):
            st = path.lstat()
            kind = ("l" if stat.S_ISLNK(st.st_mode)
                    else "d" if stat.S_ISDIR(st.st_mode) else "f")
            size = st.st_size if kind == "f" else 0
            f.write(f"{rel}\t{kind}\t{size}\t{st.st_uid}:{st.st_gid}"
                    f"\t{stat.S_IMODE(st.st_mode):04o}\n")

    with open(out / "configs.txt", "w") as f:
        files = [nfsroot / c for c in CONFIG_FILES]
        for pattern in CONFIG_GLOBS:
            files += sorted(nfsroot.glob(pattern))
        for path in files:
            rel = os.path.relpath(path, nfsroot)
            f.write(f"===== {rel} =====\n")
            try:
                f.write(path.read_text() + "\n")
            except FileNotFoundError:
                f.write("(absent)\n\n")

    with open(out / "services.txt", "w") as f:
        for d in LIST_ONLY_DIRS:
            f.write(f"===== {d} =====\n")
            p = nfsroot / d
            if not p.is_dir():
                f.write("(absent)\n")
                continue
            for entry in sorted(p.iterdir()):
                st = entry.lstat()
                target = f" -> {os.readlink(entry)}" if entry.is_symlink() else ""
                f.write(f"{entry.name}\t{st.st_size}{target}\n")
            f.write("\n")

    print(f"manifest written to {out}")


if __name__ == "__main__":
    sys.exit(main())
