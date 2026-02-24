from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from regime.classifier import RegimeResult


async def insert_regime(
    pool: Any,
    time: datetime,
    result: RegimeResult,
    previous_regime: str | None,
    regime_duration_minutes: int | None,
) -> None:
    """
    Insert one row into regime_state.

    Skipped when result.regime is None (uncertain) — the DB CHECK constraint
    only allows the 5 named regimes; NULL is not permitted by the schema.
    """
    if result.regime is None:
        return

    await pool.execute(
        """
        INSERT INTO regime_state
          (time, regime, confidence, contributing_factors,
           previous_regime, regime_duration_minutes)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        time,
        result.regime,
        result.confidence,
        json.dumps(result.contributing_factors),
        previous_regime,
        regime_duration_minutes,
    )
