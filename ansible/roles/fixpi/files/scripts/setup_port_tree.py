#!/usr/bin/env python3
"""Build the setup / config port's boot tree from the fleet's (Tim, 18 Oct 2026).

A device on the setup port (port `setup_port` of every switch) boots with every
restriction the fleet's boot files apply lifted. dnsmasq serves this tree, not
the fleet's, to that port's VLAN alone (roles/pxe ports.conf.j2), so the Pi
learns where it is from what the gateway gives its port: no per-Pi variable.

The tree is the fleet's boot tree, hard-linked file by file, except:
  config.txt        without `dtoverlay=disable-wifi` and `dtoverlay=disable-bt`
                    (the radios, wlan0 included, come up), with
                    `eeprom_write_protect=0` in place of `=1`, and with the
                    header SPI and I2C buses on (`dtparam=spi=on`,
                    `dtparam=i2c_arm=on`, off in the fleet's);
  cmdline*.txt      with `fpgas.setup_port=1` (the root's udev rule that
                    unblocks the radios acts on it) and
                    `systemd.restore_state=0` (no saved rfkill block is
                    restored onto the radios).

Fails loud when the fleet's files no longer carry what is lifted: the
derivation would otherwise serve a setup tree that silently keeps a
restriction, or drops one the fleet no longer has.

usage: setup_port_tree.py <fleet boot dir> <setup boot dir>
Prints "changed" when it replaced the setup tree, "unchanged" otherwise.
"""

import filecmp
import os
import shutil
import sys
from pathlib import Path

LIFTED = ("dtoverlay=disable-wifi", "dtoverlay=disable-bt")
PROTECT, UNPROTECT = "eeprom_write_protect=1", "eeprom_write_protect=0"
ADDED = ("dtparam=spi=on", "dtparam=i2c_arm=on")
CMDLINES = ("cmdline.txt", "cmdline-pi5.txt")
FLAGS = ("fpgas.setup_port=1", "systemd.restore_state=0")
DERIVED = ("config.txt", *CMDLINES)


def fail(why):
    sys.exit(f"setup_port_tree: {why}")


def setup_config(text):
    lines = text.splitlines()
    for line in (*LIFTED, PROTECT):
        if lines.count(line) != 1:
            fail(f"the fleet config.txt carries {line!r} {lines.count(line)} times, not once")
    for line in (UNPROTECT, *ADDED):
        if line in lines:
            fail(f"the fleet config.txt already carries {line!r}")
    out = [UNPROTECT if line == PROTECT else line for line in lines if line not in LIFTED]
    # In [all], after any model section the fleet's file ends in.
    return "\n".join([*out, "[all]", *ADDED]) + "\n"


def setup_cmdline(name, text):
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].strip():
        fail(f"the fleet {name} is not one line")
    words = lines[0].split()
    for flag in FLAGS:
        if any(w.split("=")[0] == flag.split("=")[0] for w in words):
            fail(f"the fleet {name} already names {flag.split('=')[0]}")
    return " ".join(words + list(FLAGS)) + "\n"


def build(fleet, staged):
    for name in DERIVED:
        if not (fleet / name).is_file():
            fail(f"{fleet / name} is missing")
    for dirpath, dirnames, filenames in os.walk(fleet):
        rel = Path(dirpath).relative_to(fleet)
        (staged / rel).mkdir(exist_ok=True)
        (staged / rel).chmod(0o755)  # dnsmasq reads the tree as its own user, whatever this process's umask
        # os.walk lists a symlink to a directory with the directories and does not follow it: it is kept as the
        # link it is, like a link to a file.
        for name in [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
            dirnames.remove(name)
            os.symlink(os.readlink(Path(dirpath) / name), staged / rel / name)
        for name in filenames:
            if rel == Path(".") and name in DERIVED:
                continue
            src = Path(dirpath) / name
            if src.is_symlink():
                os.symlink(os.readlink(src), staged / rel / name)
            else:
                os.link(src, staged / rel / name)
        dirnames.sort()
    (staged / "config.txt").write_text(setup_config((fleet / "config.txt").read_text()))
    for name in CMDLINES:
        (staged / name).write_text(setup_cmdline(name, (fleet / name).read_text()))
    for name in DERIVED:  # as the directories: readable by dnsmasq, whatever the umask
        (staged / name).chmod(0o644)


def same(a, b):
    """Whether two trees hold the same names, links and contents."""
    cmp = filecmp.dircmp(a, b, ignore=[], hide=[])  # its default ignore list hides .git, RCS, tags, ...
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    for name in cmp.common_files:
        pa, pb = Path(a) / name, Path(b) / name
        if pa.is_symlink() or pb.is_symlink():
            if not (pa.is_symlink() and pb.is_symlink() and os.readlink(pa) == os.readlink(pb)):
                return False
        elif os.stat(pa).st_ino != os.stat(pb).st_ino and not filecmp.cmp(pa, pb, shallow=False):
            return False
    for name in cmp.common_dirs:  # dircmp follows a link to a directory: compare it as the link it is
        pa, pb = Path(a) / name, Path(b) / name
        if pa.is_symlink() or pb.is_symlink():
            if not (pa.is_symlink() and pb.is_symlink() and os.readlink(pa) == os.readlink(pb)):
                return False
        elif not same(pa, pb):
            return False
    return True


def main(argv):
    if len(argv) != 3:
        fail("usage: setup_port_tree.py <fleet boot dir> <setup boot dir>")
    fleet, setup = Path(argv[1]), Path(argv[2])
    if not fleet.is_dir():
        fail(f"{fleet} is not a directory")
    staged, old = setup.with_name(setup.name + ".new"), setup.with_name(setup.name + ".old")
    for leftover in (staged, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    staged.mkdir()
    build(fleet, staged)
    if setup.is_dir() and same(staged, setup):
        shutil.rmtree(staged)
        print("unchanged")
        return
    # Two renames: a TFTP read in the instant between them is refused, and the
    # board's bootloader retries it. dnsmasq needs the directory to exist when it
    # starts (roles/pxe creates it empty on a fresh gateway).
    if setup.exists():
        setup.rename(old)
    staged.rename(setup)
    if old.exists():
        shutil.rmtree(old)
    print("changed")


if __name__ == "__main__":
    main(sys.argv)
