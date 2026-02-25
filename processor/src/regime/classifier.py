"""
Regime classifier — pure functions, no I/O.

Entry point: classify_regime(inputs, params) → RegimeResult

Feature inputs must be pre-built via _build_regime_inputs(), which translates
raw FE-1/FE-2 feature names to the semantic names used in thresholds.yaml.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from regime.config import RegimeParams

# Floating-point guard — same as vol_expansion._ZERO_STD_THRESHOLD.
# Duplicated here to avoid cross-module coupling; the value is the same.
_ZERO_STD_THRESHOLD = 1e-10

# Minimum buffer length before rv_4h_zscore is computed (4h of 5m cycles).
_MIN_BUFFER_SAMPLES = 48

# Tiebreak order when two regimes share equal confidence (highest priority wins).
_PRIORITY: list[str] = [
    "DELEVERAGING",
    "RISK_OFF_STRESS",
    "VOL_EXPANSION",
    "RISK_ON_TREND",
    "CHOP_RANGE",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RegimeResult:
    """Output of classify_regime."""

    regime: str | None          # None when below min_confidence or no primary met
    confidence: float           # 0.0–1.0
    contributing_factors: dict[str, Any]  # fields that contributed to the score


# ---------------------------------------------------------------------------
# rv_4h_zscore — in-memory rolling buffer z-score
# ---------------------------------------------------------------------------


def _compute_rv_4h_zscore(buf: deque[float], rv_1h: float) -> float | None:
    """
    Population z-score of rv_1h against the rolling buffer.

    Returns None during warmup (fewer than _MIN_BUFFER_SAMPLES entries).
    Returns 0.0 for a constant buffer to guard against floating-point noise.

    Call BEFORE appending rv_1h to the buffer so the current value is scored
    against historical data only (same convention as vol_expansion).
    """
    if len(buf) < _MIN_BUFFER_SAMPLES:
        return None
    n = len(buf)
    mean = sum(buf) / n
    variance = sum((x - mean) ** 2 for x in buf) / n
    std = math.sqrt(variance)
    if std < _ZERO_STD_THRESHOLD:
        return 0.0
    return (rv_1h - mean) / std


# ---------------------------------------------------------------------------
# Feature input mapping
# ---------------------------------------------------------------------------


def _build_regime_inputs(
    per_sym: dict[str, Any],
    cross: dict[str, Any],
    rv_4h_zscore: float,
    params: RegimeParams,
) -> dict[str, Any]:
    """
    Translate raw FE-1/FE-2 feature names to the semantic names in thresholds.yaml.

    Fields not yet available (FE-3/FE-4 data: vix, dxy_momentum, btc_spx_correlation,
    funding_zscore, liquidations_1h_usd, oi_drop_1h) default to 0.0 until those
    feature engines ship.
    """
    bb_upper = per_sym.get("bb_upper", 0.0)
    bb_lower = per_sym.get("bb_lower", 0.0)
    bb_mid = per_sym.get("bb_mid", 1.0) or 1.0  # guard against zero
    bb_bandwidth = (bb_upper - bb_lower) / bb_mid

    return {
        "btc_trend": per_sym.get("r_1h", 0.0),
        "volatility_regime": "high" if rv_4h_zscore > params.volatility_regime_high_zscore_threshold else "low",
        "rv_4h_zscore": rv_4h_zscore,
        "macro_stress": cross.get("macro_stress", 0.0),
        "volume_zscore": per_sym.get("volume_zscore", 0.0),
        "price_range": "tight" if bb_bandwidth < params.tight_bb_bandwidth_max else "wide",
        "breakout_flags": [
            per_sym.get("breakout_4h_high", 0.0),
            per_sym.get("breakout_4h_low", 0.0),
            per_sym.get("breakout_24h_high", 0.0),
            per_sym.get("breakout_24h_low", 0.0),
        ],
        "candle_size": per_sym.get("atr_ratio", 0.0),
        # FE-3/FE-4 stubs — default to 0.0
        "vix": cross.get("vix", 0.0),
        "btc_spx_correlation": cross.get("btc_spx_correlation", 0.0),
        "dxy_momentum": cross.get("dxy_momentum", 0.0),
        "funding_zscore": per_sym.get("funding_zscore", 0.0),
        "liquidations_1h_usd": cross.get("liquidations_1h_usd", 0.0),
        "oi_drop_1h": cross.get("oi_drop_1h", 0.0),
    }


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------


def _eval_condition(
    inputs: dict[str, Any],
    field: str,
    operator: str,
    value: Any,
) -> bool:
    """
    Evaluate a single threshold condition.

    Special operators:
      "tight"     — price_range == "tight" (ignores field/value)
      "all_false" — all breakout_flags == 0.0 (ignores field/value)
    Standard operators: >, >=, <, <=, ==
    """
    match operator:
        case "tight":
            return inputs.get("price_range") == "tight"
        case "all_false":
            return all(x == 0.0 for x in inputs.get("breakout_flags", []))
        case _:
            v = inputs.get(field)
            if v is None:
                return False
            match operator:
                case ">":
                    return float(v) > float(value)
                case ">=":
                    return float(v) >= float(value)
                case "<":
                    return float(v) < float(value)
                case "<=":
                    return float(v) <= float(value)
                case "==":
                    return v == value
                case _:
                    return False


# ---------------------------------------------------------------------------
# Regime scoring
# ---------------------------------------------------------------------------


def _score_regime(
    name: str,
    cfg: dict[str, Any],
    inputs: dict[str, Any],
    params: RegimeParams,
) -> tuple[float, dict[str, Any]] | None:
    """
    Score one regime definition.

    Returns (confidence, contributing_factors) if primary condition is met,
    None otherwise.

    Confidence = base_weight
               + condition_weight × (# additional conditions met)
               + zscore_bonus if rv_4h_zscore ≥ zscore_bonus_threshold
    Capped at 1.0.
    """
    primary = cfg["primary_condition"]
    if not _eval_condition(inputs, primary["field"], primary["operator"], primary["value"]):
        return None

    factors: dict[str, Any] = {primary["field"]: inputs.get(primary["field"])}
    confidence = params.base_weight

    for cond in cfg.get("additional_conditions", []):
        if _eval_condition(inputs, cond["field"], cond["operator"], cond["value"]):
            confidence += params.condition_weight
            factors[cond["field"]] = inputs.get(cond["field"])

    if inputs.get("rv_4h_zscore", 0.0) >= params.zscore_bonus_threshold:
        confidence += params.zscore_bonus

    return min(1.0, confidence), factors


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------


def classify_regime(inputs: dict[str, Any], params: RegimeParams) -> RegimeResult:
    """
    Classify market regime from pre-built feature inputs.

    Returns RegimeResult with regime=None when:
    - No regime's primary condition is met, OR
    - Best candidate's confidence < min_confidence.

    Tiebreak: highest priority in _PRIORITY list wins when confidences are equal.
    """
    candidates: list[tuple[str, float, dict[str, Any]]] = []
    for name, cfg in params.regimes.items():
        result = _score_regime(name, cfg, inputs, params)
        if result is not None:
            confidence, factors = result
            candidates.append((name, confidence, factors))

    if not candidates:
        return RegimeResult(regime=None, confidence=0.0, contributing_factors={})

    def _sort_key(x: tuple[str, float, dict]) -> tuple[float, int]:
        name, conf, _ = x
        priority_rank = _PRIORITY.index(name) if name in _PRIORITY else len(_PRIORITY)
        return (conf, -priority_rank)

    best_name, best_conf, best_factors = max(candidates, key=_sort_key)

    if best_conf < params.min_confidence:
        return RegimeResult(regime=None, confidence=best_conf, contributing_factors=best_factors)

    return RegimeResult(regime=best_name, confidence=best_conf, contributing_factors=best_factors)
