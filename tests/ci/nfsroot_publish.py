#!/usr/bin/env python3
"""Package the built Pi NFS root tree as a single-layer OCI image and push
it to GHCR (issue #34, design doc
docs/superpowers/specs/2026-08-31-ci-nfsroot-build-design.md).

The image filesystem holds top-level boot/ and root/ directories mirroring
/srv/nfs/rpi/<dist>. Tags:

  - <dist>-armhf-YYYYMMDD-<sha7>   always (pinnable; production references
    this in host_vars so a rebuild is reproducible)
  - <dist>-armhf                   rolling, only from main
  - $NFSROOT_EXTRA_TAG             when set: a tag the caller chose before
    the build started (the VM test polls for ci-<run_id> so it can run its
    server phase while this build is still going)
  - inputs-<key>                   always: the content key of the image's
    inputs (nfsroot_inputs.py), which later runs look up to reuse it

`--reuse` publishes the same tags without building: when an image for this
checkout's inputs key already exists it is retagged in the registry
(docker buildx imagetools: manifests only, no layer is pulled or pushed),
and the `reused` step output says whether that happened.

Run after ansible/ci-nfsroot.yml on the runner (needs sudo for tar to read
the root-owned tree, and a docker login to ghcr.io).
"""

import os
import subprocess
import sys
import time

import nfsroot_inputs

DIST = "bookworm"
NFSROOT = f"/srv/nfs/rpi/{DIST}"
IMAGE = "ghcr.io/fpgas-online/nfsroot"


def run(cmd, **kwargs):
    cmd = [str(c) for c in cmd]
    print("+", " ".join(cmd), flush=True)
    check = kwargs.pop("check", True)
    return subprocess.run(cmd, check=check, **kwargs)


def tags() -> tuple[str, str, list[str]]:
    """(dated tag, inputs tag, the other tags this run publishes)."""
    sha = os.environ.get("GITHUB_SHA", "0000000")[:7]
    dated = f"{IMAGE}:{DIST}-armhf-{time.strftime('%Y%m%d')}-{sha}"
    inputs = f"{IMAGE}:inputs-{nfsroot_inputs.key()}"
    others = []
    if extra := os.environ.get("NFSROOT_EXTRA_TAG"):
        others.append(f"{IMAGE}:{extra}")
    if os.environ.get("GITHUB_REF_NAME") == "main":
        others.append(f"{IMAGE}:{DIST}-armhf")
    return dated, inputs, others


def write_outputs(**values):
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def summary(lines):
    text = "\n".join(lines)
    print(text, flush=True)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write(text + "\n")


def reuse() -> int:
    """Retag the image already built from these inputs, if there is one."""
    dated, inputs, others = tags()
    found = run(["docker", "manifest", "inspect", inputs],
                check=False, capture_output=True).returncode == 0
    if not found:
        print(f"{inputs} does not exist: this run builds the image", flush=True)
        write_outputs(reused="false")
        return 0
    cmd = ["docker", "buildx", "imagetools", "create"]
    for t in [dated] + others:
        cmd += ["--tag", t]
    run(cmd + [inputs])
    write_outputs(reused="true", image=dated)
    summary(["## nfsroot reused (no image input changed)", "",
             f"- from `{inputs}`"] + [f"- `{t}`" for t in [dated] + others])
    return 0


def build() -> int:
    dated, inputs, others = tags()

    tar = subprocess.Popen(
        ["sudo", "tar", "-C", NFSROOT, "--numeric-owner", "--xattrs",
         "-c", "boot", "root"],
        stdout=subprocess.PIPE,
    )
    run(["docker", "import", "-", dated], stdin=tar.stdout)
    tar.stdout.close()
    if tar.wait() != 0:
        raise RuntimeError("tar of the nfsroot tree failed")

    size = int(
        run(
            ["docker", "image", "inspect", "--format", "{{.Size}}", dated],
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    run(["docker", "push", dated])
    # The layer is in the registry now, so every further tag is a
    # manifest-only push. inputs-<key> goes last: a later run that finds it
    # (and reuses the image) never sees a half-published set of tags.
    pushed = [dated]
    for t in others + [inputs]:
        run(["docker", "tag", dated, t])
        run(["docker", "push", t])
        pushed.append(t)

    # The dated tag is this build's identity: downstream jobs (the VM test)
    # consume it via the workflow_call output.
    write_outputs(image=dated)
    summary(["## nfsroot published", "",
             f"- size: {size / 1e9:.2f} GB uncompressed"]
            + [f"- `{tag}`" for tag in pushed])
    return 0


def main():
    return reuse() if "--reuse" in sys.argv[1:] else build()


if __name__ == "__main__":
    sys.exit(main())
