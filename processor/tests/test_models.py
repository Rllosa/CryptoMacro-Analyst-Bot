from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from models import CandleMessage

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

VALID_PAYLOAD: dict = {
    "symbol": "BTCUSDT",
    "exchange": "binance",
    "timeframe": "1m",
    "time": "2026-02-16T12:00:00Z",
    "open": 68000.0,
    "high": 68500.0,
    "low": 67800.0,
    "close": 68200.0,
    "volume": 10.5,
    "quote_volume": 714450.0,
    "trades": 1234,
}


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


def test_valid_payload_parses():
    candle = CandleMessage.model_validate(VALID_PAYLOAD)
    assert candle.symbol == "BTCUSDT"
    assert candle.exchange == "binance"
    assert candle.timeframe == "1m"
    assert candle.open == 68000.0
    assert candle.trades == 1234


def test_time_z_suffix_becomes_utc():
    candle = CandleMessage.model_validate(VALID_PAYLOAD)
    assert candle.time.tzinfo is not None
    assert candle.time == datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_time_iso_offset_format():
    payload = {**VALID_PAYLOAD, "time": "2026-02-16T12:00:00+00:00"}
    candle = CandleMessage.model_validate(payload)
    assert candle.time.tzinfo is not None


def test_trades_is_optional():
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "trades"}
    candle = CandleMessage.model_validate(payload)
    assert candle.trades is None


def test_missing_required_field_raises():
    for field in (
        "symbol",
        "exchange",
        "timeframe",
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    ):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != field}
        with pytest.raises(ValidationError):
            CandleMessage.model_validate(payload)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


def test_zero_price_rejected():
    with pytest.raises(ValidationError):
        CandleMessage.model_validate({**VALID_PAYLOAD, "open": 0.0})


def test_negative_price_rejected():
    with pytest.raises(ValidationError):
        CandleMessage.model_validate({**VALID_PAYLOAD, "close": -1.0})


def test_zero_volume_allowed():
    # volume >= 0 is valid per contract
    candle = CandleMessage.model_validate({**VALID_PAYLOAD, "volume": 0.0})
    assert candle.volume == 0.0


# ---------------------------------------------------------------------------
# to_db_row tests
# ---------------------------------------------------------------------------


def test_to_db_row_structure():
    candle = CandleMessage.model_validate(VALID_PAYLOAD)
    row = candle.to_db_row()
    # (time, symbol, timeframe, open, high, low, close, volume, quote_volume, num_trades)
    assert len(row) == 10
    time, symbol, timeframe, open_, high, low, close, volume, quote_volume, num_trades = row
    assert isinstance(time, datetime)
    assert symbol == "BTCUSDT"
    assert timeframe == "1m"
    assert open_ == 68000.0
    assert high == 68500.0
    assert low == 67800.0
    assert close == 68200.0
    assert volume == 10.5
    assert quote_volume == 714450.0
    assert num_trades == 1234


def test_to_db_row_trades_none_when_absent():
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "trades"}
    candle = CandleMessage.model_validate(payload)
    row = candle.to_db_row()
    assert row[9] is None  # num_trades position


def test_to_db_row_exchange_not_in_tuple():
    # exchange is NATS-only; must NOT appear in the DB row
    candle = CandleMessage.model_validate(VALID_PAYLOAD)
    row = candle.to_db_row()
    assert "binance" not in row
