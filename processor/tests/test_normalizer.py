from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from normalizer import Normalizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(payload: dict) -> MagicMock:
    """Build a mock NATS message with .data (bytes) and .ack() coroutine."""
    msg = MagicMock()
    msg.data = json.dumps(payload).encode()
    msg.ack = AsyncMock()
    return msg


VALID_PAYLOAD = {
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


def _make_normalizer() -> Normalizer:
    settings = MagicMock()
    settings.batch_size = 100
    settings.batch_timeout_secs = 5.0
    pool = MagicMock()
    return Normalizer(settings, pool)


# ---------------------------------------------------------------------------
# _parse_message tests
# ---------------------------------------------------------------------------


def test_parse_valid_message_returns_tuple():
    norm = _make_normalizer()
    msg = _make_msg(VALID_PAYLOAD)
    row = norm._parse_message(msg)
    assert row is not None
    assert len(row) == 10
    assert row[1] == "BTCUSDT"


def test_parse_invalid_json_returns_none():
    norm = _make_normalizer()
    msg = MagicMock()
    msg.data = b"not-valid-json{{"
    row = norm._parse_message(msg)
    assert row is None


def test_parse_missing_field_returns_none():
    norm = _make_normalizer()
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "open"}
    msg = _make_msg(payload)
    row = norm._parse_message(msg)
    assert row is None


def test_parse_zero_price_returns_none():
    norm = _make_normalizer()
    msg = _make_msg({**VALID_PAYLOAD, "close": 0.0})
    row = norm._parse_message(msg)
    assert row is None


def test_parse_negative_price_returns_none():
    norm = _make_normalizer()
    msg = _make_msg({**VALID_PAYLOAD, "high": -1.5})
    row = norm._parse_message(msg)
    assert row is None


def test_parse_without_trades_returns_none_in_last_column():
    norm = _make_normalizer()
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "trades"}
    msg = _make_msg(payload)
    row = norm._parse_message(msg)
    assert row is not None
    assert row[9] is None  # num_trades


# ---------------------------------------------------------------------------
# request_shutdown test
# ---------------------------------------------------------------------------


def test_request_shutdown_sets_event():
    norm = _make_normalizer()
    assert not norm._shutdown.is_set()
    norm.request_shutdown()
    assert norm._shutdown.is_set()
