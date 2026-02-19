from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from alerts.models import AlertRecord

# INSERT components hoisted at module level — never rebuilt per call
_ALERT_COLS = (
    "(id, time, alert_type, severity, symbol, title, description, "
    "trigger_conditions, context, regime_at_trigger)"
)
_ALERT_PH = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"

_INSERT_SQL = (
    f"INSERT INTO alerts {_ALERT_COLS} VALUES {_ALERT_PH} "
    "ON CONFLICT DO NOTHING"
)


async def insert_alert(pool: AsyncConnectionPool, record: AlertRecord) -> None:
    """
    Persist a single alert row to the `alerts` hypertable.

    One INSERT per fire — alerts are rare events (target < 10/day),
    so no batching is needed or beneficial here.
    JSONB columns use psycopg Jsonb() to ensure correct wire encoding.
    """
    params: tuple[Any, ...] = (
        record.id,
        record.time,
        record.alert_type,
        record.severity,
        record.symbol,
        record.title,
        record.description,
        Jsonb(record.trigger_conditions),
        Jsonb(record.context),
        record.regime_at_trigger,
    )
    async with pool.connection() as conn:
        await conn.execute(_INSERT_SQL, params)
