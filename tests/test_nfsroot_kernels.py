"""Tests for the Pi NFS root kernel maintenance helper.

The helper decides which kernels may be purged from a long-lived NFS root.
Getting that wrong moves ~20 netbooted boards onto a kernel nobody has ever
booted, or strands them on a kernel whose modules have just been deleted, so
the keep/purge decision is unit-tested against synthetic roots rather than
only exercised by a 2-hour production run.
"""

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "ansible/roles/nspawn-pi/files/nfsroot_kernels.py"

_spec = importlib.util.spec_from_file_location("nfsroot_kernels", HELPER)
nk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nk)


# --- version and flavour parsing ---------------------------------------


def test_version_key_orders_numerically_not_lexically():
    # The bug a string sort produces: "6.12.96" > "6.12.109" alphabetically,
    # which would mark the NEWER kernel as superseded and purge it.
    assert nk.version_key("6.12.109+rpt-rpi-v7") > nk.version_key("6.12.96+rpt-rpi-v7")
    assert nk.version_key("6.12.96+rpt-rpi-v7") > nk.version_key("6.6.31+rpt-rpi-v7")


def test_version_key_uses_the_debian_abi_number():
    assert nk.version_key("6.1.0-50-armmp") > nk.version_key("6.1.0-9-armmp")


@pytest.mark.parametrize(
    "version,expected",
    [
        ("6.12.96+rpt-rpi-v6", "+rpt-rpi-v6"),
        ("6.12.96+rpt-rpi-v7l", "+rpt-rpi-v7l"),
        ("6.12.96+rpt-rpi-v8", "+rpt-rpi-v8"),
        ("6.1.0-50-armmp", "-armmp"),
    ],
)
def test_flavour(version, expected):
    assert nk.flavour(version) == expected


def test_flavours_do_not_compete():
    # v7 and v7l are different board families; the newest v7 must never make
    # a v7l kernel look superseded.
    versions = ["6.12.96+rpt-rpi-v7", "6.12.96+rpt-rpi-v7l"]
    assert nk.select_keep(versions, served=set()) == set(versions)


# --- the keep decision -------------------------------------------------


TWEED_INSTALLED = [
    "6.1.0-50-armmp",
    "6.12.109+rpt-rpi-v6",
    "6.12.109+rpt-rpi-v7",
    "6.12.109+rpt-rpi-v7l",
    "6.12.96+rpt-rpi-v6",
    "6.12.96+rpt-rpi-v7",
    "6.12.96+rpt-rpi-v7l",
    "6.12.96+rpt-rpi-v8",
]


def test_served_kernels_are_kept_even_when_superseded():
    # tweed 2026-09-23: the TFTP payload serves 6.12.96 for every flavour
    # while the chroot has installed 6.12.109 for v6/v7/v7l. Purging the
    # older-but-served kernels would delete the modules of the kernel ~20
    # boards are running.
    served = {
        "6.12.96+rpt-rpi-v6",
        "6.12.96+rpt-rpi-v7",
        "6.12.96+rpt-rpi-v7l",
        "6.12.96+rpt-rpi-v8",
    }
    keep = nk.select_keep(TWEED_INSTALLED, served)
    assert served <= keep
    # ...and the newest of each flavour, which the next netboot sync publishes.
    assert {"6.12.109+rpt-rpi-v6", "6.12.109+rpt-rpi-v7", "6.12.109+rpt-rpi-v7l"} <= keep
    # ...and the Orange Pi H3 boards' Debian armmp kernel.
    assert "6.1.0-50-armmp" in keep
    # Which, today, is everything: pruning is preventive, not retroactive.
    assert keep == set(TWEED_INSTALLED)


def test_an_old_served_kernel_survives_a_much_newer_root():
    # The payload-vs-root gap can be large: a root that has moved to 6.12.x
    # while the boards still boot 6.6.31 must keep every 6.6.31 flavour.
    installed = TWEED_INSTALLED + [
        "6.6.31+rpt-rpi-v6",
        "6.6.31+rpt-rpi-v7",
        "6.6.31+rpt-rpi-v7l",
        "6.6.31+rpt-rpi-v8",
    ]
    served = {f"6.6.31+rpt-rpi-{f}" for f in ("v6", "v7", "v7l", "v8")}
    keep = nk.select_keep(installed, served)
    assert served <= keep
    packages = {v: [(f"linux-image-{v}", "armhf")] for v in installed}
    purge = nk.purge_plan(packages, installed, keep)
    assert not [spec for spec in purge if "6.6.31" in spec]


def test_superseded_unserved_kernels_are_purged():
    installed = ["6.12.96+rpt-rpi-v7", "6.12.109+rpt-rpi-v7", "6.12.120+rpt-rpi-v7"]
    keep = nk.select_keep(installed, served={"6.12.109+rpt-rpi-v7"})
    assert keep == {"6.12.109+rpt-rpi-v7", "6.12.120+rpt-rpi-v7"}
    packages = {
        "6.12.96+rpt-rpi-v7": [
            ("linux-image-6.12.96+rpt-rpi-v7", "armhf"),
            ("linux-headers-6.12.96+rpt-rpi-v7", "armhf"),
        ],
        "6.12.109+rpt-rpi-v7": [("linux-image-6.12.109+rpt-rpi-v7", "armhf")],
    }
    assert nk.purge_plan(packages, installed, keep) == [
        "linux-headers-6.12.96+rpt-rpi-v7:armhf",
        "linux-image-6.12.96+rpt-rpi-v7:armhf",
    ]


def test_keep_globs_protect_extra_versions():
    installed = ["6.12.96+rpt-rpi-v7", "6.12.109+rpt-rpi-v7"]
    keep = nk.select_keep(installed, served=set(), keep_globs=["6.12.96+rpt-rpi-*"])
    assert keep == set(installed)


def test_meta_packages_are_never_purge_candidates():
    # linux-image-rpi-v7 and linux-headers-6.12.96+rpt-common-rpi have no
    # /boot/vmlinuz of their own; apt's autoremove handles them.
    installed = ["6.12.109+rpt-rpi-v7"]
    packages = {
        "rpi-v7": [("linux-image-rpi-v7", "armhf")],
        "6.12.96+rpt-common-rpi": [("linux-headers-6.12.96+rpt-common-rpi", "armhf")],
        "6.12.109+rpt-rpi-v7": [("linux-image-6.12.109+rpt-rpi-v7", "armhf")],
    }
    keep = nk.select_keep(installed, served=set())
    assert nk.purge_plan(packages, installed, keep) == []


# --- reading a real tree -----------------------------------------------


def make_root(tmp_path, kernels, served=None, husks=()):
    """Build a miniature NFS root: boot/ payload + root/ rootfs."""
    boot = tmp_path / "root" / "boot"
    boot.mkdir(parents=True)
    modules = tmp_path / "root" / "lib" / "modules"
    modules.mkdir(parents=True)
    for version, content in kernels.items():
        (boot / f"vmlinuz-{version}").write_bytes(content)
        (boot / f"initrd.img-{version}").write_bytes(b"initrd")
        (modules / version / "kernel").mkdir(parents=True)
    for version in husks:
        # What dpkg leaves behind after a kernel removal: depmod's index
        # files, no modules. Not a kernel, and never counted or touched.
        (modules / version).mkdir(parents=True)
        (modules / version / "modules.dep").write_text("")
    payload = tmp_path / "boot"
    payload.mkdir()
    for name, content in (served or {}).items():
        (payload / name).write_bytes(content)
    return tmp_path


def test_resolve_served_matches_by_content_not_name(tmp_path):
    root = make_root(
        tmp_path,
        kernels={
            "6.12.96+rpt-rpi-v8": b"old-v8-bytes",
            "6.12.109+rpt-rpi-v7": b"new-v7-bytes",
        },
        served={"kernel8.img": b"old-v8-bytes"},
    )
    matched, orphans = nk.resolve_served(root)
    assert set(matched.values()) == {"6.12.96+rpt-rpi-v8"}
    assert orphans == []
    assert nk.served_versions(root) == {"6.12.96+rpt-rpi-v8"}


def test_resolve_served_reports_a_payload_matching_nothing(tmp_path):
    root = make_root(
        tmp_path,
        kernels={"6.12.109+rpt-rpi-v7": b"new-v7-bytes"},
        served={"kernel7.img": b"a-kernel-no-longer-installed"},
    )
    matched, orphans = nk.resolve_served(root)
    assert matched == {}
    assert [p.name for p in orphans] == ["kernel7.img"]


def test_module_trees_ignores_index_only_leftovers(tmp_path):
    root = make_root(
        tmp_path,
        kernels={"6.12.96+rpt-rpi-v7": b"v7"},
        husks=("6.6.31+rpt-rpi-v8", "6.1.21+"),
    )
    assert nk.module_trees(root) == ["6.12.96+rpt-rpi-v7"]


def test_stale_initramfs_flags_missing_and_outdated(tmp_path):
    root = make_root(
        tmp_path,
        kernels={"6.12.96+rpt-rpi-v7": b"v7", "6.12.109+rpt-rpi-v7": b"v7new"},
    )
    (root / "root" / "boot" / "initrd.img-6.12.109+rpt-rpi-v7").unlink()
    assert nk.stale_initramfs(root, ["6.12.109+rpt-rpi-v7"]) == ["6.12.109+rpt-rpi-v7"]
    # Untouched inputs: rebuilding would reproduce the same image, which on
    # tweed costs 6-40 emulated minutes each. Nothing to do.
    assert nk.stale_initramfs(root, ["6.12.96+rpt-rpi-v7"]) == []
    # A module landing after the image was built does make it stale.
    newer = os.stat(root / "root" / "boot" / "initrd.img-6.12.96+rpt-rpi-v7").st_mtime
    os.utime(
        root / "root" / "lib" / "modules" / "6.12.96+rpt-rpi-v7" / "kernel",
        (newer + 60, newer + 60),
    )
    assert nk.stale_initramfs(root, ["6.12.96+rpt-rpi-v7"]) == ["6.12.96+rpt-rpi-v7"]


# --- the apt safety net ------------------------------------------------


class _PruneArgs:
    def __init__(self, nfs_root):
        self.nfs_root = str(nfs_root)
        self.tftp_root = None
        self.keep = []
        self.dry_run = False


def test_prune_refuses_to_act_when_the_payload_matches_no_kernel(tmp_path, monkeypatch):
    """The CI-build shape, and the shape of a root whose served kernel is gone.

    ci-nfsroot.yml re-syncs the payload only after this runs, so at prune time
    boot/ still holds the stock image's kernels while the chroot apt runs have
    moved the rootfs on. "What the fleet boots" is then not knowable from the
    tree, so the only safe move is to keep everything -- and, crucially, to
    reach apt not at all.
    """
    root = make_root(
        tmp_path,
        kernels={
            "6.12.96+rpt-rpi-v6": b"new-v6",
            "6.12.109+rpt-rpi-v6": b"newer-v6",
        },
        served={"kernel.img": b"the-stock-images-kernel"},
    )
    monkeypatch.setattr(
        nk,
        "dpkg_kernel_packages",
        lambda _root: {
            v: [(f"linux-image-{v}", "armhf")]
            for v in ("6.12.96+rpt-rpi-v6", "6.12.109+rpt-rpi-v6")
        },
    )

    def refuse(*_args, **_kwargs):
        raise AssertionError("prune must not invoke apt when nothing is served")

    monkeypatch.setattr(nk, "chroot_apt", refuse)

    # Without the guard this plan is non-empty: 6.12.96 is superseded by
    # 6.12.109 and nothing marks it as the kernel in service.
    plan = nk.load_plan(_PruneArgs(root))
    assert plan["served"] == []
    assert plan["purge"] == ["linux-image-6.12.96+rpt-rpi-v6:armhf"]

    assert nk.cmd_prune(_PruneArgs(root)) == 0
    assert (root / "root" / "boot" / "vmlinuz-6.12.96+rpt-rpi-v6").exists()


def test_check_reports_an_orphaned_payload_but_only_fails_on_demand(tmp_path):
    """A payload older than the root's kernels is normal between netboot runs.

    tweed serves 6.12.96 while the chroot has moved to 6.12.109, so this must
    not fail an ordinary rebuild; --strict-payload is for a caller that has
    just published a payload and expects it to match.
    """
    root = make_root(
        tmp_path,
        kernels={"6.12.109+rpt-rpi-v6": b"newer-v6"},
        served={"kernel.img": b"something-else"},
    )

    class Args:
        nfs_root = str(root)
        tftp_root = None
        max_trees = 10
        strict_payload = False

    assert nk.cmd_check(Args()) == 0
    Args.strict_payload = True
    assert nk.cmd_check(Args()) == 1


def test_simulated_removals_parses_apt_output():
    output = (
        "NOTE: This is only a simulation!\n"
        "Purg linux-image-6.12.96+rpt-rpi-v7 [1:6.12.96-1+rpt1]\n"
        "Remv linux-image-rpi-v7:armhf [1:6.12.96-1+rpt1]\n"
        "Inst something [1.0]\n"
    )
    assert nk.simulated_removals(output) == {
        "linux-image-6.12.96+rpt-rpi-v7",
        "linux-image-rpi-v7",
    }


def test_refresh_initramfs_rebuilds_stale_versions_in_parallel(tmp_path, monkeypatch):
    """Every stale version is rebuilt (-c when it has no image yet), and one
    failure fails the command after all builds have run."""
    calls = []

    class Result:
        def __init__(self, rc):
            self.returncode, self.stdout, self.stderr = rc, "", ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result(1 if cmd[-1] == "6.12.1-v7" else 0)

    monkeypatch.setattr(nk.subprocess, "run", fake_run)
    monkeypatch.setattr(nk, "rootfs_kernels", lambda root: {"6.12.1-v7": None, "6.12.1-v8": None})
    monkeypatch.setattr(nk, "stale_initramfs", lambda root, versions: list(versions))

    class Args:
        nfs_root = str(tmp_path)
        dry_run = False

    assert nk.cmd_refresh_initramfs(Args()) == 1
    assert sorted(c[-1] for c in calls) == ["6.12.1-v7", "6.12.1-v8"]
    assert all(c[:3] == ["chroot", str(tmp_path / "root"), "update-initramfs"] and c[3] == "-c" for c in calls)

    calls.clear()
    monkeypatch.setattr(nk.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), Result(0))[1])
    assert nk.cmd_refresh_initramfs(Args()) == 0
    assert len(calls) == 2
