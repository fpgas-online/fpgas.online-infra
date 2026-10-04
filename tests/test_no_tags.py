"""Deploys and verification run whole playbooks, with no Ansible tags (issue #157).

These tests fail if:
  - any play, task, role or include under ansible/ carries a tag, `always`
    included,
  - the VM harness passes --tags or --skip-tags to a playbook,
  - the harness's dry run of roles/server_user stops being a playbook of
    exactly that role (the VM test runs it; the pytest job has no ansible
    collections for a syntax-check),
  - a live doc (README, CLAUDE.md, docs/access.md, the runbooks) gives a
    --tags / --skip-tags command.
"""

import re
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


def _ansible_yaml():
    return sorted(p for pattern in ("*.yml", "*.yaml") for p in ANSIBLE.rglob(pattern))


def test_no_tags_under_ansible():
    found = {}
    for f in _ansible_yaml():
        for doc in yaml.load_all(f.read_text(), Loader=_Loader):
            for tag in _tags(doc):
                found.setdefault(str(f.relative_to(REPO)), set()).add(tag)
    assert not found, f"tags: {found}"


def test_no_tags_key_in_any_ansible_yaml_line():
    # Belt and braces for a file the YAML scan cannot see into (a Jinja
    # template, a key the loader drops): no uncommented line has a tags key,
    # block (`tags:`, `- tags:`) or flow (`{role: x, tags: y}`).
    for f in _ansible_yaml():
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert not re.search(r"(^\s*(- )?|[{,]\s*)tags\s*:", line), \
                f"{f.relative_to(REPO)}:{n}: {line}"


def test_the_tag_scan_sees_ansible_files():
    files = _ansible_yaml()
    assert ANSIBLE / "site.yml" in files
    assert len(files) > 50


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


def test_live_docs_give_no_tag_commands():
    live = [REPO / "README.md", REPO / "CLAUDE.md", REPO / "docs" / "access.md",
            *(REPO / "docs" / "superpowers" / "runbooks").glob("*.md")]
    for f in live:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            assert not re.search(r"--(skip-)?tags[ =]\S", line), f"{f.relative_to(REPO)}:{n}: {line}"
