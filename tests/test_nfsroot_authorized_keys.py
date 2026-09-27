"""fixpi's NFS root authorized_keys (ansible/roles/fixpi/tasks/authorized_keys.yml).

Runs the real task file with ansible-playbook against a scratch root. What
it guards: a converge with no key change must leave both files alone, since
every rewrite is a new inode, which the booted boards answer with ESTALE, and
a root change that makes nfsroot_generation reboot the fleet. Also, the files
are exclusive (a key dropped from the configuration disappears), and a
GitHub fetch that fails keeps that user's keys instead of dropping them.
"""

import json
import os
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TASKS = REPO / "ansible/roles/fixpi/tasks/authorized_keys.yml"

SERVER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIServerServerServerServerServerServerServer videoteam@tweed"
CONTROLLER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIControllerControllerControllerControllerCo fpgas.online-ansible"
JUMP = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJumpJumpJumpJumpJumpJumpJumpJumpJumpJumpJump pi@tweed (jump account)"
# A user GitHub does not have: the fetch fails with or without network.
MISSING_GH_USER = "no-such-user-fpgas-online-zz9"
KEPT = f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeptKeptKeptKeptKeptKeptKeptKeptKeptKeptKe gh:{MISSING_GH_USER}"
REVOKED = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQRevokedRevokedRevoked # ssh-import-id gh:asinghani"


def make_root(tmp_path: Path) -> dict:
    root = tmp_path / "nfs"
    for sub in ("root/root/.ssh", "root/home/pi/.ssh"):
        (root / sub).mkdir(parents=True)
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "server.pub").write_text(SERVER + "\n")
    (keys / "jump.pub").write_text(JUMP + "\n")
    (keys / "controller").write_text("not read\n")
    (keys / "controller.pub").write_text(CONTROLLER + "\n")
    me = str(os.getuid())
    return {
        "nfs_root": str(root),
        "fixpi_server_user_pubkey": str(keys / "server.pub"),
        "fixpi_jump_ssh_pubkey": str(keys / "jump.pub"),
        "ansible_ssh_private_key_file": str(keys / "controller"),
        "fixpi_github_key_users": [],
        # Owned by the test user: the test does not run as root.
        "fixpi_authorized_keys_files": [
            {"path": "root", "id": me},
            {"path": "home/pi", "id": me},
        ],
    }


def converge(tmp_path: Path, variables: dict) -> int:
    """Run the task file once; return how many tasks reported changed."""
    playbook = tmp_path / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": variables,
        "tasks": [{"ansible.builtin.include_tasks": str(TASKS)}],
    }]))
    # An empty config (and a cwd without one): not the repo's ansible.cfg,
    # whose become and profile_tasks callback a local run must not use.
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\n")
    env = dict(
        os.environ,
        ANSIBLE_CONFIG=str(config),
        ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json",
        ANSIBLE_LOCALHOST_WARNING="False",
        ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
    )
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(playbook)],
        env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    stats = json.loads(result.stdout)["stats"]["localhost"]
    assert stats["failures"] == 0
    return stats["changed"]


def keys_of(variables: dict, path: str) -> list[str]:
    text = (Path(variables["nfs_root"]) / "root" / path / ".ssh/authorized_keys").read_text()
    return text.splitlines()


def test_first_converge_writes_the_complete_lists(tmp_path):
    v = make_root(tmp_path)
    assert converge(tmp_path, v) > 0
    assert keys_of(v, "root") == [SERVER, CONTROLLER]
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER, JUMP]


def test_second_converge_changes_nothing(tmp_path):
    v = make_root(tmp_path)
    converge(tmp_path, v)
    files = [Path(v["nfs_root"]) / "root" / p / ".ssh/authorized_keys" for p in ("root", "home/pi")]
    before = [(f.stat().st_ino, f.stat().st_mtime_ns, f.read_text()) for f in files]
    assert converge(tmp_path, v) == 0
    assert [(f.stat().st_ino, f.stat().st_mtime_ns, f.read_text()) for f in files] == before


def test_keys_not_in_the_configuration_disappear(tmp_path):
    v = make_root(tmp_path)
    # What an old image or ssh-import-id left behind.
    pi = Path(v["nfs_root"]) / "root/home/pi/.ssh/authorized_keys"
    pi.write_text(SERVER + "\n" + REVOKED + "\n")
    converge(tmp_path, v)
    assert REVOKED not in keys_of(v, "home/pi")
    # Dropping the jump key from the configuration drops it from the root.
    converge(tmp_path, {**v, "fixpi_jump_ssh_pubkey": ""})
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER]


def test_a_failed_github_fetch_keeps_that_users_keys(tmp_path):
    v = make_root(tmp_path)
    v["fixpi_github_key_users"] = [MISSING_GH_USER]
    for path in ("root", "home/pi"):
        f = Path(v["nfs_root"]) / "root" / path / ".ssh/authorized_keys"
        f.write_text(SERVER + "\n" + CONTROLLER + "\n" + KEPT + "\n" + (JUMP + "\n" if path == "home/pi" else ""))
        f.chmod(0o600)  # as a previous converge left it
    assert converge(tmp_path, v) == 0
    assert keys_of(v, "root") == [SERVER, CONTROLLER, KEPT]
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER, KEPT, JUMP]
