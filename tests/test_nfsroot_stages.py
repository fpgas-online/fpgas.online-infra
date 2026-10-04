"""nfsroot_stages.py `scheduled`: rebuild from the download once the base is a day old.

GitHub starts this repo's schedules late or drops them, so the choice goes
by the published base stage's build-time label, never by which cron fired.
"""
import calendar
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "ci"))
import nfsroot_stages  # noqa: E402

NOW = calendar.timegm(time.strptime("2026-09-26T08:00:00Z", nfsroot_stages.TIME_FORMAT))


def with_base_labels(monkeypatch, found):
    monkeypatch.setattr(nfsroot_stages, "base_tag", lambda: "ghcr.io/x/nfsroot:base-k")
    monkeypatch.setattr(nfsroot_stages, "image_labels", lambda tag: found)


def test_a_fresh_base_is_only_refreshed(monkeypatch):
    with_base_labels(monkeypatch, {nfsroot_stages.BUILT_LABEL: "2026-09-25T09:00:00Z"})
    age = nfsroot_stages.base_age(NOW)
    assert age == 23 * 3600
    assert not nfsroot_stages.scratch_due(age)


def test_a_day_old_base_is_rebuilt(monkeypatch):
    with_base_labels(monkeypatch, {nfsroot_stages.BUILT_LABEL: "2026-09-25T08:00:00Z"})
    assert nfsroot_stages.scratch_due(nfsroot_stages.base_age(NOW))


def test_an_unknown_age_is_rebuilt(monkeypatch):
    # Missing base (labels None), a base from before the label existed, and
    # a label that does not parse all mean: rebuild from the download.
    for found in (None, {}, {nfsroot_stages.BUILT_LABEL: "yesterday"}):
        with_base_labels(monkeypatch, found)
        assert nfsroot_stages.base_age(NOW) is None
        assert nfsroot_stages.scratch_due(None)


def test_stages_carry_their_build_time(monkeypatch):
    monkeypatch.setattr(nfsroot_stages.nfsroot_inputs, "base_key", lambda: "k")
    built = nfsroot_stages.labels("base")[nfsroot_stages.BUILT_LABEL]
    parsed = calendar.timegm(time.strptime(built, nfsroot_stages.TIME_FORMAT))
    assert abs(parsed - time.time()) < 60
