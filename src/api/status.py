from __future__ import annotations

from fastapi import APIRouter, Depends

from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
async def get_status(rt: AppRuntime = Depends(get_runtime)) -> dict:
    products_status: dict[str, dict] = {}
    for name, p in rt.products.items():
        products_status[name] = {
            "day_pnl": p.day_tracker.state.total_pnl_usd,
            "day_trades_opened": p.day_tracker.state.trades_opened,
            "week_net_pnl": p.week_tracker.state.net_pnl_usd,
            "week_state": p.week_tracker.state.state,
            "week_cycle_id": p.week_tracker.state.cycle_id,
        }

    # Phase 6 — freeze state (read-only, no admin key needed for visibility).
    try:
        fs = await rt.freeze_manager.get_state()
        freeze_info = {
            "frozen": fs.frozen,
            "reason": fs.reason,
            "frozen_at": fs.frozen_at.isoformat() if fs.frozen_at else None,
            "frozen_by": fs.frozen_by,
        }
    except Exception:  # noqa: BLE001
        freeze_info = {"frozen": False, "reason": None, "frozen_at": None, "frozen_by": None}

    return {
        "dry_run": rt.settings.dry_run,
        "last_tick": rt.last_tick.isoformat() if rt.last_tick else None,
        "last_tick_status": rt.last_tick_status,
        "freeze": freeze_info,
        "products": products_status,
    }
