from __future__ import annotations

import math

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from features.config import FeatureParams

# Candles per year for a 5-minute series — used to annualise realised volatility
_PERIODS_PER_YEAR = 365 * 24 * 12  # 105 120


def compute_returns(close: pd.Series) -> dict[str, float]:
    """
    Compute simple price returns over four lookback windows.

    Windows (in 5m candle counts):
      r_5m  = 1 candle  (5 minutes)
      r_1h  = 12 candles
      r_4h  = 48 candles
      r_1d  = 288 candles

    Returns NaN for any window where there are not enough rows.
    """
    n = len(close)
    windows = {"r_5m": 1, "r_1h": 12, "r_4h": 48, "r_1d": 288}
    result: dict[str, float] = {}
    last = float(close.iloc[-1])
    for name, lag in windows.items():
        if n > lag:
            prior = float(close.iloc[-(lag + 1)])
            result[name] = (last / prior) - 1.0
        else:
            result[name] = math.nan
    return result


def compute_realized_vol(close: pd.Series, window: int) -> float:
    """
    Annualised realised volatility from log returns over `window` 5m candles.

    Requires window + 1 rows to produce one log return per candle.
    Returns NaN when there are insufficient rows.
    """
    if len(close) < window + 1:
        return math.nan
    log_ret = np.log(close / close.shift(1))
    rv = log_ret.rolling(window).std().iloc[-1]
    if math.isnan(rv):
        return math.nan
    return float(rv * math.sqrt(_PERIODS_PER_YEAR))


def compute_rsi(close: pd.Series, period: int) -> float:
    """RSI over `period` candles. Returns NaN when insufficient data."""
    if len(close) < period + 1:
        return math.nan
    val = RSIIndicator(close, window=period).rsi().iloc[-1]
    return float(val) if not math.isnan(val) else math.nan


def compute_macd(
    close: pd.Series, fast: int, slow: int, signal: int
) -> tuple[float, float, float]:
    """
    MACD line, signal line, and histogram.

    Returns (macd, macd_signal, macd_hist). Each is NaN when insufficient data.
    """
    if len(close) < slow + signal:
        return math.nan, math.nan, math.nan
    ind = MACD(close, window_fast=fast, window_slow=slow, window_sign=signal)
    m = float(ind.macd().iloc[-1])
    s = float(ind.macd_signal().iloc[-1])
    h = float(ind.macd_diff().iloc[-1])
    return m, s, h


def compute_bollinger(
    close: pd.Series, period: int, std: float
) -> tuple[float, float, float, float, float]:
    """
    Bollinger Bands: upper, middle, lower, %B, bandwidth.

    Returns five NaNs when insufficient data.
    """
    _nan5 = (math.nan, math.nan, math.nan, math.nan, math.nan)
    if len(close) < period:
        return _nan5
    bb = BollingerBands(close, window=period, window_dev=std)
    upper = float(bb.bollinger_hband().iloc[-1])
    middle = float(bb.bollinger_mavg().iloc[-1])
    lower = float(bb.bollinger_lband().iloc[-1])
    pct_b = float(bb.bollinger_pband().iloc[-1])
    bwidth = float(bb.bollinger_wband().iloc[-1])
    return upper, middle, lower, pct_b, bwidth


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> float:
    """ATR over `period` candles. Returns NaN when insufficient data."""
    if len(close) < period + 1:
        return math.nan
    val = AverageTrueRange(high, low, close, window=period).average_true_range().iloc[-1]
    return float(val) if not math.isnan(val) else math.nan


def compute_ema_slope(close: pd.Series, period: int) -> float:
    """
    EMA slope expressed as a fraction: (ema[-1] / ema[-2]) - 1.

    Returns NaN when fewer than period + 1 rows are available.
    """
    if len(close) < period + 1:
        return math.nan
    ema = EMAIndicator(close, window=period).ema_indicator()
    last = float(ema.iloc[-1])
    prev = float(ema.iloc[-2])
    if math.isnan(last) or math.isnan(prev) or prev == 0.0:
        return math.nan
    return (last / prev) - 1.0


def compute_volume_zscore(volume: pd.Series, window: int) -> float:
    """
    Z-score of the latest volume vs a rolling `window` mean and std.

    Returns 0.0 when std is zero (constant volume series).
    Returns NaN when insufficient data.
    """
    if len(volume) < window:
        return math.nan
    rolling = volume.rolling(window)
    mean = float(rolling.mean().iloc[-1])
    std = float(rolling.std().iloc[-1])
    if math.isnan(mean) or math.isnan(std):
        return math.nan
    if std == 0.0:
        return 0.0
    return (float(volume.iloc[-1]) - mean) / std


def compute_breakout_flags(
    high: pd.Series, low: pd.Series, close_last: float, window: int
) -> tuple[bool, bool]:
    """
    Detect whether the latest close breaks above the rolling high or below the rolling low.

    Uses the last `window` candles (excluding the current one) as the reference range.
    Returns (breakout_high, breakout_low).
    """
    if len(high) < window + 1:
        return False, False
    # Exclude the current (last) candle from the reference range
    ref_high = float(high.iloc[-(window + 1) : -1].max())
    ref_low = float(low.iloc[-(window + 1) : -1].min())
    return close_last > ref_high, close_last < ref_low


def compute_all_features(df: pd.DataFrame, params: FeatureParams) -> dict[str, float]:
    """
    Compute all per-asset features from a DataFrame of 5m candles.

    The DataFrame must have columns: open, high, low, close, volume,
    indexed by time (ascending). Columns must be numeric (float64).

    Returns a flat dict of feature_name → float value. NaN entries are
    included — the caller decides whether to skip them on DB write.
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --- Returns ---
    features: dict[str, float] = compute_returns(close)

    # --- Realised volatility ---
    features["rv_1h"] = compute_realized_vol(close, params.rv_window_1h)
    features["rv_4h"] = compute_realized_vol(close, params.rv_window_4h)

    # --- RSI ---
    features["rsi_14"] = compute_rsi(close, params.rsi_period)

    # --- MACD ---
    macd, macd_sig, macd_hist = compute_macd(
        close, params.macd_fast, params.macd_slow, params.macd_signal
    )
    features["macd"] = macd
    features["macd_signal"] = macd_sig
    features["macd_hist"] = macd_hist

    # --- Bollinger Bands ---
    bb_upper, bb_mid, bb_lower, bb_pct_b, bb_bwidth = compute_bollinger(
        close, params.bollinger_period, params.bollinger_std
    )
    features["bb_upper"] = bb_upper
    features["bb_middle"] = bb_mid
    features["bb_lower"] = bb_lower
    features["bb_pct_b"] = bb_pct_b
    features["bb_bandwidth"] = bb_bwidth

    # --- ATR ---
    features["atr_14"] = compute_atr(high, low, close, params.atr_period)

    # --- EMA slope ---
    features["ema_slope"] = compute_ema_slope(close, params.ema_slope_period)

    # --- Volume z-score ---
    features["volume_zscore"] = compute_volume_zscore(volume, params.volume_zscore_window)

    # --- Breakout flags (stored as 1.0/0.0 for the numeric EAV column) ---
    close_last = float(close.iloc[-1])
    bo4h_high, bo4h_low = compute_breakout_flags(high, low, close_last, params.breakout_4h_window)
    bo24h_high, bo24h_low = compute_breakout_flags(
        high, low, close_last, params.breakout_24h_window
    )
    features["breakout_4h_high"] = 1.0 if bo4h_high else 0.0
    features["breakout_4h_low"] = 1.0 if bo4h_low else 0.0
    features["breakout_24h_high"] = 1.0 if bo24h_high else 0.0
    features["breakout_24h_low"] = 1.0 if bo24h_low else 0.0

    return features
