from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.risk.models import DayState, RiskDecision, RiskParams

BKK = ZoneInfo("Asia/Bangkok")


class DayTracker:
    def __init__(self, params: RiskParams, tz: ZoneInfo = BKK) -> None:
        self.params = params
        self.tz = tz
        self.state = DayState(date=datetime.now(tz).date())

    def can_open_new_trade(self) -> RiskDecision:
        if self.state.trades_opened >= self.params.daily_max_trades:
            return RiskDecision(False, "Daily max trades reached", "daily_max_trades")
        if self.state.total_pnl_usd <= -self.params.daily_loss_limit_usd:
            return RiskDecision(False, "Daily loss limit reached", "daily_loss_limit")
        return RiskDecision(True)

    def record_trade_open(self) -> None:
        self.state.trades_opened += 1

    def record_trade_close(self, realized_pnl_usd: float) -> None:
        self.state.realized_pnl_usd += realized_pnl_usd
        self.state.trades_closed += 1

    def update_floating(self, floating_pnl_usd: float) -> None:
        self.state.floating_pnl_usd = floating_pnl_usd

    def maybe_reset(self, now: datetime) -> None:
        today = now.astimezone(self.tz).date()
        if today != self.state.date:
            self.state = DayState(date=today)
