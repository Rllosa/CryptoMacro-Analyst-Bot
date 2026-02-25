import asyncio
import json
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class ComponentHealth(BaseModel):
    status: HealthStatus
    last_check: Optional[str] = None
    message: Optional[str] = None
    latency_ms: Optional[float] = None


class HealthResponse(BaseModel):
    status: HealthStatus
    timestamp: str
    components: dict[str, ComponentHealth]


class HealthStore:
    def __init__(self) -> None:
        self._latest: Optional[HealthResponse] = None

    @property
    def latest(self) -> HealthResponse:
        if self._latest is None:
            now = datetime.now(timezone.utc).isoformat()
            return HealthResponse(
                status=HealthStatus.DEGRADED,
                timestamp=now,
                components={
                    "timescaledb": ComponentHealth(status=HealthStatus.DEGRADED, message="Not yet checked"),
                    "redis": ComponentHealth(status=HealthStatus.DEGRADED, message="Not yet checked"),
                    "binance_ws": ComponentHealth(status=HealthStatus.DEGRADED, message="Not yet checked"),
                    "nats": ComponentHealth(status=HealthStatus.DEGRADED, message="Not yet checked"),
                },
            )
        return self._latest

    def update(self, response: HealthResponse) -> None:
        self._latest = response


async def _check_redis_key_age(redis, key: str, max_age_seconds: int) -> HealthStatus:
    try:
        raw = await redis.get(key)
        if raw is None:
            return HealthStatus.DEGRADED
        data = json.loads(raw)
        ts_str = data.get("time") or data.get("timestamp")
        if ts_str is None:
            return HealthStatus.DEGRADED
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return HealthStatus.HEALTHY if age < max_age_seconds else HealthStatus.DEGRADED
    except Exception:
        return HealthStatus.DEGRADED


async def _run_health_checks(pool, redis) -> HealthResponse:
    now = datetime.now(timezone.utc).isoformat()
    components: dict[str, ComponentHealth] = {}

    # timescaledb
    t0 = time.monotonic()
    try:
        await pool.check()
        latency = (time.monotonic() - t0) * 1000
        components["timescaledb"] = ComponentHealth(
            status=HealthStatus.HEALTHY, last_check=now, latency_ms=round(latency, 2)
        )
    except Exception as e:
        components["timescaledb"] = ComponentHealth(
            status=HealthStatus.DOWN, last_check=now, message=str(e)
        )

    # redis
    t0 = time.monotonic()
    try:
        await redis.ping()
        latency = (time.monotonic() - t0) * 1000
        components["redis"] = ComponentHealth(
            status=HealthStatus.HEALTHY, last_check=now, latency_ms=round(latency, 2)
        )
    except Exception as e:
        components["redis"] = ComponentHealth(
            status=HealthStatus.DOWN, last_check=now, message=str(e)
        )

    # binance_ws — features data freshness (stale if > 120s)
    bws_status = await _check_redis_key_age(redis, "features:latest:btc", 120)
    components["binance_ws"] = ComponentHealth(status=bws_status, last_check=now)

    # coinglass — derivatives data freshness (stale if > 15m)
    cg_status = await _check_redis_key_age(redis, "derivatives:latest:btcusdt", 900)
    components["coinglass"] = ComponentHealth(status=cg_status, last_check=now)

    # yahoo_finance — macro data freshness (stale if > 24h)
    yf_status = await _check_redis_key_age(redis, "macro:latest:vix", 86400)
    components["yahoo_finance"] = ComponentHealth(status=yf_status, last_check=now)

    # Static DEGRADED — not yet integrated in Phase 1
    for name in ("nats", "fred", "onchain_provider", "discord", "claude_api"):
        components[name] = ComponentHealth(
            status=HealthStatus.DEGRADED, last_check=now, message="Not yet integrated"
        )

    # Overall: DOWN if any critical DOWN, DEGRADED if any DEGRADED, else HEALTHY
    statuses = [c.status for c in components.values()]
    if HealthStatus.DOWN in statuses:
        overall = HealthStatus.DOWN
    elif HealthStatus.DEGRADED in statuses:
        overall = HealthStatus.DEGRADED
    else:
        overall = HealthStatus.HEALTHY

    return HealthResponse(status=overall, timestamp=now, components=components)


async def health_poll_loop(store: HealthStore, pool, redis, interval: int = 30) -> None:
    while True:
        result = await _run_health_checks(pool, redis)
        store.update(result)
        await asyncio.sleep(interval)
