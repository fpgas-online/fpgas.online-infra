"""nfsroot_promote_guard.should_promote: bookworm-armhf never moves backwards.

main's VM test runs overlap, so they can finish in any order; the tag must
still end on the newest build: of the newest commit, and of that commit,
the latest run (its packages are fresher).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "ci"))
from nfsroot_promote_guard import should_promote  # noqa: E402

# main's history, oldest first: a -> b -> c
HISTORY = ["a" * 40, "b" * 40, "c" * 40]
A, B, C = HISTORY


def ancestor(x: str, y: str) -> bool:
    return HISTORY.index(x) <= HISTORY.index(y)


def test_first_promotion():
    assert should_promote((A, "2026-09-27T01:00:00Z"), None, ancestor)[0]


def test_a_newer_commit_is_promoted():
    assert should_promote((C, "2026-09-27T01:00:00Z"), (B, "2026-09-27T02:00:00Z"), ancestor)[0]


def test_an_older_commit_finishing_late_is_not():
    # b's run started first but c's finished and promoted first.
    go, why = should_promote((B, "2026-09-27T01:00:00Z"), (C, "2026-09-27T01:05:00Z"), ancestor)
    assert not go and C[:7] in why


def test_a_later_run_of_the_same_commit_is_promoted():
    # e.g. a scheduled run of the unchanged main head: fresher packages.
    assert should_promote((C, "2026-09-27T05:00:00Z"), (C, "2026-09-27T01:00:00Z"), ancestor)[0]


def test_an_earlier_run_of_the_same_commit_finishing_late_is_not():
    assert not should_promote((C, "2026-09-27T01:00:00Z"), (C, "2026-09-27T05:00:00Z"), ancestor)[0]


def test_an_unknown_promoted_commit_does_not_block():
    # A commit no longer in main's history (never happens without a force
    # push): promote rather than freeze the tag.
    assert should_promote((C, "2026-09-27T01:00:00Z"), ("f" * 40, "2026-09-27T02:00:00Z"),
                          lambda x, y: False)[0]
