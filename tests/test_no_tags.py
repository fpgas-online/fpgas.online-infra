"""Deploys and verification run whole playbooks, with no Ansible tags (issue #157).

These tests fail if:
  - any task, role or include under ansible/ carries a tag other than
    `always` (kept for now for the partial-run workarounds it exists for),
  - the VM harness passes --tags or --skip-tags to a playbook,
  - the harness's dry run of roles/server_user stops being a valid playbook
    of exactly that role,
  - a live doc (README, CLAUDE.md, docs/access.md, the runbooks) gives a
    --tags / --skip-tags command.
"""

import os
import re
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ANSIBLE = REPO / "ansible"
HARNESS = REPO / "tests" / "vm" / "run_tests.py"
SERVER_USER_CHECK = REPO / "tests" / "vm" / "server_user_check.yml"


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts Ansible's !vault and other local tags."""


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _tags(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "tags":
                for tag in value if isinstance(value, list) else [value]:
                    yield from (t.strip() for t in str(tag).split(","))
            yield from _tags(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _tags(item)


def test_no_tag_but_always_under_ansible():
    found = {}
    for f in sorted(ANSIBLE.rglob("*.yml")):
        for doc in yaml.load_all(f.read_text(), Loader=_Loader):
            for tag in _tags(doc):
                if tag != "always":
                    found.setdefault(str(f.relative_to(REPO)), set()).add(tag)
    assert not found, f"tags other than always: {found}"


def test_the_harness_passes_no_tags():
    text = HARNESS.read_text()
    assert "--tags" not in text
    assert "--skip-tags" not in text
    assert "skip_tags" not in text


def test_server_user_check_plays_exactly_the_server_user_role():
    plays = yaml.safe_load(SERVER_USER_CHECK.read_text())
    assert len(plays) == 1
    # the same hosts web.yml plays roles/server_user on
    web = yaml.safe_load((ANSIBLE / "web.yml").read_text())
    assert plays[0]["hosts"] == web[0]["hosts"]
    assert plays[0]["roles"] == ["server_user"]
    assert web[0]["roles"][0] == "server_user"
    assert not {"tasks", "pre_tasks", "post_tasks", "tags"} & plays[0].keys()


def test_server_user_check_syntax():
    env = {**os.environ, "ANSIBLE_ROLES_PATH": str(ANSIBLE / "roles")}
    result = subprocess.run(
        ["ansible-playbook", "-i", "tests/inventory/test-hosts", "--syntax-check", str(SERVER_USER_CHECK)],
        cwd=REPO, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_live_docs_give_no_tag_commands():
    live = [REPO / "README.md", REPO / "CLAUDE.md", REPO / "docs" / "access.md",
            *(REPO / "docs" / "superpowers" / "runbooks").glob("*.md")]
    for f in live:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            assert not re.search(r"--(skip-)?tags[ =]\S", line), f"{f.relative_to(REPO)}:{n}: {line}"
