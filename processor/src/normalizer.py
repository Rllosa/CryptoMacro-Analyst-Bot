from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import nats
import nats.errors
import structlog
from pydantic import ValidationError
from psycopg_pool import AsyncConnectionPool

from config import Settings
from db import upsert_candles
from models import CandleMessage

log = structlog.get_logger()

# How long to wait for NATS/stream to become available on startup
_NATS_CONNECT_MAX_RETRIES = 10


class Normalizer:
    """
    Subscribes to market.candles.> on NATS JetStream and batch-writes
    candles to market_candles with deduplication.

    Flush policy: whichever comes first — batch_size messages accumulated,
    or batch_timeout_secs elapsed since last flush. This bounds both latency
    and write amplification under variable load.
    """

    def __init__(self, settings: Settings, pool: AsyncConnectionPool) -> None:
        self._settings = settings
        self._pool = pool
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after draining the current batch."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main event loop: consume NATS messages, accumulate into batches,
        flush to TimescaleDB, ack after successful write.

        Runs until request_shutdown() is called. On shutdown, any buffered
        messages are flushed before the NATS subscription is closed.
        """
        nc, sub = await self._connect_with_retry()
        log.info(
            "normalizer.subscribed",
            subject=self._settings.nats_subject,
            consumer=self._settings.nats_consumer_name,
        )

        # batch holds (msg, db_row) pairs
        batch: list[tuple[Any, tuple]] = []
        last_flush = time.monotonic()

        try:
            while not self._shutdown.is_set():
                msg = await self._next_message(sub)

                if msg is not None:
                    row = self._parse_message(msg)
                    if row is not None:
                        batch.append((msg, row))

                elapsed = time.monotonic() - last_flush
                should_flush = len(batch) >= self._settings.batch_size or (
                    batch and elapsed >= self._settings.batch_timeout_secs
                )

                if should_flush:
                    await self._flush(batch)
                    batch = []
                    last_flush = time.monotonic()

            # Drain remaining on shutdown
            if batch:
                await self._flush(batch)
        finally:
            await sub.unsubscribe()
            await nc.drain()
            log.info("normalizer.stopped")

    async def _next_message(self, sub: Any) -> Any | None:
        """Fetch next NATS message, returning None on timeout."""
        try:
            return await asyncio.wait_for(sub.next_msg(), timeout=1.0)
        except (asyncio.TimeoutError, nats.errors.TimeoutError):
            return None

    def _parse_message(self, msg: Any) -> tuple | None:
        """
        Deserialize and validate a NATS candle message.

        Returns a DB row tuple on success, or None if the message is malformed.
        JSON errors and schema validation errors are logged separately so
        bad-JSON and bad-schema failures are distinguishable in logs.
        """
        try:
            data = json.loads(msg.data.decode())
        except json.JSONDecodeError as exc:
            log.warning("normalizer.parse_failed.invalid_json", error=str(exc))
            return None

        try:
            candle = CandleMessage.model_validate(data)
            return candle.to_db_row()
        except ValidationError as exc:
            log.warning(
                "normalizer.parse_failed.invalid_schema",
                error=str(exc),
                symbol=data.get("symbol"),
            )
            return None

    async def _flush(self, batch: list[tuple[Any, tuple]]) -> None:
        """
        Write batch to TimescaleDB, then ack all messages.

        Messages are NOT acked on DB failure — JetStream will redeliver
        them after ack_wait expires, providing at-least-once delivery.
        """
        rows = [row for _, row in batch]
        msgs = [msg for msg, _ in batch]
        try:
            attempted = await upsert_candles(self._pool, rows)
            for msg in msgs:
                await msg.ack()
            log.info("normalizer.flushed", attempted=attempted, batch_size=len(rows))
        except Exception as exc:
            # Do not ack — JetStream will redeliver after ack_wait expires
            log.error("normalizer.flush_failed", error=str(exc), batch_size=len(rows))

    async def _connect_with_retry(self) -> tuple[Any, Any]:
        """Connect to NATS and subscribe, retrying until stream is available."""
        for attempt in range(1, _NATS_CONNECT_MAX_RETRIES + 1):
            try:
                nc = await nats.connect(self._settings.nats_url)
                js = nc.jetstream()
                sub = await js.subscribe(
                    self._settings.nats_subject,
                    stream=self._settings.nats_stream,
                    durable=self._settings.nats_consumer_name,
                )
                return nc, sub
            except Exception as exc:
                if attempt >= _NATS_CONNECT_MAX_RETRIES:
                    raise
                delay = min(2 ** (attempt - 1), 30)
                log.warning(
                    "nats.connect_failed",
                    attempt=attempt,
                    error=str(exc),
                    retry_in_secs=delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"Could not connect to NATS at {self._settings.nats_url} "
            f"after {_NATS_CONNECT_MAX_RETRIES} attempts"
        )
