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
  ensure     leave the upgraded stage in NFSROOT: unpack the published one,
             or build it if none exists for this checkout's key (PR and push
             builds)
  scheduled  `all` when the published base stage is missing, carries no
             build time, or was built SCRATCH_AFTER or longer ago; otherwise
             `upgraded` (every scheduled run)
  upgraded   rebuild and publish the upgraded stage on the published base
  all        rebuild and publish both stages, starting from the RasPiOS
             download (also a from-scratch dispatch)

The scheduled choice goes by the base stage's age, not by which cron fired:
GitHub runs schedules best-effort, and has started this repo's hourly one
about every 4-6 hours and a daily one 5.5 hours late. Whichever scheduled
run comes first once the base is a day old rebuilds from the download.

`all` and `upgraded` (and so `scheduled`) write the step output
scratch=true when the stages were rebuilt from the download, which tells
the build job to build on them instead of converging main's last image.

NFSROOT (nfsroot_publish.NFSROOT) is emptied first. Needs sudo, skopeo and
a `docker login` to ghcr.io, and the repo's uv venv with the collections
from requirements.yml.
"""
import calendar
import sys
import time
from pathlib import Path

import nfsroot_inputs
from nfsroot_publish import IMAGE, NFSROOT, push_tree, run, write_outputs
from nfsroot_warm import extract
from nfsroot_warm import labels as image_labels

STAGE_LABEL = "org.fpgas-online.nfsroot.stage"
BUILT_LABEL = "org.fpgas-online.nfsroot.built"  # UTC, TIME_FORMAT
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SCRATCH_AFTER = 24 * 3600  # seconds


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
    return {nfsroot_inputs.BASE_LABEL: nfsroot_inputs.base_key(), STAGE_LABEL: stage,
            BUILT_LABEL: time.strftime(TIME_FORMAT, time.gmtime())}


def base_age(now: float | None = None) -> float | None:
    """Seconds since the published base stage was built; None if unknown."""
    built = (image_labels(base_tag()) or {}).get(BUILT_LABEL)
    if not built:
        return None
    try:
        then = calendar.timegm(time.strptime(built, TIME_FORMAT))
    except ValueError:
        return None
    return (time.time() if now is None else now) - then


def scratch_due(age: float | None) -> bool:
    return age is None or age >= SCRATCH_AFTER


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
    if mode not in ("ensure", "scheduled", "upgraded", "all"):
        print(__doc__, file=sys.stderr)
        return 2
    if mode == "scheduled":
        age = base_age()
        mode = "all" if scratch_due(age) else "upgraded"
        shown = "unknown" if age is None else f"{age / 3600:.1f} h"
        print(f"base stage age: {shown}; rebuilding {mode}", flush=True)
    empty_root()
    if mode == "ensure":
        if unpack(upgraded_tag()):
            write_outputs(stage="unpacked")
            return 0
        # A PR build waits on this, so it publishes only the stage later
        # runs start from; the next scheduled run rebuilds a missing base.
        if not unpack(base_tag()):
            build_base(publish=False)
        build_upgraded()
        write_outputs(stage="built")
        return 0
    if mode == "upgraded" and unpack(base_tag()):
        build_upgraded()
        write_outputs(scratch="false")
        return 0
    build_base(publish=True)
    build_upgraded()
    write_outputs(scratch="true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
