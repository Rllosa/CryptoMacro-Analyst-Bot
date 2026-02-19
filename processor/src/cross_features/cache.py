from __future__ import annotations

import json
import math
from datetime import datetime

# 10-minute TTL — matches the feature interval × 2 (rule 8.2)
_CROSS_FEATURE_TTL_SECS = 600


def _serialise(features: dict[str, float]) -> dict[str, float | None]:
    """Convert NaN to None for JSON serialisation (JSON has no NaN literal)."""
    return {k: None if math.isnan(v) else v for k, v in features.items()}


async def cache_cross_features(
    redis: object,
    cycle_time: datetime,
    features: dict[str, float],
) -> None:
    """
    Cache the latest cross-feature snapshot in Redis.

    Key:   cross_features:latest
    TTL:   600 s — expires well before the next cycle is due.
    Value: JSON with iso timestamp and feature dict (NaN → null).
    """
    key = "cross_features:latest"
    payload = json.dumps({"time": cycle_time.isoformat(), "features": _serialise(features)})
    await redis.setex(key, _CROSS_FEATURE_TTL_SECS, payload)
