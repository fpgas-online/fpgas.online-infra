"""fixpi's NFS root authorized_keys (ansible/roles/fixpi/tasks/authorized_keys.yml).

Runs the real task file with ansible-playbook against a scratch root. What
it guards: a converge with no key change must leave both files alone, since
every rewrite is a new inode, which the booted boards answer with ESTALE, and
a root change that makes nfsroot_generation reboot the fleet. Also, the files
are exclusive (a key dropped from the configuration disappears); a GitHub
fetch that fails transiently (no answer, 429, 5xx) keeps that user's keys,
while a 404 drops them; and --check runs cleanly and changes nothing.

GitHub is played by a local HTTP server (fixpi_github_keys_base_url), so the
tests need no network.
"""

import os
import re
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import yaml

REPO = Path(__file__).resolve().parent.parent
TASKS = REPO / "ansible/roles/fixpi/tasks/authorized_keys.yml"

SERVER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIServerServerServerServerServerServerServer videoteam@tweed"
CONTROLLER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIControllerControllerControllerControllerCo fpgas.online-ansible"
JUMP = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJumpJumpJumpJumpJumpJumpJumpJumpJumpJumpJump pi@tweed (jump account)"
GH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGitHubGitHubGitHubGitHubGitHubGitHubGit"
KEPT = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeptKeptKeptKeptKeptKeptKeptKeptKeptKeptKe gh:alice"
REVOKED = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQRevokedRevokedRevoked # ssh-import-id gh:asinghani"


@pytest.fixture
def github():
    """A fake github.com: set answers[user] = (status, body) per test."""
    answers: dict[str, tuple[int, str]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            user = self.path.strip("/").removesuffix(".keys")
            status, body = answers.get(user, (404, "Not Found"))
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", answers
    server.shutdown()


def closed_port_url() -> str:
    """A URL nothing listens on: a transport error (uri status -1)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def seed(v: dict, extra: list[str]) -> None:
    """Both files as a previous converge left them, plus `extra` lines."""
    for path in ("root", "home/pi"):
        f = Path(v["nfs_root"]) / "root" / path / ".ssh/authorized_keys"
        lines = [SERVER, CONTROLLER, *extra] + ([JUMP] if path == "home/pi" else [])
        f.write_text("\n".join(lines) + "\n")
        f.chmod(0o600)


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


def converge(tmp_path: Path, variables: dict, check: bool = False) -> int:
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
    assert result.returncode == 0, result.stdout + result.stderr
    recap = re.search(r"^localhost\s*:.*\bchanged=(\d+).*\bfailed=(\d+)", result.stdout, re.M)
    assert recap, result.stdout
    assert recap.group(2) == "0", result.stdout
    return int(recap.group(1))


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


def test_github_keys_are_fetched_and_tagged(tmp_path, github):
    url, answers = github
    answers["alice"] = (200, GH_KEY + "\n")
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    converge(tmp_path, v)
    assert keys_of(v, "root") == [SERVER, CONTROLLER, GH_KEY + " gh:alice"]
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER, GH_KEY + " gh:alice", JUMP]
    assert converge(tmp_path, v) == 0


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_transient_github_failure_keeps_that_users_keys(tmp_path, github, status):
    url, answers = github
    answers["alice"] = (status, "try later")
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    seed(v, [KEPT])
    assert converge(tmp_path, v) == 0
    assert keys_of(v, "root") == [SERVER, CONTROLLER, KEPT]
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER, KEPT, JUMP]


def test_no_answer_from_github_keeps_that_users_keys(tmp_path):
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": closed_port_url(),
         "fixpi_github_key_users": ["alice"]}
    seed(v, [KEPT])
    assert converge(tmp_path, v) == 0
    assert keys_of(v, "root") == [SERVER, CONTROLLER, KEPT]


def test_a_404_drops_that_users_keys(tmp_path, github):
    url, _answers = github  # alice unknown: 404
    v = {**make_root(tmp_path), "fixpi_github_keys_base_url": url, "fixpi_github_key_users": ["alice"]}
    seed(v, [KEPT])
    converge(tmp_path, v)
    assert keys_of(v, "root") == [SERVER, CONTROLLER]
    assert keys_of(v, "home/pi") == [SERVER, CONTROLLER, JUMP]


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
    answers["alice"] = (200, "")
    assert converge(tmp_path, v, check=True) > 0
    assert GH_KEY + " gh:alice" in keys_of(v, "root")
