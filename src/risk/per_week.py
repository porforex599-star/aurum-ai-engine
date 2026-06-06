from __future__ import annotations

from src.risk.models import ProductCode, RiskDecision, RiskParams, WeekState


class WeekTracker:
    def __init__(self, cycle_id: str, product: ProductCode, params: RiskParams) -> None:
        self.params = params
        self.state = WeekState(cycle_id=cycle_id, product=product)

    def record_pnl_delta(self, delta_usd: float) -> None:
        self.state.net_pnl_usd += delta_usd
        self._reevaluate_state()

    def record_trade_closed(self, pnl_usd: float) -> None:
        self.record_pnl_delta(pnl_usd)
        self.state.trades_in_cycle += 1

    def _reevaluate_state(self) -> None:
        if self.state.net_pnl_usd >= self.params.target_win_usd:
            self.state.state = "expired_win"
        elif self.state.net_pnl_usd <= -self.params.target_loss_usd:
            self.state.state = "expired_loss"

    def check_target(self) -> RiskDecision:
        if self.state.state == "expired_win":
            return RiskDecision(False, "Week target win hit", "week_target_win", force_close=True)
        if self.state.state == "expired_loss":
            return RiskDecision(False, "Week target loss hit", "week_target_loss", force_close=True)
        return RiskDecision(True)

    def is_expired(self) -> bool:
        return self.state.state in ("expired_win", "expired_loss")
