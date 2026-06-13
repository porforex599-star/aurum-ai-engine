"""Phase 6 — Admin endpoints for engine freeze/unfreeze.

All endpoints require the `X-Admin-Key` header to match the `ADMIN_KEY` env
var. If `ADMIN_KEY` isn't set, the endpoints return 503 — that's intentional;
the engine refuses to admit it has admin endpoints at all if no key is wired.

A freeze stops NEW open intents from being executed by the tick loop. Closes
and SL trails still run — that's by design so positions can wind down safely.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.config import get_settings
from src.core.supabase_client import SupabaseClient, get_customers_client
from src.engine.master_account import is_product_position
from src.engine.runtime import AppRuntime, get_runtime
from src.products.models import CloseIntent, IntentKind
from src.services.chart_img import capture_layout_snapshot
from src.services.snapshot_storage import upload_snapshot_to_path

router = APIRouter(prefix="/admin", tags=["admin"])

# Public /room feed (aurum-signals) — returned so an admin can jump straight to
# the published post after a manual publish.
ROOM_URL = "https://aurum-signals-ecru.vercel.app/room"

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


class TestCaptureBody(BaseModel):
    """Request body for the manual chart-img capture endpoint.

    Defaults target the current Gold Panel V.2 shared layout on 5m XAUUSD.
    """

    layout_id: str = "uoSX32t7"
    symbol: str = "OANDA:XAUUSD"
    interval: str = "5"
    post_id: str | None = None


class PublishAnalysisBody(BaseModel):
    """Request body for an admin manual analysis publish to /room.

    ``symbol``/``timeframe`` are display strings (what /room renders);
    ``chart_symbol``/``chart_interval``/``layout_id`` drive the chart-img
    capture. ``direction`` maps to the ``bias`` column and ``conviction``
    (3-5 stars) maps to ``confidence`` as a percentage (conviction * 20).
    """

    symbol: str = "XAUUSD"
    timeframe: str = "M5"
    direction: Literal["bull", "bear"]
    conviction: int = Field(..., ge=3, le=5)
    layout_id: str = "uoSX32t7"
    chart_symbol: str = "OANDA:XAUUSD"
    chart_interval: str = "5"
    # analysis_posts has these NOT NULL; expose them so an admin can override
    # the sensible defaults when relevant.
    key_level: float = 0.0
    risk_level: Literal["low", "medium", "high"] = "medium"


def get_chart_store() -> SupabaseClient:
    """Supabase client for the customers project (owns the analysis-snapshots
    bucket and the ``analysis_posts`` table). Overridable in tests."""
    return get_customers_client()


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


# -------------------- chart-img manual capture --------------------


@router.post("/chart/test-capture", tags=["Admin"])
async def test_capture_chart(
    body: TestCaptureBody,
    store: SupabaseClient = Depends(get_chart_store),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Manually trigger a chart-img layout capture → Supabase Storage upload.

    Reuses the same capture/upload helpers as the Aurum Sniper webhook (PR
    #18/#19). When ``post_id`` is provided the PNG overwrites
    ``{post_id}.png`` and ``analysis_posts.chart_image_url`` is updated;
    otherwise it lands under ``test/{utc_iso_timestamp}.png`` and the DB is
    left untouched. Latency is measured from before the capture call until the
    upload completes.
    """
    started = time.perf_counter()

    png_bytes = await capture_layout_snapshot(
        symbol=body.symbol,
        interval=body.interval,
        layout_id=body.layout_id,
    )
    if not png_bytes:
        raise HTTPException(
            status_code=502,
            detail="chart-img capture failed (check CHARTIMG_API_KEY / layout_id)",
        )

    if body.post_id:
        storage_path = f"{body.post_id}.png"
    else:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        storage_path = f"test/{timestamp}.png"

    public_url = await upload_snapshot_to_path(store, storage_path, png_bytes)
    if not public_url:
        raise HTTPException(status_code=502, detail="Supabase Storage upload failed")

    latency_ms = round((time.perf_counter() - started) * 1000)

    post_id_updated: str | None = None
    if body.post_id:
        try:
            await store.update_row(
                get_settings().ANALYSIS_TABLE,
                {
                    "chart_image_url": public_url,
                    "chart_image_generated_at": datetime.now(timezone.utc).isoformat(),
                },
                match={"id": body.post_id},
            )
            post_id_updated = body.post_id
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "test-capture update analysis_posts failed: post_id={} exc={}: {}",
                body.post_id,
                type(exc).__name__,
                str(exc)[:200],
            )
            raise HTTPException(
                status_code=502,
                detail="chart uploaded but analysis_posts UPDATE failed",
            ) from exc

    logger.info(
        "admin test-capture: layout={} symbol={} interval={} path={} latency_ms={} "
        "post_id_updated={}",
        body.layout_id,
        body.symbol,
        body.interval,
        storage_path,
        latency_ms,
        post_id_updated,
    )

    return {
        "chart_image_url": public_url,
        "storage_path": storage_path,
        "latency_ms": latency_ms,
        "post_id_updated": post_id_updated,
    }


# -------------------- manual analysis publish to /room --------------------


@router.post("/analysis/publish", tags=["Admin"])
async def publish_analysis(
    body: PublishAnalysisBody,
    store: SupabaseClient = Depends(get_chart_store),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Manually publish a new analysis post to /room.

    Mirrors the Sniper webhook's persist→capture→update flow (reusing the same
    chart-img + storage helpers) so an admin can publish without a Pine alert:

    1. INSERT a row into ``analysis_posts`` (``source='admin_manual'``,
       ``chart_image_url`` NULL) — Supabase Realtime broadcasts it to /room.
    2. Capture the TradingView layout → upload ``{post_id}.png`` to Storage.
    3. UPDATE ``chart_image_url`` so /room re-renders with the chart.

    The chart step is best-effort: if capture/upload fails the post is still
    published (chart_image_url stays NULL) and the endpoint returns 200, matching
    the webhook's resilience — the post is already live on /room.
    """
    table = get_settings().ANALYSIS_TABLE
    bias = "bullish" if body.direction == "bull" else "bearish"

    row = {
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "bias": bias,
        "key_level": body.key_level,
        "risk_level": body.risk_level,
        "confidence": body.conviction * 20,  # 3-5 stars → 60/80/100%
        "source": "admin_manual",
    }
    try:
        inserted = await store.insert_row(table, row)
    except Exception as exc:  # noqa: BLE001
        logger.error("admin publish insert failed: {}", exc)
        raise HTTPException(
            status_code=502, detail="failed to insert analysis post"
        ) from exc

    post_id = inserted.get("id")
    if post_id is None:
        logger.error("admin publish insert returned no id: {}", inserted)
        raise HTTPException(status_code=502, detail="insert returned no post id")
    post_id = str(post_id)

    # Capture chart → upload → UPDATE chart_image_url (best-effort).
    started = time.perf_counter()
    chart_image_url: str | None = None
    png_bytes = await capture_layout_snapshot(
        symbol=body.chart_symbol,
        interval=body.chart_interval,
        layout_id=body.layout_id,
    )
    if png_bytes:
        chart_image_url = await upload_snapshot_to_path(
            store, f"{post_id}.png", png_bytes
        )
    latency_ms = round((time.perf_counter() - started) * 1000)

    if chart_image_url:
        try:
            await store.update_row(
                table,
                {
                    "chart_image_url": chart_image_url,
                    "chart_image_generated_at": datetime.now(timezone.utc).isoformat(),
                },
                match={"id": post_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "admin publish update chart_image_url failed: post_id={} exc={}: {}",
                post_id,
                type(exc).__name__,
                str(exc)[:200],
            )
            chart_image_url = None
    else:
        logger.warning(
            "admin publish: chart capture/upload failed for post {} — "
            "published without chart_image_url",
            post_id,
        )

    logger.info(
        "admin publish: post_id={} symbol={} bias={} conviction={} latency_ms={} "
        "chart={}",
        post_id,
        body.symbol,
        bias,
        body.conviction,
        latency_ms,
        bool(chart_image_url),
    )

    return {
        "post_id": post_id,
        "chart_image_url": chart_image_url,
        "latency_ms": latency_ms,
        "room_url": ROOM_URL,
    }
