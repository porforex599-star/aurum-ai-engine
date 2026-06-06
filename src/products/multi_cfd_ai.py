from __future__ import annotations

from dataclasses import replace
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


class MultiCfdAIProduct:
    def __init__(
        self,
        customer_id: str,
        week_cycle_id: str,
        filters_by_symbol: dict[str, list[Callable[[], FilterResult]]] | None = None,
        symbols: tuple[str, ...] | None = None,
    ) -> None:
        self.customer_id = customer_id
        self.config = ProductConfig.multi_cfd_ai()
        if symbols is not None:
            self.config = replace(self.config, symbols=tuple(symbols))
        self.day_tracker = DayTracker(self.config.risk_params)
        self.week_tracker = WeekTracker(
            week_cycle_id, ProductCode.MULTI_CFD_AI, self.config.risk_params
        )
        self.strategy = AresStrategy()
        self.filters_by_symbol = filters_by_symbol or {}

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

    def evaluate(
        self,
        snapshots: dict[str, MarketSnapshot],
        open_positions: list[OpenPosition],
        now: datetime,
    ) -> list[TradeIntent] | list[CloseIntent]:
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

        if not self.is_within_trading_hours(now):
            return []
        if not self.day_tracker.can_open_new_trade().allowed:
            return []

        own_positions = [p for p in open_positions if p.symbol in self.config.symbols]
        available_slots = self.config.max_positions - len(own_positions)
        if available_slots <= 0:
            return []

        open_symbols = {p.symbol for p in own_positions}
        candidates: list[tuple[float, TradeIntent]] = []
        for symbol, snap in snapshots.items():
            if symbol not in self.config.symbols:
                continue
            if "XAU" in symbol or "XAG" in symbol:
                continue
            if symbol in open_symbols:
                continue
            sym_filters = self.filters_by_symbol.get(symbol, [])
            if sym_filters:
                fr = run_all_filters(sym_filters)
                if not fr.allowed:
                    continue
            sig = self.strategy.evaluate(snap)
            if sig is None:
                continue
            intent = TradeIntent(
                kind=IntentKind.OPEN,
                symbol=symbol,
                side=sig.side,
                lot=self.config.lot,
                entry_price=None,
                sl_price=sig.sl_price,
                tp_price=sig.tp_price,
                reason=sig.reason,
                setup=sig.setup,
                confidence=sig.confidence,
            )
            candidates.append((sig.confidence, intent))

        candidates.sort(key=lambda t: t[0], reverse=True)
        return [intent for _, intent in candidates[:available_slots]]

    def record_trade_opened(self) -> None:
        self.day_tracker.record_trade_open()

    def record_trade_closed(self, pnl_usd: float) -> None:
        self.day_tracker.record_trade_close(pnl_usd)
        self.week_tracker.record_trade_closed(pnl_usd)
