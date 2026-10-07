"""The Pis' IPv6 (Tim's answer welland-1, 2026-10-07): dhcpcd on eth0, IPv6 only.

eth0 carries the NFS root, which the kernel configured over IPv4 (ip=dhcp). These
tests fail if the unit stops being IPv6-only (dhcpcd would then also run DHCPv4 on
the root's interface), lets dhcpcd rewrite resolv.conf or the hostname, stops being
installed and enabled by onpi, or if verify-pi stops checking the result.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UNIT = REPO / "ansible/roles/onpi/files/etc/systemd/system/fpgas-ipv6.service"
TASKS = REPO / "ansible/roles/onpi/tasks/ipv6.yml"
MAIN = REPO / "ansible/roles/onpi/tasks/main.yml"
VERIFY_PI = REPO / "ansible/verify-pi.yml"


def test_the_unit_runs_dhcpcd_for_ipv6_only_on_eth0():
    exec_start = next(l for l in UNIT.read_text().splitlines() if l.startswith("ExecStart="))
    args = exec_start.split()
    assert args[0] == "ExecStart=/usr/sbin/dhcpcd"
    assert "--ipv6only" in args
    assert "--nobackground" in args
    assert args[-1] == "eth0"
    for hook in ("resolv.conf", "hostname"):
        assert f"--nohook {hook}" in exec_start


def test_onpi_installs_and_enables_it():
    tasks = TASKS.read_text()
    assert "name: dhcpcd-base" in tasks
    assert "dest: /etc/systemd/system/fpgas-ipv6.service" in tasks
    assert "name: fpgas-ipv6.service" in tasks and "enabled: true" in tasks
    assert "include_tasks: ipv6.yml" in MAIN.read_text()


def test_verify_pi_checks_the_address_and_the_route():
    text = VERIFY_PI.read_text()
    assert '"ip6_default": run("ip", "-6", "route", "show", "default")' in text
    assert "Assert the per-port IPv6 address and an IPv6 default route" in text
