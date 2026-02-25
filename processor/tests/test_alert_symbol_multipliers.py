"""
Unit tests for SymbolMultipliers (alerts/symbol_multipliers.py).

Structure:
  - load() — 2 tests reading the real configs/symbols.yaml
  - get()  — 2 tests (unknown symbol default, direct construction)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alerts.symbol_multipliers import SymbolMultipliers

_SYMBOLS_PATH = str(
    Path(__file__).parents[2] / "configs" / "symbols.yaml"
)


# ---------------------------------------------------------------------------
# load() tests — read actual symbols.yaml
# ---------------------------------------------------------------------------


def test_load_returns_btc_multiplier_1_0() -> None:
    mults = SymbolMultipliers.load(_SYMBOLS_PATH)
    assert mults.get("BTCUSDT") == pytest.approx(1.0)


def test_load_returns_hype_multiplier_2_5() -> None:
    mults = SymbolMultipliers.load(_SYMBOLS_PATH)
    assert mults.get("HYPEUSDT") == pytest.approx(2.5)


def test_load_all_known_symbols() -> None:
    """All 4 symbols are present with the expected calibrated values."""
    mults = SymbolMultipliers.load(_SYMBOLS_PATH)
    assert mults.get("BTCUSDT") == pytest.approx(1.0)
    assert mults.get("ETHUSDT") == pytest.approx(1.2)
    assert mults.get("SOLUSDT") == pytest.approx(1.5)
    assert mults.get("HYPEUSDT") == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# get() tests — constructed directly (no I/O)
# ---------------------------------------------------------------------------


def test_get_unknown_symbol_defaults_to_1_0() -> None:
    """Unknown symbols fall back to 1.0 so they behave like BTC (safe default)."""
    mults = SymbolMultipliers(multipliers={"BTCUSDT": 1.0})
    assert mults.get("XYZUSDT") == pytest.approx(1.0)
