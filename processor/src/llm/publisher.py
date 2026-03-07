"""
LLM-3: NATS publisher for daily brief reports (SOLO-57).

Mirrors processor/src/alerts/publisher.py exactly — same idempotent setup_stream
pattern, same JetStream publish. The Discord bot subscribes to DAILY_BRIEF stream
with a durable consumer and posts the payload to #daily-brief.
"""

from __future__ import annotations

import json
from typing import Any

# NATS subject and stream — hoisted at module level (not rebuilt per call)
_REPORTS_SUBJECT = "reports.daily_brief"
_REPORTS_STREAM = "DAILY_BRIEF"


async def setup_stream(nc: Any) -> None:
    """
    Create the DAILY_BRIEF JetStream stream if it does not already exist.

    Called once on processor startup. Idempotent — safe to call if stream exists.
    """
    js = nc.jetstream()
    try:
        await js.add_stream(name=_REPORTS_STREAM, subjects=[_REPORTS_SUBJECT])
    except Exception:
        # Stream already exists — not an error
        pass


async def publish_report(nc: Any, payload: dict[str, Any]) -> None:
    """
    Publish a daily brief payload to NATS JetStream subject `reports.daily_brief`.

    Uses JetStream so the Discord bot's durable consumer receives the message
    even if the bot was temporarily offline.
    """
    js = nc.jetstream()
    await js.publish(_REPORTS_SUBJECT, json.dumps(payload).encode())
