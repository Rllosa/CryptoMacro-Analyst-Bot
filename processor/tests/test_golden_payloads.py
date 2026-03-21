"""
SOLO-89: QA-2 — Golden-fixture snapshot tests for alert evaluators.

Each test fires a deterministic evaluate_and_fire() call, captures the NATS
payload, and compares it byte-for-byte against a committed JSON fixture.

If the payload shape changes intentionally, the developer re-runs:
    cd processor
    REGEN_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden_payloads.py -v
and commits the updated fixture files.

Non-deterministic fields (alert_id) are normalised to "<uuid>" before comparison.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from alerts.config import AlertParams
from alerts.engine import AlertEngine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"

_GOLDEN_VOL_EXPANSION         = _FIXTURES / "golden_vol_expansion.json"
_GOLDEN_LEADERSHIP_ROTATION   = _FIXTURES / "golden_leadership_rotation.json"
_GOLDEN_BREAKOUT               = _FIXTURES / "golden_breakout.json"

# ---------------------------------------------------------------------------
# Shared helpers  (mirror _make_engine from test_alert_engine.py)
# ---------------------------------------------------------------------------

_FIRE_TIME = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)


class _FakePersistence:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def record_met(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def record_not_met(self, key: str) -> None:
        self._counts[key] = 0

    async def get(self, key: str) -> int:
        return self._counts.get(key, 0)


def _make_engine(alert_type: str, cooldown_minutes: int) -> AlertEngine:
    params = AlertParams(
        cooldown_minutes={alert_type: cooldown_minutes},
        persistence_cycles={alert_type: 1},
    )
    engine = AlertEngine(
        pool=AsyncMock(),
        redis=AsyncMock(),
        nc=AsyncMock(),
        params=params,
    )
    engine._cooldown.is_active = AsyncMock(return_value=False)
    engine._cooldown.activate = AsyncMock()
    engine._persistence = _FakePersistence()
    return engine


def _capture(alert_type: str, cooldown_minutes: int, kwargs: dict) -> dict:
    """Fire evaluate_and_fire, return the captured NATS payload."""
    captured: list[dict] = []

    async def _capture_publish(nc, payload: dict) -> None:
        captured.append(payload)

    engine = _make_engine(alert_type, cooldown_minutes)
    with (
        patch("alerts.engine.insert_alert", AsyncMock()),
        patch("alerts.engine.publish_alert", _capture_publish),
    ):
        asyncio.run(engine.evaluate_and_fire(**kwargs))

    assert len(captured) == 1, "Expected exactly one NATS publish"
    return captured[0]


def _normalise(payload: dict) -> dict:
    """Replace non-deterministic alert_id with sentinel before comparison."""
    p = dict(payload)
    if "alert_id" in p:
        p["alert_id"] = "<uuid>"
    return p


def _load_or_regen(fixture_path: Path, payload: dict) -> dict:
    """
    Return fixture dict. If REGEN_GOLDEN=1 or file is missing, write the
    fixture first (used to bootstrap or intentionally update snapshots).
    """
    normalised = _normalise(payload)
    if os.environ.get("REGEN_GOLDEN") == "1" or not fixture_path.exists():
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(json.dumps(normalised, indent=2, sort_keys=True) + "\n")
    return json.loads(fixture_path.read_text())


# ---------------------------------------------------------------------------
# Golden fixture tests
# ---------------------------------------------------------------------------


def test_golden_vol_expansion() -> None:
    """VOL_EXPANSION NATS payload must match golden_vol_expansion.json exactly."""
    kwargs = dict(
        alert_type="VOL_EXPANSION",
        symbol="BTCUSDT",
        direction="up",
        conditions_met=True,
        severity="HIGH",
        trigger_values={"rv_1h_zscore": 2.5, "volume_zscore": 1.8, "direction": "up"},
        context={"regime": "RISK_ON_TREND", "confidence": 0.85},
        input_snapshot={"rv_1h": 0.0225, "volume_zscore": 1.8, "rsi_14": 68.0},
        fire_time=_FIRE_TIME,
    )
    payload = _capture("VOL_EXPANSION", cooldown_minutes=30, kwargs=kwargs)
    fixture = _load_or_regen(_GOLDEN_VOL_EXPANSION, payload)
    assert _normalise(payload) == fixture


def test_golden_leadership_rotation() -> None:
    """LEADERSHIP_ROTATION NATS payload must match golden_leadership_rotation.json exactly."""
    kwargs = dict(
        alert_type="LEADERSHIP_ROTATION",
        symbol=None,
        direction="ETH_over_BTC",
        conditions_met=True,
        severity="MEDIUM",
        trigger_values={"pair": "ETH/BTC", "rs_zscore": 2.1, "direction": "outperform"},
        context={"regime": "RISK_ON_TREND", "confidence": 0.80},
        input_snapshot={"eth_btc_rs": 0.05, "eth_btc_rs_zscore": 2.1},
        fire_time=_FIRE_TIME,
    )
    payload = _capture("LEADERSHIP_ROTATION", cooldown_minutes=120, kwargs=kwargs)
    fixture = _load_or_regen(_GOLDEN_LEADERSHIP_ROTATION, payload)
    assert _normalise(payload) == fixture


def test_golden_breakout() -> None:
    """BREAKOUT NATS payload must match golden_breakout.json exactly."""
    kwargs = dict(
        alert_type="BREAKOUT",
        symbol="BTCUSDT",
        direction="up",
        conditions_met=True,
        severity="HIGH",
        trigger_values={"direction": "up", "level": 95000.0, "volume_zscore": 2.2},
        context={"regime": "RISK_ON_TREND", "confidence": 0.90},
        input_snapshot={"close": 95100.0, "volume_zscore": 2.2, "breakout_4h_high": 1.0},
        fire_time=_FIRE_TIME,
    )
    payload = _capture("BREAKOUT", cooldown_minutes=60, kwargs=kwargs)
    fixture = _load_or_regen(_GOLDEN_BREAKOUT, payload)
    assert _normalise(payload) == fixture
