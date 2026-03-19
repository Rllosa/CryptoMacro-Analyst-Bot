"""
EV-4: Backtesting Framework — deterministic tests.

All tests are pure-logic (no DB, no Redis, no NATS).
Fixture data drives known inputs → expected simulated alert outputs.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

import pytest

from eval.backtest import (
    SimulatedAlert,
    _InMemoryCooldowns,
    _InMemoryPersistence,
    _BO_DIRECTIONS,
    _LR_PAIRS,
    _breakout_signals,
    _build_csv,
    _build_rv_zscores,
    _evaluate_signal,
    _leadership_rotation_signals,
    _price_near,
    _vol_expansion_signals,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _t(hours: float = 0.0) -> datetime:
    return _T0 + timedelta(hours=hours)


def _fresh_state() -> tuple[_InMemoryCooldowns, _InMemoryPersistence]:
    return _InMemoryCooldowns(), _InMemoryPersistence()


_COOLDOWN_MINS = {"VOL_EXPANSION": 30, "BREAKOUT": 60, "LEADERSHIP_ROTATION": 120}
_PERSISTENCE = {"VOL_EXPANSION": 2, "BREAKOUT": 2, "LEADERSHIP_ROTATION": 2}


# ---------------------------------------------------------------------------
# _evaluate_signal: core engine logic
# ---------------------------------------------------------------------------


class TestEvaluateSignal:
    def test_returns_none_when_conditions_not_met(self):
        cd, ps = _fresh_state()
        result = _evaluate_signal(
            alert_type="VOL_EXPANSION", symbol="BTCUSDT", direction="up",
            conditions_met=False, severity="MEDIUM", fire_time=_t(),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
        )
        assert result is None

    def test_persistence_accumulates_before_fire(self):
        cd, ps = _fresh_state()
        # First cycle — persistence=1 < required=2 → no fire
        r1 = _evaluate_signal(
            alert_type="VOL_EXPANSION", symbol="BTCUSDT", direction="up",
            conditions_met=True, severity="MEDIUM", fire_time=_t(),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
        )
        assert r1 is None

        # Second cycle — persistence=2 == required=2 → fires
        r2 = _evaluate_signal(
            alert_type="VOL_EXPANSION", symbol="BTCUSDT", direction="up",
            conditions_met=True, severity="MEDIUM", fire_time=_t(hours=5 / 60),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
        )
        assert r2 is not None
        assert r2.alert_type == "VOL_EXPANSION"
        assert r2.severity == "MEDIUM"

    def test_cooldown_suppresses_after_fire(self):
        cd, ps = _fresh_state()
        # Build up persistence=2 and fire
        for i in range(2):
            _evaluate_signal(
                alert_type="VOL_EXPANSION", symbol="BTCUSDT", direction="up",
                conditions_met=True, severity="MEDIUM", fire_time=_t(hours=i * 5 / 60),
                cooldowns=cd, persistence=ps,
                cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
            )
        # Next cycle within 30-min cooldown → suppressed
        r = _evaluate_signal(
            alert_type="VOL_EXPANSION", symbol="BTCUSDT", direction="up",
            conditions_met=True, severity="MEDIUM", fire_time=_t(hours=0.2),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
        )
        assert r is None

    def test_fires_again_after_cooldown_expires(self):
        cd, ps = _fresh_state()
        # Fire at t=0 (2 cycles of persistence)
        for i in range(2):
            _evaluate_signal(
                alert_type="VOL_EXPANSION", symbol="BTCUSDT", direction="up",
                conditions_met=True, severity="MEDIUM", fire_time=_t(hours=i * 5 / 60),
                cooldowns=cd, persistence=ps,
                cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
            )
        # After cooldown (30 min + some) → should fire again after persistence
        base = _t(hours=1)  # 60 min past T0, well past 30-min cooldown
        for i in range(2):
            r = _evaluate_signal(
                alert_type="VOL_EXPANSION", symbol="BTCUSDT", direction="up",
                conditions_met=True, severity="MEDIUM", fire_time=base + timedelta(minutes=i * 5),
                cooldowns=cd, persistence=ps,
                cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
            )
        assert r is not None

    def test_condition_break_resets_persistence(self):
        cd, ps = _fresh_state()
        # First cycle: conditions met
        _evaluate_signal(
            alert_type="BREAKOUT", symbol="ETHUSDT", direction="high_4h",
            conditions_met=True, severity="MEDIUM", fire_time=_t(),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
        )
        # Gap: conditions not met — resets counter
        _evaluate_signal(
            alert_type="BREAKOUT", symbol="ETHUSDT", direction="high_4h",
            conditions_met=False, severity="MEDIUM", fire_time=_t(hours=5 / 60),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
        )
        # Cycle again with conditions met — counter restarts, no fire yet
        r = _evaluate_signal(
            alert_type="BREAKOUT", symbol="ETHUSDT", direction="high_4h",
            conditions_met=True, severity="MEDIUM", fire_time=_t(hours=10 / 60),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=_PERSISTENCE,
        )
        assert r is None

    def test_fires_with_persistence_one(self):
        """Alert type with persistence_required=1 fires on first qualifying cycle."""
        cd, ps = _fresh_state()
        persistence = {"LEADERSHIP_ROTATION": 1}
        r = _evaluate_signal(
            alert_type="LEADERSHIP_ROTATION", symbol=None, direction="eth_over_btc",
            conditions_met=True, severity="MEDIUM", fire_time=_t(),
            cooldowns=cd, persistence=ps,
            cooldown_minutes=_COOLDOWN_MINS, persistence_required=persistence,
        )
        assert r is not None
        assert r.symbol is None


# ---------------------------------------------------------------------------
# VOL_EXPANSION signals
# ---------------------------------------------------------------------------


class _FakeVEParams:
    rv_1h_zscore_threshold = 2.0
    volume_zscore_threshold = 1.5
    high_rv_1h_zscore = 2.5
    high_volume_zscore = 2.0


class TestVolExpansionSignals:
    _params = _FakeVEParams()

    def _fire(self, rv_z, vol_z, breakout_4h_high=False, breakout_24h_high=False,
              breakout_4h_low=False, breakout_24h_low=False, multiplier=1.0):
        cd, ps = _fresh_state()
        features = {
            "volume_zscore": vol_z,
            "breakout_4h_high": float(breakout_4h_high),
            "breakout_24h_high": float(breakout_24h_high),
            "breakout_4h_low": float(breakout_4h_low),
            "breakout_24h_low": float(breakout_24h_low),
        }
        # Run 2 cycles to satisfy persistence=2
        alerts = []
        for i in range(2):
            alerts = _vol_expansion_signals(
                features, rv_z, self._params, multiplier,
                _t(hours=i * 5 / 60), cd, ps, _COOLDOWN_MINS, _PERSISTENCE, "BTCUSDT",
            )
        return alerts

    def test_fires_on_up_breakout(self):
        alerts = self._fire(rv_z=2.5, vol_z=1.8, breakout_4h_high=True)
        assert any(a.direction == "up" for a in alerts)

    def test_no_fire_below_rv_threshold(self):
        alerts = self._fire(rv_z=1.0, vol_z=2.0, breakout_4h_high=True)
        assert not alerts

    def test_no_fire_without_breakout(self):
        alerts = self._fire(rv_z=3.0, vol_z=2.5)
        assert not alerts

    def test_severity_escalates_to_high(self):
        alerts = self._fire(rv_z=3.0, vol_z=2.5, breakout_24h_high=True)
        assert any(a.severity == "HIGH" for a in alerts)

    def test_severity_medium_on_4h_only(self):
        """24h breakout absent → severity stays MEDIUM even if rv/vol are high."""
        alerts = self._fire(rv_z=3.0, vol_z=2.5, breakout_4h_high=True)
        assert all(a.severity == "MEDIUM" for a in alerts)

    def test_multiplier_raises_effective_threshold(self):
        """multiplier=2.0 doubles effective threshold — same rv_z=2.5 should not fire."""
        alerts = self._fire(rv_z=2.5, vol_z=1.8, breakout_4h_high=True, multiplier=2.0)
        assert not alerts

    def test_no_fire_when_rv_zscore_none(self):
        alerts = self._fire(rv_z=None, vol_z=2.0, breakout_4h_high=True)
        assert not alerts


# ---------------------------------------------------------------------------
# BREAKOUT signals
# ---------------------------------------------------------------------------


class _FakeBOParams:
    volume_zscore_min = 1.0
    severity_4h = "MEDIUM"
    severity_24h = "HIGH"


class TestBreakoutSignals:
    _params = _FakeBOParams()

    def _fire(self, flag_key, vol_z=1.5, multiplier=1.0):
        cd, ps = _fresh_state()
        features = {"volume_zscore": vol_z, flag_key: 1.0}
        alerts = []
        for i in range(2):
            alerts = _breakout_signals(
                features, self._params, multiplier,
                _t(hours=i * 5 / 60), cd, ps, _COOLDOWN_MINS, _PERSISTENCE, "ETHUSDT",
            )
        return alerts

    def test_fires_high_24h(self):
        alerts = self._fire("breakout_24h_high")
        assert any(a.direction == "high_24h" and a.severity == "HIGH" for a in alerts)

    def test_fires_high_4h_medium(self):
        alerts = self._fire("breakout_4h_high")
        assert any(a.direction == "high_4h" and a.severity == "MEDIUM" for a in alerts)

    def test_4h_excluded_when_24h_set(self):
        """When breakout_24h_high is set, high_4h direction must not fire."""
        cd, ps = _fresh_state()
        features = {
            "volume_zscore": 1.5,
            "breakout_4h_high": 1.0,
            "breakout_24h_high": 1.0,
        }
        alerts = []
        for i in range(2):
            alerts = _breakout_signals(
                features, self._params, 1.0,
                _t(hours=i * 5 / 60), cd, ps, _COOLDOWN_MINS, _PERSISTENCE, "BTCUSDT",
            )
        directions = {a.direction for a in alerts}
        assert "high_24h" in directions
        assert "high_4h" not in directions

    def test_no_fire_below_volume_threshold(self):
        alerts = self._fire("breakout_4h_high", vol_z=0.5)
        assert not alerts


# ---------------------------------------------------------------------------
# LEADERSHIP_ROTATION signals
# ---------------------------------------------------------------------------


class _FakeLRParams:
    rs_zscore_threshold = 2.0


class TestLeadershipRotationSignals:
    _params = _FakeLRParams()
    _persist_1 = {"LEADERSHIP_ROTATION": 1}

    def _fire(self, zscore_key, z_value):
        cd, ps = _fresh_state()
        cross = {zscore_key: z_value}
        return _leadership_rotation_signals(
            cross, self._params, _t(), cd, ps, _COOLDOWN_MINS, self._persist_1,
        )

    def test_fires_alt_over_btc(self):
        alerts = self._fire("eth_btc_rs_zscore", z_value=2.5)
        assert any(a.direction == "eth_over_btc" for a in alerts)

    def test_fires_btc_over_alt(self):
        alerts = self._fire("eth_btc_rs_zscore", z_value=-2.5)
        assert any(a.direction == "btc_over_eth" for a in alerts)

    def test_no_fire_below_threshold(self):
        alerts = self._fire("sol_btc_rs_zscore", z_value=1.0)
        assert not alerts

    def test_symbol_is_none(self):
        alerts = self._fire("hype_btc_rs_zscore", z_value=3.0)
        assert all(a.symbol is None for a in alerts)

    def test_missing_zscore_key_skipped(self):
        cd, ps = _fresh_state()
        cross: dict = {}  # no keys at all
        alerts = _leadership_rotation_signals(
            cross, self._params, _t(), cd, ps, _COOLDOWN_MINS, self._persist_1,
        )
        assert alerts == []


# ---------------------------------------------------------------------------
# _build_rv_zscores
# ---------------------------------------------------------------------------


class TestBuildRvZscores:
    def test_warmup_returns_none_before_min_samples(self):
        """Fewer than _MIN_BUFFER_SAMPLES (24) rv_1h values → zscore is None."""
        computed = {
            (_t(hours=i), "BTCUSDT"): {"rv_1h": 0.01 + i * 0.001}
            for i in range(10)
        }
        zs = _build_rv_zscores(computed)
        assert all(v is None for v in zs.values())

    def test_zscore_nonzero_after_warmup(self):
        """After 24+ values, zscore is numeric for the last entry."""
        computed = {
            (_t(hours=i), "BTCUSDT"): {"rv_1h": 0.01 + (i % 5) * 0.005}
            for i in range(30)
        }
        zs = _build_rv_zscores(computed)
        non_null = [v for v in zs.values() if v is not None]
        assert len(non_null) > 0

    def test_current_value_excluded_from_distribution(self):
        """Varying baseline followed by a large spike → zscore > 0 for the spike.
        Uses oscillating baseline so std > 0 (avoids _ZERO_STD_THRESHOLD = 0.0 path).
        """
        spike_rv = 0.50
        times = [_t(hours=i) for i in range(30)]
        computed: dict = {}
        for i, t in enumerate(times[:-1]):
            # Oscillate between 0.01 and 0.02 so std is non-zero
            computed[(t, "BTCUSDT")] = {"rv_1h": 0.01 + (i % 2) * 0.01}
        computed[(times[-1], "BTCUSDT")] = {"rv_1h": spike_rv}

        zs = _build_rv_zscores(computed)
        last_z = zs.get((times[-1], "BTCUSDT"))
        assert last_z is not None
        assert last_z > 0


# ---------------------------------------------------------------------------
# _price_near
# ---------------------------------------------------------------------------


class TestPriceNear:
    def _make_cache(self, entries: list[tuple[str, datetime, float]]):
        return {(sym, bucket): price for sym, bucket, price in entries}

    def test_exact_hour_match(self):
        target = _t().replace(minute=0, second=0, microsecond=0)
        cache = self._make_cache([("BTCUSDT", target, 50000.0)])
        assert _price_near(cache, "BTCUSDT", target) == 50000.0

    def test_returns_none_when_no_candles(self):
        assert _price_near({}, "BTCUSDT", _t()) is None

    def test_returns_none_for_wrong_symbol(self):
        target = _t().replace(minute=0, second=0, microsecond=0)
        cache = self._make_cache([("ETHUSDT", target, 3000.0)])
        assert _price_near(cache, "BTCUSDT", target) is None


# ---------------------------------------------------------------------------
# _build_csv
# ---------------------------------------------------------------------------


class TestBuildCsv:
    def test_header_and_row_present(self):
        alerts = [
            SimulatedAlert(
                fire_time=_t(),
                alert_type="VOL_EXPANSION",
                symbol="BTCUSDT",
                direction="up",
                severity="MEDIUM",
                move_4h_pct=1.5,
                move_12h_pct=2.3,
            )
        ]
        csv_text = _build_csv(alerts)
        assert "alert_type" in csv_text
        assert "VOL_EXPANSION" in csv_text
        assert "BTCUSDT" in csv_text

    def test_cross_asset_symbol_shown_as_market_wide(self):
        alerts = [
            SimulatedAlert(
                fire_time=_t(),
                alert_type="LEADERSHIP_ROTATION",
                symbol=None,
                direction="eth_over_btc",
                severity="MEDIUM",
            )
        ]
        csv_text = _build_csv(alerts)
        assert "market-wide" in csv_text

    def test_empty_alerts_returns_header_only(self):
        csv_text = _build_csv([])
        lines = csv_text.strip().splitlines()
        assert len(lines) == 1  # header only
        assert "alert_type" in lines[0]
