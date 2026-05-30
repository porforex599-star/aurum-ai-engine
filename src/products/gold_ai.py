from __future__ import annotations

from datetime import datetime, time
from typing import Callable
from zoneinfo import ZoneInfo

from src.products.models import (
    CloseIntent,
    IntentKind,
    OpenPosition,
    ProductConfig,
    TradeIntent,
)
from src.risk.filters import FilterResult, run_all_filters
from src.risk.models import ProductCode
from src.risk.per_day import DayTracker
from src.risk.per_week import WeekTracker
from src.strategy.ares import AresStrategy
from src.strategy.models import MarketSnapshot


class GoldAIProduct:
    def __init__(
        self,
        customer_id: str,
        week_cycle_id: str,
        filters: list[Callable[[], FilterResult]] | None = None,
    ) -> None:
        self.customer_id = customer_id
        self.config = ProductConfig.gold_ai()
        self.day_tracker = DayTracker(self.config.risk_params)
        self.week_tracker = WeekTracker(
            week_cycle_id, ProductCode.GOLD_AI, self.config.risk_params
        )
        self.strategy = AresStrategy()
        self.filters = filters or []

    def is_within_trading_hours(self, now: datetime) -> bool:
        th = self.config.trading_hours
        local = now.astimezone(ZoneInfo(th.tz))
        day_code = local.strftime("%a").upper()[:3]
        if day_code not in th.days:
            return False
        open_h, open_m = map(int, th.open_time.split(":"))
        close_h, close_m = map(int, th.close_time.split(":"))
        current_t = local.time()
        return time(open_h, open_m) <= current_t < time(close_h, close_m)

    def is_friday_close_time(self, now: datetime) -> bool:
        th = self.config.trading_hours
        if not th.enable_friday_close:
            return False
        local = now.astimezone(ZoneInfo(th.tz))
        if local.strftime("%a").upper()[:3] != "FRI":
            return False
        close_h, close_m = map(int, th.close_time.split(":"))
        return local.time() >= time(close_h, close_m)

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        open_positions: list[OpenPosition],
        now: datetime,
    ) -> TradeIntent | list[CloseIntent] | None:
        self.day_tracker.maybe_reset(now)

        week_check = self.week_tracker.check_target()
        if not week_check.allowed and week_check.force_close:
            return [
                CloseIntent(
                    IntentKind.CLOSE,
                    p.position_id,
                    week_check.reason or "",
                    week_check.code or "week_target",
                )
                for p in open_positions
                if p.symbol in self.config.symbols
            ]

        if self.is_friday_close_time(now):
            own = [p for p in open_positions if p.symbol in self.config.symbols]
            if own:
                return [
                    CloseIntent(
                        IntentKind.CLOSE,
                        p.position_id,
                        "Friday 17:00 BKK close",
                        "friday_close",
                    )
                    for p in own
                ]

        if not self.is_within_trading_hours(now):
            return None

        own_positions = [p for p in open_positions if p.symbol in self.config.symbols]
        if len(own_positions) >= self.config.max_positions:
            return None

        day_check = self.day_tracker.can_open_new_trade()
        if not day_check.allowed:
            return None

        if self.filters:
            filter_result = run_all_filters(self.filters)
            if not filter_result.allowed:
                return None

        signal = self.strategy.evaluate(snapshot)
        if signal is None:
            return None

        return TradeIntent(
            kind=IntentKind.OPEN,
            symbol=self.config.symbols[0],
            side=signal.side,
            lot=self.config.lot,
            entry_price=None,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price,
            reason=signal.reason,
            setup=signal.setup,
            confidence=signal.confidence,
        )

    def record_trade_opened(self) -> None:
        self.day_tracker.record_trade_open()

    def record_trade_closed(self, pnl_usd: float) -> None:
        self.day_tracker.record_trade_close(pnl_usd)
        self.week_tracker.record_trade_closed(pnl_usd)
