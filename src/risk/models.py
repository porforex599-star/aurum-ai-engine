from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo


class ProductCode(str, Enum):
    GOLD_AI = "gold_ai"
    MULTI_CFD_AI = "multi_cfd_ai"


@dataclass(frozen=True)
class RiskParams:
    target_win_usd: float
    target_loss_usd: float
    daily_loss_limit_usd: float
    daily_max_trades: int
    be_offset_usd: float
    trail_start_usd: float
    trail_step_usd: float

    @classmethod
    def default(cls) -> "RiskParams":
        return cls(
            target_win_usd=95.0,
            target_loss_usd=70.0,
            daily_loss_limit_usd=50.0,
            daily_max_trades=5,
            be_offset_usd=15.0,
            trail_start_usd=25.0,
            trail_step_usd=10.0,
        )


@dataclass
class TradeContext:
    trade_id: str
    symbol: str
    product: ProductCode
    side: str
    lot: float
    entry_price: float
    current_price: float
    current_pnl_usd: float
    opened_at: datetime
    current_sl: float | None = None


@dataclass
class DayState:
    date: date
    trades_opened: int = 0
    trades_closed: int = 0
    realized_pnl_usd: float = 0.0
    floating_pnl_usd: float = 0.0

    @property
    def total_pnl_usd(self) -> float:
        return self.realized_pnl_usd + self.floating_pnl_usd


@dataclass
class WeekState:
    cycle_id: str
    product: ProductCode
    net_pnl_usd: float = 0.0
    trades_in_cycle: int = 0
    state: str = "active"


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str | None = None
    code: str | None = None
    force_close: bool = False
