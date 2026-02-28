"""Write Cryptopanic news posts to news_events (PostgreSQL)."""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Pre-built INSERT components — hoisted at module level, never rebuilt per call.
_COLS = "(source, headline, url, published_at, currencies, importance)"
_PH = "(%s, %s, %s, %s, %s, %s)"


async def insert_news_events(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-insert rows into news_events.

    Each row: (source: str, headline: str, url: str | None,
               published_at: datetime, currencies: list[str], importance: str)
    ON CONFLICT (url) DO NOTHING — silently skips already-seen posts (dedup by URL).
    Returns number of rows attempted.
    """
    if not rows:
        return 0
    values_clause = ", ".join(_PH for _ in rows)
    query = (
        f"INSERT INTO news_events {_COLS} VALUES {values_clause} "
        "ON CONFLICT (url) DO NOTHING"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
