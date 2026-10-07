"""verify-pi.yml fails when it selects no Pi, instead of passing by checking nothing.

The production inventory lists no Pis, so `ansible-playbook ansible/verify-pi.yml`
and `... --limit fpgas.online` both select nothing. Ansible reports that as
"skipping: no hosts matched" and exits 0, which read as a passed verification
after the 2026-10-04 deploy. The playbook's first play turns it into an error.
These tests run the real playbook and fail if:
  - a run that selects no Pi exits 0, with or without --limit,
  - a run that selects a Pi is stopped by the guard,
  - the guard play stops being the first play, or is dropped by a --limit.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
VERIFY_PI = REPO / "ansible/verify-pi.yml"
GUARD = "Refuse to verify no Pis"
# TEST-NET-1 (RFC 5737): never routed, and never connected to here either.
PI_A, PI_B = "192.0.2.1", "192.0.2.2"


def _run(*args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "ANSIBLE_NOCOLOR": "1",
        # The repo's ansible.cfg sets become = True for production runs.
        # Tests must never escalate on the machine running them.
        "ANSIBLE_BECOME": "False",
    }
    return subprocess.run(["ansible-playbook", str(VERIFY_PI), *args], cwd=REPO, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True)


def test_the_guard_is_the_first_play_and_survives_any_limit():
    plays = yaml.safe_load(VERIFY_PI.read_text())
    guard = plays[0]
    assert guard["name"] == GUARD
    # `all` keeps the play under a --limit naming any inventory host (a
    # `localhost` play is dropped by such a limit); `localhost` keeps it
    # when the inventory is empty.
    assert guard["hosts"] == "all:localhost"
    assert plays[1]["name"] == "Verify running Pi"


@pytest.mark.parametrize(
    "args",
    [
        # the production inventory: no pi_live group at all
        ["-i", "ansible/inventory"],
        # what the 2026-10-04 deploy ran
        ["-i", "ansible/inventory", "--limit", "fpgas.online"],
        # an inventory with no hosts
        ["-i", ","],
        # the pattern matches a Pi, the limit removes it
        ["-i", f"{PI_A},{PI_B},", "-e", f"verify_pi_hosts={PI_A}", "--limit", PI_B],
    ],
    ids=["production-inventory", "limit-to-server", "empty-inventory", "limit-removes-the-pi"],
)
def test_selecting_no_pi_is_an_error(args):
    r = _run(*args)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "verify-pi.yml selects no hosts" in r.stdout + r.stderr


@pytest.mark.parametrize(
    "args",
    [
        # with the VM test's inventory for its one gateway (the Pi play
        # refuses to run without one: tests/test_verify_pi_operator.py)
        ["-i", "tests/inventory", "-i", f"{PI_A},", "-e", f"verify_pi_hosts={PI_A}"],
        ["-i", "tests/inventory", "-i", f"{PI_A},{PI_B},", "-e", "verify_pi_hosts=192.0.2.*", "--limit", PI_B],
    ],
    ids=["one-pi", "limit-keeps-a-pi"],
)
def test_selecting_a_pi_passes_the_guard(args):
    # The Pi is not there: a local connection with no interpreter makes the
    # verify play's first task fail at once, without any network. What is
    # under test is that the guard passed and the verify play started.
    r = _run(*args, "-e", "ansible_connection=local", "-e", "ansible_python_interpreter=/nonexistent")
    out = r.stdout + r.stderr
    assert "verify-pi.yml selects no hosts" not in out, out
    assert "verify-pi.yml will check: " in r.stdout, out
    assert "PLAY [Verify running Pi]" in r.stdout, out
    assert "TASK [Collect the Pi's state]" in r.stdout, out
