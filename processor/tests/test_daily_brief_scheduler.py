"""
Tests for LLM-3: DailyBriefScheduler (SOLO-57)

Pure unit tests — no real network, DB, or NATS calls.
ContextBuilder and ClaudeClient are mocked at the module level.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from llm.scheduler import DailyBriefScheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.anthropic_api_key = "test-key"
    s.claude_model_daily = "claude-sonnet-4-6"
    return s


def _make_context() -> dict[str, Any]:
    return {
        "regime": {
            "current": "RISK_ON_TREND",
            "confidence": 0.82,
            "as_of": "2026-01-01T05:00:00+00:00",
            "recent_transitions": [],
        },
        "features": {
            "BTC": {"r_1h": 0.012, "rsi_14": 62.0, "rv_4h_zscore": 0.3, "volume_zscore": 0.5},
        },
        "cross_features": {"dxy_momentum": -0.2, "vix": 18.5, "macro_stress": 35.0},
        "derivatives": {},
        "recent_alerts": [],
        "sections_available": {"regime": True, "features": True},
    }


_VALID_CLAUDE_JSON = json.dumps({
    "regime_analysis": "Market in RISK_ON_TREND with 82% confidence. BTC up 1.2% in 1h.",
    "key_insights": ["BTC momentum positive.", "DXY weakening supports crypto."],
    "watch_list": ["BTC $100k level", "VIX above 20"],
})


def _make_pool_with_cursor(mock_cursor: AsyncMock) -> MagicMock:
    """Return a mock pool whose async context managers yield mock_cursor for execute calls."""
    mock_actual_conn = MagicMock()
    mock_actual_conn.cursor.return_value = MagicMock(
        __aenter__=AsyncMock(return_value=mock_cursor),
        __aexit__=AsyncMock(return_value=None),
    )
    mock_pool = MagicMock()
    mock_pool.connection.return_value = MagicMock(
        __aenter__=AsyncMock(return_value=mock_actual_conn),
        __aexit__=AsyncMock(return_value=None),
    )
    return mock_pool


# ---------------------------------------------------------------------------
# T1 — Fires at 05:00 UTC (brief hour, minute=0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_fires_at_05_utc() -> None:
    """Scheduler fires _generate_and_publish once when UTC hour=5, minute=0."""
    fixed_time = datetime(2026, 1, 1, 5, 0, 30, tzinfo=timezone.utc)

    with patch("llm.scheduler.datetime") as mock_dt, \
         patch("llm.scheduler.asyncio.sleep", new_callable=AsyncMock,
               side_effect=asyncio.CancelledError), \
         patch("llm.scheduler.asyncio.create_task") as mock_create_task:
        mock_dt.now.return_value = fixed_time

        scheduler = DailyBriefScheduler(_make_settings(), MagicMock(), MagicMock(), MagicMock())
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run()

    mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# T2 — No double-fire: same hour seen twice → create_task called exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_no_double_fire_same_hour() -> None:
    """Scheduler does not fire twice for the same UTC brief hour."""
    fixed_time = datetime(2026, 1, 1, 5, 0, 30, tzinfo=timezone.utc)

    with patch("llm.scheduler.datetime") as mock_dt, \
         patch("llm.scheduler.asyncio.sleep", new_callable=AsyncMock,
               side_effect=[None, asyncio.CancelledError()]), \
         patch("llm.scheduler.asyncio.create_task") as mock_create_task:
        mock_dt.now.return_value = fixed_time

        scheduler = DailyBriefScheduler(_make_settings(), MagicMock(), MagicMock(), MagicMock())
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run()

    # Two loop iterations at the same hour → fired only once
    mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# T3 — Claude failure → _generate_and_publish does not raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_claude_failure_does_not_raise() -> None:
    """If ClaudeClient raises, _generate_and_publish logs and returns without raising."""
    context = _make_context()

    with patch("llm.scheduler.ContextBuilder") as mock_cb_cls, \
         patch("llm.scheduler.ClaudeClient") as mock_client_cls:
        mock_cb_cls.return_value.build = AsyncMock(return_value=context)
        mock_client_cls.return_value.complete_with_usage = AsyncMock(
            side_effect=Exception("Anthropic API error")
        )

        scheduler = DailyBriefScheduler(_make_settings(), MagicMock(), MagicMock(), MagicMock())
        # Must complete without raising — all errors are caught internally
        await scheduler._generate_and_publish()


# ---------------------------------------------------------------------------
# T4 — Well-formed F-7 envelope passes JSON schema validation
# ---------------------------------------------------------------------------


def test_t4_valid_envelope_passes_schema() -> None:
    """A correctly structured envelope passes jsonschema validation against daily_brief.json."""
    now = datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)
    envelope = {
        "report_id": str(uuid4()),
        "report_type": "daily_brief",
        "generated_at": now.isoformat(),
        "time_range": {
            "start": (now - timedelta(hours=12)).isoformat(),
            "end": now.isoformat(),
        },
        "regime_summary": {
            "current_regime": "RISK_ON_TREND",
            "confidence": 0.82,
            "transitions": [],
            "analysis": "Market is in RISK_ON_TREND. BTC leading with momentum.",
        },
        "alert_summary": {
            "total_alerts": 0,
            "by_type": {},
            "by_severity": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "notable_alerts": [],
        },
        "market_summary": {
            "assets": {
                "BTC": {
                    "price_change_pct": 0.012,
                    "volume_change_pct": 0.5,
                    "volatility_regime": "low",
                },
            },
        },
        "key_insights": ["BTC momentum positive.", "DXY weakening supports crypto."],
        "watch_list": ["BTC $100k level", "VIX above 20"],
        "llm_metadata": {
            "model": "claude-sonnet-4-6",
            "tokens_used": 620,
            "cost_usd": 0.003,
            "generation_time_ms": 1200,
        },
    }

    scheduler = DailyBriefScheduler(_make_settings(), MagicMock(), MagicMock(), MagicMock())
    # _validate raises jsonschema.ValidationError on failure; no exception = pass
    scheduler._validate(envelope)


# ---------------------------------------------------------------------------
# T5 — DB write executes INSERT with report_type='daily_brief'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_db_write_correct_report_type() -> None:
    """_write_db executes a parameterized INSERT with report_type='daily_brief'."""
    now = datetime(2026, 1, 1, 5, 0, 0, tzinfo=timezone.utc)
    envelope = {
        "report_id": str(uuid4()),
        "report_type": "daily_brief",
        "generated_at": now.isoformat(),
        "llm_metadata": {
            "model": "claude-sonnet-4-6",
            "tokens_used": 500,
            "cost_usd": 0.002,
            "generation_time_ms": 800,
        },
    }
    context: dict[str, Any] = {"regime": {"current": "CHOP_RANGE", "confidence": 0.6}}

    mock_cursor = AsyncMock()
    mock_pool = _make_pool_with_cursor(mock_cursor)

    scheduler = DailyBriefScheduler(_make_settings(), MagicMock(), mock_pool, MagicMock())
    await scheduler._write_db(envelope, context)

    mock_cursor.execute.assert_called_once()
    _query, params = mock_cursor.execute.call_args[0]
    assert params[0] == "daily_brief"


# ---------------------------------------------------------------------------
# T6 — NATS publish_report is called after a successful generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t6_nats_publish_called() -> None:
    """publisher.publish_report is called once after successful brief generation."""
    context = _make_context()
    mock_cursor = AsyncMock()
    mock_pool = _make_pool_with_cursor(mock_cursor)

    with patch("llm.scheduler.ContextBuilder") as mock_cb_cls, \
         patch("llm.scheduler.ClaudeClient") as mock_client_cls, \
         patch("llm.scheduler.publisher.publish_report", new_callable=AsyncMock) as mock_publish, \
         patch.object(DailyBriefScheduler, "_validate"):
        mock_cb_cls.return_value.build = AsyncMock(return_value=context)
        mock_client_cls.return_value.complete_with_usage = AsyncMock(
            return_value=(_VALID_CLAUDE_JSON, 500, 120)
        )

        scheduler = DailyBriefScheduler(_make_settings(), MagicMock(), mock_pool, MagicMock())
        await scheduler._generate_and_publish()

    mock_publish.assert_awaited_once()
    _nc_arg, envelope_arg = mock_publish.call_args[0]
    assert envelope_arg["report_type"] == "daily_brief"
