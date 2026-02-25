from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(frozen=True)
class RegimeParams:
    """Parameters for the regime classifier — loaded from thresholds.yaml."""

    base_weight: float             # 0.4  — confidence if primary condition met
    condition_weight: float        # 0.15 — weight per additional condition met
    zscore_bonus_threshold: float  # 3.0  — rv_4h_zscore level that earns confidence bonus
    zscore_bonus: float            # 0.1  — confidence bonus for extreme volatility
    min_confidence: float          # 0.4  — below this → regime output is None (uncertain)
    regimes: dict[str, Any]        # raw regime definitions from thresholds.yaml
    tight_bb_bandwidth_max: float  # 0.03 — bb_bandwidth < this → "tight" price range
    volatility_regime_high_zscore_threshold: float  # 0.5 — "high" when rv_4h_zscore > this

    @classmethod
    def load(cls, thresholds_path: str) -> RegimeParams:
        with open(thresholds_path) as f:
            cfg = yaml.safe_load(f)["regime_classifier"]
        return cls(
            base_weight=cfg["confidence"]["base_weight"],
            condition_weight=cfg["confidence"]["condition_weight"],
            zscore_bonus_threshold=cfg["confidence"]["zscore_bonus_threshold"],
            zscore_bonus=cfg["confidence"]["zscore_bonus"],
            min_confidence=cfg["min_confidence"],
            regimes=cfg["regimes"],
            tight_bb_bandwidth_max=cfg.get("tight_bb_bandwidth_max", 0.03),
            volatility_regime_high_zscore_threshold=cfg.get(
                "volatility_regime_high_zscore_threshold", 0.5
            ),
        )
