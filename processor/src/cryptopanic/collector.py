"""
Cryptopanic News Feed Collector (DI-8).

Polls Cryptopanic every 5 minutes for high-importance crypto news headlines
(filter=hot, currencies=BTC,ETH,SOL) and stores them in news_events for async
LLM classification (LLM-2b).

Consumer contract: LLM-2b reads rows WHERE classified = FALSE and sets TRUE
after classification. Rule 1.1 preserved — classification never runs in the
5-minute alert path; this collector is ingestion-only.

Data flow:
  Cryptopanic public API → news_events table (PostgreSQL)

No Redis cache — news data is consumed from the DB by LLM-2b.
API: public free tier, no auth required. Optional auth_token for higher limits.
Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import structlog

from config import Settings
from cryptopanic.db import insert_news_events

log = structlog.get_logger()

_BASE_URL = "https://cryptopanic.com/api/v1"
_ENDPOINT = "/posts/"
_CURRENCIES = "BTC,ETH,SOL"  # HYPE is not indexed by Cryptopanic
_MAX_FAILURES = 5


class CryptoppanicCollector:
    """
    Background service that polls Cryptopanic for hot news every 5 minutes.

    Fetches filter=hot posts for BTC, ETH, SOL. Posts older than
    cryptopanic_max_age_minutes are discarded client-side. Deduplication
    is handled at the DB layer via ON CONFLICT (url) DO NOTHING.

    Failure handling: consecutive failure counter; degrades gracefully after
    _MAX_FAILURES without crashing the service (rule 1.3).
    """

    def __init__(self, settings: Settings, pool: Any, redis: Any) -> None:
        self._settings = settings
        self._pool = pool
        # redis held for interface consistency — not used by this collector
        self._redis = redis
        self._shutdown = asyncio.Event()
        self._consecutive_failures = 0

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: collect news every cryptopanic_poll_interval_secs until shutdown.

        Uses time.monotonic() for drift-free timing.
        On startup: immediately runs a cycle to capture posts from the last
        max_age_minutes window — no separate backfill needed.
        """
        log.info(
            "cryptopanic_collector.starting",
            interval_secs=self._settings.cryptopanic_poll_interval_secs,
            max_age_minutes=self._settings.cryptopanic_max_age_minutes,
        )

        async with aiohttp.ClientSession() as session:
            while not self._shutdown.is_set():
                cycle_start = time.monotonic()
                cycle_time = datetime.now(tz=timezone.utc)

                try:
                    await self._run_cycle(session, cycle_time)
                    self._consecutive_failures = 0
                except Exception as exc:
                    self._consecutive_failures += 1
                    log.warning(
                        "cryptopanic_collector.cycle_failed",
                        error=str(exc),
                        consecutive=self._consecutive_failures,
                    )
                    if self._consecutive_failures >= _MAX_FAILURES:
                        log.warning(
                            "cryptopanic_collector.degraded",
                            consecutive=self._consecutive_failures,
                        )

                elapsed = time.monotonic() - cycle_start
                sleep_secs = max(
                    0.0, self._settings.cryptopanic_poll_interval_secs - elapsed
                )
                if sleep_secs > 0:
                    await asyncio.sleep(sleep_secs)

        log.info("cryptopanic_collector.stopped")

    async def _run_cycle(
        self, session: aiohttp.ClientSession, cycle_time: datetime
    ) -> None:
        """Fetch hot posts, filter by age, and insert into news_events."""
        data = await self._fetch_posts(session)
        results = data.get("results", [])
        rows = _parse_posts(
            results, cycle_time, self._settings.cryptopanic_max_age_minutes
        )

        if rows:
            written = await insert_news_events(self._pool, rows)
            log.info(
                "cryptopanic_collector.cycle_complete",
                fetched=len(results),
                attempted=written,
            )
        else:
            log.info(
                "cryptopanic_collector.no_new_posts",
                cycle_time=cycle_time.isoformat(),
            )

    async def _fetch_posts(self, session: aiohttp.ClientSession) -> dict:
        """
        Fetch /posts/ from Cryptopanic.

        Includes optional auth_token when configured.
        Raises on HTTP error — caller handles per-cycle failure tracking.
        """
        url = f"{_BASE_URL}{_ENDPOINT}"
        params: dict[str, str] = {
            "filter": "hot",
            "currencies": _CURRENCIES,
            "kind": "news",
        }
        if self._settings.cryptopanic_api_key:
            params["auth_token"] = self._settings.cryptopanic_api_key

        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


def _parse_posts(
    results: list[dict], now: datetime, max_age_minutes: int
) -> list[tuple]:
    """
    Parse Cryptopanic /posts/ results into insert-ready tuples.

    Each result item is checked for:
    - title present (skip if missing)
    - published_at within max_age_minutes of now (skip stale posts)

    importance = 'high' if votes["important"] > 0, else 'medium'.
    currencies extracted as list of currency code strings e.g. ["BTC", "ETH"].

    Output row: (source, headline, url, published_at, currencies, importance)
    Pure function — no I/O.
    """
    cutoff = now - timedelta(minutes=max_age_minutes)
    rows: list[tuple] = []

    for item in results:
        title = item.get("title")
        if not title:
            continue

        published_str = item.get("published_at") or item.get("created_at")
        if not published_str:
            continue

        try:
            published_at = datetime.fromisoformat(
                published_str.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            continue

        if published_at < cutoff:
            continue

        url = item.get("url")
        votes = item.get("votes") or {}
        importance = "high" if (votes.get("important") or 0) > 0 else "medium"
        currencies = [
            c["code"]
            for c in (item.get("currencies") or [])
            if c.get("code")
        ]

        rows.append(("cryptopanic", title, url, published_at, currencies, importance))

    return rows
