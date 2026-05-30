from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Timeframe(Enum):
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"


class SetupName(Enum):
    LIQUIDITY_SWEEP = "liquidity_sweep"
    ORDER_BLOCK = "order_block"
    MEAN_REVERSION = "mean_reversion"
    TREND_CONTINUATION = "trend_continuation"


class SignalSide(Enum):
    BUY = "buy"
    SELL = "sell"


class TrendBias(Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    bars_m15: list[Bar]
    bars_h1: list[Bar]
    bars_h4: list[Bar]
    current_spread_points: float = 0.0


@dataclass(frozen=True)
class Signal:
    setup: SetupName
    side: SignalSide
    entry_price: float
    sl_price: float
    tp_price: float | None
    confidence: float
    reason: str
    timestamp: datetime
