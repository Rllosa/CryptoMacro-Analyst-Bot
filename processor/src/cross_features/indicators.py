from __future__ import annotations

import math

import pandas as pd

from features.config import FeatureParams

# Pre-built (symbol, rs_key, zscore_key) tuples — key names hoisted at module level
# so they are never reconstructed via f-string inside the compute loop.
_ALT_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("ETHUSDT", "eth_btc_rs", "eth_btc_rs_zscore"),
    ("SOLUSDT", "sol_btc_rs", "sol_btc_rs_zscore"),
    ("HYPEUSDT", "hype_btc_rs", "hype_btc_rs_zscore"),
)
_BTC = "BTCUSDT"


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
    Compute all cross-asset features from a wide close price DataFrame.

    Produces RS ratios and z-scores for ETH/BTC, SOL/BTC, HYPE/BTC.
    Correlation features (corr_btc_sp500, corr_btc_dxy, corr_btc_sp500_7d)
    are intentionally absent — they are not inserted until macro data is
    available in FE-3. Absence in the output means no rows written to DB.
    macro_stress is stubbed at 0.0 until FE-3 integrates FRED/Yahoo data.

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

    # Stub until FE-3 integrates macro data (FRED + Yahoo Finance)
    features["macro_stress"] = 0.0

    return features
