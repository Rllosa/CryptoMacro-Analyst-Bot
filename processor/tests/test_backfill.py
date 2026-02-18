from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backfill import _kline_to_row, detect_gap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal Binance REST kline array (15 elements; we only use a few indices)
# [open_time, open, high, low, close, volume, close_time, quote_vol, trades, ...]
_SAMPLE_KLINE = [
    1708084800000,  # 0: open_time (ms) → 2024-02-16T12:00:00Z
    "68000.00",  # 1: open
    "68500.20",  # 2: high
    "67800.50",  # 3: low
    "68200.10",  # 4: close
    "10.500",  # 5: volume
    1708084859999,  # 6: close_time
    "714450.00",  # 7: quote_volume
    1234,  # 8: trades
    "5.250",  # 9: taker_buy_base
    "357225.00",  # 10: taker_buy_quote
    "0",  # 11: ignore
]


# ---------------------------------------------------------------------------
# _kline_to_row tests
# ---------------------------------------------------------------------------


def test_kline_to_row_symbol():
    row = _kline_to_row("BTCUSDT", _SAMPLE_KLINE)
    assert row[1] == "BTCUSDT"


def test_kline_to_row_timeframe():
    row = _kline_to_row("BTCUSDT", _SAMPLE_KLINE)
    assert row[2] == "1m"


def test_kline_to_row_time_is_utc_datetime():
    row = _kline_to_row("BTCUSDT", _SAMPLE_KLINE)
    dt = row[0]
    assert isinstance(dt, datetime)
    assert dt.tzinfo == timezone.utc
    # 1708084800000 ms → 2024-02-16T12:00:00Z
    assert dt == datetime(2024, 2, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_kline_to_row_prices_are_floats():
    row = _kline_to_row("BTCUSDT", _SAMPLE_KLINE)
    _, _, _, open_, high, low, close, *_ = row
    assert isinstance(open_, float)
    assert open_ == pytest.approx(68000.0)
    assert high == pytest.approx(68500.2)
    assert low == pytest.approx(67800.5)
    assert close == pytest.approx(68200.1)


def test_kline_to_row_volume_and_quote():
    row = _kline_to_row("ETHUSDT", _SAMPLE_KLINE)
    volume = row[7]
    quote_volume = row[8]
    assert volume == pytest.approx(10.5)
    assert quote_volume == pytest.approx(714450.0)


def test_kline_to_row_trades_is_int():
    row = _kline_to_row("BTCUSDT", _SAMPLE_KLINE)
    assert row[9] == 1234
    assert isinstance(row[9], int)


def test_kline_to_row_tuple_length():
    row = _kline_to_row("BTCUSDT", _SAMPLE_KLINE)
    assert len(row) == 10


# ---------------------------------------------------------------------------
# detect_gap tests
# ---------------------------------------------------------------------------


def test_detect_gap_none_returns_false():
    assert detect_gap(None, timedelta(minutes=5)) is False


def test_detect_gap_fresh_data_returns_false():
    recent = datetime.now(tz=timezone.utc) - timedelta(minutes=2)
    assert detect_gap(recent, timedelta(minutes=5)) is False


def test_detect_gap_stale_data_returns_true():
    stale = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    assert detect_gap(stale, timedelta(minutes=5)) is True


def test_detect_gap_well_within_threshold_returns_false():
    # Data is 2 minutes old, threshold is 10 minutes — clearly no gap
    recent = datetime.now(tz=timezone.utc) - timedelta(minutes=2)
    assert detect_gap(recent, timedelta(minutes=10)) is False


def test_detect_gap_tz_naive_datetime_handled():
    # Naive datetime should be treated as UTC without raising
    naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    assert detect_gap(naive, timedelta(minutes=5)) is True
