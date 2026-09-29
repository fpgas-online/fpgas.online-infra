"""fixpi's site layer never reaches the public CI image (issue #157).

The site layer is the pi user's password, the NFS root's authorized_keys and
the pi/root keypairs (fixpi's userconf.yml, ansible-home.yml and the
authorized_keys.yml / github_keys.yml they include). The CI image build
(ci-nfsroot.yml) publishes the root to GHCR for every site, so it must never
carry any of it; each gateway's site.yml applies its own after extraction.

Until #157 that was `--skip-tags pipw,keys` in the build workflow. Now the
build runs in full and fixpi gates the site layer on fixpi_image_build,
which only inventory-ci-nfsroot sets. These tests fail if:
  - any fixpi task that writes the password or a key can run while
    fixpi_image_build is true (walking every include from tasks/main.yml,
    so a secret-writing task added to an ungated file is caught too),
  - the site-layer includes, run as main.yml writes them, are not skipped
    with fixpi_image_build true (or are skipped with it false),
  - the gate stops being false for the image build, or stops being true
    for a gateway (the role default),
  - the image build stops setting fixpi_image_build, or stops refusing to
    run without it, or stops checking the built image for the site layer,
  - anything still runs the build with --skip-tags, or tags a task pipw/keys.
"""

import os
import re
import subprocess
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ANSIBLE = REPO / "ansible"
FIXPI_TASKS = ANSIBLE / "roles/fixpi/tasks"

GATE = "not (fixpi_image_build | bool)"

# The files that make up the site layer: each must be reachable from
# tasks/main.yml only through the gate.
SITE_LAYER_FILES = {"userconf.yml", "ansible-home.yml", "authorized_keys.yml", "github_keys.yml"}

# What a task that writes the password or a key looks like: the plaintext
# password, the shadow file, a keypair generator, an authorized_keys file,
# the GitHub key download. Matched against the task's parsed YAML, so a
# comment that mentions one does not count.
SECRET = re.compile(
    r"pi_pw|/etc/shadow|openssh_keypair|generate_ssh_key|authorized_keys|ssh_key_fetch|fixpi_github_key"
)

INCLUDE_KEYS = (
    "include_tasks", "ansible.builtin.include_tasks",
    "import_tasks", "ansible.builtin.import_tasks",
)


def _whens(task: dict) -> list[str]:
    when = task.get("when", [])
    return [str(c) for c in when] if isinstance(when, list) else [str(when)]


def _include_file(task: dict) -> str | None:
    for key in INCLUDE_KEYS:
        if key in task:
            value = task[key]
            return value["file"] if isinstance(value, dict) else value
    return None


def fixpi_tasks(path: Path = FIXPI_TASKS / "main.yml", whens: tuple = (), via: tuple = ()):
    """Yield (task, conditions it runs under, include chain) for every task
    fixpi's tasks/main.yml runs, following include_tasks/import_tasks and
    blocks. An include's own task is yielded too (it is where a gate sits)."""
    yield from _walk(yaml.safe_load(path.read_text()) or [], whens, via + (path.name,))


def _walk(tasks: list, whens: tuple, chain: tuple):
    for task in tasks:
        conds = whens + tuple(_whens(task))
        yield task, conds, chain
        for section in ("block", "rescue", "always"):
            yield from _walk(task.get(section, []), conds, chain)
        included = _include_file(task)
        if included:
            yield from fixpi_tasks(FIXPI_TASKS / included, conds, chain)


def gated(conds) -> bool:
    return any(" ".join(c.split()) == GATE for c in conds)


def test_the_walk_sees_the_whole_site_layer():
    """Guard the walker itself: it must reach every site-layer file and the
    tasks that write the password and the keys."""
    reached = {chain[-1] for _, _, chain in fixpi_tasks()}
    assert SITE_LAYER_FILES <= reached, reached
    names = {task.get("name") for task, _, _ in fixpi_tasks()}
    for expected in (
        "Set the pi user's password in the NFS root",
        "Generate ssh keys for pi users pi and root",
        "Write the NFS root's authorized_keys (only when the key list changed)",
        "Download the operators' GitHub keys",
        "Create the ansible user's .ssh dir in the NFS root",
    ):
        assert expected in names, expected


def test_no_task_that_writes_a_secret_runs_in_the_image_build():
    """Every fixpi task that touches the password or a key is behind the gate."""
    leaks = [
        f"{' -> '.join(chain)}: {task.get('name')}"
        for task, conds, chain in fixpi_tasks()
        if SECRET.search(yaml.safe_dump(task)) and not gated(conds)
    ]
    assert not leaks, leaks


def test_the_site_layer_files_run_only_behind_the_gate():
    """Not one task of userconf.yml etc. runs in the image build: the whole
    files were skipped under --skip-tags pipw,keys, so the image is unchanged."""
    ungated = [
        f"{' -> '.join(chain)}: {task.get('name')}"
        for task, conds, chain in fixpi_tasks()
        if chain[-1] in SITE_LAYER_FILES and not gated(conds)
    ]
    assert not ungated, ungated


@pytest.mark.parametrize("value, runs", [
    (True, False), ("true", False), ("True", False), ("yes", False),
    (False, True), ("false", True), ("no", True),
])
def test_the_gate(value, runs):
    """The gate, as Ansible's jinja evaluates it: off for the image build."""
    env = jinja2.Environment()
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "yes", "1", "on", "y")
    assert env.compile_expression(GATE)(fixpi_image_build=value) is runs


def test_a_gateway_applies_the_site_layer_by_default():
    defaults = yaml.safe_load((ANSIBLE / "roles/fixpi/defaults/main.yml").read_text())
    assert defaults["fixpi_image_build"] is False
    for inventory in (ANSIBLE / "inventory", REPO / "tests/inventory"):
        for f in inventory.rglob("*.yml"):
            assert "fixpi_image_build" not in f.read_text(), f


def test_the_image_build_sets_the_gate_and_refuses_to_run_without_it():
    ci_vars = yaml.safe_load((ANSIBLE / "inventory-ci-nfsroot/group_vars/all/all.yml").read_text())
    assert ci_vars["fixpi_image_build"] is True
    plays = yaml.safe_load((ANSIBLE / "ci-nfsroot.yml").read_text())
    first = plays[0]["tasks"][0]
    assert "ansible.builtin.assert" in first, first
    assert "fixpi_image_build" in first["ansible.builtin.assert"]["that"]


def test_the_image_build_checks_the_image_for_the_site_layer():
    """Belt and braces: the build fails if the image carries the site layer
    all the same, whatever the gate did."""
    names = [t.get("name") for t in yaml.safe_load((ANSIBLE / "ci-nfsroot.yml").read_text())[-1]["tasks"]]
    for expected in (
        "Assert the image has no password for the pi user",
        "Assert the image carries no authorized_keys or user keypairs",
        "Check the image has no userconf.txt",
    ):
        assert expected in names, expected


def test_nothing_uses_the_pipw_or_keys_tags():
    """The gate replaced them: no task carries them, no build skips them."""
    for f in ANSIBLE.rglob("*.yml"):
        for line in f.read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert not re.search(r"tags:.*\b(pipw|keys)\b", line), f"{f}: {line}"
            assert not re.fullmatch(r"\s*- (pipw|keys)\s*", line), f"{f}: {line}"
    workflow = (REPO / ".github/workflows/nfsroot-build.yml").read_text()
    assert "--skip-tags" not in workflow
    assert "--tags" not in workflow


def _run_site_layer_includes(tmp_path: Path, image_build: bool) -> tuple[int, str]:
    """Run fixpi/tasks/main.yml's site-layer includes, exactly as written
    there (gate included), against an empty scratch root."""
    entries = []
    for task in yaml.safe_load((FIXPI_TASKS / "main.yml").read_text()):
        included = _include_file(task)
        if included in SITE_LAYER_FILES:
            entry = dict(task)
            entry["ansible.builtin.include_tasks"] = str(FIXPI_TASKS / included)
            entries.append(entry)
    assert [e["name"] for e in entries] == [
        "Prepare the ansible user's home for its key",
        "Set the pi user's password and console banner",
    ]
    root = tmp_path / "nfs"
    root.mkdir()
    playbook = tmp_path / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"fixpi_image_build": image_build, "nfs_root": str(root), "user": "pi",
                 "pi_pw": "not-a-real-password", "fixpi_ansible_user": "ansible",
                 "fixpi_ansible_uid": 1001},
        "tasks": entries,
    }]))
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    env = dict(os.environ, ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg"),
               ANSIBLE_STDOUT_CALLBACK="ansible.builtin.default", ANSIBLE_NOCOLOR="1",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"),
               ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
    result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(playbook)], env=env,
                            cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert list(root.iterdir()) == [], "the site layer wrote into the root"
    return result.returncode, result.stdout + result.stderr


def test_the_image_build_skips_the_site_layer_at_run_time(tmp_path):
    rc, output = _run_site_layer_includes(tmp_path, image_build=True)
    assert rc == 0, output
    assert output.count("skipping: [localhost]") == 2, output
    assert "TASK [Check the NFS root has the ansible user" not in output


def test_a_gateway_runs_the_site_layer(tmp_path):
    """The control: with the gate off the site layer runs. Its first task
    fails on the empty scratch root, before anything is written."""
    rc, output = _run_site_layer_includes(tmp_path, image_build=False)
    assert rc != 0, output
    assert "TASK [Check the NFS root has the ansible user (built into the image)]" in output
