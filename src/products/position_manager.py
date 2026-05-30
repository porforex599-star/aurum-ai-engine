from __future__ import annotations

from src.products.models import IntentKind, ModifySLIntent, OpenPosition
from src.risk.models import RiskParams
from src.risk.per_trade import (
    compute_new_trail_sl,
    should_move_to_be,
    should_start_trail,
)


class PositionManager:
    """Stateless. Given current open positions + risk params, compute SL adjustments."""

    def evaluate_position(
        self,
        position: OpenPosition,
        params: RiskParams,
        *,
        pip_value_usd: float = 10.0,
    ) -> ModifySLIntent | None:
        side = position.side.value

        if should_start_trail(position.current_pnl_usd, params):
            current_sl = (
                position.current_sl
                if position.current_sl is not None
                else position.entry_price
            )
            new_sl = compute_new_trail_sl(
                current_price=position.current_price,
                current_sl=current_sl,
                side=side,
                params=params,
                lot=position.lot,
                pip_value_usd=pip_value_usd,
            )
            if position.current_sl is None or self._is_improvement(
                side, position.current_sl, new_sl
            ):
                return ModifySLIntent(
                    IntentKind.MODIFY_SL,
                    position.position_id,
                    new_sl,
                    "Trail SL update",
                )
            return None

        if should_move_to_be(position.current_pnl_usd, params):
            be = position.entry_price
            if position.current_sl is None or self._is_improvement(
                side, position.current_sl, be
            ):
                return ModifySLIntent(
                    IntentKind.MODIFY_SL,
                    position.position_id,
                    be,
                    "Move SL to breakeven",
                )

        return None

    @staticmethod
    def _is_improvement(side: str, current_sl: float, new_sl: float) -> bool:
        if side == "buy":
            return new_sl > current_sl
        if side == "sell":
            return new_sl < current_sl
        raise ValueError(f"Unknown side: {side}")

    def evaluate_all(
        self,
        positions: list[OpenPosition],
        params: RiskParams,
        *,
        pip_value_usd: float = 10.0,
    ) -> list[ModifySLIntent]:
        out: list[ModifySLIntent] = []
        for p in positions:
            r = self.evaluate_position(p, params, pip_value_usd=pip_value_usd)
            if r is not None:
                out.append(r)
        return out
