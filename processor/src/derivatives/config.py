from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DerivativeParams:
    """
    Computation parameters for the derivatives feature engine, loaded from
    thresholds.yaml.  Frozen so the values are safe to share across async tasks.
    """

    funding_zscore_lookback_days: int
    funding_zscore_min_samples: int
    oi_drop_threshold_pct: float

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> DerivativeParams:
        """Build DerivativeParams from the parsed thresholds.yaml dict."""
        p = thresholds["derivatives_params"]
        return cls(
            funding_zscore_lookback_days=int(p["funding_zscore_lookback_days"]),
            funding_zscore_min_samples=int(p["funding_zscore_min_samples"]),
            oi_drop_threshold_pct=float(p["oi_drop_threshold_pct"]),
        )

    @classmethod
    def load(cls, thresholds_path: str) -> DerivativeParams:
        """Load and parse thresholds.yaml, then return DerivativeParams."""
        path = Path(thresholds_path)
        with path.open() as fh:
            thresholds = yaml.safe_load(fh)
        return cls.from_thresholds(thresholds)
