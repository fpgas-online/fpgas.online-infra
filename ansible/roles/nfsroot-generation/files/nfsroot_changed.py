#!/usr/bin/env python3
"""Did this site.yml run change the Pi NFS root?

Runs on the SERVER against the Pi root tree at the end of a run, and prints
one JSON object for roles/nfsroot-generation/tasks/end.yml:

    {"bump": true, "count": 3, "reason": "3 files changed", "sample": [...]}

Why ctime against the update lock
---------------------------------
Every running board holds file handles into this tree. A file replaced by a
rename (Ansible's copy/template, dpkg unpacking a package, dpkg rewriting
/var/lib/dpkg/status) is a new inode, and the boards' overlays answer
ESTALE for the old one until they reboot. roles/nfsroot-generation/tasks/
begin.yml writes the lock at the start of the run, so any file whose ctime
is newer than the lock's was created, replaced or modified by this run.
ctime rather than mtime because dpkg unpacks files with the package's own
(old) mtimes, and nothing in userspace can set a ctime.

Only files and symlinks count. Directories get a new ctime whenever an entry
is added or removed, including the temp files every converge creates and
deletes again; a real change to a directory's contents shows up as a changed
file inside it.

No lock at all means the run cannot tell what changed (the root was
re-extracted, or the lock was removed by hand), so it answers "bump".
"""

import argparse
import json
import os
import stat
import sys


def scan(root: str, since_ns: int, ignore: set[str], sample_size: int):
    root_dev = os.lstat(root).st_dev
    count = 0
    sample: list[str] = []
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        try:
            entries = list(os.scandir(os.path.join(root, rel_dir)))
        except OSError as e:
            print(f"nfsroot_changed: cannot list /{rel_dir}: {e}", file=sys.stderr)
            continue
        for entry in entries:
            rel = os.path.join(rel_dir, entry.name)
            if rel in ignore:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue  # removed since the listing
            if stat.S_ISDIR(st.st_mode):
                # like find -xdev: /proc, /dev etc. may still be bind-mounted
                if st.st_dev == root_dev:
                    stack.append(rel)
                continue
            if st.st_ctime_ns > since_ns:
                count += 1
                if len(sample) < sample_size:
                    sample.append("/" + rel)
    return count, sorted(sample)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", help="the Pi root tree, e.g. /srv/nfs/rpi/bookworm/root")
    ap.add_argument("lock", help="the update lock, as a path inside the root")
    ap.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="path relative to the root to leave out (repeatable)",
    )
    ap.add_argument("--sample", type=int, default=20, help="changed paths to list")
    args = ap.parse_args(argv)

    lock = os.path.join(args.root, args.lock.lstrip("/"))
    try:
        since_ns = os.lstat(lock).st_ctime_ns
    except FileNotFoundError:
        result = {
            "bump": True,
            "count": None,
            "reason": f"no update lock at {args.lock}, cannot tell what changed",
            "sample": [],
        }
    else:
        ignore = {p.strip("/") for p in args.ignore}
        count, sample = scan(args.root, since_ns, ignore, args.sample)
        result = {
            "bump": count > 0,
            "count": count,
            "reason": f"{count} files changed" if count else "nothing changed",
            "sample": sample,
        }
    json.dump(result, sys.stdout)
    print()


if __name__ == "__main__":
    main()
