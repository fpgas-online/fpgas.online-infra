"""fixpi's tasks report changed only when they change something (the 2026-10-08 converge).

That converge reported eight fixpi tasks changed although the NFS root's
generation hash saw no change: the sunxi DTB publish copied the package DTB
over the published, I2C-patched one (changed) and patched it again in place
(changed), serving the unpatched DTB in between; `Enable sshd` touched its
marker's times; the payload sync's synchronize counted any itemized line.
These tests fail if:
  - the DTB publish stops producing a DTB with every header I2C controller
    at status=okay, stops reporting ok on a second run with the same kernel,
    stops replacing the published file by a rename (a booting board would
    see a half-written or unpatched file), or leaves its staged copy behind,
  - the copy-then-patch-in-place tasks come back,
  - `Enable sshd` stops preserving the marker's times,
  - the payload sync stops printing its itemized lines when it reports a
    change, or gains a changed_when that could hide one.
The DTB test runs the role's own shell script on a DTB compiled with dtc
(device-tree-compiler; CI's pytest job installs it).
"""

import re
import subprocess
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parent.parent
TASKS = REPO / "ansible/roles/fixpi/tasks"
DEFAULTS = yaml.safe_load((REPO / "ansible/roles/fixpi/defaults/main.yml").read_text())
PUBLISH = "Publish the sunxi DTBs with the header I2C controllers enabled"
KVER = "6.1.0-50-armmp"
DTB = "sun8i-h3-orangepi-pc.dtb"

# A stand-in for the kernel package's DTB: every H3 TWI disabled, as mainline ships them.
DTS = """/dts-v1/;
/ {
    soc {
        #address-cells = <1>;
        #size-cells = <1>;
        i2c@1c2ac00 { reg = <0x1c2ac00 0x400>; status = "disabled"; };
        i2c@1c2b000 { reg = <0x1c2b000 0x400>; status = "disabled"; };
        i2c@1c2b400 { reg = <0x1c2b400 0x400>; status = "disabled"; };
    };
};
"""


def _task(path: str, name: str) -> dict:
    (task,) = [t for t in yaml.safe_load((TASKS / path).read_text()) if t.get("name") == name]
    return task


def _script(tmp_path: Path) -> tuple[str, Path, Path]:
    nfs_root, tftp_root = tmp_path / "nfs", tmp_path / "tftp"
    pkg = nfs_root / f"root/usr/lib/linux-image-{KVER}"
    pkg.mkdir(parents=True)
    (tftp_root / "sunxi/dtbs").mkdir(parents=True)
    dts = tmp_path / "pkg.dts"
    dts.write_text(DTS)
    subprocess.run(["dtc", "-q", "-I", "dts", "-O", "dtb", "-o", str(pkg / DTB), str(dts)], check=True)
    template = _task("sunxi.yml", PUBLISH)["ansible.builtin.shell"]
    script = jinja2.Template(template).render(
        nfs_root=nfs_root, tftp_root=tftp_root, fixpi_sunxi_kver=KVER, item=DTB,
        fixpi_sunxi_i2c_nodes=DEFAULTS["fixpi_sunxi_i2c_nodes"],
    )
    return script, pkg / DTB, tftp_root / "sunxi/dtbs" / DTB


def _status(dtb: Path, node: str) -> str:
    return subprocess.run(["fdtget", str(dtb), node, "status"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_the_publish_patches_once_then_reports_ok_and_replaces_by_rename(tmp_path):
    script, pkg, published = _script(tmp_path)
    first = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert "changed=1" in first.stdout
    for node in DEFAULTS["fixpi_sunxi_i2c_nodes"]:
        node = node.split("#")[0].strip()
        assert _status(published, node) == "okay", node
    # a controller not on the list is left as the package has it
    assert _status(published, "/soc/i2c@1c2b400") == "disabled"
    # the package's file is untouched
    assert _status(pkg, "/soc/i2c@1c2b000") == "disabled"
    inode = published.stat().st_ino

    second = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert "changed=0" in second.stdout
    assert published.stat().st_ino == inode, "an unchanged DTB was rewritten"
    assert sorted(p.name for p in published.parent.iterdir()) == [DTB], "a staged copy was left behind"

    # A new kernel's DTB is installed by a rename: a new inode, never an in-place write.
    subprocess.run(["fdtput", "-t", "s", str(pkg), "/", "model", "new kernel"], check=True)
    third = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert "changed=1" in third.stdout, third.stderr
    assert published.stat().st_ino != inode
    assert _status(published, "/soc/i2c@1c2b000") == "okay"


def test_the_copy_then_patch_in_place_tasks_are_gone():
    text = (TASKS / "sunxi.yml").read_text()
    assert "Publish sunxi DTBs to tftp sunxi/dtbs/" not in text
    assert "Enable the header I2C controllers in the published sunxi DTBs" not in text
    script = _task("sunxi.yml", PUBLISH)["ansible.builtin.shell"]
    assert re.search(r'mv -f "\$staged" "\$dst"', script)
    assert "{{ tftp_root }}/sunxi/dtbs/{{ item }}" in script


def test_enable_sshd_keeps_the_markers_times():
    module = _task("userconf.yml", "Enable sshd")["ansible.builtin.file"]
    assert module["state"] == "touch"
    assert module["access_time"] == "preserve" and module["modification_time"] == "preserve"


def test_the_payload_sync_shows_what_it_changed_and_hides_nothing():
    sync = _task("netboot.yml", "Sync matched kernel+initramfs+DTB payload from root/boot/firmware")
    assert sync["register"] == "fixpi_payload_sync"
    assert "changed_when" not in sync
    show = _task("netboot.yml", "Show what the payload sync changed")
    assert show["when"] == "fixpi_payload_sync is changed"
    assert "fixpi_payload_sync.stdout_lines" in show["ansible.builtin.debug"]["msg"]
