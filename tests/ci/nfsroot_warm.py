#!/usr/bin/env python3
"""Seed the NFS root build with the latest published image (a warm build).

A from-scratch build downloads RasPiOS and runs every Pi role's apt work
on it (~10 min on the arm64 runner). A warm build instead extracts the
image main last published -- which those same roles produced -- and runs
ansible/ci-nfsroot.yml over it: the roles converge it to this checkout,
so their apt runs are mostly no-ops. This is how tweed converged its live
root for years, before the image moved to CI. The weekly scheduled build
(and a workflow_dispatch with from_scratch) still builds from RasPiOS, so
the from-scratch path is exercised and a warm chain never gets older than
a week.

usage: nfsroot_warm.py IMAGE NFSROOT
Writes warm=true|false and base=<digest> to $GITHUB_OUTPUT. A missing or
unreadable image, or one whose RasPiOS base (the base-key label that
nfsroot_publish.py records) differs from this checkout's, means a build
from scratch instead.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import nfsroot_inputs


def run(cmd, **kwargs):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], **kwargs)


def output(**values):
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def main() -> int:
    image, nfsroot = sys.argv[1], Path(sys.argv[2])
    want = nfsroot_inputs.base_key()
    config = run(["skopeo", "inspect", "--config", f"docker://{image}"],
                 capture_output=True, text=True)
    labels = (json.loads(config.stdout).get("config") or {}).get("Labels") or {} \
        if config.returncode == 0 else {}
    have = labels.get(nfsroot_inputs.BASE_LABEL)
    if have != want:
        print(f"{image} descends from base {have}, this checkout's is {want}: "
              "building from scratch", flush=True)
        output(warm="false")
        return 0
    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as tmp:
        dest = Path(tmp) / "image"
        if run(["skopeo", "copy", f"docker://{image}", f"dir:{dest}"]).returncode != 0:
            print(f"cannot fetch {image}: building from scratch", flush=True)
            output(warm="false")
            return 0
        manifest = json.loads((dest / "manifest.json").read_text())
        run(["sudo", "install", "-d", nfsroot], check=True)
        for layer in manifest["layers"]:
            blob = dest / layer["digest"].split(":", 1)[1]
            # GNU tar recognises gzip and zstd by their magic bytes.
            run(["sudo", "tar", "-x", "--numeric-owner", "--xattrs", "--xattrs-include=*",
                 "-C", nfsroot, "-f", blob], check=True)
        digest = run(["skopeo", "inspect", "--format", "{{.Digest}}", f"dir:{dest}"],
                     capture_output=True, text=True, check=True).stdout.strip()
    print(f"warm build from {image}@{digest}", flush=True)
    output(warm="true", base=digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
