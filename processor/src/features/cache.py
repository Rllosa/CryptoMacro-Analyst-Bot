from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import structlog

log = structlog.get_logger()

# TTL for feature snapshots in Redis — 10 minutes (rule 8.2)
_FEATURE_TTL_SECS = 600


def _serialise(features: dict[str, float]) -> dict[str, Any]:
    """Replace NaN with None so the payload is valid JSON."""
    return {k: (None if math.isnan(v) else v) for k, v in features.items()}


async def cache_features(
    redis: Any, symbol: str, cycle_time: datetime, features: dict[str, float]
) -> None:
    """
    Write the latest feature snapshot for a symbol to Redis.

    Key:   features:latest:{symbol_lower}   (e.g. features:latest:btcusdt)
    TTL:   600 seconds
    Value: JSON {"time": "<ISO 8601>", "features": {...}}
    """
    key = f"features:latest:{symbol.lower()}"
    payload = json.dumps({"time": cycle_time.isoformat(), "features": _serialise(features)})
    try:
        await redis.setex(key, _FEATURE_TTL_SECS, payload)
    except Exception as exc:
        # Redis failure degrades gracefully — DB write already happened
        log.warning("features.cache_failed", symbol=symbol, error=str(exc))
