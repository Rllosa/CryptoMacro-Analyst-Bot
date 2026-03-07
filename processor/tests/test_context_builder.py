"""
Tests for LLM-1: ContextBuilder (SOLO-55)

All tests are pure unit tests — no Docker, no real DB/Redis.
All I/O is mocked with AsyncMock.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm.context import ContextBuilder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TS = "2026-03-07T18:00:00+00:00"
_NOW = datetime(2026, 3, 7, 18, 0, 0, tzinfo=timezone.utc)

_FEATURE_BTC  = {"r_1h": -0.024, "rv_4h_zscore": 1.2, "rsi_14": 38.2, "volume_zscore": 1.5}
_FEATURE_ETH  = {"r_1h": -0.018, "rv_4h_zscore": 0.9, "rsi_14": 42.1, "volume_zscore": 0.8}
_FEATURE_SOL  = {"r_1h": -0.031, "rv_4h_zscore": 1.4, "rsi_14": 35.6, "volume_zscore": 2.1}
_FEATURE_HYPE = {"r_1h": -0.045, "rv_4h_zscore": 1.8, "rsi_14": 31.0, "volume_zscore": 3.2}
_CROSS        = {"macro_stress": 64.0, "vix": 28.4, "dxy_momentum": 1.2, "eth_btc_rs": -0.15}
_REGIME       = {"regime": "RISK_OFF_STRESS", "confidence": 0.82, "time": _TS}
_DERIV_BTC    = {"funding_zscore": 1.5, "liquidations_1h_usd": 12_000_000.0, "oi_drop_1h": 0.0}

_ALERT_ROWS = [
    (_NOW, "VOL_EXPANSION", "HIGH", "BTC", "BTC volatility expansion"),
]
_REGIME_ROWS = [
    (_NOW, "RISK_OFF_STRESS", 0.82, "CHOP_RANGE"),
]


def _make_redis(
    *,
    features: bool = True,
    cross: bool = True,
    regime: bool = True,
    derivatives: bool = True,
    feature_error_asset: str | None = None,
) -> AsyncMock:
    """Build a Redis mock returning JSON blobs for the expected keys."""
    per_asset = {
        "btcusdt": _FEATURE_BTC,
        "ethusdt": _FEATURE_ETH,
        "solusdt": _FEATURE_SOL,
        "hypeusdt": _FEATURE_HYPE,
    }
    deriv_asset = {
        "btcusdt": _DERIV_BTC,
        "ethusdt": {},
        "solusdt": {},
        "hypeusdt": {},
    }

    async def _get(key: str):
        if key.startswith("features:latest:"):
            sym = key.split(":")[-1]
            if not features:
                return None
            if feature_error_asset and sym == feature_error_asset:
                raise ConnectionError("Redis timeout")
            return json.dumps({"features": per_asset.get(sym, {})})
        if key == "cross_features:latest":
            return json.dumps({"features": _CROSS}) if cross else None
        if key == "regime:latest":
            return json.dumps(_REGIME) if regime else None
        if key.startswith("derivatives:latest:"):
            sym = key.split(":")[-1]
            return json.dumps({"features": deriv_asset.get(sym, {})}) if derivatives else None
        return None

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=_get)
    return redis


def _make_pool(
    *,
    alerts: list | None = None,
    regime_rows: list | None = None,
    raise_on_alerts: bool = False,
) -> MagicMock:
    """
    Build a DB pool mock with query-aware cursor dispatch.
    Detects which table is being queried from the SQL string.
    """
    if alerts is None:
        alerts = list(_ALERT_ROWS)
    if regime_rows is None:
        regime_rows = list(_REGIME_ROWS)

    def _make_cursor(alert_rows: list, reg_rows: list, fail_alerts: bool):
        last_query: dict[str, str] = {"sql": ""}

        async def _execute(query, params=None):
            last_query["sql"] = query

        async def _fetchall():
            if fail_alerts and "alerts" in last_query["sql"]:
                raise RuntimeError("DB connection error")
            if "regime_state" in last_query["sql"]:
                return reg_rows
            return alert_rows  # alerts table

        cur = AsyncMock()
        cur.execute = AsyncMock(side_effect=_execute)
        cur.fetchall = AsyncMock(side_effect=_fetchall)
        return cur

    @asynccontextmanager
    async def _cursor_ctx():
        yield _make_cursor(alerts, regime_rows, raise_on_alerts)

    conn = AsyncMock()
    conn.cursor = _cursor_ctx

    @asynccontextmanager
    async def _connection():
        yield conn

    pool = MagicMock()
    pool.connection = _connection
    return pool


# ---------------------------------------------------------------------------
# T1 — Full data: all sections present, sections_available all True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t1_full_data_all_sections_present() -> None:
    """Full Redis + DB data → complete context, all sections_available True."""
    builder = ContextBuilder(_make_redis(), _make_pool())
    ctx = await builder.build()

    assert ctx["regime"] is not None
    assert ctx["regime"]["current"] == "RISK_OFF_STRESS"
    assert ctx["regime"]["confidence"] == pytest.approx(0.82)

    assert ctx["features"] is not None
    assert set(ctx["features"].keys()) == {"BTC", "ETH", "SOL", "HYPE"}
    assert ctx["features"]["BTC"]["r_1h"] == pytest.approx(-0.024)

    assert ctx["cross_features"] is not None
    assert ctx["cross_features"]["macro_stress"] == pytest.approx(64.0)

    assert ctx["derivatives"] is not None
    assert ctx["derivatives"]["BTC"]["funding_zscore"] == pytest.approx(1.5)

    assert ctx["recent_alerts"] is not None
    assert len(ctx["recent_alerts"]) == 1
    assert ctx["recent_alerts"][0]["type"] == "VOL_EXPANSION"

    sa = ctx["sections_available"]
    assert sa["features"] is True
    assert sa["cross_features"] is True
    assert sa["regime"] is True
    assert sa["derivatives"] is True
    assert sa["recent_alerts"] is True


# ---------------------------------------------------------------------------
# T2 — No derivatives: graceful omission, other sections intact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t2_no_derivatives_graceful_omission() -> None:
    """derivatives:latest:* all None → derivatives section None, no exception."""
    builder = ContextBuilder(_make_redis(derivatives=False), _make_pool())
    ctx = await builder.build()

    assert ctx["derivatives"] is None
    assert ctx["sections_available"]["derivatives"] is False

    # All other sections unaffected
    assert ctx["features"] is not None
    assert ctx["cross_features"] is not None
    assert ctx["regime"] is not None
    assert ctx["recent_alerts"] is not None
    assert ctx["sections_available"]["features"] is True
    assert ctx["sections_available"]["recent_alerts"] is True


# ---------------------------------------------------------------------------
# T3 — No regime: regime section None, False flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t3_no_regime_data() -> None:
    """regime:latest returns None → regime section None, False flag, rest intact."""
    builder = ContextBuilder(_make_redis(regime=False), _make_pool())
    ctx = await builder.build()

    assert ctx["regime"] is None
    assert ctx["sections_available"]["regime"] is False

    assert ctx["features"] is not None
    assert ctx["cross_features"] is not None
    assert ctx["sections_available"]["features"] is True
    assert ctx["sections_available"]["cross_features"] is True


# ---------------------------------------------------------------------------
# T4 — No alerts: empty list, recent_alerts section still True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t4_no_alerts_empty_list() -> None:
    """DB returns empty alerts → recent_alerts is [], sections_available True (empty ≠ failure)."""
    builder = ContextBuilder(_make_redis(), _make_pool(alerts=[]))
    ctx = await builder.build()

    assert ctx["recent_alerts"] == []
    assert ctx["sections_available"]["recent_alerts"] is True


# ---------------------------------------------------------------------------
# T5 — Redis error on one feature key: that asset skipped, build succeeds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t5_redis_error_on_one_feature_asset() -> None:
    """ConnectionError on btcusdt feature key → BTC absent, ETH/SOL/HYPE present, no raise."""
    builder = ContextBuilder(
        _make_redis(feature_error_asset="btcusdt"),
        _make_pool(),
    )
    ctx = await builder.build()

    # BTC missing but others present
    assert "BTC" not in ctx["features"]
    assert "ETH" in ctx["features"]
    assert "SOL" in ctx["features"]
    assert "HYPE" in ctx["features"]

    # features section still marked available (partial data is still data)
    assert ctx["sections_available"]["features"] is True


# ---------------------------------------------------------------------------
# T6 — sections_available always has all 5 keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t6_sections_available_always_complete() -> None:
    """sections_available always has all 5 required keys, regardless of failures."""
    builder = ContextBuilder(
        _make_redis(features=False, cross=False, regime=False, derivatives=False),
        _make_pool(alerts=[], raise_on_alerts=True),
    )
    ctx = await builder.build()

    required_keys = {"features", "cross_features", "regime", "derivatives", "recent_alerts"}
    assert set(ctx["sections_available"].keys()) == required_keys
    assert "generated_at" in ctx
