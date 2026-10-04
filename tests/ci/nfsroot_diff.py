#!/usr/bin/env python3
"""Diff two nfsroot manifests written by nfsroot_manifest.py (issue #34).

Usage: nfsroot_diff.py <manifest_dir_a> <manifest_dir_b>

Prints a human-readable comparison: package set differences, boot payload
differences (by sha256), config file diffs, service enablement diffs, and
a summary of root/ tree paths present on only one side (grouped so package
version churn does not drown the signal).
"""

import difflib
import sys
from collections import Counter
from pathlib import Path


def read_tsv(path: Path, key_fields: int):
    rows = {}
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        parts = line.split("\t")
        rows["\t".join(parts[:key_fields])] = "\t".join(parts[key_fields:])
    return rows


def section(title):
    print(f"\n=== {title} ===")


def diff_maps(a, b, label_a, label_b, limit=40):
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    for name, items in ((f"only in {label_a}", only_a),
                        (f"only in {label_b}", only_b)):
        print(f"{name}: {len(items)}")
        for k in items[:limit]:
            print(f"  {k}")
        if len(items) > limit:
            print(f"  ... and {len(items) - limit} more")
    print(f"differing: {len(changed)}")
    for k in changed[:limit]:
        print(f"  {k}: {a[k]!r} vs {b[k]!r}")
    if len(changed) > limit:
        print(f"  ... and {len(changed) - limit} more")
    return only_a, only_b, changed


def main():
    a_dir, b_dir = Path(sys.argv[1]), Path(sys.argv[2])
    label_a, label_b = a_dir.name, b_dir.name

    section(f"packages ({label_a} vs {label_b})")
    diff_maps(read_tsv(a_dir / "packages.tsv", 1),
              read_tsv(b_dir / "packages.tsv", 1), label_a, label_b)

    section("boot/ payload (path -> size, sha256)")
    diff_maps(read_tsv(a_dir / "boot-files.tsv", 1),
              read_tsv(b_dir / "boot-files.tsv", 1), label_a, label_b)

    for name in ("configs.txt", "services.txt"):
        section(name)
        a_text = (a_dir / name).read_text().splitlines(keepends=True)
        b_text = (b_dir / name).read_text().splitlines(keepends=True)
        diff = list(difflib.unified_diff(a_text, b_text, label_a, label_b, n=1))
        if diff:
            sys.stdout.writelines(diff[:400])
            if len(diff) > 400:
                print(f"... and {len(diff) - 400} more diff lines")
        else:
            print("identical")

    section("root/ tree (paths only on one side, grouped by depth-3 prefix)")
    a_files = read_tsv(a_dir / "root-files.tsv", 1)
    b_files = read_tsv(b_dir / "root-files.tsv", 1)
    for name, only in ((f"only in {label_a}", set(a_files) - set(b_files)),
                       (f"only in {label_b}", set(b_files) - set(a_files))):
        groups = Counter("/".join(p.split("/")[:3]) for p in only)
        print(f"{name}: {len(only)} paths in {len(groups)} groups")
        for prefix, count in groups.most_common(30):
            print(f"  {count:6d}  {prefix}")


if __name__ == "__main__":
    sys.exit(main())
