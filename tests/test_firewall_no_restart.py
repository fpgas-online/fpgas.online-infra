"""The converge never restarts nftables; changed rules are validated, then loaded by an atomic reload at once.

Debian's nftables.service stops with `nft flush ruleset` and starts with
`nft -f /etc/nftables.conf`. With `state: restarted` every converge left the
gateway with no filter and no NAT between the stop and the start. These tests
fail if:
  - a task in the firewall role restarts a service, or the role grows a
    handler again (a handler is skipped when a later role fails, and the next
    converge, finding the file unchanged, would never load it),
  - the rules write stops validating the file with `nft -c -f`,
  - the service stops being enabled and started,
  - the reload stops following the write directly, on its change,
  - the template stops beginning with `flush ruleset` (which is what makes the
    reload one atomic transaction).
"""

from pathlib import Path

import yaml

ROLE = Path(__file__).resolve().parent.parent / "ansible/roles/firewall"


def _tasks() -> list[dict]:
    return yaml.safe_load((ROLE / "tasks/main.yml").read_text())


def _named(name: str) -> dict:
    (task,) = [t for t in _tasks() if t["name"] == name]
    return task


def test_no_firewall_task_restarts_a_service_and_there_are_no_handlers():
    for task in _tasks():
        module = task.get("ansible.builtin.systemd") or task.get("ansible.builtin.service") or {}
        assert module.get("state") != "restarted", f"{task['name']} restarts a service"
    assert not (ROLE / "handlers").exists(), "the firewall role has handlers again: load rules in a task"


def test_the_rules_write_is_validated_and_registered():
    write = _named("Write nftables rules")
    assert write["ansible.builtin.template"]["validate"] == "nft -c -f %s"
    assert write["register"] == "firewall_rules"
    assert "notify" not in write


def test_the_service_is_enabled_and_started():
    task = _named("Enable nftables service")
    assert task["ansible.builtin.systemd"] == {"service": "nftables.service", "enabled": True, "state": "started"}


def test_changed_rules_are_reloaded_straight_after_the_write():
    names = [t["name"] for t in _tasks()]
    write, start, load = (names.index(n) for n in
                          ("Write nftables rules", "Enable nftables service", "Load changed nftables rules"))
    assert write < start < load == start + 1
    task = _named("Load changed nftables rules")
    assert task["ansible.builtin.systemd"] == {"service": "nftables.service", "state": "reloaded"}
    assert task["when"] == "firewall_rules is changed"


def test_the_template_begins_with_flush_ruleset():
    first_rule = next(
        line.strip()
        for line in (ROLE / "templates/nftables.conf.j2").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "{#"))
    )
    assert first_rule == "flush ruleset"
