#!/usr/bin/env python3
"""EXPERIMENT (not for merging): where does the server's image pull time go?

Run after `run_tests.py --keep-vm`. Times, for IMAGE's single layer:
on the runner itself, and inside the server VM (user-mode networking),
the download alone, download+zstd, download+zstd+tar to disk, and a
fresh `podman pull`.

usage: bench_pull.py IMAGE
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = "fpgas-online/nfsroot"
KEY = Path("tests/vm/workdir/test_key")
SSH = ["ssh", "-i", str(KEY), "-p", "2222", "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "debian@127.0.0.1"]


def layer_url(image: str) -> tuple[str, str, int]:
    tag = image.rsplit(":", 1)[1]
    tok = json.load(urllib.request.urlopen(
        f"https://ghcr.io/token?scope=repository:{REPO}:pull"))["token"]
    m = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://ghcr.io/v2/{REPO}/manifests/{tag}",
        headers={"Authorization": f"Bearer {tok}",
                 "Accept": "application/vnd.oci.image.manifest.v1+json"})))
    layer = m["layers"][0]
    return f"https://ghcr.io/v2/{REPO}/blobs/{layer['digest']}", tok, layer["size"]


def timed(label: str, cmd: list[str], size: int) -> None:
    t = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.monotonic() - t
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    print(f"{label:48s} {dt:7.1f} s  {size / dt / 1e6:7.1f} MB/s(zstd)  rc={r.returncode}"
          + (f"  {tail}" if r.returncode else ""), flush=True)


def main() -> int:
    image = sys.argv[1]
    url, tok, size = layer_url(image)
    print(f"layer {url}\nsize {size / 1e9:.2f} GB zstd\n", flush=True)
    curl = f"curl -fsSL -H 'Authorization: Bearer {tok}' '{url}'"

    timed("runner: download -> /dev/null", ["bash", "-c", f"{curl} > /dev/null"], size)
    timed("runner: download | zstd -d -> /dev/null",
          ["bash", "-c", f"{curl} | zstd -d > /dev/null"], size)

    def vm(label, script):
        timed(label, SSH + [f"bash -c {json.dumps('set -o pipefail; ' + script)}"], size)

    # 20G disk already holds the image and the extracted root: free the
    # image's 4 GB first (the fresh podman pull at the end re-fetches it).
    subprocess.run(SSH + [f"sudo apt-get install -y -q zstd >/dev/null; "
                          f"sudo podman rmi -f {image} || true; df -h /"], check=False)
    vm("vm: download -> /dev/null", f"{curl} > /dev/null")
    vm("vm: download -> file", f"{curl} > /var/tmp/layer.zst")
    vm("vm: zstd -d file -> /dev/null (cpu only)", "zstd -d < /var/tmp/layer.zst > /dev/null")
    vm("vm: zstd -d file | tar -x (disk only)",
       "sudo rm -rf /var/tmp/x && sudo mkdir /var/tmp/x && "
       "zstd -d < /var/tmp/layer.zst | sudo tar -x -C /var/tmp/x && sync; sudo rm -rf /var/tmp/x /var/tmp/layer.zst")
    vm("vm: download | zstd -d -> /dev/null", f"{curl} | zstd -d > /dev/null")
    vm("vm: download | zstd -d | tar -x (streamed extract)",
       f"sudo rm -rf /var/tmp/y && sudo mkdir /var/tmp/y && "
       f"{curl} | zstd -d | sudo tar -x -C /var/tmp/y && sync")
    vm("vm: cp -a extracted tree (disk copy, cf. rsync)",
       "sudo rm -rf /var/tmp/z && sudo cp -a /var/tmp/y /var/tmp/z && sync; sudo rm -rf /var/tmp/y /var/tmp/z")
    vm("vm: podman pull (fresh)",
       f"sudo podman rmi -f {image} || true; sudo podman pull -q {image} && sync")
    print(file=sys.stderr)
    subprocess.run(SSH + ["nproc; free -g; df -h /var/tmp /var/lib/containers"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
