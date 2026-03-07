"""
LLM-3: Daily Brief Scheduler (SOLO-57)

Generates Claude-powered market briefs at 09:00 and 19:00 Dubai time (05:00 and
15:00 UTC). Also handles on-demand trigger from the Discord /brief command via NATS.

Pipeline per generation:
  1. ContextBuilder assembles Redis + DB snapshot
  2. ClaudeClient generates JSON (regime_analysis, key_insights, watch_list)
  3. Scheduler wraps result in F-7 envelope, validates against JSON schema
  4. Writes to analysis_reports DB table
  5. Publishes to NATS DAILY_BRIEF stream → bot posts to Discord

Never raises — all failures are logged and the system continues.
LLM output never touches alerts/regime_state tables (Rule 1.2).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import jsonschema
import structlog

from llm.client import ClaudeClient
from llm.context import ContextBuilder
from llm import publisher
from llm.prompts import daily_brief as daily_brief_prompt

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Module-level constants (not rebuilt per call)
# ---------------------------------------------------------------------------

# Dubai is UTC+4; 09:00 Dubai = 05:00 UTC, 19:00 Dubai = 15:00 UTC
_BRIEF_HOURS_UTC: frozenset[int] = frozenset({5, 15})

# F-7 schema path: from processor/src/llm/ → project root → schema/contracts/
_SCHEMA_PATH = Path(__file__).parents[3] / "schema" / "contracts" / "daily_brief.json"

# Approximate cost per token for Sonnet 4.6 (USD per token)
# Input: $3/MTok, Output: $15/MTok
_COST_PER_INPUT_TOKEN = 3.0 / 1_000_000
_COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000

# Volatility label thresholds (rv_4h_zscore)
_VOL_LOW_THRESH = 0.5
_VOL_HIGH_THRESH = 1.5

# Valid F-7 regime values (INDETERMINATE is not in F-7 schema)
_VALID_REGIMES = frozenset(
    {"RISK_ON_TREND", "RISK_OFF_STRESS", "CHOP_RANGE", "VOL_EXPANSION", "DELEVERAGING"}
)


def _vol_label(rv_z: float) -> str:
    if rv_z < _VOL_LOW_THRESH:
        return "low"
    if rv_z < _VOL_HIGH_THRESH:
        return "medium"
    return "high"


def _compute_cost(in_tok: int, out_tok: int) -> float:
    return round(in_tok * _COST_PER_INPUT_TOKEN + out_tok * _COST_PER_OUTPUT_TOKEN, 6)


class DailyBriefScheduler:
    """
    Runs the twice-daily brief generation loop and handles on-demand triggers.

    Scheduling: wakes once per minute, checks if current UTC hour is a brief hour
    (05 or 15) and minute is 0. Tracks last-fired hour to prevent double-fire.

    On-demand: call trigger() from a NATS callback; it fires generate_and_publish
    as a background task without blocking.
    """

    def __init__(self, settings: Any, redis: Any, pool: Any, nc: Any) -> None:
        self._settings = settings
        self._redis = redis
        self._pool = pool
        self._nc = nc
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def run(self) -> None:
        """Scheduling loop — runs for the lifetime of the processor."""
        log.info("daily_brief_scheduler.starting", brief_hours_utc=sorted(_BRIEF_HOURS_UTC))
        last_fired_hour = -1

        while not self._shutdown.is_set():
            now = datetime.now(tz=timezone.utc)

            if (
                now.hour in _BRIEF_HOURS_UTC
                and now.minute == 0
                and now.hour != last_fired_hour
            ):
                last_fired_hour = now.hour
                log.info("daily_brief_scheduler.scheduled_trigger", utc_hour=now.hour)
                asyncio.create_task(self._generate_and_publish())

            # Sleep to the next whole minute boundary (prevents drift)
            sleep_secs = max(1.0, 60 - now.second)
            await asyncio.sleep(sleep_secs)

    async def trigger(self) -> None:
        """On-demand trigger — called from NATS briefs.request subscriber."""
        log.info("daily_brief_scheduler.on_demand_trigger")
        asyncio.create_task(self._generate_and_publish())

    async def _generate_and_publish(self) -> None:
        """Full brief generation pipeline. Never raises."""
        t0 = time.monotonic()
        log.info("daily_brief_scheduler.generating")

        try:
            # 1. Assemble context
            context = await ContextBuilder(self._redis, self._pool).build()

            # 2. Build prompt and call Claude
            prompt = daily_brief_prompt.build(context)
            client = ClaudeClient(api_key=self._settings.anthropic_api_key,
                                  model=self._settings.claude_model_daily)
            text, in_tok, out_tok = await client.complete_with_usage(
                prompt,
                model=self._settings.claude_model_daily,
                system=daily_brief_prompt.SYSTEM,
                max_tokens=1024,
            )

            # 3. Parse Claude's JSON response
            claude_json = json.loads(text)
            regime_analysis: str = claude_json.get("regime_analysis", "")
            key_insights: list[str] = claude_json.get("key_insights", [])
            watch_list: list[str] = claude_json.get("watch_list", [])

            # 4. Build F-7 envelope
            now = datetime.now(tz=timezone.utc)
            envelope = self._build_envelope(
                context=context,
                regime_analysis=regime_analysis,
                key_insights=key_insights,
                watch_list=watch_list,
                model=self._settings.claude_model_daily,
                in_tok=in_tok,
                out_tok=out_tok,
                t0=t0,
                now=now,
            )

            # 5. Validate against F-7 schema
            self._validate(envelope)

            # 6. Persist to analysis_reports
            await self._write_db(envelope, context)

            # 7. Publish to NATS → bot posts to Discord
            await publisher.publish_report(self._nc, envelope)

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log.info(
                "daily_brief_scheduler.published",
                tokens_used=in_tok + out_tok,
                cost_usd=_compute_cost(in_tok, out_tok),
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            log.warning("daily_brief_scheduler.generation_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_envelope(
        self,
        *,
        context: dict[str, Any],
        regime_analysis: str,
        key_insights: list[str],
        watch_list: list[str],
        model: str,
        in_tok: int,
        out_tok: int,
        t0: float,
        now: datetime,
    ) -> dict[str, Any]:
        regime = context.get("regime") or {}
        features = context.get("features") or {}
        cross = context.get("cross_features") or {}
        alerts = context.get("recent_alerts") or []

        # Regime — map INDETERMINATE / None to CHOP_RANGE for F-7 compatibility
        current_regime = regime.get("current") or "CHOP_RANGE"
        if current_regime not in _VALID_REGIMES:
            current_regime = "CHOP_RANGE"

        transitions = [
            {
                "time": t.get("at", ""),
                "from_regime": t.get("from", ""),
                "to_regime": t.get("to", ""),
            }
            for t in (regime.get("recent_transitions") or [])
        ]

        # Alert summary derived from context
        by_type: dict[str, int] = dict(Counter(a.get("type", "") for a in alerts))
        by_severity: dict[str, int] = {
            "HIGH": sum(1 for a in alerts if a.get("severity") == "HIGH"),
            "MEDIUM": sum(1 for a in alerts if a.get("severity") == "MEDIUM"),
            "LOW": sum(1 for a in alerts if a.get("severity") == "LOW"),
        }
        notable_alerts = [
            {
                "alert_id": str(uuid4()),  # synthetic — context doesn't include DB alert IDs
                "alert_type": a.get("type", ""),
                "symbol": a.get("symbol"),
                "severity": a.get("severity", ""),
                "summary": a.get("title", ""),
            }
            for a in alerts[:5]
        ]

        # Market summary — per-asset from features
        assets: dict[str, Any] = {}
        for asset, feat in features.items():
            assets[asset] = {
                "price_change_pct": feat.get("r_1h", 0.0),
                "volume_change_pct": feat.get("volume_zscore", 0.0),
                "volatility_regime": _vol_label(feat.get("rv_4h_zscore", 0.0)),
            }

        return {
            "report_id": str(uuid4()),
            "report_type": "daily_brief",
            "generated_at": now.isoformat(),
            "time_range": {
                "start": (now - timedelta(hours=12)).isoformat(),
                "end": now.isoformat(),
            },
            "regime_summary": {
                "current_regime": current_regime,
                "confidence": float(regime.get("confidence") or 0.0),
                "transitions": transitions,
                "analysis": regime_analysis,
            },
            "alert_summary": {
                "total_alerts": len(alerts),
                "by_type": by_type,
                "by_severity": by_severity,
                "notable_alerts": notable_alerts,
            },
            "market_summary": {
                "assets": assets,
                "correlations": {
                    "btc_spx": None,
                    "btc_dxy": cross.get("dxy_momentum"),
                },
            },
            "key_insights": key_insights[:5],
            "watch_list": watch_list[:5],
            "llm_metadata": {
                "model": model,
                "tokens_used": in_tok + out_tok,
                "cost_usd": _compute_cost(in_tok, out_tok),
                "generation_time_ms": int((time.monotonic() - t0) * 1000),
            },
        }

    def _validate(self, envelope: dict[str, Any]) -> None:
        """Validate envelope against F-7 JSON schema. Raises jsonschema.ValidationError on failure."""
        with _SCHEMA_PATH.open() as f:
            schema = json.load(f)
        jsonschema.validate(instance=envelope, schema=schema)

    async def _write_db(self, envelope: dict[str, Any], context: dict[str, Any]) -> None:
        """Persist the brief to analysis_reports (single parameterized INSERT)."""
        now_str = envelope["generated_at"][:16].replace("T", " ")
        session = "AM" if datetime.now(tz=timezone.utc).hour < 12 else "PM"
        title = f"Daily Brief — {now_str} UTC ({session})"

        query = """
            INSERT INTO analysis_reports
              (report_type, title, content, regime_context, model_used, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        params = (
            "daily_brief",
            title,
            json.dumps(envelope),
            json.dumps(context.get("regime")),
            envelope["llm_metadata"]["model"],
            json.dumps({
                "tokens_used": envelope["llm_metadata"]["tokens_used"],
                "cost_usd": envelope["llm_metadata"]["cost_usd"],
                "generation_time_ms": envelope["llm_metadata"]["generation_time_ms"],
            }),
        )

        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
