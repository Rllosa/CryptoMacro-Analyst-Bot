from __future__ import annotations

import json
from typing import Any

# NATS subject hoisted at module level — one string, allocated at import time
_ALERTS_SUBJECT = "alerts.fired"


async def publish_alert(nc: Any, payload: dict[str, Any]) -> None:
    """
    Publish an alert payload to NATS subject `alerts.fired`.

    Uses regular NATS publish (not JetStream) — DEL-1 will add a durable
    JetStream consumer when the Discord bot is implemented. Regular publish
    is sufficient for Phase 1 since the consumer is not yet live.
    """
    await nc.publish(_ALERTS_SUBJECT, json.dumps(payload).encode())
