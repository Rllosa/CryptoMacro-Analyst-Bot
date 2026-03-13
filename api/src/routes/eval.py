"""
EV-2: /api/eval/metrics endpoint.

Reads pre-computed quality metrics from Redis (written hourly by the processor's
MetricsService). Returns 503 if metrics have not yet been computed.

No DB calls in the request path — all data comes from the Redis cache.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_REDIS_KEY_7D = "eval:metrics:7d"
_REDIS_KEY_30D = "eval:metrics:30d"


@router.get("/api/eval/metrics")
async def get_eval_metrics(request: Request) -> JSONResponse:
    """
    Return alert quality metrics for 7d and 30d windows.

    Metrics are computed hourly by the processor and cached in Redis.
    Returns 503 if neither window has been computed yet.
    """
    redis = request.app.state.redis
    raw_7d, raw_30d = await asyncio.gather(
        redis.get(_REDIS_KEY_7D),
        redis.get(_REDIS_KEY_30D),
    )

    if raw_7d is None and raw_30d is None:
        return JSONResponse(
            {"error": "metrics not yet computed — check back after the first hourly cycle"},
            status_code=503,
        )

    return JSONResponse(
        {
            "7d": json.loads(raw_7d) if raw_7d else None,
            "30d": json.loads(raw_30d) if raw_30d else None,
        }
    )
