"""Schemas for the Aurum Sniper alert webhook.

Pine Script alerts arrive as JSON. Pine sometimes emits its own vocabulary
(buy/sell/long/short); we normalize that to the canonical bias values
(bullish/bearish) *before* anything is persisted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Bias = Literal["bullish", "bearish"]
RiskLevel = Literal["low", "medium", "high"]

# Pine Script → canonical bias vocabulary.
_BIAS_ALIASES: dict[str, Bias] = {
    "bullish": "bullish",
    "buy": "bullish",
    "long": "bullish",
    "bull": "bullish",
    "up": "bullish",
    "bearish": "bearish",
    "sell": "bearish",
    "short": "bearish",
    "bear": "bearish",
    "down": "bearish",
}


class TargetZone(BaseModel):
    id: str
    # Pine V.2 labels its targets "TP1", "TP2", … — default keeps Phase 3
    # callers (which only sent id/price) working unchanged.
    label: str = "TP"
    price: float


class PatternMarker(BaseModel):
    """A Pine V.3 chart marker for a detected candlestick pattern."""

    time: int  # UNIX seconds
    kind: Literal["3ls_bull", "3ls_bear", "engulf_bull", "engulf_bear"]
    price: float


class SdZone(BaseModel):
    """A supply/demand zone emitted by Pine V.3, scoped to a timeframe."""

    tf: str  # "2H", "30M", etc.
    type: Literal["supply", "demand"]
    high: float
    low: float
    mitigated: bool = False


class Candle(BaseModel):
    """A single OHLC candle (Pine V.3 chart context)."""

    time: int  # UNIX seconds
    open: float
    high: float
    low: float
    close: float


class SniperAlertPayload(BaseModel):
    """Validated + vocab-normalized inbound alert."""

    symbol: str = Field(..., min_length=1)
    timeframe: str = Field(..., min_length=1)
    bias: Bias
    key_level: float
    # Optional for backward compatibility with Phase 3 callers that predate
    # these fields. Dropped from the persisted row when None (see to_post_row).
    invalidation_price: float | None = None
    rr_ratio: float | None = None
    target_zones: list[TargetZone] = Field(default_factory=list)
    risk_level: RiskLevel
    confidence: int = Field(..., ge=0, le=100)
    note: str | None = None
    timestamp_utc: datetime | None = None
    # Phase 4 (Pine V.3) chart-context fields. The list fields default to []
    # (matching the `analysis_posts` NOT NULL DEFAULT '[]' columns); `candles`
    # is nullable and dropped from the row when omitted (see to_post_row).
    pattern_markers: list[PatternMarker] = Field(default_factory=list)
    sd_zones: list[SdZone] = Field(default_factory=list)
    candles: list[Candle] | None = None

    @field_validator("bias", mode="before")
    @classmethod
    def _normalize_bias(cls, value: object) -> object:
        if isinstance(value, str):
            return _BIAS_ALIASES.get(value.strip().lower(), value)
        return value

    @field_validator("risk_level", mode="before")
    @classmethod
    def _normalize_risk(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    def to_post_row(self) -> dict:
        """Serialize to a row ready for `analysis_posts` insertion (JSON-safe).

        `None` values are dropped so DB column defaults (e.g. `timestamp_utc`)
        still apply when the alert omits those optional fields.
        """
        return self.model_dump(mode="json", exclude_none=True)


class SniperAlertResponse(BaseModel):
    post_id: str
    broadcast: bool = True
