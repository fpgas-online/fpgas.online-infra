"""The inventory's revoked static keys (ssh_public_keys_revoked).

roles/jump and roles/operators delete these from the accounts; the VM test
(tests/vm/run_tests.py) seeds them and checks the converge removes them.
These checks keep the list itself sound: every line is a key ssh-keygen
parses, and no revoked key is still granted through ssh_public_keys.
"""

import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SSH_KEYS = REPO / "ansible/inventory/group_vars/all/ssh_keys.yml"


def load() -> dict:
    return yaml.safe_load(SSH_KEYS.read_text())


def test_revoked_keys_parse() -> None:
    revoked = load()["ssh_public_keys_revoked"]
    assert revoked
    out = subprocess.run(["ssh-keygen", "-lf", "-"], input="\n".join(revoked) + "\n",
                         capture_output=True, text=True, check=True)
    assert len(out.stdout.splitlines()) == len(revoked)


def test_revoked_keys_not_granted() -> None:
    data = load()
    granted = {line.split()[1] for entry in data["ssh_public_keys"] for line in entry["keys"]}
    revoked = {line.split()[1] for line in data["ssh_public_keys_revoked"]}
    assert not granted & revoked
