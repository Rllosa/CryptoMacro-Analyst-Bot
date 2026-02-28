from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
import yaml

from features.config import FeatureParams

# Pre-built (symbol, rs_key, zscore_key) tuples — key names hoisted at module level
# so they are never reconstructed via f-string inside the compute loop.
_ALT_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("ETHUSDT", "eth_btc_rs", "eth_btc_rs_zscore"),
    ("SOLUSDT", "sol_btc_rs", "sol_btc_rs_zscore"),
    ("HYPEUSDT", "hype_btc_rs", "hype_btc_rs_zscore"),
)
_BTC = "BTCUSDT"


# ---------------------------------------------------------------------------
# Macro Stress Config (FE-3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MacroStressParams:
    """
    Normalization bounds and weights for the macro_stress composite.

    Loaded from thresholds.yaml["macro_stress"]. Phase 2 uses VIX + DXY
    momentum only; yield curve and equity drawdown are deferred to Phase 5+.
    """

    vix_weight: float
    dxy_weight: float
    vix_min: float
    vix_max: float
    dxy_momentum_min: float
    dxy_momentum_max: float

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> MacroStressParams:
        ms = thresholds["macro_stress"]
        return cls(
            vix_weight=ms["weights"]["vix_level"],
            dxy_weight=ms["weights"]["dxy_momentum"],
            vix_min=ms["normalization"]["vix"]["min"],
            vix_max=ms["normalization"]["vix"]["max"],
            dxy_momentum_min=ms["normalization"]["dxy_momentum"]["min"],
            dxy_momentum_max=ms["normalization"]["dxy_momentum"]["max"],
        )

    @classmethod
    def load(cls, thresholds_path: str) -> MacroStressParams:
        with open(thresholds_path) as f:
            return cls.from_thresholds(yaml.safe_load(f))


# ---------------------------------------------------------------------------
# Macro helpers (pure, no I/O)
# ---------------------------------------------------------------------------


def _clamp_norm(value: float | None, low: float, high: float) -> float:
    """
    Normalize value to [0, 100] using [low, high] bounds, then clamp.

    Returns 0.0 when value is None (safe default for missing data).
    """
    if value is None:
        return 0.0
    span = high - low
    if span <= 0:
        return 0.0
    return max(0.0, min(100.0, (value - low) / span * 100.0))


def _compute_dxy_momentum(
    dxy_current: float | None,
    dxy_5d_ago: float | None,
) -> float:
    """
    5-day DXY rate-of-change as a percentage.

    Returns 0.0 when either value is unavailable or when dxy_5d_ago is zero.
    """
    if dxy_current is None or dxy_5d_ago is None or dxy_5d_ago == 0.0:
        return 0.0
    return (dxy_current - dxy_5d_ago) / abs(dxy_5d_ago) * 100.0


# ---------------------------------------------------------------------------
# Macro Stress Composite (FE-3)
# ---------------------------------------------------------------------------


def compute_macro_features(
    vix: float | None,
    dxy_current: float | None,
    dxy_5d_ago: float | None,
    params: MacroStressParams,
) -> dict[str, float]:
    """
    Compute macro_stress (0–100), vix (pass-through), and dxy_momentum.

    Phase 2 formula:
      vix_norm     = clamp((vix - vix_min) / (vix_max - vix_min) * 100, 0, 100)
      dxy_momentum = (dxy_current - dxy_5d_ago) / |dxy_5d_ago| * 100
      dxy_stress   = clamp((dxy_momentum - dxy_min) / (dxy_max - dxy_min) * 100, 0, 100)
      macro_stress = vix_norm * vix_weight + dxy_stress * dxy_weight

    Safe defaults: any missing input → treated as 0 contribution (graceful degradation,
    no crash). This means macro_stress falls back toward 0 when data is unavailable,
    preventing false RISK_OFF_STRESS triggers during outages.
    """
    dxy_momentum = _compute_dxy_momentum(dxy_current, dxy_5d_ago)
    vix_norm = _clamp_norm(vix, params.vix_min, params.vix_max)
    dxy_stress = _clamp_norm(dxy_momentum, params.dxy_momentum_min, params.dxy_momentum_max)
    macro_stress = vix_norm * params.vix_weight + dxy_stress * params.dxy_weight

    return {
        "macro_stress": macro_stress,
        "vix": vix if vix is not None else 0.0,
        "dxy_momentum": dxy_momentum,
    }


# ---------------------------------------------------------------------------
# Relative Strength (existing)
# ---------------------------------------------------------------------------


def compute_rs_and_zscore(
    alt_close: pd.Series,
    btc_close: pd.Series,
    lookback: int,
    zscore_window: int,
) -> tuple[float, float]:
    """
    Compute relative strength (alpha) and its z-score for alt vs BTC.

    RS[t] = (alt[t]/alt[t-N] - 1) - (btc[t]/btc[t-N] - 1)

    Uses return difference (alpha) rather than a ratio — avoids instability
    when BTC has near-zero or negative returns over the lookback window.

    Args:
        alt_close: Close price series for the alt (ascending time).
        btc_close: Close price series for BTC (ascending time, same length).
        lookback: N-period window for each return computation (rs_lookback).
        zscore_window: Rolling window for z-score normalisation (rs_zscore_window).

    Returns:
        (rs_current, rs_zscore).
        Both NaN when fewer than 2 RS values can be computed.
        rs_zscore is 0.0 when the RS series has zero variance.
    """
    alt_ret = alt_close / alt_close.shift(lookback) - 1.0
    btc_ret = btc_close / btc_close.shift(lookback) - 1.0
    rs_series = (alt_ret - btc_ret).dropna()

    if len(rs_series) < 2:
        return math.nan, math.nan

    rs_current = float(rs_series.iloc[-1])
    rs_window = rs_series.iloc[-zscore_window:]
    mean = float(rs_window.mean())
    std = float(rs_window.std())

    if std < 1e-10:
        return rs_current, 0.0

    return rs_current, float((rs_current - mean) / std)


def compute_all_cross_features(
    closes: pd.DataFrame,
    params: FeatureParams,
) -> dict[str, float]:
    """
    Compute relative-strength cross-asset features from a wide close price DataFrame.

    Produces RS ratios and z-scores for ETH/BTC, SOL/BTC, HYPE/BTC.
    Macro features (macro_stress, vix, dxy_momentum) are computed separately
    by CrossFeatureEngine and merged in after this call — they require async
    Redis/DB I/O that does not belong in a pure computation function.

    Correlation features (btc_spx_correlation, btc_dxy_correlation, etc.)
    are intentionally absent — they will be added in a future task once
    daily BTC/macro correlation windows are implemented.

    Args:
        closes: Wide DataFrame, columns = symbol names, index = time (ascending).
        params: Feature parameters loaded from thresholds.yaml.

    Returns:
        Dict of feature_name → float. NaN for pairs with insufficient data.
    """
    features: dict[str, float] = {}
    btc = closes[_BTC] if _BTC in closes.columns else pd.Series(dtype=float)

    for alt_sym, rs_key, z_key in _ALT_PAIRS:
        if alt_sym not in closes.columns or btc.empty:
            features[rs_key] = math.nan
            features[z_key] = math.nan
            continue

        rs, z = compute_rs_and_zscore(
            closes[alt_sym], btc, params.rs_lookback, params.rs_zscore_window
        )
        features[rs_key] = rs
        features[z_key] = z

    return features
