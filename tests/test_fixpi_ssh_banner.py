"""The boards' sshd shows the login name and password before the password prompt (#215).

The board pages say "password is in login banner". fixpi's site layer
writes a plain-text banner into the NFS root and an sshd drop-in that
names it. These tests fail if:
  - the banner stops carrying `user: <user>` and `password: <pi_pw>` lines,
    or carries a literal password instead of the site's variable,
  - the banner stops parsing the way its consumer parses it,
  - the drop-in stops being valid sshd configuration that sets `Banner` to
    the file the task writes,
  - the tasks leave the site layer (the published image must not carry a
    site's password; tests/test_fixpi_site_layer.py guards the gate),
  - a real sshd given the rendered banner does not show it to the probe
    verify-pi.yml uses, before authentication,
  - verify-pi.yml stops checking a booted board for it.
"""

import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXPI = REPO / "ansible/roles/fixpi"
BANNER_TEMPLATE = FIXPI / "templates/etc/ssh/sshd_banner.j2"
DROPIN = FIXPI / "files/etc/ssh/sshd_config.d/banner.conf"
USERCONF = FIXPI / "tasks/userconf.yml"
VERIFY_PI = REPO / "ansible/verify-pi.yml"
SSHD = Path("/usr/sbin/sshd")

PASSWORD = "not-the-real-one"
# The probe verify-pi.yml's collector runs on the board (plus a port here).
PROBE_OPTIONS = ["-o", "PreferredAuthentications=none", "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]

# The consumer: fpgas-online/fpgas.online-e2e-tests, e2e/sshbanner.py,
# password_from_banner(). Its direct-ssh test reads the password out of the
# banner with this expression (copied from there; first match wins, and the
# password must end its line). If the banner's wording changes, change it so
# that this still finds the password, or change the consumer first.
E2E_PASSWORD = re.compile(
    r"password(?:\s+is|:)?\s*[\"']?(?P<pw>[^\"'\r\n]+?)[\"']?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def banner(password: str = PASSWORD) -> str:
    env = jinja2.Environment(trim_blocks=True, keep_trailing_newline=True, undefined=jinja2.StrictUndefined)
    return env.from_string(BANNER_TEMPLATE.read_text()).render(user="pi", pi_pw=password)


def test_the_banner_names_the_user_and_the_sites_password():
    lines = banner().splitlines()
    assert "user: pi" in lines
    assert f"password: {PASSWORD}" in lines
    # From the site's variables, never a literal.
    template = BANNER_TEMPLATE.read_text()
    assert "{{ pi_pw }}" in template and "{{ user }}" in template


def test_the_banner_is_plain_text():
    """Not the console banner's getty escapes: sshd sends the file as it is."""
    text = banner()
    assert "\\" not in text
    assert text.endswith("\n") and text.isascii()


@pytest.mark.parametrize("password", [PASSWORD, "fpgas-test-pi", "with spaces in it", "p@ss:w0rd/+="])
def test_the_banner_parses_the_way_the_e2e_suite_reads_it(password):
    match = E2E_PASSWORD.search(banner(password))
    assert match is not None
    assert match.group("pw").strip() == password


def test_the_dropin_points_sshd_at_the_file_the_task_writes():
    settings = [line.split() for line in DROPIN.read_text().splitlines() if line.strip() and not line.startswith("#")]
    assert settings == [["Banner", "/etc/ssh/sshd_banner"]]
    tasks = {t["name"]: t for t in yaml.safe_load(USERCONF.read_text())}
    template = tasks["Ssh login banner with the user and password"]["ansible.builtin.template"]
    assert template["src"] == "etc/ssh/sshd_banner.j2"
    assert template["dest"] == "{{ nfs_root }}/root/etc/ssh/sshd_banner"
    copy = tasks["Sshd shows the login banner"]["ansible.builtin.copy"]
    assert copy["src"] == "files/etc/ssh/sshd_config.d/banner.conf"
    # .conf, in the directory Debian's sshd_config Includes.
    assert copy["dest"] == "{{ nfs_root }}/root/etc/ssh/sshd_config.d/banner.conf"


def test_verify_pi_checks_a_booted_board():
    text = VERIFY_PI.read_text()
    for option in PROBE_OPTIONS[1::2]:
        assert f'"{option}"' in text, option
    assert "Assert sshd shows a login banner with the user and a password before authentication" in text
    assert "Assert the banner's password is the site's pi password" in text


needs_sshd = pytest.mark.skipif(not SSHD.exists() or not shutil.which("ssh-keygen"),
                                reason="needs /usr/sbin/sshd (openssh-server)")


def _sshd_config(tmp_path: Path, banner_path: str, port: int | None = None) -> Path:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "host_key")], check=True)
    dropins = tmp_path / "sshd_config.d"
    dropins.mkdir()
    (dropins / "banner.conf").write_text(DROPIN.read_text().replace("/etc/ssh/sshd_banner", banner_path))
    config = tmp_path / "sshd_config"
    config.write_text(
        f"Include {dropins}/*.conf\n"
        f"HostKey {tmp_path / 'host_key'}\nPidFile {tmp_path / 'sshd.pid'}\n"
        "UsePAM no\nPasswordAuthentication yes\nListenAddress 127.0.0.1\n"
        + (f"Port {port}\n" if port else ""))
    return config


@needs_sshd
def test_sshd_accepts_the_dropin_as_it_is(tmp_path):
    """The file exactly as shipped, through an Include like Debian's."""
    config = _sshd_config(tmp_path, "/etc/ssh/sshd_banner")
    out = subprocess.run([str(SSHD), "-T", "-f", str(config)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "banner /etc/ssh/sshd_banner" in out.stdout.splitlines()


@needs_sshd
def test_a_real_sshd_shows_the_banner_before_authentication(tmp_path):
    (tmp_path / "banner").write_text(banner())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = _sshd_config(tmp_path, str(tmp_path / "banner"), port)
    sshd = subprocess.Popen([str(SSHD), "-D", "-e", "-f", str(config)], stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert sshd.poll() is None, sshd.stderr.read()
                assert time.monotonic() < deadline, "sshd did not start listening"
                time.sleep(0.1)
        probe = subprocess.run(["ssh", "-p", str(port), *PROBE_OPTIONS, "-o", "ConnectTimeout=30",
                                "-F", "/dev/null", "pi@127.0.0.1", "true"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    finally:
        sshd.terminate()
        sshd.wait(timeout=10)
    # Refused, after the banner: what verify-pi.yml asserts on a board.
    assert probe.returncode == 255
    assert "Permission denied" in probe.stderr
    assert "user: pi" in probe.stderr.splitlines()
    assert E2E_PASSWORD.search(probe.stderr).group("pw").strip() == PASSWORD
