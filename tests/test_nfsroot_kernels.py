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
HELPER = REPO / "ansible/roles/nspawn_pi/files/nfsroot_kernels.py"

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


# --- the interface names inside the initramfs (#299) -------------------

# setup-pi#18's 11-eth-uplink.link (comments left out), and the 3B+-only file the root carried before it.
UPLINK_LINK = (b"# the board's own NIC\n[Match]\nKernelCommandLine=nfsroot\nType=ether\n"
               b"Path=platform-3f980000.usb-usb-0:1.1.1:1.0 platform-fd580000.ethernet "
               b"platform-1f00100000.ethernet platform-1c30000.ethernet\n\n[Link]\nName=eth-uplink\n")
OLD_UPLINK_LINK = b"[Match]\nPath=platform-3f980000.usb-usb-0:1.1.1:1.0\nType=ether\n\n[Link]\nName=eth-uplink\n"
FPGA_LINK = b"[Match]\nPath=platform-3f980000.usb-usb-0:1.2:1.0\nType=ether\n\n[Link]\nName=eth-fpga\n"


def add_root_links(root, links, where="etc/systemd/network"):
    directory = root / "root" / where
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in links.items():
        (directory / name).write_bytes(content)
    return directory


@pytest.mark.parametrize("where", ["etc/systemd/network", "usr/lib/systemd/network", "etc/udev/rules.d"])
def test_stale_initramfs_follows_what_the_udev_hook_copies(tmp_path, where):
    """A .link or udev rule change must rebuild the images: the hook copies them in, and the .link files
    decide the interface names there (11-eth-uplink.link)."""
    root = make_root(tmp_path, kernels={"6.12.96+rpt-rpi-v7": b"v7"})
    initrd = root / "root" / "boot" / "initrd.img-6.12.96+rpt-rpi-v7"
    directory = add_root_links(root, {"11-eth-uplink.link": UPLINK_LINK}, where)
    built = os.stat(initrd).st_mtime
    # The file keeps an old mtime (dpkg sets the package's), but the directory changed when it was put there.
    os.utime(directory / "11-eth-uplink.link", (built - 3600, built - 3600))
    os.utime(directory, (built - 3600, built - 3600))
    assert nk.stale_initramfs(root, ["6.12.96+rpt-rpi-v7"]) == []
    os.utime(directory, (built + 60, built + 60))
    assert nk.stale_initramfs(root, ["6.12.96+rpt-rpi-v7"]) == ["6.12.96+rpt-rpi-v7"]


def test_root_links_etc_replaces_usr_lib_by_name(tmp_path):
    add_root_links(tmp_path, {"99-default.link": b"[Link]\nNamePolicy=keep\n", "73-usb-net-by-mac.link": b"x"},
                   "usr/lib/systemd/network")
    # RasPiOS masks both with empty files in /etc; the hook copies /etc's after /usr/lib's.
    add_root_links(tmp_path, {"99-default.link": b"", "73-usb-net-by-mac.link": b"", "11-eth-uplink.link": UPLINK_LINK})
    assert nk.root_links(tmp_path) == {"99-default.link": b"", "73-usb-net-by-mac.link": b"",
                                       "11-eth-uplink.link": UPLINK_LINK}


def test_a_dev_null_symlink_is_a_mask_and_replaces_usr_lib(tmp_path):
    """RasPiOS masks the distribution defaults with /etc symlinks to /dev/null (build of 16 Oct: skipping them
    let /usr/lib's real 99-default.link stand in, and every image looked stale)."""
    add_root_links(tmp_path, {"99-default.link": b"[Match]\nOriginalName=*\n[Link]\nNamePolicy=keep\n"},
                   "usr/lib/systemd/network")
    etc = add_root_links(tmp_path, {"11-eth-uplink.link": UPLINK_LINK})
    (etc / "99-default.link").symlink_to("/dev/null")
    (etc / "10-elsewhere.link").symlink_to("/usr/lib/somewhere/x.link")
    links = nk.root_links(tmp_path)
    assert links["99-default.link"] == b""
    assert links["10-elsewhere.link"] == b"@symlink /usr/lib/somewhere/x.link\n"
    # the unknown symlink sorts first and might match any NIC, so coverage fails loudly
    assert len(nk.coverage_problems(links)) == 4
    # the masked default does not capture the NICs
    del links["10-elsewhere.link"]
    assert nk.coverage_problems(links) == []


def test_link_names_reads_only_the_link_section():
    assert nk.link_names(UPLINK_LINK) == ["eth-uplink"]
    assert nk.link_names(b"[Match]\nName=eth0\n[Link]\nNamePolicy=keep\n") == []


def test_link_problems_accepts_images_carrying_the_roots_links():
    expected = {"11-eth-uplink.link": UPLINK_LINK, "12-eth-fpga.link": FPGA_LINK}
    assert nk.link_problems(expected, {"6.12.96+rpt-rpi-v7": dict(expected), "6.1.0-50-armmp": dict(expected)}) == []


def test_link_problems_fails_a_stale_image():
    expected = {"11-eth-uplink.link": UPLINK_LINK, "12-eth-fpga.link": FPGA_LINK}
    old = OLD_UPLINK_LINK
    problems = nk.link_problems(expected, {
        "6.12.96+rpt-rpi-v7": {"11-eth-uplink.link": old, "12-eth-fpga.link": FPGA_LINK},
        "6.12.96+rpt-rpi-v8": {"12-eth-fpga.link": FPGA_LINK, "10-old.link": b""},
        "6.1.0-50-armmp": dict(expected),
    })
    assert len(problems) == 2, problems
    assert "initrd.img-6.12.96+rpt-rpi-v7" in problems[0] and "different ['11-eth-uplink.link']" in problems[0]
    assert ("initrd.img-6.12.96+rpt-rpi-v8" in problems[1] and "missing ['11-eth-uplink.link']" in problems[1]
            and "not in the root ['10-old.link']" in problems[1])


def test_link_problems_fails_a_root_that_names_no_uplink():
    expected = {"12-eth-fpga.link": FPGA_LINK}
    problems = nk.link_problems(expected, {"6.12.96+rpt-rpi-v7": dict(expected)})
    assert len(problems) == 4 and all("no .link file matches its onboard NIC" in p for p in problems), problems


def test_the_old_3b_plus_only_file_fails_coverage():
    """The root's file before setup-pi#18: propagated byte for byte into every image, and still wrong, because
    it names only the Pi 3B+'s NIC. check-links must fail it for the three other models."""
    expected = {"11-eth-uplink.link": OLD_UPLINK_LINK, "12-eth-fpga.link": FPGA_LINK,
                "73-usb-net-by-mac.link": b"", "99-default.link": b""}
    problems = nk.link_problems(expected, {"6.12.96+rpt-rpi-v7": dict(expected)})
    assert [p.split(":")[0] for p in problems] == ["Pi 4B, CM4", "Pi 5, CM5", "Orange Pi PC"], problems


def test_coverage_takes_the_first_matching_file_like_udev():
    # A file sorting earlier that matches the NIC (here: no Path=, so it may match anything) wins in udev.
    early = b"[Match]\nOriginalName=*\n\n[Link]\nNamePolicy=kernel\n"
    problems = nk.coverage_problems({"10-early.link": early, "11-eth-uplink.link": UPLINK_LINK})
    assert len(problems) == 4 and all("is 10-early.link" in p for p in problems), problems
    # an empty file is a mask, not a match; the distribution defaults sort after 11-
    assert nk.coverage_problems({"10-early.link": b"", "11-eth-uplink.link": UPLINK_LINK,
                                 "99-default.link": early}) == []


def test_coverage_list_is_setup_pis():
    """The same four ID_PATHs as fpgas-online-setup-pi's tests/test_link_files.py (setup-pi#18)."""
    assert sorted(nk.UPLINK_ID_PATHS.values()) == sorted(nk.link_match(UPLINK_LINK)["Path"].split())


def test_drop_ins_are_compared_too(tmp_path):
    directory = add_root_links(tmp_path, {"11-eth-uplink.link": UPLINK_LINK})
    (directory / "11-eth-uplink.link.d").mkdir()
    (directory / "11-eth-uplink.link.d" / "mtu.conf").write_bytes(b"[Link]\nMTUBytes=1500\n")
    expected = nk.root_links(tmp_path)
    assert set(expected) == {"11-eth-uplink.link", "11-eth-uplink.link.d/mtu.conf"}
    problems = nk.link_problems(expected, {"6.12.96+rpt-rpi-v7": {"11-eth-uplink.link": UPLINK_LINK}})
    assert len(problems) == 1 and "missing ['11-eth-uplink.link.d/mtu.conf']" in problems[0], problems


def test_initramfs_links_unpacks_with_the_roots_own_tools(tmp_path, monkeypatch):
    """unmkinitramfs runs in the chroot, into a directory under the root's /tmp that is gone afterwards;
    the main archive is read from <dir>/main when early archives come first."""
    (tmp_path / "root" / "tmp").mkdir(parents=True)
    calls = []

    class Result:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert cmd[:2] == ["chroot", str(tmp_path / "root")] and cmd[2] == "unmkinitramfs"
        out = tmp_path / "root" / cmd[4].lstrip("/")
        (out / "early" / "kernel").mkdir(parents=True)
        (out / "main" / "usr/lib/systemd/network").mkdir(parents=True)
        (out / "main" / "usr/lib/systemd/network" / "11-eth-uplink.link").write_bytes(UPLINK_LINK)
        return Result()

    monkeypatch.setattr(nk.subprocess, "run", fake_run)
    links = nk.initramfs_links(tmp_path, "6.12.96+rpt-rpi-v7")
    assert links == {"11-eth-uplink.link": UPLINK_LINK}
    assert calls[0][3] == "/boot/initrd.img-6.12.96+rpt-rpi-v7" and calls[0][4].startswith("/tmp/nfsroot-kernels-")
    assert list((tmp_path / "root" / "tmp").iterdir()) == []


def test_initramfs_links_fails_loud_when_unpacking_fails(tmp_path, monkeypatch):
    (tmp_path / "root" / "tmp").mkdir(parents=True)

    class Result:
        returncode, stdout, stderr = 2, "", "cpio: premature end of archive"

    monkeypatch.setattr(nk.subprocess, "run", lambda cmd, **kw: Result())
    with pytest.raises(RuntimeError, match="premature end of archive"):
        nk.initramfs_links(tmp_path, "6.12.96+rpt-rpi-v7")


def test_check_links_command(tmp_path, monkeypatch):
    root = make_root(tmp_path, kernels={"6.12.96+rpt-rpi-v7": b"v7", "6.1.0-50-armmp": b"armmp"})
    add_root_links(root, {"11-eth-uplink.link": UPLINK_LINK})

    class Args:
        nfs_root = str(root)

    monkeypatch.setattr(nk, "initramfs_links", lambda nfs_root, version: {"11-eth-uplink.link": UPLINK_LINK})
    assert nk.cmd_check_links(Args()) == 0
    monkeypatch.setattr(nk, "initramfs_links", lambda nfs_root, version: {})
    assert nk.cmd_check_links(Args()) == 1
