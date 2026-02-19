from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AlertParams:
    """
    All alert-engine parameters loaded from thresholds.yaml.

    Frozen so values are safe to share across async tasks without copying.
    Every cooldown duration and persistence requirement lives here —
    zero hardcoded thresholds in engine code.
    """

    cooldown_minutes: dict[str, int]
    persistence_cycles: dict[str, int]

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> AlertParams:
        """Build AlertParams from the parsed thresholds.yaml dict."""
        return cls(
            cooldown_minutes=dict(thresholds["cooldowns"]["per_alert_type"]),
            persistence_cycles=dict(thresholds["persistence"]["per_alert_type"]),
        )

    @classmethod
    def load(cls, thresholds_path: str) -> AlertParams:
        """Load and parse thresholds.yaml, then return AlertParams."""
        with Path(thresholds_path).open() as fh:
            thresholds = yaml.safe_load(fh)
        return cls.from_thresholds(thresholds)
