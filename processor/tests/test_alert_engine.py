"""
Integration-style tests for AlertEngine (alerts/engine.py).

All external I/O (DB insert, NATS publish, Redis cooldown) is mocked via AsyncMock.
Four deterministic test vectors cover the key evaluation paths; a contract test
verifies that a fired payload satisfies the F-7 alert_payload.json schema.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from alerts.config import AlertParams
from alerts.engine import AlertEngine
from alerts.validator import validate_payload


# ---------------------------------------------------------------------------
# Fake persistence — async in-memory, avoids Redis setup in engine tests
# ---------------------------------------------------------------------------


class _FakePersistence:
    """Async in-memory persistence for use in AlertEngine unit tests.

    Keeps the engine tests focused on evaluation logic (cooldown, firing,
    dedup) rather than the Redis-backed persistence implementation, which
    is tested separately in test_alert_persistence.py.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def record_met(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def record_not_met(self, key: str) -> None:
        self._counts[key] = 0

    async def get(self, key: str) -> int:
        return self._counts.get(key, 0)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIRE_TIME = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc)

_BASE_KWARGS: dict = dict(
    alert_type="VOL_EXPANSION",
    symbol="BTCUSDT",
    direction="up",
    conditions_met=True,
    severity="HIGH",
    trigger_values={"rv_1h": 0.05},
    context={"regime": "RISK_ON_TREND"},
    input_snapshot={"rv_1h": 0.05, "rsi_14": 72.0},
    fire_time=_FIRE_TIME,
)


def _params(persistence: int = 1, cooldown: int = 60) -> AlertParams:
    return AlertParams(
        cooldown_minutes={"VOL_EXPANSION": cooldown},
        persistence_cycles={"VOL_EXPANSION": persistence},
    )


def _make_engine(persistence: int = 1, cooldown_active: bool = False) -> AlertEngine:
    engine = AlertEngine(
        pool=AsyncMock(),
        redis=AsyncMock(),
        nc=AsyncMock(),
        params=_params(persistence=persistence),
    )
    # Replace I/O on the cooldown registry directly (no need to patch the module)
    engine._cooldown.is_active = AsyncMock(return_value=cooldown_active)
    engine._cooldown.activate = AsyncMock()
    # Use in-memory fake so engine tests focus on evaluation logic, not Redis
    engine._persistence = _FakePersistence()
    return engine


def _run(coro):  # thin wrapper to keep test bodies clean
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# V1 — Persistence not met (N=2, first cycle only)
# ---------------------------------------------------------------------------


def test_v1_persistence_not_met_returns_false() -> None:
    """Single cycle with N=2 required → returns False; DB and NATS not called."""
    engine = _make_engine(persistence=2)
    mock_insert = AsyncMock()
    mock_publish = AsyncMock()

    with (
        patch("alerts.engine.insert_alert", mock_insert),
        patch("alerts.engine.publish_alert", mock_publish),
        patch("alerts.engine.validate_payload"),
    ):
        result = _run(engine.evaluate_and_fire(**_BASE_KWARGS))

    assert result is False
    mock_insert.assert_not_called()
    mock_publish.assert_not_called()


def test_v1_persistence_count_is_one_after_first_cycle() -> None:
    async def _inner() -> None:
        engine = _make_engine(persistence=2)
        with (
            patch("alerts.engine.insert_alert", AsyncMock()),
            patch("alerts.engine.publish_alert", AsyncMock()),
            patch("alerts.engine.validate_payload"),
        ):
            await engine.evaluate_and_fire(**_BASE_KWARGS)
        assert await engine._persistence.get("VOL_EXPANSION:BTCUSDT:up") == 1

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# V2 — Persistence satisfied (2 consecutive cycles)
# ---------------------------------------------------------------------------


def test_v2_second_cycle_fires_alert() -> None:
    """Two consecutive met cycles with N=2 → second call returns True."""
    engine = _make_engine(persistence=2)
    mock_insert = AsyncMock()
    mock_publish = AsyncMock()

    with (
        patch("alerts.engine.insert_alert", mock_insert),
        patch("alerts.engine.publish_alert", mock_publish),
        patch("alerts.engine.validate_payload"),
    ):
        r1 = _run(engine.evaluate_and_fire(**_BASE_KWARGS))
        r2 = _run(engine.evaluate_and_fire(**_BASE_KWARGS))

    assert r1 is False
    assert r2 is True
    mock_insert.assert_called_once()
    mock_publish.assert_called_once()


def test_v2_cooldown_activated_after_fire() -> None:
    engine = _make_engine(persistence=2)

    with (
        patch("alerts.engine.insert_alert", AsyncMock()),
        patch("alerts.engine.publish_alert", AsyncMock()),
        patch("alerts.engine.validate_payload"),
    ):
        _run(engine.evaluate_and_fire(**_BASE_KWARGS))
        _run(engine.evaluate_and_fire(**_BASE_KWARGS))

    engine._cooldown.activate.assert_called_once()


def test_v2_persistence_resets_to_zero_after_fire() -> None:
    """After firing, persistence counter resets so next N cycles are required again."""
    async def _inner() -> None:
        engine = _make_engine(persistence=2)
        with (
            patch("alerts.engine.insert_alert", AsyncMock()),
            patch("alerts.engine.publish_alert", AsyncMock()),
            patch("alerts.engine.validate_payload"),
        ):
            await engine.evaluate_and_fire(**_BASE_KWARGS)
            await engine.evaluate_and_fire(**_BASE_KWARGS)  # fires here
        assert await engine._persistence.get("VOL_EXPANSION:BTCUSDT:up") == 0

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# V3 — Cooldown suppression
# ---------------------------------------------------------------------------


def test_v3_cooldown_active_returns_false() -> None:
    """Cooldown active → returns False even when conditions_met=True."""
    engine = _make_engine(persistence=1, cooldown_active=True)
    mock_insert = AsyncMock()
    mock_publish = AsyncMock()

    with (
        patch("alerts.engine.insert_alert", mock_insert),
        patch("alerts.engine.publish_alert", mock_publish),
    ):
        result = _run(engine.evaluate_and_fire(**_BASE_KWARGS))

    assert result is False
    mock_insert.assert_not_called()
    mock_publish.assert_not_called()


def test_v3_cooldown_suppression_resets_persistence() -> None:
    """Cooldown suppression resets the persistence counter (don't carry stale count)."""
    async def _inner() -> None:
        engine = _make_engine(persistence=2)
        # First cycle builds count=1 with cooldown inactive
        with (
            patch("alerts.engine.insert_alert", AsyncMock()),
            patch("alerts.engine.publish_alert", AsyncMock()),
            patch("alerts.engine.validate_payload"),
        ):
            await engine.evaluate_and_fire(**_BASE_KWARGS)
        # Now activate cooldown
        engine._cooldown.is_active = AsyncMock(return_value=True)
        with (
            patch("alerts.engine.insert_alert", AsyncMock()),
            patch("alerts.engine.publish_alert", AsyncMock()),
        ):
            await engine.evaluate_and_fire(**_BASE_KWARGS)
        assert await engine._persistence.get("VOL_EXPANSION:BTCUSDT:up") == 0

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# V4 — Condition drops mid-persistence
# ---------------------------------------------------------------------------


def test_v4_condition_drop_resets_persistence() -> None:
    """Cycle1=met(1), Cycle2=not_met(reset), Cycle3=met(1) — should not fire with N=2."""
    engine = _make_engine(persistence=2)
    kwargs_not_met = {**_BASE_KWARGS, "conditions_met": False}

    with (
        patch("alerts.engine.insert_alert", AsyncMock()),
        patch("alerts.engine.publish_alert", AsyncMock()),
        patch("alerts.engine.validate_payload"),
    ):
        r1 = _run(engine.evaluate_and_fire(**_BASE_KWARGS))       # met → count=1
        r2 = _run(engine.evaluate_and_fire(**kwargs_not_met))     # not met → reset
        r3 = _run(engine.evaluate_and_fire(**_BASE_KWARGS))       # met → count=1

    assert r1 is False
    assert r2 is False
    assert r3 is False  # count=1, still need 2


def test_v4_persistence_count_restarts_from_one_after_drop() -> None:
    async def _inner() -> None:
        engine = _make_engine(persistence=2)
        kwargs_not_met = {**_BASE_KWARGS, "conditions_met": False}
        with (
            patch("alerts.engine.insert_alert", AsyncMock()),
            patch("alerts.engine.publish_alert", AsyncMock()),
            patch("alerts.engine.validate_payload"),
        ):
            await engine.evaluate_and_fire(**_BASE_KWARGS)
            await engine.evaluate_and_fire(**kwargs_not_met)
            await engine.evaluate_and_fire(**_BASE_KWARGS)
        assert await engine._persistence.get("VOL_EXPANSION:BTCUSDT:up") == 1

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# V5 — conditions_met=False never fires
# ---------------------------------------------------------------------------


def test_conditions_not_met_never_fires() -> None:
    engine = _make_engine(persistence=1)
    kwargs_not_met = {**_BASE_KWARGS, "conditions_met": False}
    mock_insert = AsyncMock()

    with patch("alerts.engine.insert_alert", mock_insert):
        result = _run(engine.evaluate_and_fire(**kwargs_not_met))

    assert result is False
    mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# Contract test — fired payload satisfies F-7 schema
# ---------------------------------------------------------------------------


def test_fired_payload_passes_f7_contract() -> None:
    """
    The payload built by evaluate_and_fire and passed to publish_alert must
    conform to the F-7 alert_payload.json schema (validate_payload must not raise).
    """
    engine = _make_engine(persistence=1)
    captured: list[dict] = []

    async def _capture_publish(nc, payload: dict) -> None:
        captured.append(payload)

    with (
        patch("alerts.engine.insert_alert", AsyncMock()),
        patch("alerts.engine.publish_alert", _capture_publish),
    ):
        result = _run(engine.evaluate_and_fire(**_BASE_KWARGS))

    assert result is True
    assert len(captured) == 1
    # Raises jsonschema.ValidationError on contract violation — must not raise
    validate_payload(captured[0])


def test_fired_payload_contains_required_fields() -> None:
    engine = _make_engine(persistence=1)
    captured: list[dict] = []

    async def _capture_publish(nc, payload: dict) -> None:
        captured.append(payload)

    with (
        patch("alerts.engine.insert_alert", AsyncMock()),
        patch("alerts.engine.publish_alert", _capture_publish),
    ):
        _run(engine.evaluate_and_fire(**_BASE_KWARGS))

    payload = captured[0]
    assert payload["alert_type"] == "VOL_EXPANSION"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["severity"] == "HIGH"
    assert "alert_id" in payload
    assert "time" in payload
    assert "conditions" in payload
    assert "context" in payload
    assert "cooldown_until" in payload
