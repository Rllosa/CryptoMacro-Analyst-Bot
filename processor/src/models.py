from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, field_validator

# Symbols the normalizer accepts — matches asset scope rule 1.5
VALID_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"}


class CandleMessage(BaseModel):
    """
    Normalized candle message received from NATS market.candles.{symbol}.
    Schema defined in schema/contracts/nats_candle_message.json.
    """

    symbol: str
    exchange: str
    timeframe: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: Optional[int] = None

    @field_validator("time", mode="before")
    @classmethod
    def parse_time(cls, v: str | datetime) -> datetime:
        """Accept ISO 8601 strings (with or without Z) and datetime objects."""
        if isinstance(v, datetime):
            return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)
        # "2026-02-16T12:00:00Z" → replace Z with +00:00 for fromisoformat
        return datetime.fromisoformat(v.replace("Z", "+00:00"))

    @field_validator("open", "high", "low", "close")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"price must be positive, got {v}")
        return v

    def to_db_row(self) -> tuple:
        """Return a tuple matching market_candles INSERT column order."""
        return (
            self.time,
            self.symbol,
            self.timeframe,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.quote_volume,
            self.trades,  # maps to num_trades column
        )
