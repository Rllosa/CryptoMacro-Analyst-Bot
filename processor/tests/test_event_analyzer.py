"""
Tests for LLM-4: EventAnalyzer (SOLO-58)

Pure unit tests — ContextBuilder and ClaudeClient are mocked.
No real network, DB, or NATS calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jsonschema
import pytest

from llm.event_analyzer import EventAnalyzer

_SCHEMA_PATH = Path(__file__).parents[2] / "schema" / "contracts" / "event_analysis.json"
_CYCLE_TIME = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)

_TRIGGER_VALUES: dict[str, Any] = {
    "liquidations_1h_usd": 60_000_000,
    "oi_drop_1h": 1.0,
    "atr_ratio": 2.5,
}

_CLAUDE_RESPONSE = json.dumps({
    "summary": "BTC cascade: $60M liquidated in 1 hour as OI collapsed 7%.",
    "interpretation": (
        "Forced deleveraging with BTC funding z-score at 2.5 before the cascade. "
        "Candle/ATR ratio of 2.5x confirms large directional move. OI dropped by "
        "more than 5%, indicating systematic position unwinding rather than organic selling."
    ),
    "watch_next": [
        "BTC $80k support — key bounce/rejection level",
        "OI recovery above cascade low within 2h",
        "Funding rate normalisation below z=1.0",
    ],
    "similar_historical_events": [],
})


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.anthropic_api_key = "test-key"
    s.thresholds_path = "configs/thresholds.yaml"
    return s


def _make_context() -> dict[str, Any]:
    return {
        "regime": {"current": "VOL_EXPANSION", "confidence": 0.78, "as_of": "2026-03-09T12:00:00"},
        "features": {
            "BTC": {"r_1h": -0.04, "rv_4h_zscore": 2.8, "volume_zscore": 2.1},
        },
        "cross_features": None,
        "derivatives": {
            "BTC": {"funding_zscore": 2.5, "liquidations_1h_usd": 60_000_000, "oi_drop_1h": 1.0},
        },
        "recent_alerts": [],
        "sections_available": {"features": True, "regime": True},
    }


# ---------------------------------------------------------------------------
# T1 — success path: analysis generated and stored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_success_path_stores_analysis() -> None:
    """Full pipeline: Claude responds → envelope validated → DB write called."""
    settings = _make_settings()
    nc = MagicMock()
    nc.jetstream.return_value = AsyncMock()

    # Mock DB pool
    mock_cur = AsyncMock()
    mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur.__aexit__ = AsyncMock(return_value=None)

    mock_conn = AsyncMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn)

    analyzer = EventAnalyzer(settings, AsyncMock(), mock_pool, nc)

    with (
        patch("llm.event_analyzer.ContextBuilder") as MockCB,
        patch("llm.event_analyzer.ClaudeClient") as MockCC,
    ):
        MockCB.return_value.build = AsyncMock(return_value=_make_context())
        mock_client = MagicMock()
        mock_client.complete_with_usage = AsyncMock(return_value=(_CLAUDE_RESPONSE, 300, 120))
        MockCC.return_value = mock_client

        await analyzer.analyze(
            alert_type="DELEVERAGING_EVENT",
            symbol="BTCUSDT",
            severity="HIGH",
            fire_time=_CYCLE_TIME,
            trigger_values=_TRIGGER_VALUES,
        )

    # DB execute was called (analysis stored)
    mock_cur.execute.assert_called_once()
    insert_args = mock_cur.execute.call_args.args
    assert insert_args[1][0] == "event_analysis"   # report_type
    assert "DELEVERAGING_EVENT" in insert_args[1][1]  # title includes alert type


# ---------------------------------------------------------------------------
# T2 — Claude down → no exception, fallback stored gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_claude_down_graceful_fallback() -> None:
    """When Claude raises, analyze() does not propagate — fallback record stored."""
    settings = _make_settings()
    nc = MagicMock()
    nc.jetstream.return_value = AsyncMock()

    mock_cur = AsyncMock()
    mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur.__aexit__ = AsyncMock(return_value=None)
    mock_conn = AsyncMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn)

    analyzer = EventAnalyzer(settings, AsyncMock(), mock_pool, nc)

    with (
        patch("llm.event_analyzer.ContextBuilder") as MockCB,
        patch("llm.event_analyzer.ClaudeClient") as MockCC,
    ):
        MockCB.return_value.build = AsyncMock(return_value=_make_context())
        mock_client = MagicMock()
        mock_client.complete_with_usage = AsyncMock(
            side_effect=Exception("Claude API unreachable")
        )
        MockCC.return_value = mock_client

        # Must not raise
        await analyzer.analyze(
            alert_type="DELEVERAGING_EVENT",
            symbol="BTCUSDT",
            severity="HIGH",
            fire_time=_CYCLE_TIME,
            trigger_values=_TRIGGER_VALUES,
        )

    # Fallback record still stored
    mock_cur.execute.assert_called_once()
    insert_args = mock_cur.execute.call_args.args
    stored = json.loads(insert_args[1][2])  # content column
    assert stored["analysis"]["summary"].startswith("LLM unavailable")
    # Metadata marks the unavailability
    meta = json.loads(insert_args[1][5])
    assert meta["llm_unavailable"] is True


# ---------------------------------------------------------------------------
# T3 — schema validates: well-formed envelope passes event_analysis.json
# ---------------------------------------------------------------------------


def test_t3_schema_validates_well_formed_envelope() -> None:
    """Hand-built envelope matching the F-7 contract must pass jsonschema validation."""
    with _SCHEMA_PATH.open() as f:
        schema = json.load(f)

    envelope = {
        "report_id": str(uuid4()),
        "report_type": "event_analysis",
        "generated_at": "2026-03-09T12:00:00+00:00",
        "trigger_alert": {
            "alert_id": str(uuid4()),
            "alert_type": "DELEVERAGING_EVENT",
            "symbol": "BTCUSDT",
            "severity": "HIGH",
            "time": "2026-03-09T12:00:00+00:00",
            "conditions": {
                "liquidations_1h_usd": 60_000_000,
                "oi_drop_1h": 1.0,
                "atr_ratio": 2.5,
            },
        },
        "context": {
            "regime": {"current": "VOL_EXPANSION", "confidence": 0.78},
            "recent_alerts": [],
            "features": {"r_1h": -0.04, "rv_4h_zscore": 2.8},
        },
        "analysis": {
            "summary": "BTC cascade: $60M liquidated in 1 hour as OI collapsed 7%.",
            "interpretation": (
                "Forced deleveraging event confirmed by OI drop and candle size. "
                "Funding z-score was elevated at 2.5 prior to the cascade, indicating "
                "crowded positioning that unwound rapidly. Next 1-3 hours likely volatile."
            ),
            "watch_next": [
                "BTC $80k support",
                "OI recovery within 2h",
            ],
            "similar_historical_events": [],
        },
        "llm_metadata": {
            "model": "claude-sonnet-4-6",
            "tokens_used": 420,
            "cost_usd": 0.0031,
            "generation_time_ms": 1800,
        },
    }

    # Must not raise
    jsonschema.validate(instance=envelope, schema=schema)
