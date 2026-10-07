"""verify-pi.yml run by an operator: the Pis by address, the gateway named, reached through it.

On 2026-10-08 a run from an operator machine with `-i <addresses>,` alone
skipped the fleet, password, sunxi and jump checks without a word (they were
gated on an `nbp` group that ad hoc inventory does not have), and a run with
`-e ansible_ssh_common_args="-o ProxyJump=..."` died with "A worker was found
in a dead state": key=value parsing split the value at the space and left a
bare `-o`. These tests run the real playbook (no network: TEST-NET addresses
and a local connection) and fail if:
  - a run with no gateway in its inventory, or with two and none named, or
    with a malformed ansible_ssh_common_args, gets past the Pi play's first
    task,
  - a run with exactly one gateway, or with one named, is stopped there,
  - -e verify_pi_via stops reaching the Pis through that gateway as the
    automation account, with the gateway's own key and ssh settings,
  - any check goes back to "the first host in nbp" instead of the named gateway.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VERIFY_PI = REPO / "ansible/verify-pi.yml"
PI = "192.0.2.1"  # TEST-NET-1 (RFC 5737): never routed
GUARD_TASK = "TASK [Assert the run names the Pis' gateway and sane ssh arguments]"


def _run(*args: str, tmp_path: Path | None = None) -> str:
    env = {**os.environ, "ANSIBLE_NOCOLOR": "1", "ANSIBLE_BECOME": "False"}
    r = subprocess.run(["ansible-playbook", str(VERIFY_PI), *args], cwd=REPO, env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return r.stdout + r.stderr


def _two_gateways(tmp_path: Path) -> Path:
    inv = tmp_path / "hosts"
    inv.write_text("[nbp]\ngw-a ansible_host=192.0.2.10\ngw-b ansible_host=192.0.2.11\n")
    return inv


# A local connection with no interpreter fails the first remote task at
# once: what is under test is whether the run got that far.
LOCAL = ["-e", "ansible_connection=local", "-e", "ansible_python_interpreter=/nonexistent"]
REACHED_THE_PI = "TASK [Collect the Pi's state]"


def test_no_gateway_in_the_inventory_is_refused():
    out = _run("-i", f"{PI},", "-e", "verify_pi_hosts=all", *LOCAL)
    assert "No gateway (group nbp) is in the inventory" in out, out
    assert REACHED_THE_PI not in out, out


def test_two_gateways_and_none_named_is_refused(tmp_path):
    out = _run("-i", str(_two_gateways(tmp_path)), "-i", f"{PI},", "-e", f"verify_pi_hosts={PI}", *LOCAL)
    assert "The inventory has 2 gateways (gw-a, gw-b)" in out, out
    assert REACHED_THE_PI not in out, out


def test_naming_a_gateway_that_is_not_one_is_refused(tmp_path):
    out = _run("-i", str(_two_gateways(tmp_path)), "-i", f"{PI},", "-e", f"verify_pi_hosts={PI}",
               "-e", "verify_pi_via=gw-c", *LOCAL)
    assert "verify_pi_via=gw-c is not one of the gateways (gw-a, gw-b)" in out, out
    assert REACHED_THE_PI not in out, out


@pytest.mark.parametrize("value", ["-o ProxyJump=ansible@gw.example", "-o", "-J"])
def test_a_bare_option_in_the_ssh_args_is_refused(value):
    # As the shell hands it over: key=value parsing keeps only "-o".
    out = _run("-i", "tests/inventory", "-i", f"{PI},", "-e", f"verify_pi_hosts={PI}",
               "-e", f"ansible_ssh_common_args={value}", *LOCAL)
    assert "ends in an option with no value" in out, out
    assert "dead state" not in out.replace('"A worker was found in a dead state"', ""), out
    assert REACHED_THE_PI not in out, out


def test_one_gateway_needs_no_name():
    out = _run("-i", "tests/inventory", "-i", f"{PI},", "-e", f"verify_pi_hosts={PI}", *LOCAL)
    assert GUARD_TASK in out and REACHED_THE_PI in out, out


def test_a_named_gateway_passes(tmp_path):
    out = _run("-i", str(_two_gateways(tmp_path)), "-i", f"{PI},", "-e", f"verify_pi_hosts={PI}",
               "-e", "verify_pi_via=gw-b", *LOCAL)
    assert REACHED_THE_PI in out, out


def test_via_reaches_the_pi_through_the_gateway_as_the_automation_account():
    # -vvv prints the ssh command ansible would run for the Pi; the VM
    # test's gateway is 127.0.0.1:2222 as debian, with the harness's key.
    out = _run("-i", "tests/inventory", "-i", f"{PI},", "-e", f"verify_pi_hosts={PI}",
               "-e", "verify_pi_via=test-vm", "-e", "ansible_python_interpreter=/nonexistent", "-vvv")
    exec_lines = [line for line in out.splitlines() if f"<{PI}> SSH: EXEC" in line]
    assert exec_lines, out
    line = exec_lines[0]
    assert "User=\"ansible\"" in line or "User=ansible" in line, line
    assert "tests/inventory/../vm/workdir/test_key" in line and "None/" not in line, line
    assert "ProxyCommand=ssh -F ansible/ssh.cfg -i " in line, line
    assert "-p 2222 -W %h:%p debian@127.0.0.1" in line, line


def test_no_check_uses_the_first_gateway_in_the_inventory():
    assert "groups['nbp'][0]" not in VERIFY_PI.read_text()
