"""The Pi root's time zone is UTC, as the gateway's (fpgas.online-infra #288): fixpi sets it in the root, and
verify-pi checks it on every board. Raspberry Pi OS ships Europe/London."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def test_fixpi_sets_the_roots_zone_to_utc():
    tasks = yaml.safe_load((REPO / "ansible/roles/fixpi/tasks/tweeks.yml").read_text())
    by_name = {t.get("name"): t for t in tasks}
    zone = by_name["Set the root's time zone to UTC"]["ansible.builtin.copy"]
    assert zone["content"] == "Etc/UTC\n" and zone["dest"] == "{{ nfs_root }}/root/etc/timezone"
    link = by_name["Point the root's localtime at UTC"]["ansible.builtin.file"]
    assert link["src"] == "/usr/share/zoneinfo/Etc/UTC" and link["state"] == "link"
    assert link["dest"] == "{{ nfs_root }}/root/etc/localtime"


def test_verify_pi_checks_the_zone():
    text = (REPO / "ansible/verify-pi.yml").read_text()
    assert "- name: Assert the Pi's time zone is UTC\n" in text
    assert "verify_pi_timezone.localtime == '/usr/share/zoneinfo/Etc/UTC'" in text
    assert "verify_pi_timezone.date == 'UTC'" in text
