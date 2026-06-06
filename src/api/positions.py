from __future__ import annotations

from fastapi import APIRouter, Depends

from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("")
async def get_positions(rt: AppRuntime = Depends(get_runtime)) -> dict:
    positions = await rt.position_poller.fetch_all()
    return {
        "count": len(positions),
        "positions": [
            {
                "id": p.position_id,
                "symbol": p.symbol,
                "side": p.side.value,
                "lot": p.lot,
                "entry": p.entry_price,
                "current": p.current_price,
                "pnl": p.current_pnl_usd,
                "sl": p.current_sl,
            }
            for p in positions
        ],
    }
