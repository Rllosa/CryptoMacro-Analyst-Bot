#!/usr/bin/env python3
"""
QA-2: Phase 2 end-to-end smoke test.

Covers:
  1. Redis + DB reachable
  2. NewsEventEvaluator: qualifying signal fires evaluate_and_fire (conditions_met=True)
  3. NewsEventEvaluator: stale signal (age_minutes > max_age_minutes) is filtered
  4. Positioning bias: _compute_direction_label returns correct label (inline assertion)
  5. F-7 schema validation: envelope with positioning_bias passes daily_brief.json
  6. Thresholds.yaml: news_classifier block has expected values

Requires: docker-compose timescaledb + redis running.
Exit code 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

_REPO = Path(__file__).parents[1]
sys.path.insert(0, str(_REPO / "processor" / "src"))

os.environ.setdefault("THRESHOLDS_PATH", str(_REPO / "configs" / "thresholds.yaml"))
os.environ.setdefault("SYMBOLS_PATH", str(_REPO / "configs" / "symbols.yaml"))
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "cryptomacro")
os.environ.setdefault("POSTGRES_PASSWORD", "cryptomacro_dev_password")
os.environ.setdefault("POSTGRES_DB", "cryptomacro")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import jsonschema
import redis.asyncio as aioredis
import yaml
from psycopg_pool import AsyncConnectionPool

from alerts.news_event import NewsEventEvaluator, NewsEventParams
from config import Settings
from llm.scheduler import _compute_direction_label

_SCHEMA_PATH = _REPO / "schema" / "contracts" / "daily_brief.json"
_THRESHOLDS_PATH = _REPO / "configs" / "thresholds.yaml"

_NEWS_REDIS_KEY = "news_signals:latest"

# Qualifying signal fixture
_QUALIFYING_SIGNAL = json.dumps({
    "news_event_id": str(uuid4()),
    "relevant": True,
    "confidence": "high",
    "direction": "bullish",
    "event_type": "regulatory",
    "assets": ["BTC"],
    "headline": "SEC approves spot BTC ETF expansion",
    "source": "reuters",
    "age_minutes": 5,
    "reasoning": "Major positive regulatory catalyst",
})

# Stale signal — age_minutes exceeds max_age_minutes (20)
_STALE_SIGNAL = json.dumps({
    "news_event_id": str(uuid4()),
    "relevant": True,
    "confidence": "high",
    "direction": "bearish",
    "event_type": "regulatory",
    "assets": ["BTC"],
    "headline": "Old bearish news",
    "source": "bloomberg",
    "age_minutes": 25,
    "reasoning": "Stale signal should be filtered",
})


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    print("\nFAIL")
    sys.exit(1)


async def _run() -> None:
    print("Running Phase 2 end-to-end smoke test (QA-2)...\n")

    settings = Settings(_env_file=None)

    # ── 1. Redis + DB reachability ───────────────────────────────────────────
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.ping()
    except Exception as exc:
        _fail(f"Redis unreachable at {settings.redis_url}: {exc}")
    _ok(f"Redis reachable ({settings.redis_url})")

    pool = AsyncConnectionPool(settings.db_dsn, open=False, min_size=1, max_size=2)
    try:
        await pool.open()
        await pool.check()
    except Exception as exc:
        _fail(f"DB unreachable at {settings.db_dsn}: {exc}")
    _ok(f"DB reachable (host={settings.postgres_host})")

    # ── 2. NewsEventEvaluator: qualifying signal fires evaluate_and_fire ─────
    await redis.delete(_NEWS_REDIS_KEY)
    await redis.lpush(_NEWS_REDIS_KEY, _QUALIFYING_SIGNAL)

    mock_engine = MagicMock()
    mock_engine.evaluate_and_fire = AsyncMock()

    params = NewsEventParams.load(str(_THRESHOLDS_PATH))
    evaluator = NewsEventEvaluator.__new__(NewsEventEvaluator)
    evaluator._settings = settings
    evaluator._redis = redis
    evaluator._engine = mock_engine
    evaluator._params = params

    cycle_time = datetime.now(tz=timezone.utc)
    await evaluator._evaluate_cycle(cycle_time)

    calls = mock_engine.evaluate_and_fire.call_args_list
    if not calls:
        _fail("evaluate_and_fire NOT called for qualifying signal")
    call_kwargs = calls[0].kwargs
    if not call_kwargs.get("conditions_met"):
        _fail(f"evaluate_and_fire called but conditions_met={call_kwargs.get('conditions_met')!r}")
    if call_kwargs.get("alert_type") != "NEWS_EVENT":
        _fail(f"Wrong alert_type: {call_kwargs.get('alert_type')!r}")
    _ok(f"NewsEventEvaluator: qualifying signal → evaluate_and_fire(conditions_met=True, symbol={call_kwargs.get('symbol')!r})")

    # ── 3. NewsEventEvaluator: stale signal filtered ─────────────────────────
    await redis.delete(_NEWS_REDIS_KEY)
    await redis.lpush(_NEWS_REDIS_KEY, _STALE_SIGNAL)

    mock_engine2 = MagicMock()
    mock_engine2.evaluate_and_fire = AsyncMock()

    evaluator2 = NewsEventEvaluator.__new__(NewsEventEvaluator)
    evaluator2._settings = settings
    evaluator2._redis = redis
    evaluator2._engine = mock_engine2
    evaluator2._params = params

    await evaluator2._evaluate_cycle(cycle_time)

    if mock_engine2.evaluate_and_fire.called:
        _fail(f"evaluate_and_fire was called for stale signal (age_minutes=25 > max={params.max_age_minutes})")
    _ok(f"NewsEventEvaluator: stale signal (age_minutes=25) filtered — evaluate_and_fire NOT called")

    # ── 4. Positioning bias direction label ───────────────────────────────────
    label = _compute_direction_label(
        regime="RISK_ON_TREND",
        confidence=0.82,
        btc_trend=0.0,
        conf_high=0.80,
        conf_medium=0.60,
        vol_trend_thresh=0.005,
    )
    if label != "Strongly BULLISH":
        _fail(f"Positioning bias label wrong: expected 'Strongly BULLISH', got {label!r}")
    _ok(f"Positioning bias: RISK_ON_TREND conf=0.82 → {label!r}")

    vol_label = _compute_direction_label(
        regime="VOL_EXPANSION",
        confidence=0.70,
        btc_trend=-0.010,
        conf_high=0.80,
        conf_medium=0.60,
        vol_trend_thresh=0.005,
    )
    if vol_label != "VOLATILE — bearish expansion":
        _fail(f"VOL_EXPANSION label wrong: expected 'VOLATILE — bearish expansion', got {vol_label!r}")
    _ok(f"Positioning bias: VOL_EXPANSION btc_trend=-0.01 → {vol_label!r}")

    # ── 5. F-7 schema: envelope with positioning_bias validates ───────────────
    with _SCHEMA_PATH.open() as f:
        schema = json.load(f)

    now = datetime.now(tz=timezone.utc)
    envelope = {
        "report_id": str(uuid4()),
        "report_type": "daily_brief",
        "generated_at": now.isoformat(),
        "time_range": {
            "start": now.isoformat(),
            "end": now.isoformat(),
        },
        "regime_summary": {
            "current_regime": "RISK_ON_TREND",
            "confidence": 0.82,
            "transitions": [],
            "analysis": "Bullish regime with strong BTC trend.",
        },
        "alert_summary": {
            "total_alerts": 1,
            "by_type": {"NEWS_EVENT": 1},
            "by_severity": {"HIGH": 1, "MEDIUM": 0, "LOW": 0},
            "notable_alerts": [
                {
                    "alert_id": str(uuid4()),
                    "alert_type": "NEWS_EVENT",
                    "symbol": "BTC",
                    "severity": "HIGH",
                    "summary": "Regulatory approval catalyst",
                }
            ],
        },
        "market_summary": {
            "assets": {
                "BTC": {
                    "price_change_pct": 0.015,
                    "volume_change_pct": 1.2,
                    "volatility_regime": "low",
                }
            },
            "correlations": {"btc_dxy": -0.3},
        },
        "key_insights": ["BTC trending strongly with low volatility."],
        "watch_list": ["Watch BTC 50k support level."],
        "positioning_bias": {
            "direction": "Strongly BULLISH",
            "regime": "RISK_ON_TREND",
            "confidence": 0.82,
            "leverage_risk": "MODERATE — funding z-score near neutral",
            "alt_exposure": "SELECTIVE — focus on BTC/ETH",
            "key_risk": "Macro stress spike could reverse regime rapidly.",
            "conditions_favor": "Long BTC with tight stops below 50k.",
        },
        "llm_metadata": {
            "model": "claude-sonnet-4-6",
            "tokens_used": 512,
            "cost_usd": 0.002,
            "generation_time_ms": 1800,
        },
    }

    try:
        jsonschema.validate(instance=envelope, schema=schema)
    except jsonschema.ValidationError as exc:
        _fail(f"F-7 schema validation failed: {exc.message}")
    _ok("F-7 schema: envelope with positioning_bias validates against daily_brief.json")

    # ── 6. Thresholds.yaml: news_classifier block ─────────────────────────────
    with _THRESHOLDS_PATH.open() as fh:
        thresholds = yaml.safe_load(fh)

    nc = thresholds.get("news_classifier", {})
    checks = [
        ("max_per_cycle", 10),
        ("max_age_minutes", 30),
        ("redis_ttl_secs", 7200),
    ]
    for field, expected in checks:
        actual = nc.get(field)
        if actual != expected:
            _fail(f"thresholds.yaml news_classifier.{field}={actual!r}, expected {expected!r}")
    _ok(f"thresholds.yaml: news_classifier.max_per_cycle={nc['max_per_cycle']}, max_age_minutes={nc['max_age_minutes']}, redis_ttl_secs={nc['redis_ttl_secs']}")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    await redis.delete(_NEWS_REDIS_KEY)
    await pool.close()
    await redis.aclose()

    print("\nPASS")


if __name__ == "__main__":
    asyncio.run(_run())
