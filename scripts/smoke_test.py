#!/usr/bin/env python3
"""
QA-1: Phase 1 end-to-end smoke test (SOLO-86).

Proves the chain: Redis feature injection → VolExpansionEvaluator → AlertEngine → DB insert.

Requires: docker-compose timescaledb + redis running (NATS is mocked).

Exit code 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Resolve repo root and add processor/src to import path
_REPO = Path(__file__).parents[1]
sys.path.insert(0, str(_REPO / "processor" / "src"))

# Set env defaults before importing Settings (which reads env on import)
os.environ.setdefault("THRESHOLDS_PATH", str(_REPO / "configs" / "thresholds.yaml"))
os.environ.setdefault("SYMBOLS_PATH", str(_REPO / "configs" / "symbols.yaml"))
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "cryptomacro")
os.environ.setdefault("POSTGRES_PASSWORD", "cryptomacro_dev_password")
os.environ.setdefault("POSTGRES_DB", "cryptomacro")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import redis.asyncio as aioredis
from psycopg_pool import AsyncConnectionPool

from alerts.config import AlertParams
from alerts.engine import AlertEngine
from alerts.vol_expansion import VolExpansionEvaluator
from config import Settings

_SYMBOL = "BTCUSDT"
_REDIS_FEATURE_KEY = f"features:latest:{_SYMBOL.lower()}"
_COOLDOWN_KEY = f"cooldown:VOL_EXPANSION:{_SYMBOL}:up"
_PERSISTENCE_KEY = f"persistence:VOL_EXPANSION:{_SYMBOL}:up"
_FIXTURE_PATH = _REPO / "tests" / "fixtures" / "smoke_vol_expansion.json"
_FEATURE_TTL_SECS = 300

# Buffer baseline: 12 low + 12 slightly-higher values → variance ensures non-zero std.
# With rv_1h=0.06 injected, z-score ≈ 121 — far above the 2.5 HIGH threshold.
_BUFFER_BASELINE = [0.005] * 12 + [0.006] * 12


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    print("\nFAIL")
    sys.exit(1)


async def _run() -> None:
    print("Running Phase 1 end-to-end smoke test (QA-1)...\n")

    # _env_file=None skips the repo-root .env (which has multi-service keys
    # that processor's Settings model rejects as extra inputs).
    # All needed values are already set via os.environ.setdefault above.
    settings = Settings(_env_file=None)

    # ── 1. Redis reachability ────────────────────────────────────────────────
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.ping()
    except Exception as exc:
        _fail(f"Redis unreachable at {settings.redis_url}: {exc}")
    _ok(f"Redis reachable ({settings.redis_url})")

    # ── 2. DB reachability ───────────────────────────────────────────────────
    pool = AsyncConnectionPool(settings.db_dsn, open=False, min_size=1, max_size=2)
    try:
        await pool.open()
        await pool.check()
    except Exception as exc:
        _fail(f"DB unreachable at {settings.db_dsn}: {exc}")
    _ok(f"DB reachable (host={settings.postgres_host})")

    # ── 3. Cleanup stale keys ────────────────────────────────────────────────
    deleted = await redis.delete(_COOLDOWN_KEY, _PERSISTENCE_KEY, _REDIS_FEATURE_KEY)
    _ok(f"Keys cleaned ({deleted} deleted)")

    # ── 4. Inject crafted features into Redis ────────────────────────────────
    fixture = json.loads(_FIXTURE_PATH.read_text())
    fixture["time"] = datetime.now(tz=timezone.utc).isoformat()
    await redis.setex(_REDIS_FEATURE_KEY, _FEATURE_TTL_SECS, json.dumps(fixture))
    _ok(f"Features injected → {_REDIS_FEATURE_KEY} (TTL={_FEATURE_TTL_SECS}s)")

    # ── 5. Build mock NATS (no JetStream stream needed) ─────────────────────
    nc = MagicMock()
    nc.jetstream.return_value = AsyncMock()

    # ── 6. Build AlertEngine + VolExpansionEvaluator ─────────────────────────
    params = AlertParams.load(settings.thresholds_path)
    engine = AlertEngine(pool=pool, redis=redis, nc=nc, params=params)
    evaluator = VolExpansionEvaluator(settings=settings, redis=redis, engine=engine)

    # Pre-fill rv_1h buffer — skips 24-cycle warmup without polluting Redis
    evaluator._rv_buffers[_SYMBOL].extend(_BUFFER_BASELINE)

    # ── 7. Cycle 1 — persistence pending ────────────────────────────────────
    cycle_time = datetime.now(tz=timezone.utc)
    await evaluator._evaluate_symbol(_SYMBOL, cycle_time)
    _ok("Cycle 1 — persistence pending 1/2")

    # ── 8. Cycle 2 — alert fires ─────────────────────────────────────────────
    await evaluator._evaluate_symbol(_SYMBOL, cycle_time)
    _ok("Cycle 2 — alert fired")

    # ── 9. Assert alert in DB ────────────────────────────────────────────────
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM alerts "
                "WHERE alert_type = %s AND symbol = %s "
                "AND time > NOW() - INTERVAL '5 minutes'",
                ("VOL_EXPANSION", _SYMBOL),
            )
            row = await cur.fetchone()
            count = row[0] if row else 0
    except Exception as exc:
        _fail(f"DB query failed: {exc}")

    if count < 1:
        _fail(f"VOL_EXPANSION alert NOT found in DB (count={count})")

    _ok(f"VOL_EXPANSION alert detected in DB (count={count})")

    # ── Cleanup ──────────────────────────────────────────────────────────────
    await pool.close()
    await redis.aclose()

    print("\nPASS")


if __name__ == "__main__":
    asyncio.run(_run())
