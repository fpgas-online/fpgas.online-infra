"""Tests for the server half of the stale-root auto-reboot (roles/nfsroot-generation).

At the end of every site.yml run, end.yml decides whether to bump the Pi NFS
root's generation marker, which makes every netbooted board reboot itself.
Bumping on a no-op converge reboots the fleet for nothing; bumping after a
failed pi play reboots it into a half-built root; not bumping after a real
change leaves boards on stale file handles. So the helper is unit-tested,
and the real task files are run with ansible-playbook against localhost and
a throwaway root (connection: local, no become -- nothing here touches the
machine running the tests beyond tmp_path).
"""

import importlib.util
import json
import os
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible/roles/nfsroot-generation"
HELPER = ROLE / "files/nfsroot_changed.py"

_spec = importlib.util.spec_from_file_location("nfsroot_changed", HELPER)
nc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nc)

LOCK = "/etc/fpgas-online/nfsroot-update.lock"
GEN = "/etc/fpgas-online/nfsroot-generation"


def make_root(base: Path) -> Path:
    root = base / "root"
    for d in ("etc/fpgas-online", "var/lib/dpkg", "var/cache/apt", "tmp", "usr/bin"):
        (root / d).mkdir(parents=True)
    (root / "var/lib/dpkg/status").write_text("Package: x\n")
    (root / "usr/bin/tool").write_text("old\n")
    (root / "etc/passwd").write_text("pi:x:1000\n")
    return root


def take_lock(root: Path):
    # ctime has ns resolution but the clock tick may be coarser; make sure
    # anything written after the lock really is newer.
    time.sleep(0.02)
    (root / LOCK.lstrip("/")).write_text("1790000000 x site.yml\n")
    time.sleep(0.02)


def run_helper(root: Path, *extra):
    out = subprocess.run(
        ["python3", str(HELPER), str(root), LOCK, *extra],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


# --- nfsroot_changed.py ------------------------------------------------------


def test_nothing_changed(tmp_path):
    root = make_root(tmp_path)
    take_lock(root)
    r = run_helper(root)
    assert r == {"bump": False, "count": 0, "reason": "nothing changed", "sample": []}


def test_replaced_file_is_a_change(tmp_path):
    root = make_root(tmp_path)
    take_lock(root)
    # dpkg's way: write status-new, rename over status (a new inode).
    new = root / "var/lib/dpkg/status-new"
    new.write_text("Package: x\nVersion: 2\n")
    new.replace(root / "var/lib/dpkg/status")
    r = run_helper(root)
    assert r["bump"] is True
    assert r["sample"] == ["/var/lib/dpkg/status"]


def test_unpacked_file_with_old_mtime_is_still_a_change(tmp_path):
    # dpkg unpacks files with the package's own mtime; only ctime is honest.
    root = make_root(tmp_path)
    take_lock(root)
    f = root / "usr/bin/tool"
    f.write_text("new\n")
    os.utime(f, (1_000_000_000, 1_000_000_000))
    assert run_helper(root)["sample"] == ["/usr/bin/tool"]


def test_ignored_paths_and_directories_do_not_count(tmp_path):
    root = make_root(tmp_path)
    take_lock(root)
    (root / "var/cache/apt/pkgcache.bin").write_text("x")
    (root / "tmp/ansible-tmp").mkdir()
    (root / "tmp/ansible-tmp/x").write_text("x")
    # a temp file created and removed: only the directory's ctime moves
    (root / "etc/.tmp").write_text("x")
    (root / "etc/.tmp").unlink()
    (root / GEN.lstrip("/")).write_text("previous generation\n")
    r = run_helper(
        root, "--ignore", "var/cache", "--ignore", "tmp", "--ignore", GEN.lstrip("/"), "--ignore", LOCK
    )
    assert r["bump"] is False, r


def test_no_lock_means_bump(tmp_path):
    root = make_root(tmp_path)
    r = run_helper(root)
    assert r["bump"] is True
    assert r["count"] is None


def test_symlink_counts_and_is_not_followed(tmp_path):
    root = make_root(tmp_path)
    take_lock(root)
    (root / "usr/bin/link").symlink_to("/nonexistent/target")
    assert run_helper(root)["sample"] == ["/usr/bin/link"]


def test_sample_is_capped(tmp_path):
    root = make_root(tmp_path)
    take_lock(root)
    for i in range(30):
        (root / f"usr/bin/f{i}").write_text("x")
    r = run_helper(root, "--sample", "5")
    assert r["count"] == 30
    assert len(r["sample"]) == 5


# --- the real task files, run by ansible-playbook ------------------------------

PLAYBOOK = """
- hosts: nbp
  gather_facts: true
  tasks:
    - include_role: {name: nfsroot-generation, tasks_from: begin.yml}

- hosts: pi
  gather_facts: false
  tasks:
    - include_role: {name: nfsroot-generation, tasks_from: pi_started.yml}
    - name: change the root
      copy: {dest: "{{ nfs_root }}/root/usr/bin/tool", content: "{{ new_tool }}"}
      when: new_tool is defined
    - fail: {msg: simulated pi play failure}
      when: fail_pi | default(false) | bool
    - meta: end_host
      when: end_pi | default(false) | bool
    - include_role: {name: nfsroot-generation, tasks_from: pi_done.yml}

- hosts: nbp
  gather_facts: true
  tasks:
    - include_role: {name: nfsroot-generation, tasks_from: end.yml}
"""


def play(tmp_path: Path, *extra_vars: str, check=False):
    inv = tmp_path / "hosts"
    inv.write_text(
        textwrap.dedent(
            f"""\
            [nbp]
            server ansible_connection=local ansible_python_interpreter={os.sys.executable}
            [pi]
            chroot ansible_connection=local ansible_python_interpreter={os.sys.executable}
            [all:vars]
            nfs_root={tmp_path}
            """
        )
    )
    pb = tmp_path / "pb.yml"
    pb.write_text(PLAYBOOK)
    cmd = ["ansible-playbook", "-i", str(inv), str(pb)]
    for v in extra_vars:
        cmd += ["-e", v]
    if check:
        cmd.append("--check")
    env = {**os.environ, "ANSIBLE_ROLES_PATH": str(REPO / "ansible/roles"), "ANSIBLE_NOCOLOR": "1"}
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def lock_of(tmp_path):
    return tmp_path / "root" / LOCK.lstrip("/")


def gen_of(tmp_path):
    return tmp_path / "root" / GEN.lstrip("/")


def test_play_noop_run_does_not_bump(tmp_path):
    make_root(tmp_path)
    r = play(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not gen_of(tmp_path).exists()
    assert not lock_of(tmp_path).exists()
    assert "nothing changed" in r.stdout


def test_play_real_change_bumps_then_unlocks(tmp_path):
    make_root(tmp_path)
    r = play(tmp_path, "new_tool=v2")
    assert r.returncode == 0, r.stdout + r.stderr
    marker = gen_of(tmp_path).read_text()
    epoch = int(marker.split()[0])
    assert abs(epoch - time.time()) < 300
    assert "1 files changed" in marker
    assert not lock_of(tmp_path).exists()


def test_play_failed_pi_play_keeps_the_lock_and_does_not_bump(tmp_path):
    make_root(tmp_path)
    r = play(tmp_path, "new_tool=v2", "fail_pi=true")
    assert r.returncode != 0
    # Every pi host failed, so ansible-playbook stops before the last play.
    assert "publish" not in r.stdout
    assert lock_of(tmp_path).exists()
    assert not gen_of(tmp_path).exists()

    # The next, successful, run keeps the first run's lock (and so its start
    # time) and publishes both runs' changes, although it changes nothing.
    first_lock = lock_of(tmp_path).read_text()
    r = play(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 files changed" in gen_of(tmp_path).read_text()
    assert first_lock.startswith("1") and not lock_of(tmp_path).exists()


def test_play_unfinished_pi_play_is_caught_by_end(tmp_path):
    # A pi play that stops without failing (end_host, or one of several pi
    # hosts dropping out) lets the last play run; end.yml must refuse.
    make_root(tmp_path)
    r = play(tmp_path, "new_tool=v2", "end_pi=true")
    assert r.returncode != 0
    assert "did not finish" in r.stdout
    assert lock_of(tmp_path).exists()
    assert not gen_of(tmp_path).exists()


def test_play_bump_never_and_always(tmp_path):
    make_root(tmp_path)
    r = play(tmp_path, "new_tool=v2", "nfsroot_generation_bump=never")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not gen_of(tmp_path).exists()
    r = play(tmp_path, "nfsroot_generation_bump=always")
    assert r.returncode == 0, r.stdout + r.stderr
    assert gen_of(tmp_path).exists()


def test_play_check_mode_writes_nothing(tmp_path):
    make_root(tmp_path)
    r = play(tmp_path, "new_tool=v2", check=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not lock_of(tmp_path).exists()
    assert not gen_of(tmp_path).exists()


def test_play_without_a_root_is_a_noop(tmp_path):
    r = play(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (tmp_path / "root").exists()
