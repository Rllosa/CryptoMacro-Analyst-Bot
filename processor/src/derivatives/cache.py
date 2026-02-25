from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import structlog

log = structlog.get_logger()

_DERIVATIVE_TTL_SECS = 600


def _serialise(features: dict[str, float | None]) -> dict[str, Any]:
    """Replace NaN with None so the payload is valid JSON."""
    result = {}
    for k, v in features.items():
        if v is None:
            result[k] = None
        elif isinstance(v, float) and math.isnan(v):
            result[k] = None
        else:
            result[k] = v
    return result


async def cache_derivatives(
    redis: Any,
    symbol: str,
    cycle_time: datetime,
    features: dict[str, float | None],
) -> None:
    """
    Write the latest derivatives feature snapshot for a symbol to Redis.

    Key:   derivatives:latest:{symbol_lower}usdt  (e.g. derivatives:latest:btcusdt)
    TTL:   600 seconds
    Value: JSON {"time": "<ISO 8601>", "features": {...}}
    """
    key = f"derivatives:latest:{symbol.lower()}usdt"
    payload = json.dumps({"time": cycle_time.isoformat(), "features": _serialise(features)})
    try:
        await redis.setex(key, _DERIVATIVE_TTL_SECS, payload)
    except Exception as exc:
        log.warning("derivatives.cache_failed", symbol=symbol, error=str(exc))
