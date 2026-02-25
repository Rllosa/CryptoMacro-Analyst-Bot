"""
Unit tests for the Coinglass collector (coinglass/).

Structure:
  - Model parsing (pure) — FundingEntry, OIEntry, LiqEntry, LongShortEntry
  - upsert_derivatives — SQL row count, empty-list short-circuit
  - CoinglassCollector — _fetch_symbol, _run_cycle, degradation, shutdown

All async tests use asyncio.run() — consistent with the rest of the suite.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coinglass.collector import CoinglassCollector
from coinglass.db import upsert_derivatives
from coinglass.models import FundingEntry, LiqEntry, LongShortEntry, OIEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 2, 24, 12, 0, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _settings_stub(poll_interval: int = 300):
    s = MagicMock()
    s.coinglass_api_key = "test-key"
    s.coinglass_base_url = "https://open-api.coinglass.com/public/v2"
    s.coinglass_poll_interval_secs = poll_interval
    return s


def _mock_pool():
    """Return (pool, mock_conn) where pool is an async context manager stub."""
    conn = AsyncMock()
    pool = MagicMock()

    @asynccontextmanager
    async def fake_connection():
        yield conn

    pool.connection = fake_connection
    return pool, conn


def _funding_raw(exchange: str = "Binance", rate: float = 0.0001) -> dict:
    return {"exchange": exchange, "fundingRate": rate}


def _oi_raw(exchange: str = "Binance", oi: float = 5_000_000_000.0) -> dict:
    return {"exchange": exchange, "openInterestUsd": oi}


def _liq_raw(exchange: str = "Binance", liq: float = 10_000_000.0) -> dict:
    return {"exchange": exchange, "liquidationUsd": liq}


def _ls_raw(exchange: str = "Binance", long: float = 0.55, short: float = 0.45) -> dict:
    return {"exchange": exchange, "longRatio": long, "shortRatio": short}


def _api_resp(items: list[dict]) -> dict:
    return {"code": "0", "msg": "success", "data": items}


def _make_collector(poll_interval: int = 300) -> CoinglassCollector:
    pool, _ = _mock_pool()
    return CoinglassCollector(_settings_stub(poll_interval), pool)


# ---------------------------------------------------------------------------
# Model parsing — FundingEntry
# ---------------------------------------------------------------------------


def test_funding_entry_parses_exchange_and_rate():
    e = FundingEntry.model_validate(_funding_raw("Binance", 0.0001))
    assert e.exchange == "Binance"
    assert e.funding_rate == pytest.approx(0.0001)


def test_funding_entry_missing_rate_is_none():
    e = FundingEntry.model_validate({"exchange": "OKX"})
    assert e.exchange == "OKX"
    assert e.funding_rate is None


def test_funding_entry_ignores_extra_fields():
    e = FundingEntry.model_validate({"exchange": "Bybit", "fundingRate": 0.0002, "unknown": 99})
    assert e.exchange == "Bybit"
    assert not hasattr(e, "unknown")


# ---------------------------------------------------------------------------
# Model parsing — OIEntry
# ---------------------------------------------------------------------------


def test_oi_entry_parses_exchange_and_oi():
    e = OIEntry.model_validate(_oi_raw("Binance", 5e9))
    assert e.exchange == "Binance"
    assert e.open_interest_usd == pytest.approx(5e9)


def test_oi_entry_missing_oi_is_none():
    e = OIEntry.model_validate({"exchange": "OKX"})
    assert e.open_interest_usd is None


# ---------------------------------------------------------------------------
# Model parsing — LiqEntry
# ---------------------------------------------------------------------------


def test_liq_entry_parses_exchange_and_liq():
    e = LiqEntry.model_validate(_liq_raw("Binance", 10_000_000.0))
    assert e.exchange == "Binance"
    assert e.liq_usd_1h == pytest.approx(10_000_000.0)


def test_liq_entry_missing_liq_is_none():
    e = LiqEntry.model_validate({"exchange": "Bybit"})
    assert e.liq_usd_1h is None


# ---------------------------------------------------------------------------
# Model parsing — LongShortEntry
# ---------------------------------------------------------------------------


def test_long_short_entry_parses_both_ratios():
    e = LongShortEntry.model_validate(_ls_raw("Binance", 0.55, 0.45))
    assert e.exchange == "Binance"
    assert e.long_account_ratio == pytest.approx(0.55)
    assert e.short_account_ratio == pytest.approx(0.45)


def test_long_short_entry_missing_ratios_are_none():
    e = LongShortEntry.model_validate({"exchange": "OKX"})
    assert e.long_account_ratio is None
    assert e.short_account_ratio is None


# ---------------------------------------------------------------------------
# upsert_derivatives — SQL path
# ---------------------------------------------------------------------------


def test_upsert_derivatives_empty_returns_zero():
    pool, _ = _mock_pool()
    result = _run(upsert_derivatives(pool, []))
    assert result == 0


def test_upsert_derivatives_returns_row_count():
    pool, conn = _mock_pool()
    rows = [
        (_NOW, "BTC", "binance", 0.0001, 5e9, 1e7, 0.55, 0.45),
        (_NOW, "BTC", "okx", 0.0002, 3e9, 5e6, 0.52, 0.48),
    ]
    result = _run(upsert_derivatives(pool, rows))
    assert result == 2
    conn.execute.assert_awaited_once()


def test_upsert_derivatives_passes_flat_params():
    """Verify execute receives a flat list (not nested tuples)."""
    pool, conn = _mock_pool()
    row = (_NOW, "ETH", "binance", 0.0003, 2e9, 8e6, 0.51, 0.49)
    _run(upsert_derivatives(pool, [row]))

    pos_args, _ = conn.execute.await_args
    flat_params = pos_args[1]  # execute(query, params) — params is second positional arg
    assert flat_params == list(row)


# ---------------------------------------------------------------------------
# CoinglassCollector._fetch_symbol
# ---------------------------------------------------------------------------


def _mock_session_with_responses(responses: dict[str, dict]) -> MagicMock:
    """
    Return a mock aiohttp.ClientSession where each endpoint path maps to a
    pre-built response dict.  The URL is matched via the endpoint suffix.
    """
    async def _fake_get(url: str, **kwargs):
        for endpoint, data in responses.items():
            if url.endswith(endpoint):
                resp = AsyncMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = data
                return resp
        resp = AsyncMock()
        resp.raise_for_status.side_effect = Exception(f"unexpected url: {url}")
        return resp

    @asynccontextmanager
    async def fake_get_ctx(url, **kwargs):
        yield await _fake_get(url, **kwargs)

    session = MagicMock()
    session.get = fake_get_ctx
    return session


def test_fetch_symbol_returns_one_row_per_exchange():
    collector = _make_collector()
    session = _mock_session_with_responses({
        "/futures/fundingRate/exchange-list":                  _api_resp([_funding_raw("Binance", 0.0001)]),
        "/futures/openInterest/exchange-list":                 _api_resp([_oi_raw("Binance", 5e9)]),
        "/futures/liquidation/exchange-list":                  _api_resp([_liq_raw("Binance", 1e7)]),
        "/futures/global-long-short-account-ratio/history":    _api_resp([_ls_raw("Binance", 0.55, 0.45)]),
    })
    rows = _run(collector._fetch_symbol(session, "BTC", _NOW))
    assert len(rows) == 1
    t, sym, exchange, fr, oi, liq, long_r, short_r = rows[0]
    assert t == _NOW
    assert sym == "BTC"
    assert exchange == "binance"
    assert fr == pytest.approx(0.0001)
    assert oi == pytest.approx(5e9)
    assert liq == pytest.approx(1e7)
    assert long_r == pytest.approx(0.55)
    assert short_r == pytest.approx(0.45)


def test_fetch_symbol_multiple_exchanges_returns_multiple_rows():
    collector = _make_collector()
    session = _mock_session_with_responses({
        "/futures/fundingRate/exchange-list":                  _api_resp([_funding_raw("Binance"), _funding_raw("OKX", 0.0002)]),
        "/futures/openInterest/exchange-list":                 _api_resp([_oi_raw("Binance"), _oi_raw("OKX", 3e9)]),
        "/futures/liquidation/exchange-list":                  _api_resp([_liq_raw("Binance"), _liq_raw("OKX", 5e6)]),
        "/futures/global-long-short-account-ratio/history":    _api_resp([_ls_raw("Binance"), _ls_raw("OKX", 0.52, 0.48)]),
    })
    rows = _run(collector._fetch_symbol(session, "ETH", _NOW))
    assert len(rows) == 2
    exchanges = {r[2] for r in rows}
    assert exchanges == {"binance", "okx"}


def test_fetch_symbol_exchange_in_one_endpoint_only_produces_nulls():
    """An exchange appearing in only /funding should have None for OI/liq/ls."""
    collector = _make_collector()
    session = _mock_session_with_responses({
        "/futures/fundingRate/exchange-list":                  _api_resp([_funding_raw("Bybit", 0.0003)]),
        "/futures/openInterest/exchange-list":                 _api_resp([]),
        "/futures/liquidation/exchange-list":                  _api_resp([]),
        "/futures/global-long-short-account-ratio/history":    _api_resp([]),
    })
    rows = _run(collector._fetch_symbol(session, "SOL", _NOW))
    assert len(rows) == 1
    _, _, _, fr, oi, liq, long_r, short_r = rows[0]
    assert fr == pytest.approx(0.0003)
    assert oi is None
    assert liq is None
    assert long_r is None
    assert short_r is None


def test_fetch_symbol_empty_responses_return_no_rows():
    collector = _make_collector()
    session = _mock_session_with_responses({
        "/futures/fundingRate/exchange-list":                  _api_resp([]),
        "/futures/openInterest/exchange-list":                 _api_resp([]),
        "/futures/liquidation/exchange-list":                  _api_resp([]),
        "/futures/global-long-short-account-ratio/history":    _api_resp([]),
    })
    rows = _run(collector._fetch_symbol(session, "HYPE", _NOW))
    assert rows == []


def test_fetch_symbol_http_error_propagates():
    """raise_for_status() raising should bubble up from _fetch_symbol."""
    collector = _make_collector()

    @asynccontextmanager
    async def bad_get(url, **kwargs):
        # Use MagicMock so raise_for_status() is synchronous (matches aiohttp behaviour)
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=Exception("HTTP 429"))
        yield resp

    session = MagicMock()
    session.get = bad_get

    with pytest.raises(Exception, match="HTTP 429"):
        _run(collector._fetch_symbol(session, "BTC", _NOW))


# ---------------------------------------------------------------------------
# CoinglassCollector._run_cycle
# ---------------------------------------------------------------------------


def test_run_cycle_writes_rows_for_good_symbols():
    collector = _make_collector()
    pool, conn = _mock_pool()
    collector._pool = pool

    good_row = (_NOW, "BTC", "binance", 0.0001, 5e9, 1e7, 0.55, 0.45)

    async def fake_fetch(session, sym, now):
        return [good_row] if sym == "BTC" else []

    collector._fetch_symbol = fake_fetch

    with patch("coinglass.collector.aiohttp.ClientSession") as MockSession:
        MockSession.return_value.__aenter__.return_value = MagicMock()
        MockSession.return_value.__aexit__ = AsyncMock(return_value=None)
        _run(collector._run_cycle())

    conn.execute.assert_awaited_once()


def test_run_cycle_symbol_failure_does_not_stop_others():
    """One symbol raising should not prevent the rest from writing."""
    collector = _make_collector()
    pool, conn = _mock_pool()
    collector._pool = pool

    good_row = (_NOW, "ETH", "binance", 0.0002, 3e9, 5e6, 0.51, 0.49)

    async def fake_fetch(session, sym, now):
        if sym == "BTC":
            raise RuntimeError("BTC fetch failed")
        if sym == "ETH":
            return [good_row]
        return []

    collector._fetch_symbol = fake_fetch

    with patch("coinglass.collector.aiohttp.ClientSession") as MockSession:
        MockSession.return_value.__aenter__.return_value = MagicMock()
        MockSession.return_value.__aexit__ = AsyncMock(return_value=None)
        _run(collector._run_cycle())

    # ETH row was written despite BTC failure
    conn.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Degradation counter
# ---------------------------------------------------------------------------


def test_consecutive_failures_increments_on_exception():
    """Counter reaches 3 after three consecutive exceptions (shutdown requested on 3rd)."""
    collector = _make_collector(poll_interval=0)

    call_count = 0

    async def fail_until_shutdown():
        nonlocal call_count
        call_count += 1
        # Third call: request shutdown but still raise so the counter is never reset
        if call_count >= 3:
            collector.request_shutdown()
        raise RuntimeError("API down")

    collector._run_cycle = fail_until_shutdown
    _run(collector.run())
    assert collector._consecutive_failures == 3
    assert call_count == 3


def test_consecutive_failures_reset_on_success():
    collector = _make_collector(poll_interval=0)

    call_count = 0

    async def mixed_cycle():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        collector.request_shutdown()

    collector._run_cycle = mixed_cycle
    _run(collector.run())
    # After a successful cycle the counter resets to 0
    assert collector._consecutive_failures == 0


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def test_shutdown_before_first_cycle_skips_cycle():
    collector = _make_collector(poll_interval=0)
    collector._run_cycle = AsyncMock()
    collector.request_shutdown()
    _run(collector.run())
    collector._run_cycle.assert_not_awaited()


def test_shutdown_after_one_cycle_stops_loop():
    collector = _make_collector(poll_interval=0)

    call_count = 0

    async def one_cycle():
        nonlocal call_count
        call_count += 1
        collector.request_shutdown()

    collector._run_cycle = one_cycle
    _run(collector.run())
    assert call_count == 1
