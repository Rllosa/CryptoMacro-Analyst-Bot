"""
LLM-1: Context Builder (SOLO-55)

Assembles all system state into a structured dict ready for Claude prompt injection.
Reads latest snapshots from Redis and recent history from TimescaleDB.

No run loop — this is a pure module called by LLM-3 (daily brief scheduler).
Never raises — missing sections are omitted gracefully via sections_available flags.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger()

# Asset symbol mapping: display name → Redis suffix
_ASSETS: dict[str, str] = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "HYPE": "hypeusdt",
}

# Redis key templates (module-level — not rebuilt per call)
_KEY_FEATURES = "features:latest:{sym}"
_KEY_CROSS = "cross_features:latest"
_KEY_REGIME = "regime:latest"
_KEY_DERIV = "derivatives:latest:{sym}"

_DEFAULT_MAX_ALERTS = 20


class ContextBuilder:
    """
    Assembles all system state into a structured context dict for Claude.

    Each data section is independently fault-tolerant: a Redis timeout, missing key,
    or DB error causes that section to be None in the output and False in
    sections_available — the rest of the context is unaffected.
    """

    def __init__(self, redis: Any, pool: Any, max_alerts: int = _DEFAULT_MAX_ALERTS) -> None:
        self._redis = redis
        self._pool = pool
        self._max_alerts = max_alerts

    async def build(self) -> dict[str, Any]:
        """
        Build and return the full context dict.

        Always returns a dict. Never raises.
        sections_available maps each section name to True/False.
        """
        generated_at = datetime.now(tz=timezone.utc).isoformat()

        sections_available: dict[str, bool] = {
            "features": False,
            "cross_features": False,
            "regime": False,
            "derivatives": False,
            "recent_alerts": False,
        }

        # Fetch all Redis keys concurrently
        feature_keys = [_KEY_FEATURES.format(sym=sym) for sym in _ASSETS.values()]
        deriv_keys = [_KEY_DERIV.format(sym=sym) for sym in _ASSETS.values()]
        all_redis_keys = feature_keys + [_KEY_CROSS, _KEY_REGIME] + deriv_keys

        raw_values = await asyncio.gather(
            *[self._redis.get(k) for k in all_redis_keys],
            return_exceptions=True,
        )

        # Unpack in insertion order
        n_assets = len(_ASSETS)
        raw_features = raw_values[:n_assets]                              # 4 feature blobs
        raw_cross = raw_values[n_assets]                                  # 1 cross blob
        raw_regime = raw_values[n_assets + 1]                             # 1 regime blob
        raw_derivs = raw_values[n_assets + 2 : n_assets + 2 + n_assets]  # 4 deriv blobs

        # --- Features section ---
        features: dict[str, Any] = {}
        for (asset, sym), raw in zip(_ASSETS.items(), raw_features):
            if isinstance(raw, Exception) or raw is None:
                if isinstance(raw, Exception):
                    log.warning("context_builder.feature_read_error", asset=asset, error=str(raw))
                continue
            try:
                features[asset] = json.loads(raw)["features"]
            except Exception as exc:
                log.warning("context_builder.feature_parse_error", asset=asset, error=str(exc))
        if features:
            sections_available["features"] = True

        # --- Cross-features section ---
        cross_features: dict[str, Any] | None = None
        if not isinstance(raw_cross, Exception) and raw_cross is not None:
            try:
                cross_features = json.loads(raw_cross)["features"]
                sections_available["cross_features"] = True
            except Exception as exc:
                log.warning("context_builder.cross_features_parse_error", error=str(exc))

        # --- Regime section ---
        regime: dict[str, Any] | None = None
        if not isinstance(raw_regime, Exception) and raw_regime is not None:
            try:
                regime_raw = json.loads(raw_regime)
                regime = {
                    "current": regime_raw.get("regime"),
                    "confidence": regime_raw.get("confidence"),
                    "as_of": regime_raw.get("time"),
                    "recent_transitions": await self._fetch_regime_transitions(),
                }
                sections_available["regime"] = True
            except Exception as exc:
                log.warning("context_builder.regime_parse_error", error=str(exc))

        # --- Derivatives section ---
        derivatives: dict[str, Any] = {}
        for (asset, sym), raw in zip(_ASSETS.items(), raw_derivs):
            if isinstance(raw, Exception) or raw is None:
                continue
            try:
                derivatives[asset] = json.loads(raw)["features"]
            except Exception as exc:
                log.warning("context_builder.deriv_parse_error", asset=asset, error=str(exc))
        if derivatives:
            sections_available["derivatives"] = True

        # --- Recent alerts section ---
        recent_alerts: list[dict[str, Any]] = []
        try:
            recent_alerts = await self._fetch_recent_alerts()
            sections_available["recent_alerts"] = True
        except Exception as exc:
            log.warning("context_builder.alerts_fetch_error", error=str(exc))

        return {
            "generated_at": generated_at,
            "regime": regime,
            "features": features if features else None,
            "cross_features": cross_features,
            "derivatives": derivatives if derivatives else None,
            "recent_alerts": recent_alerts,
            "sections_available": sections_available,
        }

    async def _fetch_recent_alerts(self) -> list[dict[str, Any]]:
        """Fetch last 6h of alerts from DB, most recent first."""
        query = """
            SELECT time, alert_type, severity, symbol, title
            FROM alerts
            WHERE time >= NOW() - INTERVAL '6 hours'
            ORDER BY time DESC
            LIMIT %s
        """
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (self._max_alerts,))
                rows = await cur.fetchall()

        return [
            {
                "type": row[1],
                "severity": row[2],
                "symbol": row[3],
                "title": row[4],
                "fired_at": row[0].isoformat() if row[0] else None,
            }
            for row in rows
        ]

    async def _fetch_regime_transitions(self) -> list[dict[str, Any]]:
        """Fetch last 24h of regime transitions from DB."""
        query = """
            SELECT time, regime, confidence, previous_regime
            FROM regime_state
            WHERE time >= NOW() - INTERVAL '24 hours'
            ORDER BY time DESC
            LIMIT 10
        """
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query)
                    rows = await cur.fetchall()

            return [
                {
                    "at": row[0].isoformat() if row[0] else None,
                    "to": row[1],
                    "confidence": float(row[2]) if row[2] is not None else None,
                    "from": row[3],
                }
                for row in rows
            ]
        except Exception as exc:
            log.warning("context_builder.regime_transitions_error", error=str(exc))
            return []
