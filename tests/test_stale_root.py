"""Tests for the Pi-side stale-NFS-root auto-reboot (roles/onpi, stale_root).

fpgas-stale-root-check decides, once a minute on every netbooted board,
whether to reboot. A wrong "yes" reboots boards into a half-built root or
takes the fleet down in one go; a wrong "no" leaves boards on dead file
handles (no key-based ssh, no dpkg) until someone PoE-cycles them. So the
decision is tested here against a synthetic lower root rather than only
exercised on the real fleet.

The logic tests run the real scripts under a NON-standalone busybox (the
Debian `busybox` package) so that `date`, `stat`, `who` and `reboot` can be
replaced with fakes on PATH; a standalone build runs its own applets and
ignores PATH. test_static_busybox_smoke runs the production arrangement,
Debian's static busybox, with no fakes at all.

    STALE_ROOT_BUSYBOX=/usr/bin/busybox \
    STALE_ROOT_STATIC_BUSYBOX=path/to/busybox-static/usr/bin/busybox \
    uv run pytest tests/test_stale_root.py
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible/roles/onpi"
CHECK = ROLE / "files/stale-root/fpgas-stale-root-check"
ARM = ROLE / "files/stale-root/fpgas-stale-root-arm"
CONF_TEMPLATE = ROLE / "templates/fpgas-stale-root.conf.j2"

BUSYBOX = os.environ.get("STALE_ROOT_BUSYBOX") or shutil.which("busybox")
STATIC_BUSYBOX = os.environ.get("STALE_ROOT_STATIC_BUSYBOX")

GEN = "/etc/fpgas-online/nfsroot-generation"
LOCK = "/etc/fpgas-online/nfsroot-update.lock"
T0 = 1_790_000_000  # "now" at boot in the fake clock


def _standalone(busybox):
    """True if this busybox runs its own applets in preference to PATH."""
    r = subprocess.run(
        [busybox, "sh", "-c", "head -c0 /dev/null"],
        env={"PATH": "/nonexistent"},
        capture_output=True,
    )
    return r.returncode == 0


needs_busybox = pytest.mark.skipif(
    BUSYBOX is None or _standalone(BUSYBOX),
    reason="needs a non-standalone busybox (Debian's `busybox` package)",
)


def render_config(**overrides):
    """The shipped config template, rendered with the role defaults."""
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = bool
    env.filters["ternary"] = lambda value, a, b: a if value else b
    variables = {"ansible_managed": "test", **defaults}
    # Defaults refer to each other ({{ nfsroot_generation_file }}).
    for _ in range(3):
        variables = {
            k: (
                [env.from_string(i).render(variables) if isinstance(i, str) else i for i in v]
                if isinstance(v, list)
                else env.from_string(v).render(variables)
                if isinstance(v, str)
                else v
            )
            for k, v in variables.items()
        }
    variables.update(overrides)
    return env.from_string(CONF_TEMPLATE.read_text()).render(variables)


class Board:
    """One fake netbooted board: lower root, overlay view, state dir, fakes."""

    def __init__(self, tmp_path, busybox, hostname="pi-sw2-p33", fakes=True):
        self.busybox = busybox
        self.lower = tmp_path / "lower"
        self.root = tmp_path / "root"
        self.state = tmp_path / "run/fpgas-stale-root"
        self.calls = tmp_path / "calls"
        self.kmsg = tmp_path / "kmsg"
        self.hostname = tmp_path / "hostname"
        self.board_inhibit = tmp_path / "run/fpgas-no-auto-reboot"
        self.fakes = fakes
        for d in (self.lower, self.root):
            (d / "etc/fpgas-online").mkdir(parents=True)
            (d / "var/lib/dpkg").mkdir(parents=True)
            (d / "var/lib/dpkg/status").write_text("Package: x\n")
        self.hostname.write_text(hostname + "\n")
        self.calls.write_text("")
        self.kmsg.write_text("")

        sbin = tmp_path / "sbin"
        sbin.mkdir()
        self.systemctl = sbin / "systemctl"
        self.systemd_shutdown = sbin / "systemd-shutdown"
        self._script(self.systemctl, f'echo "systemctl $*" >>"{self.calls}"')
        self._script(self.systemd_shutdown, "exit 0")

        conf = render_config(
            stale_root_board_inhibit=str(self.board_inhibit),
            stale_root_jitter=0,
            stale_root_user_grace=600,
        )
        conf += "\n".join(
            [
                "",
                f"LOWER={self.lower}",
                f"ROOT={self.root}",
                f"HOSTNAME_FILE={self.hostname}",
                f"KMSG={self.kmsg}",
                f"SYSTEMCTL={self.systemctl}",
                f"SYSTEMD_SHUTDOWN={self.systemd_shutdown}",
                "QUIET_DIRS=/var/lib/dpkg",
                "",
            ]
        )
        self.config = tmp_path / "fpgas-stale-root.conf"
        self.config.write_text(conf)
        self.now = T0

    def _script(self, path, body):
        path.write_text(f"#!{self.busybox} sh\n{body}\n")
        path.chmod(0o755)

    def env(self):
        return {"FPGAS_STALE_ROOT_STATE": str(self.state), "PATH": "/nonexistent"}

    def arm(self):
        r = subprocess.run(
            [self.busybox, "sh", str(ARM)],
            env={
                **self.env(),
                "PATH": str(Path(self.busybox).parent) + ":/usr/bin:/bin",
                "FPGAS_STALE_ROOT_BUSYBOX": self.busybox,
                "FPGAS_STALE_ROOT_CHECK": str(CHECK),
                "FPGAS_STALE_ROOT_CONFIG": str(self.config),
            },
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        if self.fakes:
            self._install_fakes()
        return r.stdout

    def _install_fakes(self):
        b = self.state / "bin"
        for name in ("date", "stat", "who", "reboot"):
            (b / name).unlink()
        s = self.state
        self._script(b / "date", f'cat "{s}/fake-now"')
        self._script(
            b / "who",
            f'[ -e "{s}/fake-who" ] && cat "{s}/fake-who"; exit 0',
        )
        self._script(b / "reboot", f'echo "reboot $*" >>"{self.calls}"')
        # ESTALE for any path listed in fake-stale, else the real stat.
        self._script(
            b / "stat",
            f"""for a in "$@"; do
    if grep -qxF -e "$a" "{s}/fake-stale"; then
        echo "stat: can't stat '$a': Stale file handle" >&2
        exit 1
    fi
done
exec "{s}/bin/busybox" stat "$@\"""",
        )
        (s / "fake-stale").write_text("")
        self.set_now(self.now)

    def set_now(self, now):
        self.now = now
        if self.fakes:
            (self.state / "fake-now").write_text(f"{now}\n")

    def check(self):
        r = subprocess.run(
            [str(self.state / "bin/busybox"), "sh", str(self.state / "check")],
            env=self.env(),
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    def slot(self, name):
        r = subprocess.run(
            [str(self.state / "bin/busybox"), "sh", str(self.state / "check"), "slot", name],
            env=self.env(),
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        return int(r.stdout)

    def write_lower(self, rel, content):
        p = self.lower / rel.lstrip("/")
        # Replace, never rewrite in place: same as Ansible's copy and dpkg.
        tmp = p.with_suffix(".tmp")
        tmp.write_text(content)
        tmp.replace(p)
        # QUIET_DIRS mtimes are compared with the fake clock.
        os.utime(p.parent, (self.now, self.now))

    def age_lower_dirs(self, age):
        t = self.now - age
        os.utime(self.lower / "var/lib/dpkg", (t, t))

    def stale(self, *paths):
        """Make stat answer ESTALE. Probe paths are relative to the fake
        overlay root (as the check script sees them); absolute host paths
        (the systemd binaries) are taken as they are."""
        full = [p if p.startswith(str(self.root.parent)) else f"{self.root}{p}" for p in paths]
        (self.state / "fake-stale").write_text("".join(f"{p}\n" for p in full))

    def deadline(self):
        f = self.state / "deadline"
        return int(f.read_text()) if f.exists() else None

    def rebooted(self):
        return [c for c in self.calls.read_text().splitlines() if "reboot" in c]


@pytest.fixture
def board(tmp_path):
    b = Board(tmp_path, BUSYBOX)
    b.write_lower(GEN, f"{T0 - 86400} 2026-09-23T00:00:00Z\n")
    b.arm()
    return b


# --- stagger slots -------------------------------------------------------


@needs_busybox
@pytest.mark.parametrize(
    "name,slot",
    [
        ("pi-sw1-p1", 0),
        ("pi-sw1-p10", 9),
        ("pi-sw2-p33", 48 + 32),
        ("pi-sw2-p08", 48 + 7),  # "08" is bad octal to shell arithmetic
        ("pi14", 14),
        ("pi07", 7),
    ],
)
def test_slot(board, name, slot):
    assert board.slot(name) == slot


@needs_busybox
def test_slot_hash_fallback_is_stable_and_in_range(board):
    a = board.slot("rpi5-netv2pcie-test")
    assert a == board.slot("rpi5-netv2pcie-test")
    assert 0 <= a < 96


@needs_busybox
def test_every_welland_board_gets_its_own_slot(board):
    # The 2026-09-15 enumeration (welland: 7 on sw1, 28 on sw2).
    sw1 = [10, 12, 14, 16, 17, 18, 38]
    sw2 = [3, 4, 5, 6, 7, 8, 16, *range(18, 25), 29, *range(33, 39), 40, 42, 43, 44, 46, 47, 48]
    names = [f"pi-sw1-p{p}" for p in sw1] + [f"pi-sw2-p{p}" for p in sw2]
    slots = [board.slot(n) for n in names]
    assert len(set(slots)) == len(names)


# --- the reboot decision ---------------------------------------------------


@needs_busybox
def test_arm_records_boot_state(board):
    assert (board.state / "boot-generation").read_text().startswith(str(T0 - 86400))
    present = (board.state / "probes-present").read_text().split()
    assert "/var/lib/dpkg/status" in present
    assert "/home/pi/.ssh/authorized_keys" not in present


@needs_busybox
def test_unchanged_root_never_reboots(board):
    for i in range(5):
        board.set_now(T0 + 60 * i)
        board.check()
    assert board.deadline() is None
    assert board.rebooted() == []


@needs_busybox
def test_generation_bump_reboots_at_the_boards_slot(board):
    gen = T0 + 1000
    board.set_now(gen + 30)
    board.write_lower(GEN, f"{gen} 2026-09-24T02:00:00Z\n")
    board.check()
    # base 60 + slot 80 * 20 s, measured from the generation's own timestamp
    # so every board shares the same reference point.
    expected = gen + 60 + 80 * 20
    assert board.deadline() == expected
    assert "generation changed" in board.kmsg.read_text()

    board.set_now(expected - 1)
    board.check()
    assert board.rebooted() == []

    board.set_now(expected)
    board.check()
    assert board.rebooted() == ["systemctl reboot"]
    assert "rebooting: generation changed" in board.kmsg.read_text()


@needs_busybox
def test_two_boards_never_share_a_reboot_second(tmp_path):
    gen = T0 + 1000
    deadlines = []
    for name in ("pi-sw2-p33", "pi-sw2-p34", "pi-sw1-p33"):
        b = Board(tmp_path / name, BUSYBOX, hostname=name)
        b.write_lower(GEN, "old\n")
        b.arm()
        b.set_now(gen + 90)  # each board notices at a different time...
        b.write_lower(GEN, f"{gen} x\n")
        b.check()
        deadlines.append(b.deadline())
    # ...but all count from the generation, so their slots stay 20 s apart.
    deadlines.sort()
    assert all(b - a >= 20 for a, b in zip(deadlines, deadlines[1:]))


@needs_busybox
def test_implausible_generation_time_falls_back_to_local_clock(board):
    # A board whose clock is not synced (or a marker from the future) must not
    # compute a deadline decades away.
    board.set_now(T0 + 100)
    board.write_lower(GEN, "999999999999 x\n")
    board.check()
    assert board.deadline() == T0 + 100 + 60 + 80 * 20


@needs_busybox
def test_update_lock_holds_and_cancels(board):
    board.set_now(T0 + 100)
    board.write_lower(GEN, f"{T0 + 50} x\n")
    board.check()
    assert board.deadline() is not None

    # The next site.yml run starts before this board's slot comes up.
    board.write_lower(LOCK, f"{T0 + 200} 2026-09-24T03:00:00Z\n")
    board.set_now(T0 + 5000)
    out = board.check()
    assert "NFS root update in progress" in out
    assert board.deadline() is None
    assert board.rebooted() == []

    # A second check does not log the same hold again.
    assert board.check() == ""


@needs_busybox
def test_abandoned_update_lock_is_ignored(board):
    board.write_lower(LOCK, f"{T0} x\n")
    board.write_lower(GEN, f"{T0 + 10} x\n")
    board.set_now(T0 + 86400 + 1)
    out = board.check()
    assert "ignoring update lock" in out
    assert board.deadline() is not None


@needs_busybox
def test_unreadable_generation_is_not_a_change(board):
    # e.g. EACCES or EIO on the marker: no evidence the root changed.
    marker = board.lower / GEN.lstrip("/")
    board.set_now(T0 + 7200)
    marker.chmod(0)
    try:
        if os.access(marker, os.R_OK):
            pytest.skip("running as root: cannot make the marker unreadable")
        out = board.check()
    finally:
        marker.chmod(0o644)
    assert "cannot read" in out
    assert board.deadline() is None


@needs_busybox
def test_abandoned_lock_is_logged_once(board):
    board.write_lower(LOCK, f"{T0} x\n")
    board.set_now(T0 + 86400 + 1)
    assert "ignoring update lock" in board.check()
    assert "ignoring update lock" not in board.check()


@needs_busybox
def test_stale_lock_or_inhibit_still_holds(board):
    # A lock the lower answers ESTALE for is not an absent lock.
    board.write_lower(LOCK, f"{T0} x\n")
    board.write_lower(GEN, f"{T0 + 10} x\n")
    board.set_now(T0 + 100)
    lock = str(board.lower / LOCK.lstrip("/"))
    board.stale(lock)
    assert "update in progress" in board.check()
    assert board.deadline() is None

    (board.lower / LOCK.lstrip("/")).unlink()
    inhibit = board.lower / "etc/fpgas-online/no-auto-reboot"
    board.stale(str(inhibit))  # stale, and not even there as a file
    assert "fleet inhibit" in board.check()


@needs_busybox
def test_unparseable_lock_still_holds(board):
    board.write_lower(LOCK, "garbage\n")
    board.write_lower(GEN, f"{T0 + 10} x\n")
    board.set_now(T0 + 999999)
    assert "update in progress" in board.check()
    assert board.deadline() is None


@needs_busybox
def test_fleet_and_board_inhibits_hold(board):
    board.write_lower(GEN, f"{T0 + 10} x\n")
    board.set_now(T0 + 10 + 60 + 80 * 20)  # already past this board's slot

    (board.lower / "etc/fpgas-online/no-auto-reboot").write_text("")
    assert "fleet inhibit" in board.check()
    (board.lower / "etc/fpgas-online/no-auto-reboot").unlink()

    board.board_inhibit.write_text("")
    assert "board inhibit" in board.check()
    board.board_inhibit.unlink()

    board.check()
    assert board.rebooted() == ["systemctl reboot"]


@needs_busybox
def test_estale_needs_confirmation_and_a_quiet_root(board):
    board.set_now(T0 + 7200)
    board.age_lower_dirs(7200)
    board.stale("/var/lib/dpkg/status")

    board.check()
    assert board.deadline() is None  # one sighting is not enough

    board.set_now(T0 + 7260)
    board.check()
    # Confirmed; no generation to share, so the reference is local "now".
    assert board.deadline() == T0 + 7260 + 60 + 80 * 20
    assert "stale files for 2 checks: /var/lib/dpkg/status" in board.kmsg.read_text()


@needs_busybox
def test_estale_while_root_is_still_changing_holds(board):
    # A rebuild run without the update lock (an old branch): files go stale
    # while dpkg is still busy. Rebooting into that root is worse than waiting.
    board.set_now(T0 + 7200)
    board.age_lower_dirs(120)
    board.stale("/var/lib/dpkg/status")
    board.check()
    out = board.check()
    assert "changed 120s ago" in out
    assert board.deadline() is None


@needs_busybox
def test_estale_count_resets_when_probes_recover(board):
    board.set_now(T0 + 7200)
    board.age_lower_dirs(7200)
    board.stale("/var/lib/dpkg/status")
    board.check()
    board.stale()
    board.check()
    board.stale("/var/lib/dpkg/status")
    board.check()
    assert board.deadline() is None


@needs_busybox
def test_vanished_probe_counts_as_stale(board):
    # Measured: a replaced /etc/hostname surfaced as ENOENT, not ESTALE.
    board.set_now(T0 + 7200)
    board.age_lower_dirs(7200)
    (board.root / "var/lib/dpkg/status").unlink()
    board.check()
    board.check()
    assert "/var/lib/dpkg/status(gone)" in board.kmsg.read_text()


@needs_busybox
def test_probe_missing_since_boot_is_not_stale(board):
    board.set_now(T0 + 7200)
    board.age_lower_dirs(7200)
    for _ in range(3):
        board.check()  # /home/pi/.ssh/authorized_keys never existed here
    assert board.deadline() is None


@needs_busybox
def test_logged_in_users_defer_the_reboot_up_to_the_grace(board):
    board.set_now(T0 + 100)
    board.write_lower(GEN, f"{T0 + 100} x\n")
    board.check()
    deadline = board.deadline()
    (board.state / "fake-who").write_text("pi pts/0 2026-09-24 02:00 (10.21.0.1)\n")

    board.set_now(deadline)
    assert "deferring reboot" in board.check()
    assert board.rebooted() == []

    board.set_now(deadline + 600)  # user grace (600 s in this fixture) is up
    board.check()
    assert board.rebooted() == ["systemctl reboot"]


@needs_busybox
def test_stale_systemd_forces_the_reboot(board):
    # systemd-shutdown is what PID 1 execs at the very end of a clean
    # reboot; if it is stale PID 1 freezes, so do not even try.
    board.set_now(T0 + 100)
    board.write_lower(GEN, f"{T0 - 5000} x\n")
    board.stale(str(board.systemd_shutdown))
    board.check()
    assert board.rebooted() == ["reboot -f"]
    assert "clean reboot unavailable" in board.kmsg.read_text()


@needs_busybox
def test_failed_systemctl_falls_back_to_forced_reboot(board):
    board._script(board.systemctl, f'echo "systemctl $*" >>"{board.calls}"; exit 1')
    board.set_now(T0 + 100)
    board.write_lower(GEN, f"{T0 - 5000} x\n")
    board.check()
    assert board.rebooted() == ["systemctl reboot", "reboot -f"]


@needs_busybox
def test_dry_run_only_logs_once(tmp_path):
    b = Board(tmp_path, BUSYBOX)
    b.config.write_text(b.config.read_text() + "DRY_RUN=1\n")
    b.write_lower(GEN, "old\n")
    b.arm()
    b.write_lower(GEN, f"{T0 - 5000} x\n")
    b.check()
    b.check()
    assert b.rebooted() == []
    assert b.kmsg.read_text().count("DRY_RUN: would reboot") == 1


def test_rendered_config_matches_the_server_role():
    """The two roles must agree on where the marker, lock and inhibit live."""
    onpi = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    server = yaml.safe_load(
        (REPO / "ansible/roles/nfsroot-generation/defaults/main.yml").read_text()
    )
    for key in ("nfsroot_generation_file", "nfsroot_update_lock", "nfsroot_fleet_inhibit"):
        assert onpi[key] == server[key], key


# --- the production arrangement ----------------------------------------------


@pytest.mark.skipif(
    not STATIC_BUSYBOX, reason="set STALE_ROOT_STATIC_BUSYBOX to a busybox-static binary"
)
def test_static_busybox_smoke(tmp_path):
    """Arm and check with Debian's static busybox and no fakes: every applet
    resolves inside the binary, and PATH points nowhere."""
    b = Board(tmp_path, STATIC_BUSYBOX, fakes=False)
    b.write_lower(GEN, "old\n")
    armed = b.arm()
    assert "armed: generation 'old', slot 80" in armed
    out = b.check()
    assert out == ""
    assert b.rebooted() == []

    now = int(time.time())
    b.write_lower(GEN, f"{now - 100000} x\n")  # implausible: use local now
    b.config.write_text(b.config.read_text() + "USER_GRACE=0\nBASE_DELAY=0\nSPACING=0\n")
    shutil.copy(b.config, b.state / "config")
    b.check()
    assert b.rebooted() == ["systemctl reboot"]
