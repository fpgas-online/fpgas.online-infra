#!/usr/bin/env python3
"""Content key of everything that shapes the CI-built Pi NFS root image.

The image (ansible/ci-nfsroot.yml, published by nfsroot_publish.py) depends
only on the files listed in INPUTS plus whatever the package repositories
serve on the day. nfsroot-build.yml tags every image it pushes with
`inputs-<key>`; a later run whose key already exists reuses that image
(a registry-side retag, seconds) instead of rebuilding it (~15 min). So a
PR that touches no image input boots exactly the image main would, and one
that does touch an input always gets a fresh build of its own checkout.

The key includes the ISO week, so an unchanged checkout still rebuilds at
least weekly and picks up new debs (the same cadence as the weekly cron).

tests/test_nfsroot_inputs.py fails if ci-nfsroot.yml starts using a role,
or a role starts reading another role's files, that INPUTS does not cover.

usage: nfsroot_inputs.py            print the key
       nfsroot_inputs.py --list     print the files that feed it
"""
import hashlib
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Paths (files or directories) whose content can change the built image.
INPUTS = [
    "ansible/ci-nfsroot.yml",
    "ansible/inventory-ci-nfsroot",
    # group_vars the CI inventory symlinks from the production inventory
    "ansible/inventory/group_vars/all/ci.yml",
    "ansible/inventory/group_vars/all/srv.yml",
    "ansible/inventory/group_vars/all/ssh_keys.yml",
    "ansible/filter_plugins",
    "ansible.cfg",
    # roles ci-nfsroot.yml runs, and files they read from other roles
    "ansible/roles/img",
    "ansible/roles/fixpi",
    "ansible/roles/nspawn-pi",
    "ansible/roles/fpgas-apt",
    "ansible/roles/cam/pi",
    "ansible/roles/onpi",
    "ansible/roles/ttsite/templates/tt-boards.yaml.j2",
    # the build and publish machinery itself
    ".github/workflows/nfsroot-build.yml",
    "tests/ci",
    "requirements.yml",
    "pyproject.toml",
    "uv.lock",
]


# Files that choose the RasPiOS image a from-scratch build starts from.
# A warm build (nfsroot_warm.py) may only converge a published image whose
# base_key label matches: a changed base must be built from scratch.
BASE_INPUTS = [
    "ansible/inventory/group_vars/all/srv.yml",
    "ansible/inventory-ci-nfsroot/group_vars/all/zz-ci-overrides.yml",
    "ansible/roles/img/tasks/build.yml",
]
BASE_LABEL = "org.fpgas-online.nfsroot.base-key"


def base_key() -> str:
    h = hashlib.sha256()
    for rel in sorted(BASE_INPUTS):
        h.update(rel.encode() + b"\0" + (REPO / rel).read_bytes() + b"\0")
    return h.hexdigest()[:20]


def input_files() -> list[str]:
    """Tracked files under INPUTS, sorted (git's view: no stray build output)."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", *INPUTS],
        check=True, capture_output=True,
    ).stdout.decode()
    return sorted(p for p in out.split("\0") if p)


def key(week: str | None = None) -> str:
    h = hashlib.sha256()
    h.update((week or time.strftime("%G-W%V", time.gmtime())).encode() + b"\0")
    for rel in input_files():
        path = REPO / rel
        h.update(rel.encode() + b"\0")
        # Hash what the build reads: a symlink's target content, not the link.
        h.update(path.read_bytes() if path.exists() else b"<missing>")
        h.update(b"\0")
    return h.hexdigest()[:20]


def main() -> int:
    if "--list" in sys.argv[1:]:
        print("\n".join(input_files()))
    else:
        print(key())
    return 0


if __name__ == "__main__":
    sys.exit(main())
