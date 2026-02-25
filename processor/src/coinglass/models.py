from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class FundingEntry(BaseModel):
    """One exchange's funding rate for a symbol, parsed from /funding response."""

    model_config = ConfigDict(extra="ignore")

    exchange: str = Field(alias="exchange")
    funding_rate: Optional[float] = Field(None, alias="fundingRate")


class OIEntry(BaseModel):
    """One exchange's open interest for a symbol, parsed from /open_interest response."""

    model_config = ConfigDict(extra="ignore")

    exchange: str = Field(alias="exchange")
    open_interest_usd: Optional[float] = Field(None, alias="openInterestUsd")


class LiqEntry(BaseModel):
    """One exchange's liquidation data for a symbol, parsed from /liquidation response."""

    model_config = ConfigDict(extra="ignore")

    exchange: str = Field(alias="exchange")
    liq_usd_1h: Optional[float] = Field(None, alias="liquidationUsd")


class LongShortEntry(BaseModel):
    """One exchange's long/short ratio for a symbol, from /long_short_ratio response."""

    model_config = ConfigDict(extra="ignore")

    exchange: str = Field(alias="exchange")
    long_account_ratio: Optional[float] = Field(None, alias="longRatio")
    short_account_ratio: Optional[float] = Field(None, alias="shortRatio")
