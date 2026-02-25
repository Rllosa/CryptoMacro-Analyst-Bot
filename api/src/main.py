#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — REST API
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from config import ApiSettings
from health import HealthStore, _run_health_checks, health_poll_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = ApiSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(settings.db_dsn, open=False)
    await pool.open()

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    store = HealthStore()

    # Run one immediate check before accepting traffic
    result = await _run_health_checks(pool, redis)
    store.update(result)

    # Background poll every 30s — store ref in app.state to prevent GC
    task = asyncio.create_task(health_poll_loop(store, pool, redis))
    app.state.health_store = store
    app.state._health_task = task

    logger.info("API startup complete")
    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await redis.aclose()
    await pool.close()
    logger.info("API shutdown complete")


app = FastAPI(
    title="CryptoMacro Analyst Bot API",
    version="0.1.0",
    description="REST API for dashboard and integrations",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    return {
        "name": "CryptoMacro Analyst Bot API",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/api/health")
async def health():
    response = app.state.health_store.latest
    http_status = 503 if response.status.value == "DOWN" else 200
    return JSONResponse(content=response.model_dump(exclude_none=True), status_code=http_status)


def main() -> None:
    logger.info("CryptoMacro API starting...")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
