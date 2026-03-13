"""
Tests for EV-2: Alert Quality Metrics

Pure unit tests — no real DB or network calls.
All tests exercise aggregate_rows() directly, bypassing the DB fetch.
"""

from __future__ import annotations

import pytest

from eval.metrics import aggregate_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    alert_type: str = "VOL_EXPANSION",
    severity: str = "MEDIUM",
    regime: str = "RISK_ON_TREND",
    move_4h: float | None = 2.0,
    move_12h: float | None = 1.5,
    has_4h: bool = True,
    has_12h: bool = True,
) -> dict:
    return {
        "alert_type": alert_type,
        "severity": severity,
        "regime_at_trigger": regime,
        "move_4h_pct": move_4h,
        "move_12h_pct": move_12h,
        "has_4h": has_4h,
        "has_12h": has_12h,
    }


# ---------------------------------------------------------------------------
# T1 — hit rate computed correctly
# ---------------------------------------------------------------------------


def test_t1_hit_rate_computed_correctly() -> None:
    """3/5 rows with |move_4h_pct| >= 1.0 → hit_rate = 0.60."""
    rows = [
        _row(move_4h=2.0),   # hit
        _row(move_4h=-1.5),  # hit (abs)
        _row(move_4h=1.0),   # hit (exact boundary)
        _row(move_4h=0.5),   # miss
        _row(move_4h=-0.3),  # miss
    ]
    result = aggregate_rows(rows, hit_threshold=1.0, min_sample=1)

    bucket = result["by_type"]["VOL_EXPANSION"]
    assert bucket["hit_rate"] == pytest.approx(0.6, abs=0.001)
    assert bucket["count"] == 5
    assert bucket["coverage_4h"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# T2 — fp_rate is complement of hit_rate
# ---------------------------------------------------------------------------


def test_t2_fp_rate_is_complement() -> None:
    """fp_rate = 1 - hit_rate, no overlap or NaN."""
    rows = [
        _row(move_4h=2.0),   # hit
        _row(move_4h=-1.5),  # hit
        _row(move_4h=0.5),   # miss
        _row(move_4h=-0.3),  # miss
        _row(move_4h=0.0),   # miss
    ]
    result = aggregate_rows(rows, hit_threshold=1.0, min_sample=1)
    bucket = result["by_type"]["VOL_EXPANSION"]

    assert bucket["hit_rate"] is not None
    assert bucket["fp_rate"] is not None
    assert bucket["hit_rate"] + bucket["fp_rate"] == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# T3 — avg_move_4h_pct is signed (not absolute)
# ---------------------------------------------------------------------------


def test_t3_avg_move_is_signed() -> None:
    """[+2.0, -3.0, +1.0] → avg_move_4h_pct = 0.0 (signed average)."""
    rows = [
        _row(move_4h=2.0),
        _row(move_4h=-3.0),
        _row(move_4h=1.0),
    ]
    result = aggregate_rows(rows, hit_threshold=1.0, min_sample=1)
    bucket = result["by_type"]["VOL_EXPANSION"]
    assert bucket["avg_move_4h_pct"] == pytest.approx(0.0, abs=0.001)


# ---------------------------------------------------------------------------
# T4 — rows without price_4h excluded from hit/fp, counted in coverage
# ---------------------------------------------------------------------------


def test_t4_missing_4h_excluded_from_hit_counted_in_coverage() -> None:
    """
    5 rows: 3 have price_4h data, 2 do not.
    hit/fp are computed over 3 rows only; coverage_4h = 3/5 = 0.6.
    """
    rows = [
        _row(move_4h=2.0,  has_4h=True),   # hit
        _row(move_4h=-1.5, has_4h=True),   # hit
        _row(move_4h=0.3,  has_4h=True),   # miss
        _row(move_4h=None, has_4h=False),  # no data
        _row(move_4h=None, has_4h=False),  # no data
    ]
    result = aggregate_rows(rows, hit_threshold=1.0, min_sample=1)
    bucket = result["by_type"]["VOL_EXPANSION"]

    assert bucket["count"] == 5
    assert bucket["coverage_4h"] == pytest.approx(0.6, abs=0.001)
    assert bucket["hit_rate"] == pytest.approx(2 / 3, abs=0.001)


# ---------------------------------------------------------------------------
# T5 — regime grouping: same alert_type, different regimes → separate buckets
# ---------------------------------------------------------------------------


def test_t5_regime_grouping() -> None:
    """Same alert type in 2 regimes → 2 separate buckets in by_regime."""
    rows = [
        _row(regime="RISK_ON_TREND",   move_4h=2.0),
        _row(regime="RISK_ON_TREND",   move_4h=1.5),
        _row(regime="RISK_OFF_STRESS", move_4h=0.3),
    ]
    result = aggregate_rows(rows, hit_threshold=1.0, min_sample=1)
    by_regime = result["by_regime"]["VOL_EXPANSION"]

    assert "RISK_ON_TREND" in by_regime
    assert "RISK_OFF_STRESS" in by_regime
    assert by_regime["RISK_ON_TREND"]["count"] == 2
    assert by_regime["RISK_OFF_STRESS"]["count"] == 1
    assert by_regime["RISK_ON_TREND"]["hit_rate"] == pytest.approx(1.0)
    assert by_regime["RISK_OFF_STRESS"]["hit_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# T6 — min_sample_size guard: hit_rate = None when count < min_sample
# ---------------------------------------------------------------------------


def test_t6_min_sample_size_guard() -> None:
    """Only 3 rows for an alert type with min_sample=5 → hit_rate = None."""
    rows = [
        _row(move_4h=2.0),
        _row(move_4h=1.5),
        _row(move_4h=0.3),
    ]
    result = aggregate_rows(rows, hit_threshold=1.0, min_sample=5)
    bucket = result["by_type"]["VOL_EXPANSION"]

    assert bucket["count"] == 3
    assert bucket["hit_rate"] is None
    assert bucket["fp_rate"] is None
    # avg_move is still computed (independent of min_sample)
    assert bucket["avg_move_4h_pct"] is not None
