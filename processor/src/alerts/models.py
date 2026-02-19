from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class AlertRecord:
    """
    Mirrors the `alerts` hypertable schema (schema/migrations/008_create_alerts.sql).

    trigger_conditions stores both the raw trigger values and the full
    input_snapshot of all feature values at fire time — enables replay.
    """

    id: str
    time: datetime
    alert_type: str
    severity: str
    symbol: str | None
    title: str
    description: str
    trigger_conditions: dict  # {"input_snapshot": {...}, "trigger_values": {...}}
    context: dict             # regime, regime_confidence, etc.
    regime_at_trigger: str | None
