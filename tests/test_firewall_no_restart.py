"""The converge never restarts nftables; rule changes reach the kernel by an atomic reload.

Debian's nftables.service stops with `nft flush ruleset` and starts with
`nft -f /etc/nftables.conf`. With `state: restarted` every converge left the
gateway with no filter and no NAT between the stop and the start. These tests
fail if:
  - a task in the firewall role restarts nftables,
  - writing the rules stops notifying the reload handler, or the handler
    stops being a reload,
  - the template stops beginning with `flush ruleset` (which is what makes the
    reload one atomic transaction).
"""

from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parent.parent / "ansible/roles/firewall"


def _tasks(name: str) -> list[dict]:
    return yaml.safe_load((ROLE / name).read_text())


def test_no_firewall_task_restarts_nftables():
    for task in _tasks("tasks/main.yml") + _tasks("handlers/main.yml"):
        module = task.get("ansible.builtin.systemd") or task.get("ansible.builtin.service") or {}
        assert module.get("state") != "restarted", f"{task['name']} restarts a service"


def test_the_service_is_enabled_and_started():
    (task,) = [t for t in _tasks("tasks/main.yml") if t["name"] == "Enable nftables service"]
    assert task["ansible.builtin.systemd"] == {"service": "nftables.service", "enabled": True, "state": "started"}


def test_writing_the_rules_notifies_an_atomic_reload():
    (write,) = [t for t in _tasks("tasks/main.yml") if t["name"] == "Write nftables rules"]
    assert write["notify"] == "Reload nftables"
    (handler,) = [h for h in _tasks("handlers/main.yml") if h["name"] == "Reload nftables"]
    assert handler["ansible.builtin.systemd"]["state"] == "reloaded"
    first_rule = next(
        line.strip()
        for line in (ROLE / "templates/nftables.conf.j2").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "{#"))
    )
    assert first_rule == "flush ruleset"
