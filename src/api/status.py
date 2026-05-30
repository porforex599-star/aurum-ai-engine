from __future__ import annotations

from fastapi import APIRouter, Depends

from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
def get_status(rt: AppRuntime = Depends(get_runtime)) -> dict:
    products_status: dict[str, dict] = {}
    for name, p in rt.products.items():
        products_status[name] = {
            "day_pnl": p.day_tracker.state.total_pnl_usd,
            "day_trades_opened": p.day_tracker.state.trades_opened,
            "week_net_pnl": p.week_tracker.state.net_pnl_usd,
            "week_state": p.week_tracker.state.state,
            "week_cycle_id": p.week_tracker.state.cycle_id,
        }
    return {
        "dry_run": rt.settings.dry_run,
        "last_tick": rt.last_tick.isoformat() if rt.last_tick else None,
        "last_tick_status": rt.last_tick_status,
        "products": products_status,
    }
