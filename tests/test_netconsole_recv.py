"""The netconsole receiver (roles/netconsole) stores the Pis' kernel logs for the site.

Every Pi's kernel sends its log over UDP to the gateway (netconsole=@/eth-uplink,@10.21.0.1/);
these tests run the receiver's script and fail if:
  - a plain or an extended datagram, or one holding several lines, stops becoming
    "<receive time> <text>" lines (header dropped, text kept, undecodable bytes replaced,
    control bytes escaped so a Pi cannot forge lines),
  - a file stops being <dir>/<sender IPv4>.log, 0644, appended to across datagrams,
  - a flooding sender can grow its file without bound,
  - the role stops being wired into the gateway play, verify-server, the unit loses its
    unprivileged user or hardening, or the one netconsole_dir variable stops reaching both
    the receiver and the site as FPGAS_NETCONSOLE_DIR,
  - the firewall's input chain stops admitting the fleet networks (the receiver needs no rule of its own).
"""

import datetime
import importlib.machinery
import importlib.util
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ANSIBLE = REPO / "ansible"
ROLE = ANSIBLE / "roles/netconsole"
SCRIPT = ROLE / "files/netconsole-recv"

_loader = importlib.machinery.SourceFileLoader("netconsole_recv", str(SCRIPT))
_spec = importlib.util.spec_from_loader("netconsole_recv", _loader)
recv = importlib.util.module_from_spec(_spec)
_loader.exec_module(recv)

STAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}[+-]\d{2}:\d{2}"


def test_a_plain_datagram_is_its_lines():
    assert recv.parse_datagram(b"[   12.5] eth-uplink: link up\n") == ["[   12.5] eth-uplink: link up"]


def test_a_datagram_with_several_lines_gives_each_line_and_skips_blank_ones():
    data = b"first\r\nsecond\n\nthird\n"
    assert recv.parse_datagram(data) == ["first", "second", "third"]


def test_an_extended_datagram_loses_its_header_and_keeps_the_text():
    assert recv.parse_datagram(b"6,416,1758,-;Out of memory: Killed process 42\n") == ["Out of memory: Killed process 42"]


def test_an_extended_datagram_with_a_fragment_marker_and_dictionary_lines():
    data = b"4,417,1760,-,ncfrag=0/32;usb 1-1: reset\n SUBSYSTEM=usb\n DEVICE=c189:0\n"
    assert recv.parse_datagram(data) == ["usb 1-1: reset", " SUBSYSTEM=usb", " DEVICE=c189:0"]


def test_the_text_of_an_extended_message_may_contain_semicolons_and_commas():
    assert recv.parse_datagram(b"6,1,2,c;a;b,c\n") == ["a;b,c"]


def test_plain_text_that_only_looks_a_little_like_a_header_is_kept_whole():
    for text in ("1,2,3;not a header", "6,416,1758,x;nor this", "a,1,2,-;nor this"):
        assert recv.parse_datagram(text.encode()) == [text]


def test_undecodable_bytes_are_replaced_not_fatal():
    assert recv.parse_datagram(b"bad \xff\xfe bytes\n") == ["bad �� bytes"]


def test_control_bytes_are_escaped_so_a_pi_cannot_forge_or_hide_lines():
    assert recv.parse_datagram(b"a\x1b[2Jb\x00c\td") == ["a\\x1b[2Jb\\x00c\td"]


def test_the_stamp_is_iso_8601_with_offset_and_microseconds():
    now = datetime.datetime(2026, 10, 18, 9, 30, 1, 5, tzinfo=datetime.timezone.utc)
    assert re.fullmatch(STAMP, recv.stamp(now))
    assert recv.stamp(now).endswith(("+00:00", "-00:00")) or recv.stamp(now)[-6] in "+-"


def test_append_makes_a_world_readable_file_and_appends(tmp_path):
    old = os.umask(0o022)
    try:
        assert recv.append(str(tmp_path), "10.21.1.1", b"one\n", 1000)
        assert recv.append(str(tmp_path), "10.21.1.1", b"two\n", 1000)
    finally:
        os.umask(old)
    path = tmp_path / "10.21.1.1.log"
    assert path.read_text() == "one\ntwo\n"
    assert path.stat().st_mode & 0o777 == 0o644


def test_append_refuses_a_file_at_its_cap_and_does_not_follow_symlinks(tmp_path):
    assert recv.append(str(tmp_path), "10.21.1.2", b"12345\n", 6)
    assert not recv.append(str(tmp_path), "10.21.1.2", b"more\n", 6)
    assert (tmp_path / "10.21.1.2.log").read_text() == "12345\n"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("x")
    (tmp_path / "10.21.1.3.log").symlink_to(elsewhere)
    with pytest.raises(OSError):
        recv.append(str(tmp_path), "10.21.1.3", b"y\n", 1000)
    assert elsewhere.read_text() == "x"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(path: Path, text: str, timeout: float = 10) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and text in path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} never contained {text!r}: {path.read_text() if path.exists() else 'no file'}")


@pytest.fixture
def receiver(tmp_path):
    port = _free_port()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--address", "127.0.0.1", "--port", str(port), "--dir", str(log_dir),
         "--max-file-bytes", "400"],
        stderr=subprocess.PIPE, text=True)
    assert "listening on 127.0.0.1" in proc.stderr.readline()
    yield port, log_dir
    proc.terminate()
    proc.wait(timeout=10)


def _send(port: int, data: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(data, ("127.0.0.1", port))


def test_the_running_receiver_writes_the_senders_file_with_receive_times(receiver):
    port, log_dir = receiver
    _send(port, b"plain line\nsecond line\n")
    _send(port, b"6,1,2,-;extended line\n")
    text = _wait_for(log_dir / "127.0.0.1.log", "extended line")
    lines = text.splitlines()
    assert len(lines) == 3
    for line, kernel in zip(lines, ["plain line", "second line", "extended line"]):
        assert re.fullmatch(STAMP + " " + re.escape(kernel), line), line
    assert lines[0].split(" ")[0] == lines[1].split(" ")[0], "one datagram, one receive time"


def test_a_flood_stops_at_the_cap_with_one_marker_line_and_the_receiver_survives(receiver):
    port, log_dir = receiver
    for n in range(40):
        _send(port, f"flood line number {n:03d} ................\n".encode())
        time.sleep(0.005)
    log = log_dir / "127.0.0.1.log"
    _wait_for(log, "reached 400 bytes")
    time.sleep(0.3)
    text = log.read_text()
    assert text.count("netconsole-recv: 127.0.0.1.log reached 400 bytes") == 1
    assert len(text) < 400 + 200, "the file grew far past the cap"
    # rotation (a rename) lets it write again
    log.rename(log_dir / "127.0.0.1.log.1")
    _send(port, b"after rotation\n")
    assert "after rotation" in _wait_for(log, "after rotation")


# --- the role's wiring ---------------------------------------------------------------------------


def _yaml(path: Path):
    return yaml.safe_load(path.read_text())


def test_the_role_is_in_the_gateway_play_before_the_web_tier_and_in_verify_server():
    site = _yaml(ANSIBLE / "site.yml")
    play = next(p for p in site if p.get("name") == "Configure the server services")
    roles = [r if isinstance(r, str) else r["role"] for r in play["roles"]]
    assert "netconsole" in roles and roles.index("netconsole") > roles.index("firewall")
    names = [p.get("name") for p in site]
    assert names.index("Configure the server services") < names.index("Import the web tier playbook")
    verify = (ANSIBLE / "verify-server.yml").read_text()
    assert re.search(r"name: netconsole\n\s+tasks_from: verify/main.yml", verify)


def test_the_unit_is_unprivileged_restarted_and_sandboxed():
    unit = (ROLE / "templates/netconsole-recv.service.j2").read_text()
    for needed in ("User={{ netconsole_user }}", "Restart=always", "NoNewPrivileges=true", "ProtectSystem=strict",
                   "ReadWritePaths={{ netconsole_dir }}", "UMask=0022", "CapabilityBoundingSet=\n"):
        assert needed in unit, needed
    assert "--address {{ netconsole_address }} --port {{ netconsole_port }} --dir {{ netconsole_dir }}" in unit
    defaults = _yaml(ROLE / "defaults/main.yml")
    assert defaults["netconsole_port"] == 6666
    assert defaults["netconsole_address"] == "{{ eth_local_address }}"
    assert "netconsole_dir" not in defaults, "netconsole_dir has one definition: the group variable"


def test_logrotate_covers_the_directory_and_runs_as_the_directorys_owner():
    conf = (ROLE / "templates/netconsole.logrotate.j2").read_text()
    assert "{{ netconsole_dir }}/*.log {" in conf
    assert "su {{ netconsole_user }} {{ netconsole_user }}" in conf
    assert "rotate {{ netconsole_logrotate_keep }}" in conf


def test_the_one_directory_variable_reaches_the_receiver_and_the_site():
    group_var = _yaml(ANSIBLE / "inventory/group_vars/all/netconsole.yml")
    assert group_var == {"netconsole_dir": "/var/log/netconsole"}
    link = REPO / "tests/inventory/group_vars/all/netconsole.yml"
    assert link.is_symlink() and link.resolve() == (ANSIBLE / "inventory/group_vars/all/netconsole.yml").resolve()
    tasks = _yaml(ANSIBLE / "roles/site/tasks/django.yml")
    (task,) = [t for t in tasks if "FPGAS_NETCONSOLE_DIR" in str(t)]
    assert task["ansible.builtin.lineinfile"]["line"] == "FPGAS_NETCONSOLE_DIR={{ netconsole_dir }}"
    assert task["ansible.builtin.lineinfile"]["path"] == "/etc/environment"
    assert "Restart django services" in task["notify"]
    for service in ("gunicorn", "daphne", "uvicorn"):
        assert "EnvironmentFile=/etc/environment" in (ANSIBLE / f"roles/site/templates/{service}.service.j2").read_text()


def test_the_firewall_admits_the_fleet_networks_on_input_without_a_netconsole_rule():
    template = (ANSIBLE / "roles/firewall/templates/nftables.conf.j2").read_text()
    for branch in template.split("{% else %}"):
        chain = branch.split("chain input {")[1].split("chain internal_networks")[0]
        assert "jump internal_networks" in chain
    internal = template.split("chain internal_networks {")[1].split("chain custom_rules")[0]
    assert "ip saddr {{ pib_network }}.0.0/16 counter accept" in internal
    assert "6666" not in template and "netconsole" not in template
