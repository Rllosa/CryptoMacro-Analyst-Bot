"""
Unit tests for PersistenceTracker (alerts/persistence.py).

Pure in-memory logic — zero async, zero mocks.
"""

from __future__ import annotations

from alerts.persistence import PersistenceTracker

# ---------------------------------------------------------------------------
# record_met tests
# ---------------------------------------------------------------------------


def test_record_met_starts_at_one() -> None:
    tracker = PersistenceTracker()
    count = tracker.record_met("VOL_EXPANSION:BTCUSDT:up")
    assert count == 1


def test_record_met_increments_consecutively() -> None:
    tracker = PersistenceTracker()
    tracker.record_met("k")
    count = tracker.record_met("k")
    assert count == 2


def test_record_met_three_times() -> None:
    tracker = PersistenceTracker()
    tracker.record_met("k")
    tracker.record_met("k")
    count = tracker.record_met("k")
    assert count == 3


# ---------------------------------------------------------------------------
# record_not_met tests
# ---------------------------------------------------------------------------


def test_record_not_met_resets_to_zero() -> None:
    tracker = PersistenceTracker()
    tracker.record_met("k")
    tracker.record_met("k")
    tracker.record_not_met("k")
    assert tracker.get("k") == 0


def test_record_not_met_on_unseen_key_is_zero() -> None:
    tracker = PersistenceTracker()
    tracker.record_not_met("never_seen")
    assert tracker.get("never_seen") == 0


def test_record_met_after_reset_restarts_from_one() -> None:
    tracker = PersistenceTracker()
    tracker.record_met("k")
    tracker.record_met("k")
    tracker.record_not_met("k")
    count = tracker.record_met("k")
    assert count == 1


# ---------------------------------------------------------------------------
# get tests
# ---------------------------------------------------------------------------


def test_get_returns_zero_for_unseen_key() -> None:
    tracker = PersistenceTracker()
    assert tracker.get("ghost") == 0


def test_get_reflects_current_count() -> None:
    tracker = PersistenceTracker()
    tracker.record_met("k")
    tracker.record_met("k")
    assert tracker.get("k") == 2


# ---------------------------------------------------------------------------
# Independence tests
# ---------------------------------------------------------------------------


def test_independent_keys_track_independently() -> None:
    tracker = PersistenceTracker()
    tracker.record_met("a")
    tracker.record_met("a")
    tracker.record_met("b")

    assert tracker.get("a") == 2
    assert tracker.get("b") == 1


def test_reset_one_key_does_not_affect_other() -> None:
    tracker = PersistenceTracker()
    tracker.record_met("a")
    tracker.record_met("b")
    tracker.record_not_met("a")

    assert tracker.get("a") == 0
    assert tracker.get("b") == 1
