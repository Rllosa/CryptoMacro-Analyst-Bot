"""
Unit tests for PersistenceTracker (alerts/persistence.py).

Redis I/O replaced with a simple in-memory fake — zero network calls.
"""

from __future__ import annotations

import asyncio

from alerts.persistence import PersistenceTracker


# ---------------------------------------------------------------------------
# Fake Redis — in-memory, zero network
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async Redis fake covering INCR, EXPIRE, DELETE, GET."""

    def __init__(self) -> None:
        self._data: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._data[key] = self._data.get(key, 0) + 1
        return self._data[key]

    async def expire(self, key: str, ttl: int) -> None:
        pass  # TTL enforcement not needed in unit tests

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def get(self, key: str) -> bytes | None:
        val = self._data.get(key)
        return str(val).encode() if val is not None else None


def _make_tracker() -> PersistenceTracker:
    return PersistenceTracker(_FakeRedis())


# ---------------------------------------------------------------------------
# record_met tests
# ---------------------------------------------------------------------------


def test_record_met_starts_at_one() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        count = await tracker.record_met("VOL_EXPANSION:BTCUSDT:up")
        assert count == 1

    asyncio.run(_inner())


def test_record_met_increments_consecutively() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_met("k")
        count = await tracker.record_met("k")
        assert count == 2

    asyncio.run(_inner())


def test_record_met_three_times() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_met("k")
        await tracker.record_met("k")
        count = await tracker.record_met("k")
        assert count == 3

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# record_not_met tests
# ---------------------------------------------------------------------------


def test_record_not_met_resets_to_zero() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_met("k")
        await tracker.record_met("k")
        await tracker.record_not_met("k")
        assert await tracker.get("k") == 0

    asyncio.run(_inner())


def test_record_not_met_on_unseen_key_is_zero() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_not_met("never_seen")
        assert await tracker.get("never_seen") == 0

    asyncio.run(_inner())


def test_record_met_after_reset_restarts_from_one() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_met("k")
        await tracker.record_met("k")
        await tracker.record_not_met("k")
        count = await tracker.record_met("k")
        assert count == 1

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# get tests
# ---------------------------------------------------------------------------


def test_get_returns_zero_for_unseen_key() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        assert await tracker.get("ghost") == 0

    asyncio.run(_inner())


def test_get_reflects_current_count() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_met("k")
        await tracker.record_met("k")
        assert await tracker.get("k") == 2

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Independence tests
# ---------------------------------------------------------------------------


def test_independent_keys_track_independently() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_met("a")
        await tracker.record_met("a")
        await tracker.record_met("b")
        assert await tracker.get("a") == 2
        assert await tracker.get("b") == 1

    asyncio.run(_inner())


def test_reset_one_key_does_not_affect_other() -> None:
    async def _inner() -> None:
        tracker = _make_tracker()
        await tracker.record_met("a")
        await tracker.record_met("b")
        await tracker.record_not_met("a")
        assert await tracker.get("a") == 0
        assert await tracker.get("b") == 1

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Redis key prefix tests
# ---------------------------------------------------------------------------


def test_key_prefix_used_consistently() -> None:
    """record_met and get use the same Redis key."""

    async def _inner() -> None:
        redis = _FakeRedis()
        tracker = PersistenceTracker(redis)
        await tracker.record_met("VOL_EXPANSION:BTCUSDT:up")
        # The underlying Redis key must be prefixed
        assert "persistence:VOL_EXPANSION:BTCUSDT:up" in redis._data

    asyncio.run(_inner())


def test_record_not_met_deletes_prefixed_key() -> None:
    """record_not_met removes the persistence: prefixed key."""

    async def _inner() -> None:
        redis = _FakeRedis()
        tracker = PersistenceTracker(redis)
        await tracker.record_met("k")
        assert "persistence:k" in redis._data
        await tracker.record_not_met("k")
        assert "persistence:k" not in redis._data

    asyncio.run(_inner())
