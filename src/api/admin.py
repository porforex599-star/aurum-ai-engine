"""Phase 6 — Admin endpoints for engine freeze/unfreeze.

All endpoints require the `X-Admin-Key` header to match the `ADMIN_KEY` env
var. If `ADMIN_KEY` isn't set, the endpoints return 503 — that's intentional;
the engine refuses to admit it has admin endpoints at all if no key is wired.

A freeze stops NEW open intents from being executed by the tick loop. Closes
and SL trails still run — that's by design so positions can wind down safely.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from loguru import logger
from pydantic import BaseModel

from src.engine.master_account import is_product_position
from src.engine.runtime import AppRuntime, get_runtime
from src.products.models import CloseIntent, IntentKind

router = APIRouter(prefix="/admin", tags=["admin"])

# Slugs the dashboard can target. A position is attributed to a product by
# symbol membership + the AURUM_AI strategy comment (no magic numbers exist).
_PRODUCT_SLUGS = ("gold_ai", "multi_cfd_ai")


def _verify_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ADMIN_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_KEY not configured")
    if x_admin_key != expected:
        raise HTTPException(status_code=401, detail="invalid X-Admin-Key")


class FreezeBody(BaseModel):
    reason: str | None = None
    by: str | None = None


class CloseAllBody(BaseModel):
    reason: str | None = None
    by: str | None = None


def _resolve_product(rt: AppRuntime, slug: str):
    """Validate the slug and return the product, or raise 400."""
    if slug not in _PRODUCT_SLUGS or slug not in rt.products:
        raise HTTPException(status_code=400, detail=f"unknown product slug: {slug}")
    return rt.products[slug]


async def _close_one(
    rt: AppRuntime, executor: Any, slug: str, pos: dict, reason: str
) -> dict:
    """Close a single attributed position; release its lock on success.

    Phase 7 Stage 2: `executor` is the product's master executor so closes route
    to the account that actually holds the position."""
    intent = CloseIntent(
        kind=IntentKind.CLOSE,
        position_id=pos["position_id"],
        reason=reason,
        code="admin_close",
    )
    ok = await executor.execute_close(intent)
    detail = {
        "position_id": pos["position_id"],
        "symbol": pos["symbol"],
        "pnl": round(pos["floating_pnl"], 2),
        "status": "closed" if ok else "failed",
    }
    if ok:
        # Keep SignalLock consistent so the engine may re-open later if a signal
        # fires (close-all does NOT touch freeze state).
        rt.signal_lock.release(slug, pos["symbol"])
    else:
        detail["error"] = (executor._last_error or {}).get("exc_msg", "unknown")
    return detail


async def _bundle_for_slug(rt: AppRuntime, slug: str):
    """Resolve the per-product master bundle, falling back to the runtime's flat
    components when the accessor isn't present (keeps existing tests working)."""
    getter = getattr(rt, "get_bundle_for_product", None)
    if getter is None:
        return SimpleNamespace(
            account_snapshot=rt.account_snapshot, order_executor=rt.order_executor
        )
    return await getter(slug)


def _state_to_dict(state) -> dict:  # type: ignore[no-untyped-def]
    return {
        "frozen": state.frozen,
        "reason": state.reason,
        "frozen_at": state.frozen_at.isoformat() if state.frozen_at else None,
        "frozen_by": state.frozen_by,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
        "cached": state.cached,
    }


@router.get("/freeze")
async def get_freeze(
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Return the current freeze state, forcing a fresh DB read."""
    state = await rt.freeze_manager.get_state(force_refresh=True)
    return _state_to_dict(state)


@router.post("/freeze")
async def post_freeze(
    body: FreezeBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Freeze the engine — new opens skipped, closes still run."""
    state = await rt.freeze_manager.set_frozen(
        frozen=True, reason=body.reason, by=body.by
    )
    rt.intent_bus.publish(
        "freeze_manager",
        "frozen",
        {"reason": body.reason, "by": body.by},
        rt.settings.dry_run,
    )
    return _state_to_dict(state)


@router.post("/unfreeze")
async def post_unfreeze(
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Unfreeze — opens resume on the next tick."""
    state = await rt.freeze_manager.set_frozen(frozen=False)
    rt.intent_bus.publish(
        "freeze_manager",
        "unfrozen",
        {},
        rt.settings.dry_run,
    )
    return _state_to_dict(state)


@router.post("/products/{slug}/close-all")
async def close_all_positions(
    slug: str,
    body: CloseAllBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Close every open position attributed to a product. Does NOT touch freeze
    state — the engine keeps running and may re-open if a signal fires."""
    product = _resolve_product(rt, slug)
    reason = body.reason or "admin_close_all"
    by = body.by or "admin"

    # Phase 7 Stage 2 — act against the master account serving this product.
    bundle = await _bundle_for_slug(rt, slug)
    # Fresh fetch (force_refresh) so we act on current broker state, not a
    # possibly-stale cached snapshot.
    snap = await bundle.account_snapshot.get(force_refresh=True)
    symbols = list(product.config.symbols)
    targets = [
        pos
        for pos in snap.positions
        if is_product_position(pos["symbol"], pos.get("comment"), symbols)
    ]

    details: list[dict] = []
    total_pnl = 0.0
    for pos in targets:
        detail = await _close_one(rt, bundle.order_executor, slug, pos, reason)
        details.append(detail)
        if detail["status"] == "closed":
            total_pnl += detail["pnl"]

    closed = sum(1 for d in details if d["status"] == "closed")
    failed = sum(1 for d in details if d["status"] == "failed")
    total_pnl = round(total_pnl, 2)
    now = datetime.now(timezone.utc)

    rt.intent_bus.publish(
        "admin",
        "admin_close_all",
        {
            "slug": slug,
            "positions_closed": closed,
            "positions_failed": failed,
            "total_pnl": total_pnl,
            "reason": reason,
            "by": by,
        },
        rt.settings.dry_run,
        now,
    )
    logger.info(
        "admin close_all {} by {}: closed={} failed={} total_pnl={} reason={}",
        slug,
        by,
        closed,
        failed,
        total_pnl,
        reason,
    )
    return {
        "product": slug,
        "positions_closed": closed,
        "positions_failed": failed,
        "total_pnl": total_pnl,
        "closed_at": now.isoformat(),
        "closed_by": by,
        "details": details,
    }


@router.post("/products/{slug}/close-position/{position_id}")
async def close_single_position(
    slug: str,
    position_id: str,
    body: CloseAllBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Close one position, only if it belongs to the product (symbol + comment)."""
    product = _resolve_product(rt, slug)
    reason = body.reason or "admin_close_position"
    by = body.by or "admin"

    # Phase 7 Stage 2 — act against the master account serving this product.
    bundle = await _bundle_for_slug(rt, slug)
    snap = await bundle.account_snapshot.get(force_refresh=True)
    symbols = list(product.config.symbols)
    pos = next(
        (
            p
            for p in snap.positions
            if p["position_id"] == position_id
            and is_product_position(p["symbol"], p.get("comment"), symbols)
        ),
        None,
    )
    if pos is None:
        raise HTTPException(
            status_code=404,
            detail=f"position {position_id} not found for product {slug}",
        )

    detail = await _close_one(rt, bundle.order_executor, slug, pos, reason)
    now = datetime.now(timezone.utc)
    rt.intent_bus.publish(
        "admin",
        "admin_close_position",
        {
            "slug": slug,
            "position_id": position_id,
            "symbol": pos["symbol"],
            "pnl": detail["pnl"],
            "status": detail["status"],
            "reason": reason,
            "by": by,
        },
        rt.settings.dry_run,
        now,
    )
    logger.info(
        "admin close_position {} {} by {}: status={} pnl={}",
        slug,
        position_id,
        by,
        detail["status"],
        detail["pnl"],
    )
    return {
        "product": slug,
        "position_id": position_id,
        "status": detail["status"],
        "pnl": detail["pnl"],
        "closed_at": now.isoformat(),
        "closed_by": by,
        **({"error": detail["error"]} if "error" in detail else {}),
    }
