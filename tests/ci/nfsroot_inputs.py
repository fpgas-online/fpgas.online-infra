#!/usr/bin/env python3
"""Content key of everything that shapes the CI-built Pi NFS root image.

The image (ansible/ci-nfsroot.yml, published by nfsroot_publish.py) depends
only on the files listed in INPUTS plus whatever the package repositories
serve on the day. nfsroot-build.yml tags every image it pushes with
`inputs-<key>`; a later run whose key already exists reuses that image
(a registry-side retag, seconds) instead of rebuilding it (~15 min). So a
PR that touches no image input boots exactly the image main would, and one
that does touch an input always gets a fresh build of its own checkout.

The key includes the UTC hour, so an unchanged checkout still rebuilds at
least hourly and picks up new debs (the same cadence as nfsroot-build.yml's
hourly schedule, whose image the later runs of that hour then reuse).

tests/test_nfsroot_inputs.py fails if ci-nfsroot.yml starts using a role,
or a role starts reading another role's files, that INPUTS does not cover.

usage: nfsroot_inputs.py            print the key
       nfsroot_inputs.py --list     print the files that feed it
"""
import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Paths (files or directories) whose content can change the built image.
INPUTS = [
    "ansible/ci-nfsroot.yml",
    # the stages it builds on (nfsroot_stages.py)
    "ansible/ci-nfsroot-base.yml",
    "ansible/ci-nfsroot-upgrade.yml",
    "ansible/ci-nfsroot-runner.yml",
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
    "ansible/roles/nspawn_pi",
    "ansible/roles/fpgas_apt",
    "ansible/roles/cam_pi",
    "ansible/roles/onpi",
    "ansible/roles/ttsite/templates/tt-boards.yaml.j2",
    # the build and publish machinery itself
    ".github/workflows/nfsroot-build.yml",
    ".github/actions/nfsroot-setup",
    "tests/ci",
    "requirements.yml",
    "pyproject.toml",
    "uv.lock",
]


# What chooses the RasPiOS image a from-scratch build starts from: the
# image file (named by these variables, rendered from the group_vars below,
# later files winning) and the tasks that download and unpack it. A warm
# build (nfsroot_warm.py) may only converge a published image whose
# base_key label matches: a changed base must be built from scratch.
#
# Only the image's identity counts, not the whole of srv.yml: an edit to
# any other variable there (tftp_root, say) is an ordinary image input that
# a warm build converges, instead of a ~10 min from-scratch build. img_host
# is left out too: it names a mirror of the same file.
BASE_VARS = ["dist", "img_path", "img_name", "zip_name"]
BASE_VAR_FILES = [
    "ansible/inventory/group_vars/all/srv.yml",
    "ansible/inventory-ci-nfsroot/group_vars/all/zz-ci-overrides.yml",
]
BASE_FILES = [
    "ansible/ci-nfsroot-base.yml",
    "ansible/ci-nfsroot-runner.yml",
    "ansible/roles/img/tasks/build.yml",
    "ansible/roles/img/files/img2files.sh",
]
BASE_INPUTS = BASE_VAR_FILES + BASE_FILES
BASE_LABEL = "org.fpgas-online.nfsroot.base-key"

_TOP_LEVEL_VAR = re.compile(r"^([A-Za-z_]\w*):\s*(.*?)\s*$")
_JINJA_REF = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def base_vars() -> dict[str, str]:
    """BASE_VARS as the build sees them: the image file it downloads.

    The group_vars files hold flat `name: value` scalars with `{{ name }}`
    references (read without PyYAML: the workflow runs this with the
    runner's bare python3).
    """
    raw: dict[str, str] = {}
    for rel in BASE_VAR_FILES:
        for line in (REPO / rel).read_text().splitlines():
            if m := _TOP_LEVEL_VAR.match(line):
                raw[m.group(1)] = m.group(2).strip("\"'")

    def render(name: str, depth: int = 0) -> str:
        if name not in raw or depth > 10:
            raise KeyError(f"cannot resolve {name} from {BASE_VAR_FILES}")
        return _JINJA_REF.sub(lambda m: render(m.group(1), depth + 1), raw[name])

    return {name: render(name) for name in BASE_VARS}


def base_key() -> str:
    h = hashlib.sha256()
    for name, value in sorted(base_vars().items()):
        h.update(f"{name}={value}".encode() + b"\0")
    for rel in sorted(BASE_FILES):
        h.update(rel.encode() + b"\0" + (REPO / rel).read_bytes() + b"\0")
    return h.hexdigest()[:20]


# What else shapes the upgraded stage (ci-nfsroot-upgrade.yml) on top of
# the base: that playbook, the chroot session it runs apt in, and the
# inventory and Ansible it runs with. Its image tag carries upgraded_key(),
# so changing any of these builds a new stage instead of reusing a stale one.
UPGRADE_INPUTS = [
    "ansible/ci-nfsroot-upgrade.yml",
    "ansible/ci-nfsroot-runner.yml",
    "ansible/roles/nspawn_pi",
    "ansible/inventory-ci-nfsroot",
    "ansible/inventory/group_vars/all/ci.yml",
    "ansible/inventory/group_vars/all/srv.yml",
    "ansible/inventory/group_vars/all/ssh_keys.yml",
    "ansible.cfg",
    "requirements.yml",
]


def _hash_files(h, paths: list[str]) -> None:
    for rel in tracked_files(paths):
        path = REPO / rel
        h.update(rel.encode() + b"\0")
        # Hash what the build reads: a symlink's target content, not the link.
        h.update(path.read_bytes() if path.exists() else b"<missing>")
        h.update(b"\0")


def upgraded_key() -> str:
    h = hashlib.sha256()
    h.update(base_key().encode() + b"\0")
    _hash_files(h, UPGRADE_INPUTS)
    return h.hexdigest()[:20]


def tracked_files(paths: list[str]) -> list[str]:
    """Tracked files under paths, sorted (git's view: no stray build output)."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", *paths],
        check=True, capture_output=True,
    ).stdout.decode()
    return sorted(p for p in out.split("\0") if p)


def input_files() -> list[str]:
    return tracked_files(INPUTS)


def period() -> str:
    """The current UTC hour: how long an image counts as fresh."""
    return time.strftime("%Y-%m-%dT%H", time.gmtime())


def key(period_: str | None = None) -> str:
    h = hashlib.sha256()
    h.update((period_ or period()).encode() + b"\0")
    _hash_files(h, INPUTS)
    return h.hexdigest()[:20]


def main() -> int:
    if "--list" in sys.argv[1:]:
        print("\n".join(input_files()))
    else:
        print(key())
    return 0


if __name__ == "__main__":
    sys.exit(main())
