from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger

from src.engine.master_account import is_product_position, public_position
from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/status", tags=["status"])


async def _master_snapshot(rt: AppRuntime):
    """Cached default-master account + positions; never raises."""
    snap = getattr(rt, "account_snapshot", None)
    if snap is None:
        return None, []
    try:
        s = await snap.get()
        return s.account, s.positions
    except Exception as exc:  # noqa: BLE001
        logger.warning("status: master snapshot failed: {}", exc)
        return None, []


async def _product_snapshot(rt: AppRuntime, slug: str):
    """Phase 7 Stage 2 — the master account + positions serving `slug`.

    Returns None when the runtime predates per-product routing (older tests /
    fakes) so the caller falls back to the shared default snapshot."""
    getter = getattr(rt, "get_bundle_for_product", None)
    if getter is None:
        return None
    try:
        bundle = await getter(slug)
        s = await bundle.account_snapshot.get()
        return s.account, s.positions
    except Exception as exc:  # noqa: BLE001
        logger.warning("status: per-product snapshot failed for {}: {}", slug, exc)
        return None


@router.get("")
async def get_status(rt: AppRuntime = Depends(get_runtime)) -> dict:
    master_account, positions = await _master_snapshot(rt)

    products_status: dict[str, dict] = {}
    for name, p in rt.products.items():
        symbols = list(p.config.symbols)
        # Prefer this product's own master (Stage 2); fall back to the shared
        # default snapshot for single-master deployments / older runtimes.
        per = await _product_snapshot(rt, name)
        if per is not None:
            prod_account, prod_positions = per
        else:
            prod_account, prod_positions = master_account, positions
        product_positions = [
            public_position(pos)
            for pos in prod_positions
            if is_product_position(pos["symbol"], pos.get("comment"), symbols)
        ]
        products_status[name] = {
            "day_pnl": p.day_tracker.state.total_pnl_usd,
            "day_trades_opened": p.day_tracker.state.trades_opened,
            "week_net_pnl": p.week_tracker.state.net_pnl_usd,
            "week_state": p.week_tracker.state.state,
            "week_cycle_id": p.week_tracker.state.cycle_id,
            "symbols": symbols,
            # No magic numbers exist in this engine — attribution is by symbol +
            # comment. Surfaced as null so the dashboard doesn't imply otherwise.
            "magic_number": None,
            # Phase 7 Stage 2 — the master account this product trades on.
            "master_account": prod_account,
            "open_positions_count": len(product_positions),
            "open_positions": product_positions,
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
        "master_account": master_account,
        "products": products_status,
    }
