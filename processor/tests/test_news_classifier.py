"""
Tests for LLM-2b: NewsClassifier (SOLO-95)

Pure unit tests — no real DB, Redis, or Claude API calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.news_classifier import NewsClassifier, _REDIS_KEY
from llm.prompts import news_classify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.anthropic_api_key = "test-key"
    s.claude_model_news = "claude-haiku-4-5-20251001"
    s.news_classifier_interval_secs = 300
    s.thresholds_path = "configs/thresholds.yaml"
    return s


def _make_row(
    headline: str = "SEC approves spot BTC ETF",
    age_minutes: int = 5,
    source: str = "cryptopanic",
) -> dict[str, Any]:
    published_at = datetime.now(tz=timezone.utc) - timedelta(minutes=age_minutes)
    return {
        "id": 42,
        "headline": headline,
        "url": "https://example.com/news/1",
        "published_at": published_at,
        "currencies": ["BTC"],
        "source": source,
    }


_VALID_CLAUDE_JSON = json.dumps({
    "relevant": True,
    "direction": "bullish",
    "confidence": "high",
    "event_type": "regulatory",
    "assets": ["BTC"],
    "reasoning": "ETF approval unlocks institutional demand.",
})


def _make_pool_with_rows(rows: list[dict]) -> MagicMock:
    """Mock pool that returns given rows on SELECT and accepts UPDATE/INSERT."""
    mock_cursor = AsyncMock()
    # fetchall returns list of tuples matching column order
    if rows:
        cols = list(rows[0].keys())
        mock_cursor.description = [(c,) for c in cols]
        mock_cursor.fetchall = AsyncMock(return_value=[
            tuple(r[c] for c in cols) for r in rows
        ])
    else:
        mock_cursor.description = []
        mock_cursor.fetchall = AsyncMock(return_value=[])

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
    return mock_pool, mock_cursor


# ---------------------------------------------------------------------------
# T1 — Fetches unclassified rows and calls Claude once per headline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_classifies_unclassified_rows() -> None:
    """One unclassified row → Claude called once, signal written to DB and Redis."""
    row = _make_row()
    mock_pool, mock_cursor = _make_pool_with_rows([row])
    _mock_pipe = MagicMock()
    _mock_pipe.lpush = MagicMock()
    _mock_pipe.ltrim = MagicMock()
    _mock_pipe.expire = MagicMock()
    _mock_pipe.execute = AsyncMock(return_value=[1, 1, 1])
    mock_redis = AsyncMock()
    mock_redis.pipeline = MagicMock(return_value=_mock_pipe)  # pipeline() is sync

    with patch("llm.news_classifier.ClaudeClient") as mock_client_cls:
        mock_client_cls.return_value.complete = AsyncMock(return_value=_VALID_CLAUDE_JSON)

        nc = NewsClassifier(_make_settings(), mock_pool, mock_redis)
        with patch.object(nc, "_load_thresholds", return_value={}):
            await nc._classify_cycle()

    # Claude called once for the one row
    mock_client_cls.return_value.complete.assert_awaited_once()
    # DB execute called at least twice (INSERT signal + UPDATE classified)
    assert mock_cursor.execute.call_count >= 2


# ---------------------------------------------------------------------------
# T2 — Rows older than max_age_minutes are not returned (DB filters them)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_no_rows_when_all_stale() -> None:
    """When DB returns no rows (cutoff filters them), no Claude calls are made."""
    mock_pool, mock_cursor = _make_pool_with_rows([])  # empty — cutoff filtered in DB

    with patch("llm.news_classifier.ClaudeClient") as mock_client_cls:
        nc = NewsClassifier(_make_settings(), mock_pool, AsyncMock())
        with patch.object(nc, "_load_thresholds", return_value={}):
            await nc._classify_cycle()

    mock_client_cls.return_value.complete.assert_not_called()


# ---------------------------------------------------------------------------
# T3 — Claude failure → row not classified, error logged, no raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_claude_failure_does_not_raise() -> None:
    """If Claude raises for a headline, _classify_cycle completes without error."""
    row = _make_row()
    mock_pool, mock_cursor = _make_pool_with_rows([row])

    with patch("llm.news_classifier.ClaudeClient") as mock_client_cls:
        mock_client_cls.return_value.complete = AsyncMock(
            side_effect=Exception("API error")
        )
        nc = NewsClassifier(_make_settings(), mock_pool, AsyncMock())
        with patch.object(nc, "_load_thresholds", return_value={}):
            # Must not raise
            await nc._classify_cycle()

    # classify failed → no DB insert for signal (cursor.execute not called for INSERT)
    insert_calls = [
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO news_signals" in str(c)
    ]
    assert len(insert_calls) == 0


# ---------------------------------------------------------------------------
# T4 — Valid Claude JSON → signal fields written correctly to DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_valid_classification_correct_db_params() -> None:
    """Claude returns valid JSON → INSERT params include correct direction and confidence."""
    row = _make_row(headline="SEC approves BTC ETF")
    mock_pool, mock_cursor = _make_pool_with_rows([row])
    _mock_pipe4 = MagicMock()
    _mock_pipe4.lpush = MagicMock()
    _mock_pipe4.ltrim = MagicMock()
    _mock_pipe4.expire = MagicMock()
    _mock_pipe4.execute = AsyncMock(return_value=[1, 1, 1])
    mock_redis = AsyncMock()
    mock_redis.pipeline = MagicMock(return_value=_mock_pipe4)  # pipeline() is sync

    with patch("llm.news_classifier.ClaudeClient") as mock_client_cls:
        mock_client_cls.return_value.complete = AsyncMock(return_value=_VALID_CLAUDE_JSON)

        nc = NewsClassifier(_make_settings(), mock_pool, mock_redis)
        with patch.object(nc, "_load_thresholds", return_value={}):
            await nc._classify_cycle()

    # Find the INSERT INTO news_signals call
    insert_call = next(
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO news_signals" in str(c)
    )
    params = insert_call[0][1]
    # params order: (news_event_id, relevant, direction, confidence, event_type,
    #               assets, reasoning, headline, source, published_at, age_minutes)
    assert params[1] is True         # relevant
    assert params[2] == "bullish"    # direction
    assert params[3] == "high"       # confidence
    assert params[4] == "regulatory" # event_type


# ---------------------------------------------------------------------------
# T5 — Redis updated with TTL after successful classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_redis_updated_with_ttl() -> None:
    """After a successful classification, news_signals:latest is updated with TTL."""
    row = _make_row()
    mock_pool, _cursor = _make_pool_with_rows([row])

    mock_pipeline = MagicMock()
    mock_pipeline.lpush = MagicMock()
    mock_pipeline.ltrim = MagicMock()
    mock_pipeline.expire = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, 1, 1])

    mock_redis = AsyncMock()
    mock_redis.pipeline = MagicMock(return_value=mock_pipeline)  # pipeline() is sync

    with patch("llm.news_classifier.ClaudeClient") as mock_client_cls:
        mock_client_cls.return_value.complete = AsyncMock(return_value=_VALID_CLAUDE_JSON)

        nc = NewsClassifier(_make_settings(), mock_pool, mock_redis)
        with patch.object(nc, "_load_thresholds", return_value={}):
            await nc._classify_cycle()

    mock_pipeline.lpush.assert_called_once()
    key_arg = mock_pipeline.lpush.call_args[0][0]
    assert key_arg == _REDIS_KEY

    mock_pipeline.expire.assert_called_once()
    ttl_arg = mock_pipeline.expire.call_args[0][1]
    assert ttl_arg == 7200  # default TTL from thresholds fallback


# ---------------------------------------------------------------------------
# T6 — Prompt builder returns non-empty string
# ---------------------------------------------------------------------------


def test_t6_news_classify_prompt_returns_string() -> None:
    """news_classify.build() returns a non-empty string for any inputs."""
    result = news_classify.build(
        headline="SEC approves BTC spot ETF",
        published_at="2026-03-07T10:00:00Z",
        source="cryptopanic",
    )
    assert isinstance(result, str)
    assert len(result) > 50
    assert "SEC approves BTC spot ETF" in result
    assert isinstance(news_classify.SYSTEM, str)
    assert len(news_classify.SYSTEM) > 0
