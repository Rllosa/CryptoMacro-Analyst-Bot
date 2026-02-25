import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import jsonschema
import pytest

from src.health import (
    ComponentHealth,
    HealthResponse,
    HealthStatus,
    _run_health_checks,
)

_HEALTH_SCHEMA_PATH = Path(__file__).parents[2] / "schema" / "contracts" / "health_response.json"


def test_health_contract():
    """HealthResponse Pydantic model output validates against JSON schema contract."""
    now = datetime.now(timezone.utc).isoformat()
    response = HealthResponse(
        status=HealthStatus.HEALTHY,
        timestamp=now,
        components={
            "timescaledb": ComponentHealth(status=HealthStatus.HEALTHY, last_check=now),
            "redis": ComponentHealth(status=HealthStatus.HEALTHY, last_check=now),
            "binance_ws": ComponentHealth(status=HealthStatus.DEGRADED, last_check=now),
            "nats": ComponentHealth(status=HealthStatus.DEGRADED, last_check=now),
        },
    )
    data = json.loads(response.model_dump_json(exclude_none=True))
    schema = json.loads(_HEALTH_SCHEMA_PATH.read_text())
    jsonschema.validate(instance=data, schema=schema)


@pytest.mark.asyncio
async def test_critical_components_healthy():
    """timescaledb HEALTHY + redis HEALTHY + fresh data → overall DEGRADED (Phase 1 static components).

    Overall is DEGRADED (not HEALTHY) because nats/fred/etc. are statically DEGRADED
    in Phase 1. The critical infra components themselves are HEALTHY.
    """
    now = datetime.now(timezone.utc).isoformat()
    fresh_data = json.dumps({"time": now})

    pool = AsyncMock()
    pool.check = AsyncMock()

    redis = AsyncMock()
    redis.ping = AsyncMock()
    redis.get = AsyncMock(return_value=fresh_data)

    result = await _run_health_checks(pool, redis)

    assert result.status == HealthStatus.DEGRADED
    assert result.components["timescaledb"].status == HealthStatus.HEALTHY
    assert result.components["redis"].status == HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_timescaledb_down():
    """timescaledb DOWN → overall DOWN regardless of other components."""
    now = datetime.now(timezone.utc).isoformat()
    fresh_data = json.dumps({"time": now})

    pool = AsyncMock()
    pool.check = AsyncMock(side_effect=Exception("connection refused"))

    redis = AsyncMock()
    redis.ping = AsyncMock()
    redis.get = AsyncMock(return_value=fresh_data)

    result = await _run_health_checks(pool, redis)

    assert result.status == HealthStatus.DOWN
    assert result.components["timescaledb"].status == HealthStatus.DOWN


@pytest.mark.asyncio
async def test_optional_component_degraded():
    """Optional component DEGRADED (stale key) → overall DEGRADED, not DOWN."""
    pool = AsyncMock()
    pool.check = AsyncMock()

    redis = AsyncMock()
    redis.ping = AsyncMock()
    # All freshness keys missing → binance_ws/coinglass/yahoo_finance DEGRADED
    redis.get = AsyncMock(return_value=None)

    result = await _run_health_checks(pool, redis)

    assert result.status == HealthStatus.DEGRADED
    assert result.components["timescaledb"].status == HealthStatus.HEALTHY
    assert result.components["redis"].status == HealthStatus.HEALTHY
    assert result.components["binance_ws"].status == HealthStatus.DEGRADED
