"""
Unit tests for CoinglassHeatmapCollector (coinglass/heatmap_collector.py) and
coinglass/heatmap_db.py.

6 deterministic test vectors — no live I/O:

  T1  _parse_heatmap() with 4 levels (2 above, 2 below) → 4 rows, correct direction/fields
  T2  _parse_heatmap() with empty y_axis → returns ([], {})
  T3  _parse_heatmap() with missing price_candlesticks → returns ([], {})
  T4  _parse_heatmap() with top_n=1 → top 1 per direction (2 rows total)
  T5  insert_heatmap_rows() with 0 rows → returns 0, no DB call
  T6  Settings default → coinglass_heatmap_poll_interval_secs == 300
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

from coinglass.heatmap_collector import _parse_heatmap
from coinglass.heatmap_db import insert_heatmap_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _now() -> datetime:
    return datetime(2026, 2, 28, 14, 0, 0, tzinfo=timezone.utc)


def _heatmap_data(
    y_axis: list,
    liq_data: list,
    candlesticks: list | None = None,
    current_price: float = 50000.0,
) -> dict:
    """Build a minimal heatmap data dict for testing."""
    if candlesticks is None:
        # [timestamp, open, high, low, close, volume] — close at index 4
        candlesticks = [[1740751200000, 49000, 51000, 48000, current_price, 1000.0]]
    return {
        "y_axis": y_axis,
        "liquidation_leverage_data": liq_data,
        "price_candlesticks": candlesticks,
    }


# ---------------------------------------------------------------------------
# T1: _parse_heatmap with 4 valid levels (2 above, 2 below)
# ---------------------------------------------------------------------------


def test_t1_parse_heatmap_two_above_two_below() -> None:
    """4 price levels: 2 above current, 2 below → 4 rows with correct direction tags."""
    now = _now()
    current_price = 50000.0
    # y_axis: prices at indices 0..3
    y_axis = [48000.0, 49000.0, 51000.0, 52000.0]
    # liq_data: [x_idx, y_idx, liq_usd] — time dim collapsed by summing
    liq_data = [
        [0, 0, 1_000_000],   # 48000 → below
        [0, 1, 800_000],     # 49000 → below
        [0, 2, 1_500_000],   # 51000 → above
        [0, 3, 600_000],     # 52000 → above
    ]
    data = _heatmap_data(y_axis, liq_data, current_price=current_price)

    rows, payload = _parse_heatmap(data, "BTC", now, top_n=20)

    assert len(rows) == 4

    # Check DB row shape: (time, symbol, price_level, liquidation_usd, direction)
    for row in rows:
        t, sym, price, liq, direction = row
        assert t == now
        assert sym == "BTC"
        assert liq > 0
        assert direction in ("above", "below")
        if price > current_price:
            assert direction == "above"
        else:
            assert direction == "below"

    # Check Redis payload structure
    assert "above" in payload
    assert "below" in payload
    assert payload["current_price"] == current_price
    assert len(payload["above"]) == 2
    assert len(payload["below"]) == 2
    # Validate payload keys
    for entry in payload["above"]:
        assert "price_level" in entry
        assert "liq_usd" in entry


# ---------------------------------------------------------------------------
# T2: _parse_heatmap with empty y_axis → ([], {})
# ---------------------------------------------------------------------------


def test_t2_parse_heatmap_empty_y_axis() -> None:
    """Empty y_axis → ([], {}) — guard triggers before any processing."""
    now = _now()
    data = _heatmap_data(y_axis=[], liq_data=[[0, 0, 1_000_000]])

    rows, payload = _parse_heatmap(data, "ETH", now, top_n=20)

    assert rows == []
    assert payload == {}


# ---------------------------------------------------------------------------
# T3: _parse_heatmap with missing price_candlesticks → ([], {})
# ---------------------------------------------------------------------------


def test_t3_parse_heatmap_missing_candlesticks() -> None:
    """Missing price_candlesticks → ([], {}) — can't determine current price."""
    now = _now()
    data = {
        "y_axis": [48000.0, 51000.0],
        "liquidation_leverage_data": [[0, 0, 500_000], [0, 1, 700_000]],
        # price_candlesticks intentionally absent
    }

    rows, payload = _parse_heatmap(data, "SOL", now, top_n=20)

    assert rows == []
    assert payload == {}


# ---------------------------------------------------------------------------
# T4: _parse_heatmap with top_n=1 → top 1 per direction (2 rows total)
# ---------------------------------------------------------------------------


def test_t4_parse_heatmap_top_n_limits_rows() -> None:
    """top_n=1 keeps only the largest cluster per direction → 2 rows."""
    now = _now()
    current_price = 50000.0
    y_axis = [47000.0, 48000.0, 51000.0, 53000.0]
    liq_data = [
        [0, 0, 200_000],     # 47000 → below
        [0, 1, 900_000],     # 48000 → below (largest below)
        [0, 2, 1_200_000],   # 51000 → above (largest above)
        [0, 3, 400_000],     # 53000 → above
    ]
    data = _heatmap_data(y_axis, liq_data, current_price=current_price)

    rows, payload = _parse_heatmap(data, "BTC", now, top_n=1)

    assert len(rows) == 2  # 1 above + 1 below

    above_rows = [r for r in rows if r[4] == "above"]
    below_rows = [r for r in rows if r[4] == "below"]
    assert len(above_rows) == 1
    assert len(below_rows) == 1

    # Largest above = 51000 (1_200_000 liq), largest below = 48000 (900_000 liq)
    assert float(above_rows[0][2]) == 51000.0
    assert float(above_rows[0][3]) == 1_200_000.0
    assert float(below_rows[0][2]) == 48000.0
    assert float(below_rows[0][3]) == 900_000.0

    assert len(payload["above"]) == 1
    assert len(payload["below"]) == 1


# ---------------------------------------------------------------------------
# T5: insert_heatmap_rows with 0 rows → returns 0, no DB call
# ---------------------------------------------------------------------------


def test_t5_insert_zero_rows_no_db_call() -> None:
    """insert_heatmap_rows([]) → returns 0 without touching the DB pool."""
    pool = MagicMock()
    result = _run(insert_heatmap_rows(pool, []))
    assert result == 0
    pool.connection.assert_not_called()


# ---------------------------------------------------------------------------
# T6: Settings default → coinglass_heatmap_poll_interval_secs == 300
# ---------------------------------------------------------------------------


def test_t6_settings_default_poll_interval() -> None:
    """coinglass_heatmap_poll_interval_secs defaults to 300 (5-minute heatmap polling)."""
    from config import Settings

    s = Settings(_env_file=None)
    assert s.coinglass_heatmap_poll_interval_secs == 300
    assert s.coinglass_heatmap_top_n == 20
