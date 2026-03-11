"""
OPS-3: Derivatives Degrade Publisher

Tracks health state transitions for derivatives pipeline components
(CoinglassCollector, DerivativesEngine) and publishes events to NATS
ops.health when a component transitions between HEALTHY ↔ DEGRADED ↔ DOWN.

Only transitions are published — not every cycle — to avoid #system-health spam.

NATS stream: OPS_HEALTH / subject: ops.health
Payload: { "component": str, "status": str, "reason": str, "timestamp": str }

Consumed by the Discord bot → posts to #system-health channel.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger()

# NATS subject and stream for ops health events
OPS_SUBJECT = "ops.health"
OPS_STREAM = "OPS_HEALTH"

# Status constants — mirrors HealthStatus in api/src/health.py
STATUS_HEALTHY = "HEALTHY"
STATUS_DEGRADED = "DEGRADED"
STATUS_DOWN = "DOWN"


async def setup_stream(nc: Any) -> None:
    """Create OPS_HEALTH JetStream stream if it does not already exist."""
    js = nc.jetstream()
    try:
        await js.add_stream(name=OPS_STREAM, subjects=[OPS_SUBJECT])
    except Exception:
        pass  # Stream already exists


class DegradePublisher:
    """
    Tracks last-known status per component; publishes to NATS only on transitions.

    Usage:
        publisher = DegradePublisher(nc)
        await publisher.report("coinglass", STATUS_DOWN, "3 consecutive failures")
        await publisher.report("coinglass", STATUS_HEALTHY, "")  # publishes recovery
    """

    def __init__(self, nc: Any) -> None:
        self._nc = nc
        # component_name → last published status
        self._last_status: dict[str, str] = {}

    async def report(self, component: str, status: str, reason: str) -> None:
        """
        Report component status. Publishes to NATS only when status changes.

        All publish failures are logged and swallowed — degrade reporting must
        never crash the components it monitors.
        """
        last = self._last_status.get(component)
        if last == status:
            return  # No transition — skip

        self._last_status[component] = status
        log.info(
            "ops.degrade_transition",
            component=component,
            previous=last,
            current=status,
            reason=reason,
        )

        payload = {
            "component": component,
            "status": status,
            "reason": reason,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        try:
            js = self._nc.jetstream()
            await js.publish(OPS_SUBJECT, json.dumps(payload).encode())
        except Exception as exc:
            log.warning(
                "ops.degrade_publish_failed",
                component=component,
                status=status,
                error=str(exc),
            )
