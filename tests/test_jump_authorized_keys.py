"""roles/jump with a key line that carries an options prefix.

The filters (ansible/filter_plugins/ssh_keys.py) pick the base64 field out of
`[options] type key [comment]`; the real role tasks (the static keys, the
shared ids' dedupe, the revoked-key deletion) and the real verify tasks run
here with ansible-playbook against a scratch authorized_keys. The tasks are
the role's own, loaded from its task files; the only change is a `path:` on
the authorized_key tasks, because the module otherwise writes the account's
real home.
"""

import getpass
import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
JUMP = REPO / "ansible/roles/jump/tasks"

spec = importlib.util.spec_from_file_location("ssh_keys_filter", REPO / "ansible/filter_plugins/ssh_keys.py")
ssh_keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssh_keys)

OPTIONS = 'restrict,port-forwarding,permitopen="10.21.2.48:22"'
BLOB = "AAAAC3NzaC1lZDI1NTE5AAAAILabelStationLabelStationLabelStationLabelSt"
LABEL_KEY = f"{OPTIONS} ssh-ed25519 {BLOB} label-station@tim-yoga"
PLAIN = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlainPlainPlainPlainPlainPlainPlainPlainPl op@example"
RSA = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQRsaRsaRsaRsaRsa carl"
GH_DUP = f"ssh-ed25519 {BLOB}"  # the label key again, as a GitHub id would publish it
GH_NEW = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGitHubGitHubGitHubGitHubGitHubGitHubGit"


# --- the filters ----------------------------------------------------------

@pytest.mark.parametrize("line, blob, options, comment", [
    (PLAIN, PLAIN.split()[1], "", "op@example"),
    (RSA, "AAAAB3NzaC1yc2EAAAADAQABAAABAQRsaRsaRsaRsaRsa", "", "carl"),
    (LABEL_KEY, BLOB, OPTIONS, "label-station@tim-yoga"),
    (f"no-pty,from=\"10.0.0.1,10.0.0.2\" {RSA}", "AAAAB3NzaC1yc2EAAAADAQABAAABAQRsaRsaRsaRsaRsa",
     'no-pty,from="10.0.0.1,10.0.0.2"', "carl"),
    (f"{GH_NEW}", GH_NEW.split()[1], "", ""),
])
def test_filters_find_the_key_whatever_precedes_it(line, blob, options, comment):
    assert ssh_keys.ssh_key_blob(line) == blob
    assert ssh_keys.ssh_key_options(line) == options
    assert ssh_keys.ssh_key_comment(line) == comment


@pytest.mark.parametrize("line", ["", "not-a-key", "restrict,port-forwarding", "ssh-ed25519"])
def test_filters_fail_loud_on_a_line_without_a_key(line):
    with pytest.raises(Exception, match="ssh key"):
        ssh_keys.ssh_key_blob(line)


def test_split_1_is_the_wrong_field_with_options():
    """Why the helper exists."""
    assert LABEL_KEY.split()[1] == "ssh-ed25519"
    assert ssh_keys.ssh_key_blob(LABEL_KEY) == BLOB


# --- the real tasks -------------------------------------------------------

def role_tasks(file: str, names: list[str], authorized_path: Path | None = None) -> list[dict]:
    """The role's tasks called `names`, in the file's order."""
    tasks = [t for t in yaml.safe_load((JUMP / file).read_text()) if t.get("name") in names]
    assert [t["name"] for t in tasks] == names
    if authorized_path:
        for t in tasks:
            if "ansible.posix.authorized_key" in t:
                t["ansible.posix.authorized_key"]["path"] = str(authorized_path)
    return tasks


STATIC_TASKS = ["Authorize the static operator keys"]
IMPORT_TASKS = ["Authorize the shared ids' downloaded keys"]
REVOKE_TASKS = ["Delete the revoked static keys"]
VERIFY_TASKS = [
    "Verify: read the jump account's authorized_keys",
    "Verify: assert every static key is authorized, with its options",
    "Verify: assert the revoked static keys are gone",
]


def run(tmp_path: Path, tasks: list[dict], variables: dict, fail: bool = False) -> str:
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "jump_user": getpass.getuser(),
            "jump_home": str(tmp_path / "home"),
            "jump_public_keys": [],
            "jump_revoked_public_keys": [],
            "jump_ssh_import_ids": [],
            "ssh_key_fetch_keys": {},
            "jump_account": {},
            **variables,
        },
        "tasks": tasks,
    }]))
    config = tmp_path / "ansible.cfg"
    config.write_text(f"[defaults]\nfilter_plugins = {REPO / 'ansible/filter_plugins'}\n")
    env = dict(
        os.environ,
        ANSIBLE_CONFIG=str(config),
        ANSIBLE_STDOUT_CALLBACK="ansible.builtin.default",
        ANSIBLE_NOCOLOR="1",
        ANSIBLE_LOCALHOST_WARNING="False",
        ANSIBLE_INVENTORY_UNPARSED_WARNING="False",
    )
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(play)],
        env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if fail:
        assert result.returncode != 0, output
    else:
        assert result.returncode == 0, output
    return output


@pytest.fixture
def authkeys(tmp_path: Path) -> Path:
    ssh = tmp_path / "home/.ssh"
    ssh.mkdir(parents=True)
    f = ssh / "authorized_keys"
    f.write_text("")
    f.chmod(0o600)
    return f


def converge(tmp_path: Path, f: Path, variables: dict) -> None:
    run(tmp_path, role_tasks("main.yml", STATIC_TASKS + IMPORT_TASKS + REVOKE_TASKS, f), variables)


def test_a_line_with_options_lands_verbatim_and_once(tmp_path, authkeys):
    v = {"jump_public_keys": [PLAIN, LABEL_KEY]}
    converge(tmp_path, authkeys, v)
    assert authkeys.read_text().splitlines() == [PLAIN, LABEL_KEY]
    before = authkeys.stat()
    converge(tmp_path, authkeys, v)
    assert authkeys.read_text().splitlines() == [PLAIN, LABEL_KEY]
    assert authkeys.stat().st_mtime_ns == before.st_mtime_ns


def test_the_dedupe_knows_the_static_key_behind_its_options(tmp_path, authkeys):
    """A GitHub id publishing the same key adds no second, option-less line."""
    v = {
        "jump_public_keys": [LABEL_KEY],
        "jump_ssh_import_ids": ["gh:alice"],
        "ssh_key_fetch_keys": {"gh:alice": [GH_DUP, GH_NEW]},
    }
    converge(tmp_path, authkeys, v)
    assert authkeys.read_text().splitlines() == [LABEL_KEY, f"{GH_NEW} # ssh-import-id gh:alice"]
    converge(tmp_path, authkeys, v)
    assert len(authkeys.read_text().splitlines()) == 2


def test_the_revoked_logic_deletes_a_key_whatever_options_it_carries(tmp_path, authkeys):
    converge(tmp_path, authkeys, {"jump_public_keys": [PLAIN, LABEL_KEY]})
    # Revoked as the bare key (the removal step adds the key to the revoked list).
    converge(tmp_path, authkeys, {"jump_public_keys": [PLAIN], "jump_revoked_public_keys": [GH_DUP + " label-station@tim-yoga"]})
    assert authkeys.read_text().splitlines() == [PLAIN]
    # And a revoked line that itself has options.
    converge(tmp_path, authkeys, {"jump_public_keys": [LABEL_KEY]})
    converge(tmp_path, authkeys, {"jump_public_keys": [], "jump_revoked_public_keys": [LABEL_KEY]})
    assert authkeys.read_text().splitlines() == [PLAIN]


# --- the real verify tasks --------------------------------------------------

def verify(tmp_path: Path, variables: dict, fail: bool = False) -> str:
    return run(tmp_path, role_tasks("verify/main.yml", VERIFY_TASKS), variables, fail=fail)


def test_verify_passes_when_the_line_and_its_options_are_in_the_file(tmp_path, authkeys):
    v = {"jump_public_keys": [PLAIN, LABEL_KEY]}
    converge(tmp_path, authkeys, v)
    verify(tmp_path, v)


def test_verify_fails_when_a_key_is_missing(tmp_path, authkeys):
    converge(tmp_path, authkeys, {"jump_public_keys": [PLAIN]})
    out = verify(tmp_path, {"jump_public_keys": [PLAIN, LABEL_KEY]}, fail=True)
    assert "label-station@tim-yoga" in out, out


def test_verify_fails_when_the_options_are_not_on_the_key_line(tmp_path, authkeys):
    authkeys.write_text(f"{PLAIN}\n{GH_DUP} label-station@tim-yoga\n")
    out = verify(tmp_path, {"jump_public_keys": [LABEL_KEY]}, fail=True)
    assert re.search(r"with the options restrict,port-forwarding", out), out


def test_verify_fails_when_the_options_are_on_another_line(tmp_path, authkeys):
    """The options line holds a different key; the label key has none."""
    authkeys.write_text(f"{OPTIONS} {PLAIN}\n{GH_DUP}\n")
    verify(tmp_path, {"jump_public_keys": [GH_DUP]})
    out = verify(tmp_path, {"jump_public_keys": [LABEL_KEY]}, fail=True)
    assert "with the options" in out, out


def test_verify_fails_on_a_revoked_key_still_present(tmp_path, authkeys):
    authkeys.write_text(LABEL_KEY + "\n")
    out = verify(tmp_path, {"jump_revoked_public_keys": [GH_DUP + " label-station@tim-yoga"]}, fail=True)
    assert "still holds the revoked key" in out, out
