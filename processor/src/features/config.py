from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class FeatureParams:
    """
    All feature-engine computation parameters loaded from thresholds.yaml.

    Frozen so the values are safe to share across async tasks without copying.
    Every numeric parameter that controls indicator computation lives here —
    zero hardcoded thresholds in indicator code.
    """

    rsi_period: int
    macd_fast: int
    macd_slow: int
    macd_signal: int
    bollinger_period: int
    bollinger_std: float
    atr_period: int
    rv_window_1h: int
    rv_window_4h: int
    volume_zscore_window: int
    rs_lookback: int
    rs_zscore_window: int
    breakout_4h_window: int
    breakout_24h_window: int
    ema_slope_period: int

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> FeatureParams:
        """Build FeatureParams from the parsed thresholds.yaml dict."""
        p = thresholds["feature_params"]
        return cls(
            rsi_period=int(p["rsi_period"]),
            macd_fast=int(p["macd_fast"]),
            macd_slow=int(p["macd_slow"]),
            macd_signal=int(p["macd_signal"]),
            bollinger_period=int(p["bollinger_period"]),
            bollinger_std=float(p["bollinger_std"]),
            atr_period=int(p["atr_period"]),
            rv_window_1h=int(p["rv_window_1h"]),
            rv_window_4h=int(p["rv_window_4h"]),
            volume_zscore_window=int(p["volume_zscore_window"]),
            rs_lookback=int(p["rs_lookback"]),
            rs_zscore_window=int(p["rs_zscore_window"]),
            breakout_4h_window=int(p["breakout_4h_window"]),
            breakout_24h_window=int(p["breakout_24h_window"]),
            ema_slope_period=int(p["ema_slope_period"]),
        )

    @classmethod
    def load(cls, thresholds_path: str) -> FeatureParams:
        """Load and parse thresholds.yaml, then return FeatureParams."""
        path = Path(thresholds_path)
        with path.open() as fh:
            thresholds = yaml.safe_load(fh)
        return cls.from_thresholds(thresholds)
