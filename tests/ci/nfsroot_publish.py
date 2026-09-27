#!/usr/bin/env python3
"""Package the built Pi NFS root tree as an OCI image of LAYERS zstd layers
(pulled in parallel, see LAYERS) and push it to GHCR (issue #34, design doc
docs/superpowers/specs/2026-08-31-ci-nfsroot-build-design.md).

The image filesystem holds top-level boot/ and root/ directories mirroring
/srv/nfs/rpi/<dist>. Tags:

  - <dist>-armhf-YYYYMMDD-<sha7>   always (pinnable; production references
    this in host_vars so a rebuild is reproducible)
  - $NFSROOT_EXTRA_TAG             when set: a tag the caller chose before
    the build started (the VM test polls for ci-<run_id> so it can run its
    server phase while this build is still going)
  - inputs-<key>                   always: the content key of the image's
    inputs (nfsroot_inputs.py), which later runs look up to reuse it

`--reuse` publishes the same tags without building: when an image for this
checkout's inputs key already exists its manifest is copied to them
(skopeo, preserving digests: no layer is pulled or pushed), and the
`reused` step output says whether that happened.

No build moves the rolling <dist>-armhf tag, which is what production
pulls unless a deploy pins another. `--promote SOURCE` does that, and the
VM test workflow runs it only on main, after the virtual Pi has netbooted
SOURCE and registered with the server, so the rolling tag only ever names
an image that has passed that test.

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
    return dated, inputs, others


MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def assert_plain_manifest(tag: str) -> None:
    """Fail unless the tag is a single image manifest, not an index.

    The servers are amd64 and the image is arm64: podman pulls a plain
    manifest whatever its architecture, but refuses an index with no amd64
    entry.
    """
    raw = run(["skopeo", "inspect", "--raw", f"docker://{tag}"],
              capture_output=True, text=True, check=True).stdout
    media = json.loads(raw).get("mediaType")
    if media != MANIFEST:
        raise RuntimeError(f"{tag} is a {media}, not a single image manifest")


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
    # Copy the single-platform manifest itself to each tag (the layers are
    # already in the registry, so only the manifest moves). Not `docker
    # buildx imagetools create`: that wraps it in an OCI index listing only
    # linux/arm64, which amd64 servers' `podman pull` then refuses ("no
    # image found in image index for architecture amd64") -- it did exactly
    # that to the rolling tag on the first main run that reused an image.
    for t in [dated] + others:
        run(["skopeo", "copy", "--preserve-digests", f"docker://{inputs}", f"docker://{t}"],
            check=True)
    for t in [dated] + others:
        assert_plain_manifest(t)
    write_outputs(reused="true", image=dated)
    summary(["## nfsroot reused (no image input changed)", "",
             f"- from `{inputs}`"] + [f"- `{t}`" for t in [dated] + others])
    return 0


# The tree is pushed as this many layers of about equal size, because a
# freshly pushed blob downloads at only ~35-80 MB/s per connection and
# podman fetches an image's layers in parallel. Measured with the real
# root (1.63 GB zstd, every layer a never-fetched blob; experiment run
# 36287961018 on the x86 VM host / the arm64 build runner):
#
#   layers   cold `podman pull`   skopeo push
#     1        74.8 s / 48.0 s    37.2-45.4 s / 26.6-37.0 s
#     4        33.0 s / 22.9 s    42.6-43.3 s / 26.2-27.1 s
#     8        30.3 s / 21.8 s    52.6-52.9 s / 37.1-37.3 s
#
# So 4: the pull is ~2.2x faster (25-45 s saved whenever the server is
# waiting for a just-built image), and more layers gain nothing there --
# podman applies layers one at a time, which becomes the limit (plain curl
# of 8 layers takes 4.7-7.5 s) -- while pushing slower. Splitting does not
# speed up the push at all: the upload is limited as a whole, not per stream.
LAYERS = 4

# (path relative to NFSROOT, find %y type, size, device:inode, link count)
Entry = tuple[bytes, str, int, str, int]


def list_tree() -> list[Entry]:
    """Every entry under NFSROOT's boot/ and root/, each directory before its contents."""
    out = run(["sudo", "find", "boot", "root", "-printf", r"%y\t%s\t%D:%i\t%n\t%p\0"],
              cwd=NFSROOT, capture_output=True, check=True).stdout
    entries = []
    for rec in out.split(b"\0"):
        if rec:
            kind, size, inode, links, path = rec.split(b"\t", 4)
            entries.append((path, kind.decode(), int(size), inode.decode(), int(links)))
    return entries


def ancestors(path: bytes) -> list[bytes]:
    parts = path.split(b"/")
    return [b"/".join(parts[:i]) for i in range(1, len(parts))]


def plan_layers(entries: list[Entry], n: int) -> list[list[bytes]]:
    """Split the tree into n tar member lists of about equal file bytes.

    The layers are unpacked in order, so a later one only adds to the
    tree. Each layer carries every directory above what it holds, with its
    real mode and owner: overlay storage (podman on the servers) gives a
    layer its own copy of each directory it touches, and one the layer did
    not carry would be made 0755 root and hide the real /tmp or /root. All
    links to a multiply-linked file stay in one layer, because a tar hard
    link can only point at a member of its own archive.
    """
    total = sum(size for _, kind, size, _, _ in entries if kind == "f") or 1
    layers: list[list[bytes]] = [[] for _ in range(n)]
    carried: list[set[bytes]] = [set() for _ in range(n)]
    link_layer: dict[str, int] = {}
    done, cur = 0, 0
    for path, kind, size, inode, links in entries:
        hard = kind == "f" and links > 1
        if hard and inode in link_layer:
            i = link_layer[inode]
        else:
            while cur < n - 1 and done >= total * (cur + 1) / n:
                cur += 1
            i = cur
            if hard:
                link_layer[inode] = i
            if kind == "f":
                done += size
        for p in ancestors(path) + ([path] if kind == "d" else []):
            if p not in carried[i]:
                carried[i].add(p)
                layers[i].append(p)
        if kind != "d":
            layers[i].append(path)
    return [layer for layer in layers if layer]


def pack_layers(layout: Path) -> list[tuple[str, str, int, int]]:
    """Tar the root into LAYERS zstd layer blobs in the OCI layout.

    Returns (layer digest, diff_id, compressed size, uncompressed size) for
    each, in the order they are to be applied.
    """
    (layout / "blobs" / "sha256").mkdir(parents=True)
    plan = plan_layers(list_tree(), LAYERS)
    layers = []
    for i, members in enumerate(plan):
        listing = layout / f"members-{i}"
        listing.write_bytes(b"\0".join(members) + b"\0")
        layers.append(pack_layer(layout, listing))
        listing.unlink()
    return layers


def pack_layer(layout: Path, members: Path) -> tuple[str, str, int, int]:
    """Tar the NUL-separated member list into a zstd layer blob in the OCI layout.

    Returns (layer digest, diff_id, compressed size, uncompressed size).
    zstd runs on every core; `docker push` compressed the 4.7 GB tar with
    single-threaded gzip (~3 min), and a gzip layer also decompresses on
    one core on every server that pulls it.
    """
    blobs = layout / "blobs" / "sha256"
    staging = layout / "layer.tar.zst"
    tar = subprocess.Popen(
        ["sudo", "tar", "-C", NFSROOT, "--numeric-owner", "--xattrs",
         "--no-recursion", "--null", "-T", members, "-c"],
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


def push_tree(tags_: list[str], labels: dict[str, str], heading: str) -> list[str]:
    """Pack NFSROOT's boot/ and root/ as LAYERS zstd layers and push them to tags_.

    The layers are uploaded once; every further tag is a manifest-only copy,
    pushed in the order given (put the tag other runs look up last, so they
    never see a half-published set). Returns the tags pushed.
    """
    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as tmp:
        layout = Path(tmp) / "oci"
        layers = pack_layers(layout)
        config, config_size = put_blob(layout, json.dumps({
            # What `docker import` on the arm64 build runner recorded.
            "architecture": "arm64",
            "os": "linux",
            "config": {"Labels": labels},
            "rootfs": {"type": "layers", "diff_ids": [diff_id for _, diff_id, _, _ in layers]},
        }).encode())
        manifest, manifest_size = put_blob(layout, json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                       "digest": config, "size": config_size},
            "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+zstd",
                        "digest": digest, "size": size} for digest, _, size, _ in layers],
        }).encode())
        layer_size = sum(size for _, _, size, _ in layers)
        raw_size = sum(raw for _, _, _, raw in layers)
        (layout / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')
        (layout / "index.json").write_text(json.dumps({
            "schemaVersion": 2,
            "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "digest": manifest, "size": manifest_size,
                           "annotations": {"org.opencontainers.image.ref.name": "nfsroot"}}],
        }))
        # skopeo reads the `docker login` credentials.
        pushed = []
        for t in tags_:
            run(["skopeo", "copy", "--preserve-digests",
                 f"oci:{layout}:nfsroot", f"docker://{t}"])
            pushed.append(t)

    for t in pushed:
        assert_plain_manifest(t)
    summary([f"## {heading}", "",
             f"- size: {raw_size / 1e9:.2f} GB uncompressed, "
             f"{layer_size / 1e9:.2f} GB zstd in {len(layers)} layers"]
            + [f"- `{tag}`" for tag in pushed])
    return pushed


def build() -> int:
    dated, inputs, others = tags()
    # The base-key label says which RasPiOS release the image descends from
    # (nfsroot_warm.py converges only an image with this checkout's base
    # key). inputs-<key> goes last: a later run that finds it (and reuses
    # the image) never sees a half-published set of tags.
    push_tree([dated] + others + [inputs],
              {nfsroot_inputs.BASE_LABEL: nfsroot_inputs.base_key()},
              "nfsroot published")
    # The dated tag is this build's identity: downstream jobs (the VM test)
    # consume it via the workflow_call output.
    write_outputs(image=dated)
    return 0


def promote(source: str) -> int:
    """Point the rolling tag at SOURCE, an image the VM test has booted."""
    rolling = f"{IMAGE}:{DIST}-armhf"
    run(["skopeo", "copy", "--preserve-digests", f"docker://{source}", f"docker://{rolling}"],
        check=True)
    assert_plain_manifest(rolling)
    digest = run(["skopeo", "inspect", "--format", "{{.Digest}}", f"docker://{rolling}"],
                 capture_output=True, text=True, check=True).stdout.strip()
    summary(["## nfsroot promoted (the VM test passed)", "",
             f"- `{rolling}` is now `{source}`", f"- digest `{digest}`"])
    return 0


def main():
    args = sys.argv[1:]
    if args[:1] == ["--promote"] and len(args) == 2:
        return promote(args[1])
    return reuse() if "--reuse" in args else build()


if __name__ == "__main__":
    sys.exit(main())
