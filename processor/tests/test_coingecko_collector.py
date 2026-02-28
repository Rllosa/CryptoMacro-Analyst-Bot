"""
Unit tests for CoinGeckoCollector (coingecko/collector.py) and coingecko/db.py.

6 deterministic test vectors — no live I/O:

  T1  _parse_global() with valid response      → 1 tuple (time, 58.4)
  T2  _parse_global() with btc_dominance=None  → [], no crash
  T3  _parse_global() with empty data dict     → [], no crash
  T4  _cache_latest() with valid row           → Redis SET correct key/value/TTL=600
  T5  upsert_market_global() with 0 rows       → returns 0, no DB call
  T6  Settings default                         → coingecko_poll_interval_secs == 600
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from coingecko.collector import CoinGeckoCollector, _parse_global
from coingecko.db import upsert_market_global


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _settings_stub():
    s = MagicMock()
    s.coingecko_poll_interval_secs = 600
    return s


def _make_collector() -> tuple[CoinGeckoCollector, AsyncMock]:
    redis = AsyncMock()
    collector = CoinGeckoCollector(_settings_stub(), MagicMock(), redis)
    return collector, redis


# ---------------------------------------------------------------------------
# T1: _parse_global with valid response → 1 tuple
# ---------------------------------------------------------------------------


def test_t1_parse_global_valid_response() -> None:
    """Valid /global response → 1 (time, btc_dominance) tuple with correct value."""
    now = datetime(2026, 2, 28, 14, 0, 0, tzinfo=timezone.utc)
    data = {"data": {"btc_dominance": 58.4, "eth_dominance": 11.2}}

    rows = _parse_global(data, now)

    assert len(rows) == 1
    t, btc_d = rows[0]
    assert t == now
    assert btc_d == 58.4


# ---------------------------------------------------------------------------
# T2: _parse_global with btc_dominance=None → []
# ---------------------------------------------------------------------------


def test_t2_parse_global_none_btc_dominance() -> None:
    """btc_dominance explicitly None → empty result, no crash."""
    now = datetime(2026, 2, 28, 14, 0, 0, tzinfo=timezone.utc)
    data = {"data": {"btc_dominance": None}}

    rows = _parse_global(data, now)

    assert rows == []


# ---------------------------------------------------------------------------
# T3: _parse_global with empty data dict → []
# ---------------------------------------------------------------------------


def test_t3_parse_global_empty_data() -> None:
    """Missing 'data' key → empty result, no crash."""
    now = datetime(2026, 2, 28, 14, 0, 0, tzinfo=timezone.utc)

    rows = _parse_global({}, now)

    assert rows == []


# ---------------------------------------------------------------------------
# T4: _cache_latest writes correct Redis key with TTL 600
# ---------------------------------------------------------------------------


def test_t4_cache_latest_writes_correct_key() -> None:
    """_cache_latest writes coingecko:latest:btc_d with btc_d value and ex=600."""
    collector, redis = _make_collector()

    now = datetime(2026, 2, 28, 14, 0, 0, tzinfo=timezone.utc)
    rows = [(now, 58.413)]
    _run(collector._cache_latest(rows))

    redis.set.assert_called_once()
    call = redis.set.call_args

    assert call.args[0] == "coingecko:latest:btc_d"
    payload = json.loads(call.args[1])
    assert payload["btc_d"] == 58.413
    assert payload["time"] == now.isoformat()
    assert call.kwargs["ex"] == 600


# ---------------------------------------------------------------------------
# T5: upsert_market_global with 0 rows → returns 0, no DB call
# ---------------------------------------------------------------------------


def test_t5_upsert_zero_rows_no_db_call() -> None:
    """upsert_market_global([]) → returns 0 without touching the DB pool."""
    pool = MagicMock()
    result = _run(upsert_market_global(pool, []))
    assert result == 0
    pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# T6: Settings default → coingecko_poll_interval_secs == 600
# ---------------------------------------------------------------------------


def test_t6_settings_default_poll_interval() -> None:
    """coingecko_poll_interval_secs defaults to 600 (10-minute BTC.D polling)."""
    from config import Settings

    s = Settings(_env_file=None)
    assert s.coingecko_poll_interval_secs == 600
