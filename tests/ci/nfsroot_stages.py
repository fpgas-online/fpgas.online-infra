#!/usr/bin/env python3
"""The two stage images the CI Pi root build starts from.

Published as tags of ghcr.io/fpgas-online/nfsroot, keyed by
nfsroot_inputs.py:

  base-<base_key>          the RasPiOS release srv.yml names, unpacked into
                           boot/ and root/ (ansible/ci-nfsroot-base.yml)
  upgraded-<upgraded_key>  that tree with every package upgraded
                           (ansible/ci-nfsroot-upgrade.yml); each refresh is
                           also tagged upgraded-<upgraded_key>-<UTC hour>

The published image (ansible/ci-nfsroot.yml) is built on the upgraded
stage whenever it is not a warm build of main's last image
(nfsroot_warm.py). The release image's packages are more than a year
behind the archive, and upgrading them was the largest single task of a
from-scratch build.

usage: nfsroot_stages.py MODE
  ensure    leave the upgraded stage in NFSROOT: unpack the published one,
            or build it if none exists for this checkout's key (PR and push
            builds)
  upgraded  rebuild and publish the upgraded stage on the published base
            (the hourly schedule)
  all       rebuild and publish both stages, starting from the RasPiOS
            download (the daily schedule)

NFSROOT (nfsroot_publish.NFSROOT) is emptied first. Needs sudo, skopeo and
a `docker login` to ghcr.io, and the repo's uv venv with the collections
from requirements.yml.
"""
import os
import sys
from pathlib import Path

import nfsroot_inputs
from nfsroot_publish import IMAGE, NFSROOT, push_tree, run, write_outputs
from nfsroot_warm import extract

STAGE_LABEL = "org.fpgas-online.nfsroot.stage"


def base_tag() -> str:
    return f"{IMAGE}:base-{nfsroot_inputs.base_key()}"


def upgraded_tag() -> str:
    return f"{IMAGE}:upgraded-{nfsroot_inputs.upgraded_key()}"


def exists(tag: str) -> bool:
    return run(["skopeo", "inspect", "--raw", f"docker://{tag}"],
               check=False, capture_output=True).returncode == 0


def playbook(name: str) -> None:
    """Run a CI build playbook as root against inventory-ci-nfsroot."""
    run(["sudo", "env",
         f"ANSIBLE_COLLECTIONS_PATH={Path.home() / '.ansible' / 'collections'}",
         "ANSIBLE_CALLBACKS_ENABLED=ansible.posix.profile_tasks",
         nfsroot_inputs.REPO / ".venv" / "bin" / "ansible-playbook",
         "-i", "inventory-ci-nfsroot/hosts", name],
        cwd=nfsroot_inputs.REPO / "ansible")


def empty_root() -> None:
    run(["sudo", "rm", "-rf", NFSROOT])


def unpack(tag: str) -> bool:
    """Unpack a published stage into NFSROOT; False (and NFSROOT empty) if it cannot be."""
    if not exists(tag):
        print(f"{tag} is not published", flush=True)
        return False
    if extract(tag, Path(NFSROOT)) is None:
        print(f"cannot fetch {tag}", flush=True)
        empty_root()
        return False
    print(f"unpacked {tag}", flush=True)
    return True


def labels(stage: str) -> dict[str, str]:
    return {nfsroot_inputs.BASE_LABEL: nfsroot_inputs.base_key(), STAGE_LABEL: stage}


def build_base(publish: bool) -> None:
    playbook("ci-nfsroot-base.yml")
    if publish:
        push_tree([base_tag()], labels("base"), "nfsroot base stage published")


def build_upgraded() -> None:
    """Upgrade the base tree in NFSROOT and publish it (the rolling tag last)."""
    playbook("ci-nfsroot-upgrade.yml")
    tag = upgraded_tag()
    push_tree([f"{tag}-{nfsroot_inputs.period()}", tag], labels("upgraded"),
              "nfsroot upgraded stage published")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in ("ensure", "upgraded", "all"):
        print(__doc__, file=sys.stderr)
        return 2
    empty_root()
    if mode == "ensure":
        if unpack(upgraded_tag()):
            write_outputs(stage="unpacked")
            return 0
        # A PR build waits on this, so it publishes only the stage later
        # runs start from; the hourly refresh rebuilds a missing base.
        if not unpack(base_tag()):
            build_base(publish=False)
        build_upgraded()
        write_outputs(stage="built")
        return 0
    if mode == "upgraded" and unpack(base_tag()):
        build_upgraded()
        return 0
    build_base(publish=True)
    build_upgraded()
    return 0


if __name__ == "__main__":
    sys.exit(main())
