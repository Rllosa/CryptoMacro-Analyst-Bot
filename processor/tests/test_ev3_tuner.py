"""
Tests for EV-3: Threshold Tuning Framework

Pure unit tests — no real DB or network calls.
All tests exercise build_recommendations() directly, bypassing the DB fetch.
"""

from __future__ import annotations

import pytest

from eval.tuner import build_recommendations, _CANDIDATE_THRESHOLDS


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
# T1 — sweep contains all candidate thresholds
# ---------------------------------------------------------------------------


def test_t1_sweep_contains_all_candidate_thresholds() -> None:
    """Each recommendation's sweep list has one entry per _CANDIDATE_THRESHOLDS value."""
    rows = [_row(move_4h=2.0) for _ in range(5)]
    recs = build_recommendations(rows, current_threshold=1.0, min_sample=1)

    assert len(recs) == 1
    sweep = recs[0]["sweep"]
    sweep_thresholds = [entry["threshold"] for entry in sweep]
    assert sweep_thresholds == list(_CANDIDATE_THRESHOLDS)
    # Each sweep entry has required keys
    for entry in sweep:
        assert "hit_rate" in entry
        assert "fp_rate" in entry
        assert "avg_move_4h_pct" in entry


# ---------------------------------------------------------------------------
# T2 — RAISE_THRESHOLD when hit_rate < 0.40
# ---------------------------------------------------------------------------


def test_t2_raise_threshold_when_hit_rate_below_0_40() -> None:
    """3/10 rows exceed threshold at 1.0 → hit_rate=0.30 → RAISE_THRESHOLD."""
    rows = (
        [_row(move_4h=2.0)] * 3    # hits
        + [_row(move_4h=0.5)] * 7  # misses
    )
    recs = build_recommendations(rows, current_threshold=1.0, min_sample=1)

    assert len(recs) == 1
    assert recs[0]["recommendation"] == "RAISE_THRESHOLD"
    assert recs[0]["current_hit_rate"] == pytest.approx(0.30, abs=0.001)


# ---------------------------------------------------------------------------
# T3 — OK when hit_rate >= 0.60 and <= 0.80
# ---------------------------------------------------------------------------


def test_t3_ok_when_hit_rate_between_0_60_and_0_80() -> None:
    """7/10 rows exceed threshold → hit_rate=0.70 → OK."""
    rows = (
        [_row(move_4h=2.0)] * 7    # hits
        + [_row(move_4h=0.5)] * 3  # misses
    )
    recs = build_recommendations(rows, current_threshold=1.0, min_sample=1)

    assert len(recs) == 1
    assert recs[0]["recommendation"] == "OK"
    assert recs[0]["current_hit_rate"] == pytest.approx(0.70, abs=0.001)


# ---------------------------------------------------------------------------
# T4 — LOWER_THRESHOLD when hit_rate > 0.80
# ---------------------------------------------------------------------------


def test_t4_lower_threshold_when_hit_rate_above_0_80() -> None:
    """9/10 rows exceed threshold → hit_rate=0.90 → LOWER_THRESHOLD."""
    rows = (
        [_row(move_4h=2.0)] * 9    # hits
        + [_row(move_4h=0.5)] * 1  # miss
    )
    recs = build_recommendations(rows, current_threshold=1.0, min_sample=1)

    assert len(recs) == 1
    assert recs[0]["recommendation"] == "LOWER_THRESHOLD"
    assert recs[0]["current_hit_rate"] == pytest.approx(0.90, abs=0.001)


# ---------------------------------------------------------------------------
# T5 — INSUFFICIENT_DATA when count < min_sample
# ---------------------------------------------------------------------------


def test_t5_insufficient_data_when_below_min_sample() -> None:
    """3 rows with min_sample=5 → INSUFFICIENT_DATA regardless of hit_rate."""
    rows = [_row(move_4h=2.0)] * 3  # would be 100% hits but sample too small
    recs = build_recommendations(rows, current_threshold=1.0, min_sample=5)

    assert len(recs) == 1
    assert recs[0]["recommendation"] == "INSUFFICIENT_DATA"
    assert recs[0]["count"] == 3


# ---------------------------------------------------------------------------
# T6 — sort order: RAISE_THRESHOLD before OK
# ---------------------------------------------------------------------------


def test_t6_recommendations_sorted_raise_before_ok() -> None:
    """Multiple alert types: RAISE_THRESHOLD appears before OK in output."""
    raise_rows = [_row(alert_type="BREAKOUT",      move_4h=2.0)] * 3 + \
                 [_row(alert_type="BREAKOUT",      move_4h=0.5)] * 7   # 0.30 → RAISE
    ok_rows    = [_row(alert_type="VOL_EXPANSION", move_4h=2.0)] * 7 + \
                 [_row(alert_type="VOL_EXPANSION", move_4h=0.5)] * 3   # 0.70 → OK
    rows = raise_rows + ok_rows

    recs = build_recommendations(rows, current_threshold=1.0, min_sample=1)

    assert len(recs) == 2
    assert recs[0]["recommendation"] == "RAISE_THRESHOLD"
    assert recs[1]["recommendation"] == "OK"
