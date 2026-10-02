"""fixpi's NFS root authorized_keys (ansible/roles/fixpi/tasks/authorized_keys.yml).

Runs the real task file with ansible-playbook against a scratch root. What
it guards: a converge with no key change must leave both files alone, since
every rewrite is a new inode, which the booted boards answer with ESTALE, and
a root change that makes nfsroot_generation reboot the fleet. Also, the files
are exclusive (a key dropped from the configuration disappears); a GitHub
download that fails or holds no key (an empty 200, a 404, 429 or 5xx, no
answer at all) fails the play at the download, after the retries, and
leaves every file as it was; and --check runs cleanly and changes nothing.

GitHub is played by a local HTTP server (conftest's keyserver, via
fixpi_github_keys_base_url), so the tests need no network.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

import yaml

from tests.conftest import closed_port_url

REPO = Path(__file__).resolve().parent.parent
TASKS = REPO / "ansible/roles/fixpi/tasks/authorized_keys.yml"

SERVER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIServerServerServerServerServerServerServer videoteam@tweed"
CONTROLLER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIControllerControllerControllerControllerCo fpgas.online-ansible"
JUMP = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJumpJumpJumpJumpJumpJumpJumpJumpJumpJumpJump pi@tweed (jump account)"
GH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGitHubGitHubGitHubGitHubGitHubGitHubGit"
GH_KEY_2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINewNewNewNewNewNewNewNewNewNewNewNewNewN"
KEPT = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeptKeptKeptKeptKeptKeptKeptKeptKeptKeptKe gh:alice"
ROLES = REPO / "ansible/roles"
REVOKED = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQRevokedRevokedRevoked # ssh-import-id gh:asinghani"


def seed(v: dict, extra: list[str]) -> None:
    """Both files as a previous converge left them, plus `extra` lines."""
    for path in ("root", "home/pi"):
        f = Path(v["nfs_root"]) / "root" / path / ".ssh/authorized_keys"
        lines = [SERVER, CONTROLLER, *extra] + ([JUMP] if path == "home/pi" else [])
        f.write_text("\n".join(lines) + "\n")
        f.chmod(0o600)
    for path in ("home/ansible",):
        f = Path(v["nfs_root"]) / "root" / path / ".ssh/authorized_keys"
        lines = [CONTROLLER]
        f.write_text("\n".join(lines) + "\n")
        f.chmod(0o600)


def make_root(tmp_path: Path) -> dict:
    root = tmp_path / "nfs"
    # home/ansible/.ssh is made by fixpi's ansible-home.yml before the writer runs.
    for sub in ("root/root/.ssh", "root/home/pi/.ssh", "root/home/ansible/.ssh"):
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
            # The automation account's: the controller key alone.
            {"path": "home/ansible", "id": me, "key_sources": ["controller"]},
        ],
    }


def converge(tmp_path: Path, variables: dict, check: bool = False, fail: bool = False) -> int | str:
    """Run the task file once; return how many tasks reported changed.

    With fail=True the run must fail instead: return its output.
    """
    playbook = tmp_path / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        # No waiting between download retries in the tests.
        "vars": {"ssh_key_fetch_delay": 0, **variables},
        "tasks": [{"ansible.builtin.include_tasks": str(TASKS)}],
    }]))
    # An empty config (and a cwd without one): not the repo's ansible.cfg,
    # whose become and profile_tasks callback a local run must not use.
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\n")
    env = dict(
        os.environ,
        ANSIBLE_CONFIG=str(config),
        # roles/ssh_key_fetch, which the task file includes.
        ANSIBLE_ROLES_PATH=str(ROLES),
        # The default callback's PLAY RECAP: the json callback is in
        # ansible.posix now, and the task file needs no collection at all
        # (an empty collections path proves it, as in the CI pytest job).
        ANSIBLE_STDOUT_CALLBACK="ansible.builtin.default",
        ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"),
        ANSIBLE_NOCOLOR="1",
        ANSIBLE_LOCALHOST_WARNING="False",
        ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
    )
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(playbook), *(["--check"] if check else [])],
        env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if fail:
        assert result.returncode != 0, output
        return output
    assert result.returncode == 0, output
    recap = re.search(r"^localhost\s*:.*\bchanged=(\d+).*\bfailed=(\d+)", result.stdout, re.M)
    assert recap, result.stdout
    assert recap.group(2) == "0", result.stdout
    return int(recap.group(1))


def snapshot(v: dict) -> list[tuple[int, int, str]]:
    """Every authorized_keys file's inode, mtime and content."""
    files = [Path(v["nfs_root"]) / "root" / p / ".ssh/authorized_keys"
             for p in ("root", "home/pi", "home/ansible")]
    return [(f.stat().st_ino, f.stat().st_mtime_ns, f.read_text()) for f in files]


def keys_of(variables: dict, path: str) -> list[str]:
    text = (Path(variables["nfs_root"]) / "root" / path / ".ssh/authorized_keys").read_text()
    return text.splitlines()


def test_first_converge_writes_the_complete_lists(tmp_path):
    v = make_root(tmp_path)
    assert converge(tmp_path, v) > 0
    assert keys_of(v, "root") == [SERVER, CONTROLLER]
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER, JUMP]
    assert keys_of(v, "home/ansible") == [CONTROLLER]


def test_the_ansible_account_gets_only_the_controller_key(tmp_path, github):
    """A `key_sources` selection: no server, GitHub or jump key, whatever is configured."""
    url, answers = github
    answers["alice"] = (200, GH_KEY + "\n")
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    f = Path(v["nfs_root"]) / "root/home/ansible/.ssh/authorized_keys"
    # A stray key someone added by hand is dropped (the file is exclusive).
    f.write_text(CONTROLLER + "\n" + REVOKED + "\n")
    converge(tmp_path, v)
    assert keys_of(v, "home/ansible") == [CONTROLLER]
    st = f.stat()
    assert (st.st_uid, st.st_gid, st.st_mode & 0o777) == (os.getuid(), os.getuid(), 0o600)
    before = (st.st_ino, st.st_mtime_ns)
    assert converge(tmp_path, v) == 0
    assert (f.stat().st_ino, f.stat().st_mtime_ns) == before


def test_second_converge_changes_nothing(tmp_path):
    v = make_root(tmp_path)
    converge(tmp_path, v)
    files = [Path(v["nfs_root"]) / "root" / p / ".ssh/authorized_keys"
             for p in ("root", "home/pi", "home/ansible")]
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


def test_github_keys_are_fetched_and_tagged(tmp_path, github):
    url, answers = github
    answers["alice"] = (200, GH_KEY + "\n")
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    converge(tmp_path, v)
    assert keys_of(v, "root") == [SERVER, CONTROLLER, GH_KEY + " gh:alice"]
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER, GH_KEY + " gh:alice", JUMP]
    assert converge(tmp_path, v) == 0


@pytest.mark.parametrize("status, body, said", [
    (200, "", "answered 200 (OK) with no key in it"),
    (404, "Not Found", "answered 404"),
    (403, "rate limited", "answered 403"),
    (429, "try later", "answered 429"),
    (500, "oops", "answered 500"),
    (503, "try later", "answered 503"),
])
def test_a_github_download_with_no_keys_fails_and_writes_nothing(tmp_path, github, status, body, said):
    """No keep-on-outage and no drop-on-404: the converge stops at the download."""
    url, answers = github
    answers["alice"] = (status, body)
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    seed(v, [KEPT])
    before = snapshot(v)
    output = converge(tmp_path, v, fail=True)
    assert f"No ssh keys for the NFS root from gh:alice: {url}/alice.keys {said}" in output, output
    assert "Build the NFS root's authorized_keys" not in output
    assert snapshot(v) == before


def test_no_answer_from_github_fails_and_writes_nothing(tmp_path):
    url = closed_port_url()
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    seed(v, [KEPT])
    before = snapshot(v)
    output = converge(tmp_path, v, fail=True)
    assert f"No ssh keys for the NFS root from gh:alice: {url}/alice.keys answered -1" in output, output
    assert snapshot(v) == before


def test_a_github_blip_is_retried(tmp_path, keyserver):
    keyserver.answers["alice"] = [(503, "try later"), (200, ""), (200, GH_KEY + "\n")]
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": keyserver.url, "fixpi_github_key_users": ["alice"]}
    converge(tmp_path, v)
    assert keys_of(v, "root") == [SERVER, CONTROLLER, GH_KEY + " gh:alice"]
    assert keyserver.hits["alice"] == 3


def test_check_mode_runs_and_changes_nothing(tmp_path, github):
    url, answers = github
    answers["alice"] = (200, GH_KEY + "\n")
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    # Fresh root: --check reports the writes it would make but makes none.
    assert converge(tmp_path, v, check=True) > 0
    assert not (Path(v["nfs_root"]) / "root/root/.ssh/authorized_keys").exists()
    # Converged root: --check sees nothing to do (the fetch really ran).
    converge(tmp_path, v)
    assert converge(tmp_path, v, check=True) == 0
    # A key GitHub no longer lists shows up as a pending change.
    answers["alice"] = (200, GH_KEY_2 + "\n")
    assert converge(tmp_path, v, check=True) > 0
    assert GH_KEY + " gh:alice" in keys_of(v, "root")
    # An empty answer fails --check too, at the download.
    answers["alice"] = (200, "")
    assert "No ssh keys for the NFS root from gh:alice" in converge(tmp_path, v, check=True, fail=True)
    assert GH_KEY + " gh:alice" in keys_of(v, "root")


def test_keys_downloaded_before_the_root_update_are_used(tmp_path, keyserver):
    """site.yml downloads them (github_keys.yml) before the update lock: not again."""
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": keyserver.url,
         "fixpi_github_key_users": ["alice"], "fixpi_github_keys": {"gh:alice": [GH_KEY]}}
    converge(tmp_path, v)
    assert keys_of(v, "root") == [SERVER, CONTROLLER, GH_KEY + " gh:alice"]
    assert keyserver.hits == {}


def test_site_downloads_the_root_keys_before_the_root_update_begins():
    """An empty or failed GitHub download must fail site.yml before the NFS
    root update lock is taken and the image extracted into the live root,
    not half-way through the update with the lock held."""
    plays = yaml.safe_load((REPO / "ansible/site.yml").read_text())
    play = next(p for p in plays if p.get("name") == "Update the Pi NFS root")
    first = play["tasks"][0]
    assert first["ansible.builtin.include_role"]["name"] == "fixpi"
    assert first["ansible.builtin.include_role"]["tasks_from"] == "github_keys.yml"
    # Runs on every gateway converge: no condition and no tag (the image
    # build keeps the site layer out with fixpi_image_build,
    # tests/test_fixpi_site_layer.py, and never runs site.yml).
    assert "when" not in first
    assert "tags" not in first
    assert "apply" not in first["ansible.builtin.include_role"]
    assert play["tasks"][1]["name"] == "Take the Pi NFS root update lock"


def test_the_early_download_fails_on_an_empty_answer(tmp_path, keyserver):
    """github_keys.yml, as site.yml runs it ahead of the lock, fails on no keys."""
    keyserver.answers["alice"] = (200, "")
    playbook = tmp_path / "early.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"ssh_key_fetch_delay": 0, "fixpi_github_keys_base_url": keyserver.url,
                 "fixpi_github_key_users": ["alice"]},
        "tasks": [{"ansible.builtin.include_role": {"name": "fixpi", "tasks_from": "github_keys.yml"}},
                  {"name": "Take the Pi NFS root update lock", "ansible.builtin.debug": {"msg": "lock"}}],
    }]))
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    env = dict(os.environ, ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg"), ANSIBLE_ROLES_PATH=str(ROLES),
               ANSIBLE_STDOUT_CALLBACK="ansible.builtin.default", ANSIBLE_NOCOLOR="1",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"),
               ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
    result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(playbook)], env=env, cwd=tmp_path,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert f"No ssh keys for the NFS root from gh:alice: {keyserver.url}/alice.keys answered 200" in output
    assert "TASK [Take the Pi NFS root update lock]" not in output
