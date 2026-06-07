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
    price: float


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
