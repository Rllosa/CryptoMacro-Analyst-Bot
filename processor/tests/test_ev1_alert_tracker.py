"""
Tests for EV-1: Alert Move Tracker

Pure unit tests — no real DB or network calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from eval.tracker import AlertMoveTracker, _PROXY_SYMBOL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_alert(
    symbol: str | None = "BTCUSDT",
    fired_at: datetime | None = None,
    alert_id: uuid.UUID | None = None,
) -> dict:
    return {
        "id": alert_id or uuid.uuid4(),
        "time": fired_at or (_now() - timedelta(hours=4)),
        "symbol": symbol,
        "alert_type": "VOL_EXPANSION",
        "severity": "MEDIUM",
    }


def _make_outcome_row(
    alert_id: uuid.UUID | None = None,
    fired_at: datetime | None = None,
    symbol: str = "BTCUSDT",
    price_at_alert: Decimal = Decimal("50000.0"),
) -> dict:
    return {
        "alert_id": alert_id or uuid.uuid4(),
        "alert_fired_at": fired_at or (_now() - timedelta(hours=12)),
        "symbol": symbol,
        "price_at_alert": price_at_alert,
    }


# ---------------------------------------------------------------------------
# T1 — 4h move computed correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_4h_move_computed_correctly() -> None:
    """Alert at T-4h, candles found → move_4h_pct = +2.0%."""
    now = _now()
    alert = _make_alert(fired_at=now - timedelta(hours=4))
    tracker = AlertMoveTracker(pool=None)

    with (
        patch("eval.tracker.fetch_alerts_for_4h_tracking", new=AsyncMock(return_value=[alert])),
        patch("eval.tracker.fetch_alerts_for_12h_tracking", new=AsyncMock(return_value=[])),
        patch(
            "eval.tracker.fetch_price_near",
            new=AsyncMock(side_effect=[50000.0, 51000.0]),  # price_at_alert, price_4h
        ),
        patch("eval.tracker.upsert_4h_outcome", new=AsyncMock()) as mock_upsert,
    ):
        await tracker._run_cycle(now)

    mock_upsert.assert_called_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["price_at_alert"] == pytest.approx(50000.0)
    assert kwargs["price_4h"] == pytest.approx(51000.0)
    assert kwargs["move_4h_pct"] == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------------------
# T2 — 12h update fills existing row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_12h_update_fills_row() -> None:
    """Existing 4h row → 12h fields filled; move_12h_pct = -4.0%."""
    now = _now()
    row = _make_outcome_row(fired_at=now - timedelta(hours=12), price_at_alert=Decimal("50000.0"))
    tracker = AlertMoveTracker(pool=None)

    with (
        patch("eval.tracker.fetch_alerts_for_4h_tracking", new=AsyncMock(return_value=[])),
        patch("eval.tracker.fetch_alerts_for_12h_tracking", new=AsyncMock(return_value=[row])),
        patch("eval.tracker.fetch_price_near", new=AsyncMock(return_value=48000.0)),
        patch("eval.tracker.update_12h_outcome", new=AsyncMock()) as mock_update,
    ):
        await tracker._run_cycle(now)

    mock_update.assert_called_once()
    kwargs = mock_update.call_args.kwargs
    assert kwargs["price_12h"] == pytest.approx(48000.0)
    assert kwargs["move_12h_pct"] == pytest.approx(-4.0, abs=0.01)


# ---------------------------------------------------------------------------
# T3 — missing candle skips gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_missing_candle_skips_gracefully() -> None:
    """No candle at T+4h → upsert not called, no exception."""
    now = _now()
    alert = _make_alert(fired_at=now - timedelta(hours=4))
    tracker = AlertMoveTracker(pool=None)

    with (
        patch("eval.tracker.fetch_alerts_for_4h_tracking", new=AsyncMock(return_value=[alert])),
        patch("eval.tracker.fetch_alerts_for_12h_tracking", new=AsyncMock(return_value=[])),
        patch(
            "eval.tracker.fetch_price_near",
            new=AsyncMock(side_effect=[50000.0, None]),  # price_at_alert ok, price_4h missing
        ),
        patch("eval.tracker.upsert_4h_outcome", new=AsyncMock()) as mock_upsert,
    ):
        await tracker._run_cycle(now)  # must not raise

    mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# T4 — cross-asset alert uses BTCUSDT proxy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_cross_asset_uses_btc_proxy() -> None:
    """NULL symbol alert → all price fetches use _PROXY_SYMBOL (BTCUSDT)."""
    now = _now()
    alert = _make_alert(symbol=None, fired_at=now - timedelta(hours=4))
    tracker = AlertMoveTracker(pool=None)

    captured: list[str] = []

    async def _capture_price(pool: object, symbol: str, target: object) -> float:
        captured.append(symbol)
        return 50000.0

    with (
        patch("eval.tracker.fetch_alerts_for_4h_tracking", new=AsyncMock(return_value=[alert])),
        patch("eval.tracker.fetch_alerts_for_12h_tracking", new=AsyncMock(return_value=[])),
        patch("eval.tracker.fetch_price_near", side_effect=_capture_price),
        patch("eval.tracker.upsert_4h_outcome", new=AsyncMock()),
    ):
        await tracker._run_cycle(now)

    assert captured, "fetch_price_near was never called"
    assert all(s == _PROXY_SYMBOL for s in captured), f"Unexpected symbols: {captured}"


# ---------------------------------------------------------------------------
# T5 — fully-tracked alert not reprocessed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_fully_tracked_not_reprocessed() -> None:
    """Cycle with no pending alerts → neither upsert nor update is called."""
    now = _now()
    tracker = AlertMoveTracker(pool=None)

    with (
        patch("eval.tracker.fetch_alerts_for_4h_tracking", new=AsyncMock(return_value=[])),
        patch("eval.tracker.fetch_alerts_for_12h_tracking", new=AsyncMock(return_value=[])),
        patch("eval.tracker.upsert_4h_outcome", new=AsyncMock()) as mock_upsert,
        patch("eval.tracker.update_12h_outcome", new=AsyncMock()) as mock_update,
    ):
        await tracker._run_cycle(now)

    mock_upsert.assert_not_called()
    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — per-alert failure does not crash cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t6_per_alert_failure_does_not_crash_cycle() -> None:
    """One alert's price fetch raises → cycle completes, second alert still tracked."""
    now = _now()
    alert1 = _make_alert(fired_at=now - timedelta(hours=4))
    alert2 = _make_alert(fired_at=now - timedelta(hours=4))
    tracker = AlertMoveTracker(pool=None)

    call_count = 0

    async def _flaky_price(pool: object, symbol: str, target: object) -> float:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            # First alert's two fetch_price_near calls both raise
            raise Exception("DB timeout")
        return 50000.0

    with (
        patch("eval.tracker.fetch_alerts_for_4h_tracking", new=AsyncMock(return_value=[alert1, alert2])),
        patch("eval.tracker.fetch_alerts_for_12h_tracking", new=AsyncMock(return_value=[])),
        patch("eval.tracker.fetch_price_near", side_effect=_flaky_price),
        patch("eval.tracker.upsert_4h_outcome", new=AsyncMock()) as mock_upsert,
    ):
        await tracker._run_cycle(now)  # must not raise

    # alert1 failed but alert2 should have been attempted
    # (upsert called for alert2 since both its prices returned 50000.0)
    mock_upsert.assert_called_once()
