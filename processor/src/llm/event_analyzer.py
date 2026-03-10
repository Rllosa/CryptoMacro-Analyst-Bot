"""
LLM-4: Event-Triggered Analysis (SOLO-58)

Generates immediate Claude analysis when a high-severity alert fires (e.g.
DELEVERAGING_EVENT). Called as a background asyncio task from the triggering
alert evaluator — never blocks the alert path.

Pipeline per analysis:
  1. ContextBuilder assembles Redis + DB snapshot
  2. ClaudeClient generates JSON (summary, interpretation, watch_next)
  3. EventAnalyzer wraps result in F-7 envelope, validates against event_analysis.json
  4. Writes to analysis_reports (report_type='event_analysis')
  5. Publishes to NATS events.analysis → bot posts to Discord

Never raises — all failures are logged and the system continues.
LLM output never touches alerts/regime_state tables (Rule 1.2).

Fallback when Claude is unavailable:
  analysis is stored with fallback text; alert delivery is unaffected.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import jsonschema
import structlog

from llm.client import ClaudeClient, MODEL_EVENT
from llm.context import ContextBuilder
from llm.prompts import deleveraging_event as deleveraging_prompt

log = structlog.get_logger()

# F-7 schema — resolved relative to processor/src/llm/ → project root
_SCHEMA_PATH = Path(__file__).parents[3] / "schema" / "contracts" / "event_analysis.json"

# NATS subject for event analysis reports
_EVENT_SUBJECT = "events.analysis"
_EVENT_STREAM = "EVENT_ANALYSIS"

# Approximate cost per token for Sonnet 4.6
_COST_PER_INPUT_TOKEN = 3.0 / 1_000_000
_COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000

# Fallback text used when Claude is unavailable — satisfies schema minLength constraints
_FALLBACK_SUMMARY = "LLM unavailable — event analysis could not be generated at this time."
_FALLBACK_INTERPRETATION = (
    "Claude API was unreachable when this alert fired. "
    "The alert itself was delivered and stored in the alerts table. "
    "This record is a placeholder — review the trigger_alert.conditions for raw signal values."
)


async def setup_stream(nc: Any) -> None:
    """Create the EVENT_ANALYSIS JetStream stream if it does not already exist."""
    js = nc.jetstream()
    try:
        await js.add_stream(name=_EVENT_STREAM, subjects=[_EVENT_SUBJECT])
    except Exception:
        pass  # Stream already exists


def _compute_cost(in_tok: int, out_tok: int) -> float:
    return round(in_tok * _COST_PER_INPUT_TOKEN + out_tok * _COST_PER_OUTPUT_TOKEN, 6)


class EventAnalyzer:
    """
    Generates LLM-4 event analysis for high-severity alerts.

    Instantiated once in main.py and passed to alert evaluators (e.g.
    DeleveragingEvaluator). Each fire spawns an independent async task via
    EventAnalyzer.analyze() — multiple concurrent analyses are safe.
    """

    def __init__(self, settings: Any, redis: Any, pool: Any, nc: Any) -> None:
        self._settings = settings
        self._redis = redis
        self._pool = pool
        self._nc = nc

    async def analyze(
        self,
        alert_type: str,
        symbol: str | None,
        severity: str,
        fire_time: datetime,
        trigger_values: dict[str, Any],
    ) -> None:
        """
        Full event analysis pipeline. Never raises.

        Spawned as a background task by the triggering evaluator — does not
        block the alert delivery path.
        """
        t0 = time.monotonic()
        log.info(
            "event_analyzer.starting",
            alert_type=alert_type,
            symbol=symbol,
        )

        try:
            # 1. Build market context
            context = await ContextBuilder(self._redis, self._pool).build()

            # 2. Try Claude; fall back to placeholder on any failure
            llm_unavailable = False
            in_tok = out_tok = 0
            analysis: dict[str, Any] = {}

            try:
                prompt = deleveraging_prompt.build(
                    alert_type=alert_type,
                    symbol=symbol,
                    trigger_values=trigger_values,
                    context=context,
                )
                client = ClaudeClient(
                    api_key=self._settings.anthropic_api_key,
                    model=MODEL_EVENT,
                )
                text, in_tok, out_tok = await client.complete_with_usage(
                    prompt,
                    model=MODEL_EVENT,
                    system=deleveraging_prompt.SYSTEM,
                    max_tokens=self._settings.__dict__.get("event_analysis_max_tokens", 512),
                )
                claude_json = json.loads(text)
                analysis = {
                    "summary": claude_json.get("summary", ""),
                    "interpretation": claude_json.get("interpretation", ""),
                    "watch_next": claude_json.get("watch_next", ["N/A"]),
                    "similar_historical_events": claude_json.get(
                        "similar_historical_events", []
                    ),
                }
            except Exception as exc:
                log.warning("event_analyzer.llm_failed", error=str(exc))
                llm_unavailable = True
                analysis = {
                    "summary": _FALLBACK_SUMMARY,
                    "interpretation": _FALLBACK_INTERPRETATION,
                    "watch_next": ["Review raw trigger values in trigger_alert.conditions"],
                }

            # 3. Build F-7 envelope
            now = datetime.now(tz=timezone.utc)
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            regime_ctx = context.get("regime") or {}
            recent_alerts = context.get("recent_alerts") or []

            envelope: dict[str, Any] = {
                "report_id": str(uuid4()),
                "report_type": "event_analysis",
                "generated_at": now.isoformat(),
                "trigger_alert": {
                    "alert_id": str(uuid4()),  # synthetic reference — alert_id not returned by engine
                    "alert_type": alert_type,
                    "symbol": symbol,
                    "severity": severity,
                    "time": fire_time.isoformat(),
                    "conditions": trigger_values,
                },
                "context": {
                    "regime": {
                        "current": regime_ctx.get("current"),
                        "confidence": float(regime_ctx.get("confidence") or 0.0),
                    },
                    "recent_alerts": [
                        {
                            "alert_type": a.get("type", ""),
                            "symbol": a.get("symbol"),
                            "time": a.get("fired_at", ""),
                        }
                        for a in recent_alerts[:10]
                    ],
                    "features": (context.get("features") or {}).get(
                        (symbol or "").replace("USDT", ""), {}
                    ),
                },
                "analysis": analysis,
                "llm_metadata": {
                    "model": MODEL_EVENT,
                    "tokens_used": in_tok + out_tok,
                    "cost_usd": _compute_cost(in_tok, out_tok),
                    "generation_time_ms": elapsed_ms,
                },
            }

            # 4. Validate against F-7 schema
            self._validate(envelope)

            # 5. Persist to analysis_reports
            await self._write_db(envelope, llm_unavailable)

            # 6. Publish to NATS → bot posts to Discord
            await self._publish(envelope)

            log.info(
                "event_analyzer.published",
                alert_type=alert_type,
                symbol=symbol,
                tokens_used=in_tok + out_tok,
                cost_usd=_compute_cost(in_tok, out_tok),
                llm_unavailable=llm_unavailable,
                elapsed_ms=elapsed_ms,
            )

        except Exception as exc:
            log.warning("event_analyzer.pipeline_failed", error=str(exc))

    def _validate(self, envelope: dict[str, Any]) -> None:
        """Validate envelope against event_analysis.json schema."""
        with _SCHEMA_PATH.open() as f:
            schema = json.load(f)
        jsonschema.validate(instance=envelope, schema=schema)

    async def _write_db(self, envelope: dict[str, Any], llm_unavailable: bool) -> None:
        """Persist event analysis to analysis_reports."""
        alert_type = envelope["trigger_alert"]["alert_type"]
        symbol = envelope["trigger_alert"]["symbol"] or "MARKET"
        generated_at = envelope["generated_at"][:16].replace("T", " ")
        title = f"Event Analysis — {alert_type} {symbol} @ {generated_at} UTC"
        if llm_unavailable:
            title += " [LLM unavailable]"

        query = """
            INSERT INTO analysis_reports
              (report_type, title, content, regime_context, model_used, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        params = (
            "event_analysis",
            title,
            json.dumps(envelope),
            json.dumps(envelope["context"].get("regime")),
            envelope["llm_metadata"]["model"],
            json.dumps({
                "tokens_used": envelope["llm_metadata"]["tokens_used"],
                "cost_usd": envelope["llm_metadata"]["cost_usd"],
                "generation_time_ms": envelope["llm_metadata"]["generation_time_ms"],
                "llm_unavailable": llm_unavailable,
            }),
        )
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)

    async def _publish(self, envelope: dict[str, Any]) -> None:
        """Publish event analysis to NATS JetStream events.analysis."""
        try:
            js = self._nc.jetstream()
            await js.publish(_EVENT_SUBJECT, json.dumps(envelope).encode())
        except Exception as exc:
            log.warning("event_analyzer.nats_publish_failed", error=str(exc))
