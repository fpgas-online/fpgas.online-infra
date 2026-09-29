"""roles/ssh_key_fetch: the one place the ssh keys trusted from GitHub and
Launchpad are downloaded (roles operators, jump, server_user and fixpi).

Runs the real role with ansible-playbook against a local fake GitHub /
Launchpad (conftest.KeyServer), so no network is needed. What it guards:
every download that yields no key -- a 200 with an empty body, a non-200,
no answer at all -- fails the play AT the download, with a message naming
the account, the id, the URL and the answer; a transient blip is retried
rather than failing; the fetch runs under --check too; and the keys come
from https://github.com/<user>.keys, never the rate-limited GitHub API.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.conftest import closed_port_url

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "ansible/roles"

KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAliceAliceAliceAliceAliceAliceAliceAlice"
KEY_B = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQBobBobBobBobBobBobBob"
LP_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICarlCarlCarlCarlCarlCarlCarlCarlCarlCarl carl@laptop"


def run(tmp_path: Path, keyserver, requests: list[dict], check: bool = False,
        github_url: str | None = None) -> tuple[bool, str, dict]:
    """Run the role once; return (succeeded, output, ssh_key_fetch_keys)."""
    out = tmp_path / "keys.json"
    playbook = tmp_path / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "ssh_key_fetch_github_url": github_url or keyserver.url,
            "ssh_key_fetch_launchpad_url": keyserver.url,
            "ssh_key_fetch_delay": 0,
        },
        "tasks": [
            {"ansible.builtin.include_role": {"name": "ssh_key_fetch"},
             "vars": {"ssh_key_fetch_requests": requests}},
            {"ansible.builtin.copy": {"content": "{{ ssh_key_fetch_keys | to_json }}", "dest": str(out)},
             "check_mode": False},
        ],
    }]))
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\n")
    env = dict(
        os.environ,
        ANSIBLE_CONFIG=str(config),
        ANSIBLE_ROLES_PATH=str(ROLES),
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
    keys = json.loads(out.read_text()) if result.returncode == 0 else {}
    return result.returncode == 0, output, keys


def failed_task(output: str) -> str:
    """The name of the task the play failed at (its first fatal:/failed: line)."""
    fatal = re.search(r"^(fatal|failed): ", output, re.M)
    assert fatal, output
    return re.findall(r"^TASK \[(?:[^:\]]+ : )?([^\]]+)\]", output[:fatal.start()], re.M)[-1]


def test_keys_are_downloaded_per_id(tmp_path, keyserver):
    keyserver.answers["alice"] = (200, KEY_A + "\n" + KEY_B + "\n")
    keyserver.answers["lp:carl"] = (200, LP_KEY + "\n")
    ok, output, keys = run(tmp_path, keyserver, [
        {"account": "tim", "id": "gh:alice"},
        {"account": "pi", "id": "lp:carl"},
        {"account": "pi", "id": "carl"},  # bare = lp:, as for ssh-import-id
    ])
    assert ok, output
    assert keys == {"gh:alice": [KEY_A, KEY_B], "lp:carl": [LP_KEY], "carl": [LP_KEY]}
    # Only the .keys / +sshkeys pages: no API request was made.
    assert set(keyserver.hits) == {"alice", "lp:carl"}


def test_no_ids_is_no_keys(tmp_path, keyserver):
    ok, output, keys = run(tmp_path, keyserver, [])
    assert ok, output
    assert keys == {}


@pytest.mark.parametrize("answer, said", [
    ((200, ""), "answered 200 (OK) with no key in it"),
    ((200, "\n\n"), "answered 200 (OK) with no key in it"),
    ((200, "<html>not keys</html>\n"), "answered 200 (OK) with no key in it"),
    ((404, "Not Found"), "answered 404"),
    ((429, "slow down"), "answered 429"),
    ((503, "try later"), "answered 503"),
])
def test_a_download_with_no_keys_fails_at_the_download(tmp_path, keyserver, answer, said):
    keyserver.answers["alice"] = (200, KEY_A + "\n")
    keyserver.answers["carl"] = answer
    ok, output, _keys = run(tmp_path, keyserver, [
        {"account": "tim", "id": "gh:alice"},
        {"account": "carl", "id": "gh:carl"},
    ])
    assert not ok, output
    assert failed_task(output) == "Fail on the downloads that gave no ssh keys"
    # Named: the account, the id, the URL and the answer.
    assert f"No ssh keys for carl from gh:carl: {keyserver.url}/carl.keys {said}" in output, output
    assert "No ssh keys for tim" not in output
    # Retried before failing (1 try + ssh_key_fetch_retries).
    assert keyserver.hits["carl"] == 4
    assert keyserver.hits["alice"] == 1


def test_no_answer_fails_at_the_download(tmp_path, keyserver):
    url = closed_port_url()
    ok, output, _keys = run(tmp_path, keyserver, [{"account": "tim", "id": "gh:alice"}], github_url=url)
    assert not ok, output
    assert failed_task(output) == "Fail on the downloads that gave no ssh keys"
    assert f"No ssh keys for tim from gh:alice: {url}/alice.keys answered -1" in output, output


def test_a_launchpad_download_with_no_keys_fails(tmp_path, keyserver):
    keyserver.answers["lp:carl"] = (200, "")
    ok, output, _keys = run(tmp_path, keyserver, [{"account": "pi", "id": "carl"}])
    assert not ok, output
    assert f"No ssh keys for pi from carl: {keyserver.url}/~carl/+sshkeys answered 200" in output, output


def test_a_transient_failure_is_retried(tmp_path, keyserver):
    keyserver.answers["alice"] = [(503, "try later"), (200, ""), (200, KEY_A + "\n")]
    ok, output, keys = run(tmp_path, keyserver, [{"account": "tim", "id": "gh:alice"}])
    assert ok, output
    assert keys == {"gh:alice": [KEY_A]}
    assert keyserver.hits["alice"] == 3


@pytest.mark.parametrize("bad", ["gh:", "api:alice", "gh:alice/../x", "gh:alice bob"])
def test_an_unknown_id_form_fails_before_any_download(tmp_path, keyserver, bad):
    ok, output, _keys = run(tmp_path, keyserver, [{"account": "tim", "id": bad}])
    assert not ok, output
    assert failed_task(output) == "Check the ssh ids are gh:<user>, lp:<user> or <user>"
    assert keyserver.hits == {}


def test_check_mode_downloads_and_fails_the_same(tmp_path, keyserver):
    keyserver.answers["alice"] = (200, KEY_A + "\n")
    ok, output, keys = run(tmp_path, keyserver, [{"account": "tim", "id": "gh:alice"}], check=True)
    assert ok, output
    assert keys == {"gh:alice": [KEY_A]}
    keyserver.answers["alice"] = (200, "")
    ok, output, _keys = run(tmp_path, keyserver, [{"account": "tim", "id": "gh:alice"}], check=True)
    assert not ok, output
    assert "No ssh keys for tim from gh:alice" in output


def test_no_role_uses_ssh_import_id_or_the_github_api():
    """ssh-import-id gh: reads api.github.com, which rate-limits shared CI IPs."""
    offenders = []
    for path in (REPO / "ansible").rglob("*"):
        if path.suffix not in (".yml", ".yaml", ".j2", ".sh", ".py") or not path.is_file():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "api.github.com" in line or re.search(r"\b(command|shell|cmd|argv)\b.*ssh-import-id", line):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert offenders == []
