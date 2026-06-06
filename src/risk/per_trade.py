from __future__ import annotations

from src.risk.models import RiskParams


def should_move_to_be(current_pnl_usd: float, params: RiskParams) -> bool:
    return current_pnl_usd >= params.be_offset_usd


def should_start_trail(current_pnl_usd: float, params: RiskParams) -> bool:
    return current_pnl_usd >= params.trail_start_usd


def compute_new_trail_sl(
    current_price: float,
    current_sl: float,
    side: str,
    params: RiskParams,
    lot: float,
    pip_value_usd: float = 10.0,
) -> float:
    step_price = params.trail_step_usd / (lot * pip_value_usd)
    if side == "buy":
        return max(current_sl, current_price - step_price)
    if side == "sell":
        return min(current_sl, current_price + step_price)
    raise ValueError(f"Unknown side: {side!r}")
