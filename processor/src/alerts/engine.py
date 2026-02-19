from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog

from alerts.config import AlertParams
from alerts.cooldown import CooldownRegistry
from alerts.db import insert_alert
from alerts.models import AlertRecord
from alerts.persistence import PersistenceTracker
from alerts.publisher import publish_alert
from alerts.validator import validate_payload

log = structlog.get_logger()


def _build_title(alert_type: str, symbol: str | None) -> str:
    """Human-readable title for the alert row and NATS payload."""
    if symbol:
        return f"{alert_type} — {symbol}"
    return f"{alert_type} — Market-Wide"


def _build_description(alert_type: str, trigger_values: dict[str, Any]) -> str:
    """Compact description summarising what triggered the alert."""
    pairs = ", ".join(f"{k}={v}" for k, v in trigger_values.items())
    return f"{alert_type} triggered: {pairs}"


def _build_nats_payload(
    record: AlertRecord,
    cooldown_minutes: int,
    fire_time: datetime,
) -> dict[str, Any]:
    """Build the NATS alert payload conforming to the F-7 alert_payload.json contract."""
    from datetime import timedelta

    cooldown_until = (fire_time + timedelta(minutes=cooldown_minutes)).isoformat()
    return {
        "alert_id": record.id,
        "alert_type": record.alert_type,
        "symbol": record.symbol,
        "severity": record.severity,
        "time": fire_time.isoformat(),
        "conditions": record.trigger_conditions,
        "context": record.context,
        "message": record.description,
        "cooldown_until": cooldown_until,
    }


class AlertEngine:
    """
    Shared alert infrastructure — cooldown, persistence, DB persistence, NATS publish.

    Not a background service. Each concrete alert evaluator (AL-2 VOL_EXPANSION,
    AL-3 LEADERSHIP_ROTATION, etc.) calls evaluate_and_fire() once per 5m cycle
    after computing whether its trigger conditions are met.

    Evaluation order (fastest rejection first):
      1. conditions_met is False → reset persistence, return False
      2. cooldown active in Redis → log suppressed, reset persistence, return False
      3. persistence count < required cycles → increment, return False
      4. Build payload, validate against F-7 schema → raises on contract violation
      5. Insert to DB → publish to NATS → activate cooldown → reset persistence
      6. Log and return True
    """

    def __init__(
        self,
        pool: Any,
        redis: Any,
        nc: Any,
        params: AlertParams,
    ) -> None:
        self._pool = pool
        self._redis = redis
        self._nc = nc
        self._params = params
        self._cooldown = CooldownRegistry(redis)
        self._persistence = PersistenceTracker()

    async def evaluate_and_fire(
        self,
        alert_type: str,
        symbol: str | None,
        direction: str,
        conditions_met: bool,
        severity: str,
        trigger_values: dict[str, Any],
        context: dict[str, Any],
        input_snapshot: dict[str, Any],
        fire_time: datetime,
    ) -> bool:
        """
        Apply persistence + cooldown logic. Fire alert if ready.

        Args:
            alert_type: One of the defined alert type strings (e.g. "VOL_EXPANSION").
            symbol: Asset symbol (e.g. "BTCUSDT") or None for market-wide alerts.
            direction: Dedup dimension (e.g. "up", "down", "ETH_over_BTC"). Together
                with alert_type and symbol, uniquely identifies the alert signature.
            conditions_met: Whether the trigger condition is currently satisfied.
            severity: "LOW", "MEDIUM", or "HIGH".
            trigger_values: The feature values that met the condition (stored + logged).
            context: Regime and other context at trigger time.
            input_snapshot: Full feature snapshot — enables alert replay.
            fire_time: UTC datetime for the alert row and NATS payload.

        Returns:
            True if the alert fired this cycle, False otherwise.
        """
        # Dedup key uniquely identifies this (type, symbol, direction) combination.
        # Built once here — never reconstructed in the checks below.
        dedup_key = f"{symbol or '_'}:{direction}"

        # 1. Condition not met — reset persistence so the N-cycle count restarts.
        if not conditions_met:
            self._persistence.record_not_met(f"{alert_type}:{dedup_key}")
            return False

        # 2. In cooldown — suppress without incrementing persistence.
        # Reset persistence so the counter restarts after cooldown expires.
        if await self._cooldown.is_active(alert_type, dedup_key):
            log.info(
                "alert.suppressed_cooldown",
                alert_type=alert_type,
                symbol=symbol,
                direction=direction,
            )
            self._persistence.record_not_met(f"{alert_type}:{dedup_key}")
            return False

        # 3. Check persistence — condition must hold for N consecutive cycles.
        persistence_key = f"{alert_type}:{dedup_key}"
        count = self._persistence.record_met(persistence_key)
        required = self._params.persistence_cycles.get(alert_type, 1)

        if count < required:
            log.info(
                "alert.persistence_pending",
                alert_type=alert_type,
                symbol=symbol,
                count=count,
                required=required,
            )
            return False

        # 4. Build record and validate payload against F-7 contract.
        record = AlertRecord(
            id=str(uuid.uuid4()),
            time=fire_time,
            alert_type=alert_type,
            severity=severity,
            symbol=symbol,
            title=_build_title(alert_type, symbol),
            description=_build_description(alert_type, trigger_values),
            trigger_conditions={
                "trigger_values": trigger_values,
                "input_snapshot": input_snapshot,
            },
            context=context,
            regime_at_trigger=context.get("regime"),
        )

        cooldown_minutes = self._params.cooldown_minutes.get(alert_type, 60)
        payload = _build_nats_payload(record, cooldown_minutes, fire_time)
        validate_payload(payload)  # raises jsonschema.ValidationError on contract violation

        # 5. Persist to DB, publish to NATS, activate cooldown.
        await insert_alert(self._pool, record)
        await publish_alert(self._nc, payload)
        await self._cooldown.activate(alert_type, dedup_key, cooldown_minutes)

        # Reset persistence after fire — next fire requires another N cycles.
        self._persistence.record_not_met(persistence_key)

        log.info(
            "alert.fired",
            alert_type=alert_type,
            symbol=symbol,
            severity=severity,
            direction=direction,
            alert_id=record.id,
        )
        return True
