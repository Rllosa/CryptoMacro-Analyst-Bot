"""
Unit tests for DeribitCollector (deribit/collector.py) and deribit/db.py.

6 deterministic test vectors — no live I/O:

  T1  _parse_candles() with 3 valid rows        → 3 correct tuples
  T2  _parse_candles() with empty data          → [], no crash
  T3  _parse_candles() with a None-value row    → that row skipped, others returned
  T4  _cache_latest() BTC+ETH rows              → Redis SET with correct keys and TTL=7200
  T5  upsert_deribit_dvol() with 0 rows         → returns 0, no DB call
  T6  Settings default                          → deribit_poll_interval_secs == 3600
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from deribit.collector import DeribitCollector, _parse_candles
from deribit.db import upsert_deribit_dvol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _settings_stub():
    s = MagicMock()
    s.deribit_poll_interval_secs = 3600
    return s


def _make_collector() -> tuple[DeribitCollector, AsyncMock]:
    redis = AsyncMock()
    collector = DeribitCollector(_settings_stub(), MagicMock(), redis)
    return collector, redis


# ---------------------------------------------------------------------------
# T1: _parse_candles with 3 valid rows → 3 correct tuples
# ---------------------------------------------------------------------------


def test_t1_parse_candles_three_valid_rows() -> None:
    """3 valid API rows → 3 (time, currency, open, high, low, close) tuples."""
    data = [
        [1_700_000_000_000, 50.0, 55.0, 48.0, 52.0],
        [1_700_003_600_000, 52.0, 58.0, 51.0, 57.0],
        [1_700_007_200_000, 57.0, 60.0, 55.0, 59.0],
    ]
    rows = _parse_candles(data, "BTC")

    assert len(rows) == 3
    # Check first row
    t, currency, open_, high, low, close = rows[0]
    assert isinstance(t, datetime)
    assert t.tzinfo is not None
    assert currency == "BTC"
    assert open_ == 50.0
    assert high == 55.0
    assert low == 48.0
    assert close == 52.0
    # Last row close
    assert rows[2][5] == 59.0


# ---------------------------------------------------------------------------
# T2: _parse_candles with empty data → []
# ---------------------------------------------------------------------------


def test_t2_parse_candles_empty_data() -> None:
    """Empty data list → empty result, no crash."""
    rows = _parse_candles([], "ETH")
    assert rows == []


# ---------------------------------------------------------------------------
# T3: _parse_candles with a row containing None → that row skipped
# ---------------------------------------------------------------------------


def test_t3_parse_candles_none_value_skipped() -> None:
    """Row with a None value is skipped; other rows are returned normally."""
    data = [
        [1_700_000_000_000, 50.0, 55.0, 48.0, None],   # None close → skip
        [1_700_003_600_000, 52.0, 58.0, 51.0, 57.0],   # valid
    ]
    rows = _parse_candles(data, "BTC")

    assert len(rows) == 1
    assert rows[0][5] == 57.0


# ---------------------------------------------------------------------------
# T4: _cache_latest writes correct Redis keys with TTL 7200
# ---------------------------------------------------------------------------


def test_t4_cache_latest_writes_correct_keys() -> None:
    """_cache_latest writes deribit:latest:btc and deribit:latest:eth with ex=7200."""
    collector, redis = _make_collector()

    now = datetime(2026, 2, 28, 14, 0, 0, tzinfo=timezone.utc)
    rows = [
        (now, "BTC", 50.0, 55.0, 48.0, 52.4),
        (now, "ETH", 60.0, 65.0, 58.0, 61.8),
    ]
    _run(collector._cache_latest(rows))

    calls = {call.args[0]: call for call in redis.set.call_args_list}

    assert "deribit:latest:btc" in calls
    assert "deribit:latest:eth" in calls

    btc_call = calls["deribit:latest:btc"]
    payload = json.loads(btc_call.args[1])
    assert payload["close"] == 52.4
    assert btc_call.kwargs["ex"] == 7200

    eth_call = calls["deribit:latest:eth"]
    payload = json.loads(eth_call.args[1])
    assert payload["close"] == 61.8
    assert eth_call.kwargs["ex"] == 7200


# ---------------------------------------------------------------------------
# T5: upsert_deribit_dvol with 0 rows → returns 0, no DB call
# ---------------------------------------------------------------------------


def test_t5_upsert_zero_rows_no_db_call() -> None:
    """upsert_deribit_dvol([]) → returns 0 without touching the DB pool."""
    pool = MagicMock()
    result = _run(upsert_deribit_dvol(pool, []))
    assert result == 0
    pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# T6: Settings default → deribit_poll_interval_secs == 3600
# ---------------------------------------------------------------------------


def test_t6_settings_default_poll_interval() -> None:
    """deribit_poll_interval_secs defaults to 3600 (1-hour DVOL resolution)."""
    from config import Settings

    s = Settings(_env_file=None)
    assert s.deribit_poll_interval_secs == 3600
