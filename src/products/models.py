from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.risk.models import ProductCode, RiskParams
from src.strategy.models import SetupName, SignalSide


class IntentKind(Enum):
    OPEN = "open"
    CLOSE = "close"
    MODIFY_SL = "modify_sl"


@dataclass(frozen=True)
class TradeIntent:
    kind: IntentKind
    symbol: str
    side: SignalSide
    lot: float
    entry_price: float | None
    sl_price: float
    tp_price: float | None
    reason: str
    setup: SetupName | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class CloseIntent:
    kind: IntentKind
    position_id: str
    reason: str
    code: str


@dataclass(frozen=True)
class ModifySLIntent:
    kind: IntentKind
    position_id: str
    new_sl_price: float
    reason: str


@dataclass(frozen=True)
class OpenPosition:
    position_id: str
    symbol: str
    side: SignalSide
    lot: float
    entry_price: float
    current_price: float
    current_pnl_usd: float
    current_sl: float | None
    opened_at: datetime


@dataclass(frozen=True)
class TradingHours:
    days: tuple[str, ...]
    open_time: str
    close_time: str
    tz: str
    enable_friday_close: bool = False


@dataclass(frozen=True)
class ProductConfig:
    product: ProductCode
    symbols: tuple[str, ...]
    lot: float
    max_positions: int
    trading_hours: TradingHours
    risk_params: RiskParams

    @classmethod
    def gold_ai(cls) -> "ProductConfig":
        return cls(
            product=ProductCode.GOLD_AI,
            symbols=("XAUUSD",),
            lot=0.03,
            max_positions=1,
            trading_hours=TradingHours(
                days=("MON", "TUE", "WED", "THU", "FRI"),
                open_time="06:00",
                close_time="17:00",
                tz="Asia/Bangkok",
                enable_friday_close=True,
            ),
            risk_params=RiskParams.default(),
        )

    @classmethod
    def multi_cfd_ai(cls) -> "ProductConfig":
        return cls(
            product=ProductCode.MULTI_CFD_AI,
            symbols=("EURUSD", "GBPUSD", "USDJPY", "US500", "NAS100", "GER40"),
            lot=0.02,
            max_positions=3,
            trading_hours=TradingHours(
                days=("MON", "TUE", "WED", "THU", "FRI"),
                open_time="00:00",
                close_time="23:59",
                tz="Asia/Bangkok",
                enable_friday_close=False,
            ),
            risk_params=RiskParams.default(),
        )
