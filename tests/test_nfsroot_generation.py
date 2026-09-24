"""Tests for roles/nfsroot-generation, the server half of the stale-root reboot.

At the end of every site.yml run, end.yml decides (through `nfsroot-generation
end`, from fpgas-online/nfsroot-watchdog) whether to bump the Pi NFS root's
generation marker, which makes every netbooted board reboot itself. Bumping on
a no-op converge reboots the fleet for nothing; bumping after a failed pi play
reboots it into a half-built root. The CLI itself is tested in its own repo;
these run the role's real task files with ansible-playbook against localhost
and a throwaway root (connection: local, no become -- nothing here touches the
machine running the tests beyond tmp_path), with nfsroot_generation_cmd
pointing at a checkout of the CLI:

    NFSROOT_GENERATION=../nfsroot-watchdog/src/nfsroot-generation uv run pytest tests/
"""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

LOCK = "/etc/nfsroot-watchdog/update.lock"
GEN = "/etc/nfsroot-watchdog/generation"


def _find_cli():
    env = os.environ.get("NFSROOT_GENERATION")
    if env:
        return Path(env).resolve()
    # A sibling checkout of fpgas-online/nfsroot-watchdog, from the main
    # checkout or from one of its .worktrees/.
    for base in (REPO.parent, REPO.parent.parent.parent):
        cand = base / "nfsroot-watchdog/src/nfsroot-generation"
        if cand.exists():
            return cand
    return None


CLI = _find_cli()
pytestmark = pytest.mark.skipif(
    CLI is None, reason="set NFSROOT_GENERATION to fpgas-online/nfsroot-watchdog's src/nfsroot-generation"
)


def make_root(base: Path) -> Path:
    root = base / "root"
    for d in ("etc", "var/lib/dpkg", "var/cache/apt", "tmp", "usr/bin"):
        (root / d).mkdir(parents=True)
    (root / "var/lib/dpkg/status").write_text("Package: x\n")
    (root / "usr/bin/tool").write_text("old\n")
    return root


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
            server ansible_connection=local ansible_python_interpreter={sys.executable}
            [pi]
            chroot ansible_connection=local ansible_python_interpreter={sys.executable}
            [all:vars]
            nfs_root={tmp_path}
            nfsroot_generation_install=false
            nfsroot_generation_cmd="{sys.executable} {CLI}"
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
    env = {
        **os.environ,
        "ANSIBLE_ROLES_PATH": str(REPO / "ansible/roles"),
        "ANSIBLE_NOCOLOR": "1",
        # The repo's ansible.cfg sets become = True for production runs.
        # Tests must never escalate on the machine running them.
        "ANSIBLE_BECOME": "False",
    }
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    _assert_nothing_owned_by_others(tmp_path)
    return r


def _assert_nothing_owned_by_others(tmp_path):
    """Everything the play wrote must belong to the user running the tests:
    anything else means a task escalated privileges on this machine."""
    uid = os.getuid()
    for p in [tmp_path, *tmp_path.rglob("*")]:
        st = p.lstat()
        assert st.st_uid == uid, f"{p} is owned by uid {st.st_uid}: a task ran with become"


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
    assert marker.split(" ", 2)[2].strip() == "1 files changed"
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
