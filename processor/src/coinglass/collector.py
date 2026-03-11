from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp
import structlog
from psycopg_pool import AsyncConnectionPool

from config import Settings
from coinglass.db import upsert_derivatives
from coinglass.models import FundingEntry, LiqEntry, LongShortEntry, OIEntry
from ops.degrade import DegradePublisher, STATUS_DEGRADED, STATUS_DOWN, STATUS_HEALTHY

log = structlog.get_logger()

_SYMBOLS = ["BTC", "ETH", "SOL", "HYPE"]


class CoinglassCollector:
    """
    Polls 4 Coinglass REST endpoints every poll_interval seconds and writes
    the merged results to derivatives_metrics.

    Cycle:
      1. For each of 4 symbols, concurrently fetch /funding, /open_interest,
         /liquidation, and /long_short_ratio.
      2. Parse responses with Pydantic models, merge by exchange.
      3. Upsert all rows to derivatives_metrics in a single multi-row INSERT.

    Graceful degradation: per-symbol failures are logged and skipped; the
    remaining symbols still write. After _MAX_FAILURES consecutive full-cycle
    failures, transitions to DOWN and notifies via DegradePublisher (OPS-3).
    """

    _MAX_FAILURES = 3

    def __init__(
        self,
        settings: Settings,
        pool: AsyncConnectionPool,
        degrade_publisher: DegradePublisher | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._shutdown = asyncio.Event()
        self._consecutive_failures = 0
        self._degrade_publisher = degrade_publisher
        # Semaphore caps concurrent outbound HTTP calls regardless of symbol count
        self._sem = asyncio.Semaphore(10)

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: poll Coinglass every coinglass_poll_interval_secs until shutdown.

        Uses monotonic timing to avoid drift — sleep_secs = interval - elapsed.
        """
        log.info(
            "coinglass_collector.starting",
            interval_secs=self._settings.coinglass_poll_interval_secs,
            symbols=_SYMBOLS,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            try:
                await self._run_cycle()
                if self._consecutive_failures > 0:
                    # Recovered — notify on transition back to HEALTHY
                    if self._degrade_publisher is not None:
                        await self._degrade_publisher.report(
                            "coinglass", STATUS_HEALTHY, "Coinglass API recovered"
                        )
                self._consecutive_failures = 0
            except Exception as exc:
                self._consecutive_failures += 1
                log.warning(
                    "coinglass_collector.cycle_failed",
                    consecutive=self._consecutive_failures,
                    error=str(exc),
                )
                if self._consecutive_failures >= self._MAX_FAILURES:
                    log.warning(
                        "coinglass_collector.degraded",
                        consecutive=self._consecutive_failures,
                    )
                    if self._degrade_publisher is not None:
                        status = STATUS_DOWN if self._consecutive_failures >= self._MAX_FAILURES * 2 else STATUS_DEGRADED
                        await self._degrade_publisher.report(
                            "coinglass",
                            status,
                            f"Coinglass API unreachable — {self._consecutive_failures} consecutive failures",
                        )

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.coinglass_poll_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("coinglass_collector.stopped")

    async def _run_cycle(self) -> None:
        """One full poll: fetch all symbols concurrently, then upsert."""
        now = datetime.now(tz=timezone.utc)
        headers = {"CG-API-KEY": self._settings.coinglass_api_key}

        async with aiohttp.ClientSession(headers=headers) as session:
            results = await asyncio.gather(
                *(self._fetch_symbol(session, sym, now) for sym in _SYMBOLS),
                return_exceptions=True,
            )

        good_rows: list[tuple] = []
        for sym, result in zip(_SYMBOLS, results):
            if isinstance(result, Exception):
                log.warning(
                    "coinglass_collector.symbol_failed",
                    symbol=sym,
                    error=str(result),
                )
            else:
                good_rows.extend(result)

        written = await upsert_derivatives(self._pool, good_rows)
        log.info("coinglass_collector.cycle_done", rows_written=written)

    async def _fetch_symbol(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        now: datetime,
    ) -> list[tuple]:
        """
        Fetch funding, open interest, liquidation, and long/short ratio for one
        symbol concurrently.  Merge per-exchange data into DB-ready row tuples.

        Returns a list of 8-tuples ready for upsert_derivatives().
        """
        base = self._settings.coinglass_base_url
        params = {"symbol": symbol}
        liq_params = {"symbol": symbol, "range": "1h"}
        timeout = aiohttp.ClientTimeout(total=10)

        async def _get(endpoint: str, ep_params: dict) -> Any:
            async with self._sem:
                async with session.get(
                    f"{base}{endpoint}", params=ep_params, timeout=timeout
                ) as resp:
                    resp.raise_for_status()
                    return await resp.json()

        funding_resp, oi_resp, liq_resp, ls_resp = await asyncio.gather(
            _get("/futures/fundingRate/exchange-list", params),
            _get("/futures/openInterest/exchange-list", params),
            _get("/futures/liquidation/exchange-list", liq_params),
            _get("/futures/global-long-short-account-ratio/history", params),
        )

        # Build per-exchange lookup dicts from each endpoint's data list.
        # Exchange names are lowercased for consistent PK values in the DB.
        funding_by_ex: dict[str, FundingEntry] = {
            e.exchange.lower(): e
            for e in (
                FundingEntry.model_validate(r)
                for r in (funding_resp.get("data") or [])
            )
        }
        oi_by_ex: dict[str, OIEntry] = {
            e.exchange.lower(): e
            for e in (OIEntry.model_validate(r) for r in (oi_resp.get("data") or []))
        }
        liq_by_ex: dict[str, LiqEntry] = {
            e.exchange.lower(): e
            for e in (LiqEntry.model_validate(r) for r in (liq_resp.get("data") or []))
        }
        ls_by_ex: dict[str, LongShortEntry] = {
            e.exchange.lower(): e
            for e in (
                LongShortEntry.model_validate(r)
                for r in (ls_resp.get("data") or [])
            )
        }

        # Union of all exchange keys — a missing key in one dict means NULL for that column
        all_exchanges = set(funding_by_ex) | set(oi_by_ex) | set(liq_by_ex) | set(ls_by_ex)

        rows: list[tuple] = []
        for exchange in all_exchanges:
            f = funding_by_ex.get(exchange)
            o = oi_by_ex.get(exchange)
            lq = liq_by_ex.get(exchange)
            ls = ls_by_ex.get(exchange)
            rows.append((
                now,
                symbol,
                exchange,
                f.funding_rate if f else None,
                o.open_interest_usd if o else None,
                lq.liq_usd_1h if lq else None,
                ls.long_account_ratio if ls else None,
                ls.short_account_ratio if ls else None,
            ))

        return rows
