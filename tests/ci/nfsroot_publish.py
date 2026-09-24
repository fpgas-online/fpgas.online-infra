#!/usr/bin/env python3
"""Package the built Pi NFS root tree as a single-layer (zstd) OCI image and
push it to GHCR (issue #34, design doc
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

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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


def pack_layer(layout: Path) -> tuple[str, str, int, int]:
    """Tar the root into a zstd layer blob in the OCI layout.

    Returns (layer digest, diff_id, compressed size, uncompressed size).
    zstd runs on every core; `docker push` compressed the 4.7 GB tar with
    single-threaded gzip (~3 min), and a gzip layer also decompresses on
    one core on every server that pulls it.
    """
    blobs = layout / "blobs" / "sha256"
    blobs.mkdir(parents=True)
    staging = layout / "layer.tar.zst"
    tar = subprocess.Popen(
        ["sudo", "tar", "-C", NFSROOT, "--numeric-owner", "--xattrs",
         "-c", "boot", "root"],
        stdout=subprocess.PIPE,
    )
    with open(staging, "wb") as out:
        zstd = subprocess.Popen(["zstd", "-T0", "-3", "-q", "-c"],
                                stdin=subprocess.PIPE, stdout=out)
        diff = hashlib.sha256()
        raw = 0
        while chunk := tar.stdout.read(8 << 20):
            diff.update(chunk)
            raw += len(chunk)
            zstd.stdin.write(chunk)
        zstd.stdin.close()
        if tar.wait() != 0 or zstd.wait() != 0:
            raise RuntimeError("packing the nfsroot tree failed")
    digest = sha256_file(staging)
    staging.rename(blobs / digest)
    return f"sha256:{digest}", f"sha256:{diff.hexdigest()}", (blobs / digest).stat().st_size, raw


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def put_blob(layout: Path, data: bytes) -> tuple[str, int]:
    digest = hashlib.sha256(data).hexdigest()
    (layout / "blobs" / "sha256" / digest).write_bytes(data)
    return f"sha256:{digest}", len(data)


def build() -> int:
    dated, inputs, others = tags()

    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as tmp:
        layout = Path(tmp) / "oci"
        layer, diff_id, layer_size, raw_size = pack_layer(layout)
        config, config_size = put_blob(layout, json.dumps({
            # What `docker import` on the arm64 build runner recorded.
            "architecture": "arm64",
            "os": "linux",
            "config": {},
            "rootfs": {"type": "layers", "diff_ids": [diff_id]},
        }).encode())
        manifest, manifest_size = put_blob(layout, json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                       "digest": config, "size": config_size},
            "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+zstd",
                        "digest": layer, "size": layer_size}],
        }).encode())
        (layout / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')
        (layout / "index.json").write_text(json.dumps({
            "schemaVersion": 2,
            "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "digest": manifest, "size": manifest_size,
                           "annotations": {"org.opencontainers.image.ref.name": "nfsroot"}}],
        }))
        # skopeo reads the `docker login` credentials. The layer is uploaded
        # once; every further tag is a manifest-only copy. inputs-<key> goes
        # last: a later run that finds it (and reuses the image) never sees a
        # half-published set of tags.
        pushed = []
        for t in [dated] + others + [inputs]:
            run(["skopeo", "copy", "--preserve-digests",
                 f"oci:{layout}:nfsroot", f"docker://{t}"])
            pushed.append(t)

    # The dated tag is this build's identity: downstream jobs (the VM test)
    # consume it via the workflow_call output.
    write_outputs(image=dated)
    summary(["## nfsroot published", "",
             f"- size: {raw_size / 1e9:.2f} GB uncompressed, "
             f"{layer_size / 1e9:.2f} GB zstd"]
            + [f"- `{tag}`" for tag in pushed])
    return 0


def main():
    return reuse() if "--reuse" in sys.argv[1:] else build()


if __name__ == "__main__":
    sys.exit(main())
