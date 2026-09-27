"""The split Pi root image unpacks to exactly the tree it was packed from.

Packs a small root-owned tree with the real nfsroot_publish.pack_layers
(sudo find / sudo tar, LAYERS layers), then unpacks it both ways the image
is consumed:

  - podman: pull the OCI layout into root's storage and `podman image
    mount` it -- the overlay merge the servers' img role rsyncs from
  - tar -x of each layer in order -- nfsroot_warm.extract (warm builds and
    the stage images)

and compares every entry's type, mode, owner, link count, symlink target,
size and xattrs with the source. The tree holds what a careless split
gets wrong: a 1777 and a 0700 directory whose contents land in several
layers, hard links far apart, an empty directory, a symlink and an xattr.

Needs passwordless sudo, podman, setfattr and zstd, so it runs in CI
(lint.yml's pytest job, NFSROOT_PODMAN_TEST=1) and skips elsewhere.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "ci"))
import nfsroot_publish  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("NFSROOT_PODMAN_TEST") != "1" or not shutil.which("podman"),
    reason="needs sudo + podman (set NFSROOT_PODMAN_TEST=1; runs in CI)")

LISTING = r"%P\t%y\t%m\t%U:%G\t%n\t%l\t%s\n"


def sudo(*cmd, **kw):
    return subprocess.run(["sudo", *map(str, cmd)], check=True, **kw)


def snapshot(top: Path) -> dict[str, str]:
    """Every entry's metadata (and user xattrs) under top, keyed by path."""
    out = sudo("find", top, "-mindepth", "1", "-printf", LISTING,
               capture_output=True, text=True).stdout
    entries = {}
    for line in out.splitlines():
        path, _, meta = line.partition("\t")
        kind = meta.split("\t")[0]
        if kind == "d":
            # A directory's size and link count belong to the filesystem, not
            # the image: overlayfs reports nlink 1 for every merged directory
            # (rsync from the mount does not copy it either).
            fields = meta.split("\t")
            meta = "\t".join(fields[:3] + fields[4:-1])
        if kind == "f":
            attrs = subprocess.run(["sudo", "getfattr", "--absolute-names", "-d", top / path],
                                   capture_output=True, text=True).stdout
            meta += "\t" + "".join(sorted(a for a in attrs.splitlines() if a.startswith("user.")))
        entries[path] = meta
    return entries


def make_tree(src: Path) -> None:
    sh = f"""
      set -e
      mkdir -p {src}/boot {src}/root/tmp/deep {src}/root/root/.ssh {src}/root/usr/lib {src}/root/usr/bin \
               {src}/root/var/empty
      head -c 300000 /dev/urandom > {src}/boot/kernel8.img
      for i in $(seq 40); do head -c 20000 /dev/urandom > {src}/root/usr/lib/lib$i.so; done
      head -c 50000 /dev/urandom > {src}/root/usr/bin/busybox
      ln {src}/root/usr/bin/busybox {src}/root/usr/lib/zz-busybox-link
      echo secret > {src}/root/root/.ssh/id; echo t > {src}/root/tmp/deep/f
      ln -s ../lib/lib1.so {src}/root/usr/bin/sym
      setfattr -n user.fpgas -v kept {src}/root/usr/lib/lib7.so
      chmod 1777 {src}/root/tmp; chmod 0700 {src}/root/root {src}/root/root/.ssh
      chown -R 1000:1000 {src}/root/usr/lib; chown 0:0 {src}/root/usr/lib
    """
    sudo("bash", "-c", sh)


def test_split_image_unpacks_to_the_source_tree(tmp_path, monkeypatch):
    src, layout, flat = tmp_path / "src", tmp_path / "oci", tmp_path / "flat"
    make_tree(src)
    monkeypatch.setattr(nfsroot_publish, "NFSROOT", str(src))
    layers = nfsroot_publish.pack_layers(layout)
    assert len(layers) == nfsroot_publish.LAYERS

    # The OCI layout push_tree would push, minus the push.
    config, csize = nfsroot_publish.put_blob(layout, json.dumps({
        "architecture": "arm64", "os": "linux", "config": {},
        "rootfs": {"type": "layers", "diff_ids": [d for _, d, _, _ in layers]}}).encode())
    manifest, msize = nfsroot_publish.put_blob(layout, json.dumps({
        "schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                   "digest": config, "size": csize},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+zstd",
                    "digest": d, "size": s} for d, _, s, _ in layers]}).encode())
    (layout / "oci-layout").write_text('{"imageLayoutVersion": "1.0.0"}')
    (layout / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [
        {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": manifest,
         "size": msize, "annotations": {"org.opencontainers.image.ref.name": "t"}}]}))

    want = snapshot(src)
    assert want["root/tmp"].split("\t")[1] == "1777"

    # nfsroot_warm.extract's way: each layer's tar, in order, into one dir.
    sudo("install", "-d", flat)
    for digest, _, _, _ in layers:
        sudo("tar", "-x", "--numeric-owner", "--xattrs", "--xattrs-include=*",
             "-C", flat, "-f", layout / "blobs" / "sha256" / digest.split(":", 1)[1])
    assert snapshot(flat) == want

    # The servers' way: podman's overlay storage, merged by a mount.
    image_id = sudo("podman", "pull", "-q", f"oci:{layout}:t",
                    capture_output=True, text=True).stdout.split()[-1]
    mnt = Path(sudo("podman", "image", "mount", image_id,
                    capture_output=True, text=True).stdout.strip())
    try:
        assert snapshot(mnt) == want
    finally:
        sudo("podman", "image", "unmount", image_id)
        sudo("podman", "rmi", "-f", image_id)
        sudo("rm", "-rf", src, flat)
