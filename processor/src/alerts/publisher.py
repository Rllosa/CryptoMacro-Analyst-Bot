from __future__ import annotations

import json
from typing import Any

# NATS subject and stream name — hoisted at module level
_ALERTS_SUBJECT = "alerts.fired"
_ALERTS_STREAM = "ALERTS"


async def setup_stream(nc: Any) -> None:
    """
    Create the ALERTS JetStream stream if it does not already exist.

    Called once on processor startup. Idempotent — safe to call if stream exists.
    """
    js = nc.jetstream()
    try:
        await js.add_stream(name=_ALERTS_STREAM, subjects=[_ALERTS_SUBJECT])
    except Exception:
        # Stream already exists — not an error
        pass


async def publish_alert(nc: Any, payload: dict[str, Any]) -> None:
    """
    Publish an alert payload to NATS JetStream subject `alerts.fired`.

    Uses JetStream publish so the Discord bot's durable consumer can receive
    messages even if the bot was temporarily offline.
    """
    js = nc.jetstream()
    await js.publish(_ALERTS_SUBJECT, json.dumps(payload).encode())
