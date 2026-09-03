#!/usr/bin/env python3
"""Package the built Pi NFS root tree as a single-layer OCI image and push
it to GHCR (issue #34, design doc
docs/superpowers/specs/2026-08-31-ci-nfsroot-build-design.md).

The image filesystem holds top-level boot/ and root/ directories mirroring
/srv/nfs/rpi/<dist>. Tags:

  - <dist>-armhf-YYYYMMDD-<sha7>   always (pinnable; production references
    this in host_vars so a rebuild is reproducible)
  - <dist>-armhf                   rolling, only from main

Run after ansible/ci-nfsroot.yml on the runner (needs sudo for tar to read
the root-owned tree, and a docker login to ghcr.io).
"""

import os
import subprocess
import sys
import time

DIST = "bookworm"
NFSROOT = f"/srv/nfs/rpi/{DIST}"
IMAGE = "ghcr.io/fpgas-online/nfsroot"


def run(cmd, **kwargs):
    cmd = [str(c) for c in cmd]
    print("+", " ".join(cmd), flush=True)
    check = kwargs.pop("check", True)
    return subprocess.run(cmd, check=check, **kwargs)


def main():
    sha = os.environ.get("GITHUB_SHA", "0000000")[:7]
    dated_tag = f"{IMAGE}:{DIST}-armhf-{time.strftime('%Y%m%d')}-{sha}"
    rolling_tag = f"{IMAGE}:{DIST}-armhf"
    on_main = os.environ.get("GITHUB_REF_NAME") == "main"

    tar = subprocess.Popen(
        ["sudo", "tar", "-C", NFSROOT, "--numeric-owner", "--xattrs",
         "-c", "boot", "root"],
        stdout=subprocess.PIPE,
    )
    run(["docker", "import", "-", dated_tag], stdin=tar.stdout)
    tar.stdout.close()
    if tar.wait() != 0:
        raise RuntimeError("tar of the nfsroot tree failed")

    size = int(
        run(
            ["docker", "image", "inspect", "--format", "{{.Size}}", dated_tag],
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    run(["docker", "push", dated_tag])
    pushed = [dated_tag]
    if on_main:
        run(["docker", "tag", dated_tag, rolling_tag])
        run(["docker", "push", rolling_tag])
        pushed.append(rolling_tag)

    # The dated tag is this build's identity: downstream jobs (the VM test)
    # consume it via the workflow_call output.
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"image={dated_tag}\n")

    lines = [
        "## nfsroot published",
        "",
        f"- size: {size / 1e9:.2f} GB uncompressed",
    ] + [f"- `{tag}`" for tag in pushed]
    if not on_main:
        lines.append(f"- rolling `{rolling_tag}` NOT updated (not on main)")
    text = "\n".join(lines)
    print(text, flush=True)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    sys.exit(main())
