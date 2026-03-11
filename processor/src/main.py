#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — Processor Service
Phase 1–2 — DI-2, DI-4, DI-5, FE-1, FE-2, AL-1

Entry point: loads config, runs backfill on startup, then runs the
NATS-to-TimescaleDB normalizer, per-asset feature engine, cross-asset
feature engine, Coinglass derivatives collector, and Yahoo Finance collector
concurrently. AlertEngine is initialized here and passed to alert evaluators
(AL-2+) — it has no run loop of its own.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

import nats as nats_client
import redis.asyncio as aioredis
import structlog

# Ensure src/ is on the path when run directly (must come before local imports)
sys.path.insert(0, str(Path(__file__).parent))

from alerts.breakout import BreakoutEvaluator  # noqa: E402
from alerts.crowded_leverage import CrowdedLeverageEvaluator  # noqa: E402
from alerts.deleveraging_event import DeleveragingEvaluator  # noqa: E402
from alerts.news_event import NewsEventEvaluator  # noqa: E402
from alerts.correlation_break import CorrelationBreakEvaluator  # noqa: E402
from coingecko.collector import CoinGeckoCollector  # noqa: E402
from coinglass.heatmap_collector import CoinglassHeatmapCollector  # noqa: E402
from cryptopanic.collector import CryptoppanicCollector  # noqa: E402
from deribit.collector import DeribitCollector  # noqa: E402
from alerts.regime_shift import RegimeShiftEvaluator  # noqa: E402
from alerts.config import AlertParams  # noqa: E402
from alerts.publisher import setup_stream  # noqa: E402
from alerts.engine import AlertEngine  # noqa: E402
from alerts.leadership_rotation import LeadershipRotationEvaluator  # noqa: E402
from alerts.vol_expansion import VolExpansionEvaluator  # noqa: E402
from regime.engine import RegimeClassifier  # noqa: E402
from backfill import run_backfill  # noqa: E402
from coinglass.collector import CoinglassCollector  # noqa: E402
from config import Settings  # noqa: E402
from yahoo_finance.collector import YahooFinanceCollector  # noqa: E402
from cross_features.engine import CrossFeatureEngine  # noqa: E402
from db import create_pool_with_retry  # noqa: E402
from derivatives.engine import DerivativesEngine  # noqa: E402
from features.engine import FeatureEngine  # noqa: E402
from llm import publisher as brief_publisher  # noqa: E402
from llm.event_analyzer import EventAnalyzer, setup_stream as event_stream_setup  # noqa: E402
from llm.news_classifier import NewsClassifier  # noqa: E402
from llm.scheduler import DailyBriefScheduler  # noqa: E402
from normalizer import Normalizer  # noqa: E402
from eval.tracker import AlertMoveTracker  # noqa: E402
from ops.degrade import DegradePublisher, setup_stream as ops_stream_setup  # noqa: E402

log = structlog.get_logger()


def _configure_logging() -> None:
    """Configure structlog for JSON output to stdout with ISO timestamps and log level filtering."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


async def main() -> None:
    """
    Service entry point: configure logging, connect to TimescaleDB and Redis,
    run startup backfill, then run the Normalizer and FeatureEngine concurrently
    until a shutdown signal is received.
    """
    _configure_logging()

    settings = Settings()
    log.info("processor.starting", nats_url=settings.nats_url, postgres_host=settings.postgres_host)

    # Connect to TimescaleDB with exponential-backoff retry
    pool = await create_pool_with_retry(settings.db_dsn)
    log.info("processor.db_connected")

    # Connect to NATS for alert publishing (AL-1+)
    nc = await nats_client.connect(settings.nats_url)
    log.info("processor.nats_connected")

    # Create ALERTS JetStream stream — idempotent, safe to call on every startup
    await setup_stream(nc)
    log.info("processor.nats_stream_ready", stream="ALERTS")

    # Create DAILY_BRIEF JetStream stream — idempotent
    await brief_publisher.setup_stream(nc)
    log.info("processor.nats_stream_ready", stream="DAILY_BRIEF")

    # Create EVENT_ANALYSIS JetStream stream — idempotent
    await event_stream_setup(nc)
    log.info("processor.nats_stream_ready", stream="EVENT_ANALYSIS")

    # Create OPS_HEALTH JetStream stream — idempotent
    await ops_stream_setup(nc)
    log.info("processor.nats_stream_ready", stream="OPS_HEALTH")

    # Connect to Redis for feature caching
    redis_client = await aioredis.from_url(settings.redis_url, decode_responses=True)
    log.info("processor.redis_connected")

    # Gap backfill on startup — fetch any missing 1m candles from Binance REST
    try:
        await run_backfill(settings, pool)
    except Exception as exc:
        log.warning("processor.backfill_failed", error=str(exc))

    normalizer = Normalizer(settings, pool)
    feature_engine = FeatureEngine(settings, pool, redis_client)
    cross_engine = CrossFeatureEngine(settings, pool, redis_client)
    # AlertEngine has no run loop — AL-2+ evaluators call evaluate_and_fire() each cycle
    alert_engine = AlertEngine(pool, redis_client, nc, AlertParams.load(settings.thresholds_path))
    degrade_publisher = DegradePublisher(nc)
    coinglass = CoinglassCollector(settings, pool, degrade_publisher)
    yahoo_finance = YahooFinanceCollector(settings, pool, redis_client)
    derivatives_engine = DerivativesEngine(settings, pool, redis_client, degrade_publisher)
    vol_expansion = VolExpansionEvaluator(settings, redis_client, alert_engine)
    leadership_rotation = LeadershipRotationEvaluator(settings, redis_client, alert_engine)
    breakout = BreakoutEvaluator(settings, redis_client, alert_engine)
    regime_classifier = RegimeClassifier(settings, pool, redis_client)
    regime_shift = RegimeShiftEvaluator(settings, redis_client, alert_engine)
    correlation_break = CorrelationBreakEvaluator(settings, redis_client, alert_engine)
    deribit = DeribitCollector(settings, pool, redis_client)
    coingecko = CoinGeckoCollector(settings, pool, redis_client)
    cryptopanic_news = CryptoppanicCollector(settings, pool, redis_client)
    coinglass_heatmap = CoinglassHeatmapCollector(settings, pool, redis_client)
    brief_scheduler = DailyBriefScheduler(settings, redis_client, pool, nc)
    news_classifier = NewsClassifier(settings, pool, redis_client)
    news_event = NewsEventEvaluator(settings, redis_client, alert_engine)
    event_analyzer = EventAnalyzer(settings, redis_client, pool, nc)
    crowded_leverage = CrowdedLeverageEvaluator(settings, redis_client, alert_engine)
    deleveraging_event = DeleveragingEvaluator(settings, redis_client, alert_engine, event_analyzer)
    alert_move_tracker = AlertMoveTracker(pool)

    # On-demand brief trigger via Core NATS (bot publishes briefs.request)
    async def _on_brief_request(msg: Any) -> None:
        asyncio.create_task(brief_scheduler.trigger())

    await nc.subscribe("briefs.request", cb=_on_brief_request)

    # Graceful shutdown on SIGTERM / SIGINT — propagate to all workers
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        log.info("processor.shutdown_requested")
        normalizer.request_shutdown()
        feature_engine.request_shutdown()
        cross_engine.request_shutdown()
        coinglass.request_shutdown()
        yahoo_finance.request_shutdown()
        derivatives_engine.request_shutdown()
        vol_expansion.request_shutdown()
        leadership_rotation.request_shutdown()
        breakout.request_shutdown()
        regime_classifier.request_shutdown()
        regime_shift.request_shutdown()
        correlation_break.request_shutdown()
        deribit.request_shutdown()
        coingecko.request_shutdown()
        cryptopanic_news.request_shutdown()
        coinglass_heatmap.request_shutdown()
        brief_scheduler.request_shutdown()
        news_classifier.request_shutdown()
        news_event.request_shutdown()
        crowded_leverage.request_shutdown()
        deleveraging_event.request_shutdown()
        alert_move_tracker.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    log.info("processor.running")
    await asyncio.gather(
        normalizer.run(),
        feature_engine.run(),
        cross_engine.run(),
        coinglass.run(),
        yahoo_finance.run(),
        derivatives_engine.run(),
        vol_expansion.run(),
        leadership_rotation.run(),
        breakout.run(),
        regime_classifier.run(),
        regime_shift.run(),
        correlation_break.run(),
        deribit.run(),
        coingecko.run(),
        cryptopanic_news.run(),
        coinglass_heatmap.run(),
        brief_scheduler.run(),
        news_classifier.run(),
        news_event.run(),
        crowded_leverage.run(),
        deleveraging_event.run(),
        alert_move_tracker.run(),
    )

    await nc.close()
    await redis_client.aclose()
    await pool.close()
    log.info("processor.stopped")


if __name__ == "__main__":
    asyncio.run(main())
