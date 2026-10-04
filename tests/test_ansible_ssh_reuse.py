"""Ansible reuses one ssh connection per host.

Ansible adds a ControlPath to its ssh command only when it sees ControlPersist
in `ssh_args`. With ControlPersist only inside the `-F ansible/ssh.cfg` file,
there was no ControlPath and every task and loop item opened a new connection:
unnoticed next to the gateway, about 10 s an item from outside the site. This
test fails if `ssh_args` loses ControlPersist, the `-F` file, or gains a
ControlPath of its own (Ansible's must be the only one).
"""

import configparser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_ssh_args_let_ansible_reuse_its_connection():
    cfg = configparser.ConfigParser()
    cfg.read(REPO / "ansible.cfg")
    ssh_args = cfg["ssh_connection"]["ssh_args"]
    assert "-F ansible/ssh.cfg" in ssh_args
    assert "ControlPersist=" in ssh_args, "without ControlPersist in ssh_args Ansible adds no ControlPath"
    assert "ControlMaster=auto" in ssh_args
    assert "ControlPath" not in ssh_args, "leave the ControlPath to Ansible"
