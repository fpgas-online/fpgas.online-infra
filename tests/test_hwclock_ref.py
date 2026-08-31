"""Unit tests for the netboot time reference publisher.

(ansible/roles/timesync/files/fpgas-hwclock-ref)
"""
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

from importlib.machinery import SourceFileLoader

MOD = (pathlib.Path(__file__).resolve().parents[1]
       / "ansible" / "roles" / "timesync" / "files" / "fpgas-hwclock-ref")
# No .py suffix: it installs as a plain executable, so name the loader explicitly.
spec = importlib.util.spec_from_loader("fpgas_hwclock_ref", SourceFileLoader("fpgas_hwclock_ref", str(MOD)))
hwclock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hwclock)

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


def synced(stratum=3):
    return {"Reference ID": "2D4DE87A (45.77.232.122)", "Stratum": str(stratum),
            "Leap status": "Normal"}


# --- clock_is_publishable ---------------------------------------------------

def test_publishes_when_disciplined_by_an_upstream():
    assert hwclock.clock_is_publishable(NOW, None, synced()) is True


def test_refuses_when_chronyd_is_not_answering():
    assert hwclock.clock_is_publishable(NOW, None, {}) is False


def test_refuses_while_not_yet_synchronised():
    tracking = synced() | {"Leap status": "Not synchronised"}
    assert hwclock.clock_is_publishable(NOW, None, tracking) is False


def test_refuses_the_local_stratum_fallback():
    # `local stratum 10` keeps chronyd serving the Pi LAN with no upstream; it
    # must not carve that self-asserted clock into the boot-time reference.
    assert hwclock.clock_is_publishable(NOW, None, synced(stratum=10)) is False


def test_refuses_unparsable_stratum():
    assert hwclock.clock_is_publishable(NOW, None, {"Leap status": "Normal"}) is False


def test_publishes_backwards_rather_than_deadlocking():
    # A monotonicity rule would make one bogus future timestamp permanent.
    future = NOW + timedelta(days=400)
    assert hwclock.clock_is_publishable(NOW, future, synced()) is True


# --- publish / read_published ----------------------------------------------

def test_publish_writes_fake_hwclock_format(tmp_path):
    target = tmp_path / "fake-hwclock.data"
    hwclock.publish(target, NOW)
    assert target.read_text() == "2026-08-31 12:00:00\n"


def test_publish_leaves_no_temp_files(tmp_path):
    # A Pi may read this over NFS at any instant, so the write is a rename;
    # stray temp files would mean the rename path changed.
    target = tmp_path / "fake-hwclock.data"
    hwclock.publish(target, NOW)
    assert [p.name for p in tmp_path.iterdir()] == ["fake-hwclock.data"]


def test_publish_creates_missing_parents(tmp_path):
    target = tmp_path / "root" / "etc" / "fake-hwclock.data"
    hwclock.publish(target, NOW)
    assert target.exists()


def test_read_published_round_trips(tmp_path):
    target = tmp_path / "fake-hwclock.data"
    hwclock.publish(target, NOW)
    assert hwclock.read_published(target) == NOW


def test_read_published_missing_file(tmp_path):
    assert hwclock.read_published(tmp_path / "absent") is None


def test_read_published_rejects_garbage(tmp_path):
    target = tmp_path / "fake-hwclock.data"
    target.write_text("not a timestamp\n")
    assert hwclock.read_published(target) is None
