"""
LLM-2b: Async News Classifier (SOLO-95)

Classifies unprocessed headlines from news_events using Claude Haiku.
Runs on a 5-minute background loop — never in the 5-minute alert hot path
(Rule 1.1 preserved). Output is written to:

  1. news_signals DB table (audit trail + durable store)
  2. news_signals:latest Redis key (TTL 2h) — AL-12 reads this for fast access

AL-12 evaluator reads structured JSON deterministically — no LLM in the
alert trigger path.

Classification cost: ~$0.002/cycle (10 headlines × Haiku pricing).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
import structlog

from llm.client import ClaudeClient
from llm.prompts import news_classify as news_classify_prompt

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_REDIS_KEY = "news_signals:latest"

# Parameterised INSERT for news_signals — built once at module load
_INSERT_SIGNAL = """
    INSERT INTO news_signals
      (news_event_id, relevant, direction, confidence, event_type,
       assets, reasoning, headline, source, published_at, age_minutes)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (news_event_id) DO NOTHING
"""

# Mark news_events row as classified
_MARK_CLASSIFIED = "UPDATE news_events SET classified = TRUE WHERE id = %s"

# Fetch unclassified headlines — parameterised cutoff passed at call time
_FETCH_UNCLASSIFIED = """
    SELECT id, headline, url, published_at, currencies, source
    FROM news_events
    WHERE classified = FALSE
      AND published_at > %s
    ORDER BY published_at DESC
    LIMIT %s
"""


class NewsClassifier:
    """
    Background service: classifies unclassified news_events rows via Claude.

    Loop cadence: news_classifier_interval_secs (default 300).
    Processes at most `max_per_cycle` headlines per iteration.
    All failures are logged and swallowed — Rule 1.3 (graceful degradation).
    """

    def __init__(self, settings: Any, pool: Any, redis: Any) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis
        self._shutdown = asyncio.Event()

        thresholds = self._load_thresholds()
        nc_cfg = thresholds.get("news_classifier") or {}
        self._max_per_cycle: int = int(nc_cfg.get("max_per_cycle", 10))
        self._max_age_minutes: int = int(nc_cfg.get("max_age_minutes", 30))
        self._redis_ttl: int = int(nc_cfg.get("redis_ttl_secs", 7200))
        self._redis_max: int = int(nc_cfg.get("redis_max_signals", 20))

    def _load_thresholds(self) -> dict[str, Any]:
        path = Path(self._settings.thresholds_path)
        try:
            with path.open() as fh:
                return yaml.safe_load(fh) or {}
        except Exception as exc:
            log.warning("news_classifier.thresholds_load_failed", error=str(exc))
            return {}

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def run(self) -> None:
        """Background loop — runs for the lifetime of the processor."""
        log.info(
            "news_classifier.starting",
            interval_secs=self._settings.news_classifier_interval_secs,
            max_per_cycle=self._max_per_cycle,
            max_age_minutes=self._max_age_minutes,
        )
        # Run immediately on startup to catch any headlines from the last window
        await self._classify_cycle()

        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(),
                    timeout=self._settings.news_classifier_interval_secs,
                )
            except asyncio.TimeoutError:
                pass
            if not self._shutdown.is_set():
                await self._classify_cycle()

    async def _classify_cycle(self) -> None:
        """One classification pass — fetch, classify, persist, cache."""
        t0 = time.monotonic()
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=self._max_age_minutes)

        rows = await self._fetch_unclassified(cutoff)
        if not rows:
            log.debug("news_classifier.no_unclassified")
            return

        log.info("news_classifier.cycle_start", count=len(rows))
        classified: list[dict[str, Any]] = []

        client = ClaudeClient(
            api_key=self._settings.anthropic_api_key,
            model=self._settings.claude_model_news,
        )

        for row in rows:
            signal = await self._classify_one(client, row, cutoff)
            if signal is not None:
                classified.append(signal)

        if classified:
            await self._persist(classified)
            await self._update_redis(classified)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "news_classifier.cycle_done",
            processed=len(rows),
            classified=len(classified),
            elapsed_ms=elapsed_ms,
        )

    async def _fetch_unclassified(self, cutoff: datetime) -> list[dict[str, Any]]:
        """Return up to max_per_cycle unclassified rows newer than cutoff."""
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(_FETCH_UNCLASSIFIED, (cutoff, self._max_per_cycle))
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in await cur.fetchall()]
        except Exception as exc:
            log.warning("news_classifier.fetch_failed", error=str(exc))
            return []

    async def _classify_one(
        self,
        client: ClaudeClient,
        row: dict[str, Any],
        cutoff: datetime,
    ) -> dict[str, Any] | None:
        """Classify a single headline. Returns signal dict or None on failure."""
        headline = row["headline"]
        published_at = row["published_at"]
        source = row.get("source", "cryptopanic")
        news_event_id = row["id"]

        prompt = news_classify_prompt.build(
            headline=headline,
            published_at=str(published_at),
            source=source,
        )

        try:
            text = await client.complete(
                prompt,
                system=news_classify_prompt.SYSTEM,
                max_tokens=256,
            )
            result = json.loads(text)
        except Exception as exc:
            log.warning(
                "news_classifier.classify_failed",
                news_event_id=news_event_id,
                headline=headline[:80],
                error=str(exc),
            )
            return None

        now = datetime.now(tz=timezone.utc)
        age_minutes = max(0, int((now - published_at.replace(tzinfo=timezone.utc)
                                  if published_at.tzinfo is None
                                  else now - published_at).total_seconds() / 60))

        return {
            "news_event_id": news_event_id,
            "relevant": bool(result.get("relevant", False)),
            "direction": str(result.get("direction", "neutral")),
            "confidence": str(result.get("confidence", "low")),
            "event_type": str(result.get("event_type", "other")),
            "assets": list(result.get("assets") or []),
            "reasoning": str(result.get("reasoning", "")),
            "headline": headline,
            "source": source,
            "published_at": published_at,
            "age_minutes": age_minutes,
        }

    async def _persist(self, signals: list[dict[str, Any]]) -> None:
        """Write signals to news_signals and mark news_events as classified."""
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    for sig in signals:
                        await cur.execute(
                            _INSERT_SIGNAL,
                            (
                                sig["news_event_id"],
                                sig["relevant"],
                                sig["direction"],
                                sig["confidence"],
                                sig["event_type"],
                                sig["assets"],
                                sig["reasoning"],
                                sig["headline"],
                                sig["source"],
                                sig["published_at"],
                                sig["age_minutes"],
                            ),
                        )
                        await cur.execute(_MARK_CLASSIFIED, (sig["news_event_id"],))
        except Exception as exc:
            log.warning("news_classifier.persist_failed", error=str(exc))

    async def _update_redis(self, signals: list[dict[str, Any]]) -> None:
        """Prepend new signals to news_signals:latest (capped list, TTL 2h)."""
        try:
            serialised = [
                json.dumps({
                    "news_event_id": s["news_event_id"],
                    "relevant": s["relevant"],
                    "direction": s["direction"],
                    "confidence": s["confidence"],
                    "event_type": s["event_type"],
                    "assets": s["assets"],
                    "headline": s["headline"],
                    "source": s["source"],
                    "published_at": str(s["published_at"]),
                    "age_minutes": s["age_minutes"],
                })
                for s in signals
            ]
            pipe = self._redis.pipeline()
            pipe.lpush(_REDIS_KEY, *serialised)
            pipe.ltrim(_REDIS_KEY, 0, self._redis_max - 1)
            pipe.expire(_REDIS_KEY, self._redis_ttl)
            await pipe.execute()
        except Exception as exc:
            log.warning("news_classifier.redis_update_failed", error=str(exc))
