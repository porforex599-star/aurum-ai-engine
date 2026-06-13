"""Aurum Sniper alert webhook.

Receives Pine Script alert JSON, normalizes vocabulary, persists it to
`analysis_posts` in the separate `aurum-customers` Supabase project (which
Supabase Realtime broadcasts to subscribed `/room` clients via
`postgres_changes`), and pushes a Telegram notification to @AurumAIEngineBot.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from loguru import logger

from src.config import get_settings
from src.core.supabase_client import SupabaseClient, get_customers_client
from src.engine.runtime import get_runtime
from src.notifier.telegram import TelegramNotifier
from src.schemas.sniper import SniperAlertPayload, SniperAlertResponse
from src.services.chart_img import capture_layout_snapshot, normalize_interval
from src.services.snapshot_storage import upload_snapshot

router = APIRouter()


def _timeframe_to_chartimg_interval(tf: str) -> str:
    """Pine timeframe → chart-img interval string (default 15m).

    Thin wrapper over the shared :func:`normalize_interval` so the chart-img
    interval vocabulary lives in exactly one place.
    """
    return normalize_interval(tf)


async def _attach_chart_snapshot(
    store: SupabaseClient, payload: SniperAlertPayload, post_id: str
) -> None:
    """Capture a TV snapshot → upload to Storage → UPDATE chart_image_url.

    Best-effort: each step is graceful and returns early on failure, so a
    missing snapshot never blocks the post that was already persisted and
    broadcast. The Realtime UPDATE re-broadcasts the row so /room can render
    the <img> once the chart is ready.
    """
    tv_symbol = payload.symbol if ":" in payload.symbol else f"OANDA:{payload.symbol}"
    interval = _timeframe_to_chartimg_interval(payload.timeframe)

    png_bytes = await capture_layout_snapshot(symbol=tv_symbol, interval=interval)
    if not png_bytes:
        return

    public_url = await upload_snapshot(store, post_id, png_bytes)
    if not public_url:
        return

    try:
        await store.update_row(
            get_settings().ANALYSIS_TABLE,
            {
                "chart_image_url": public_url,
                "chart_image_generated_at": datetime.now(timezone.utc).isoformat(),
            },
            match={"id": post_id},
        )
        logger.info("Attached chart snapshot to post {} ({})", post_id, public_url)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "update analysis_posts.chart_image_url failed: post_id={} exc={}: {}",
            post_id,
            type(exc).__name__,
            str(exc)[:200],
        )


def get_analysis_store() -> SupabaseClient:
    """The Supabase client for the customers project (where analysis_posts live)."""
    return get_customers_client()


def get_analysis_notifier() -> TelegramNotifier | None:
    """The engine's Telegram notifier, or None if the runtime isn't up yet.

    Notifications are best-effort, so a missing runtime must not fail the
    webhook — we just skip the Telegram step.
    """
    try:
        return get_runtime().notifier
    except RuntimeError:
        return None


def _verify_secret(provided: str | None) -> None:
    expected = get_settings().AURUM_SNIPER_WEBHOOK_SECRET

    if not expected:
        logger.error("AURUM_SNIPER_WEBHOOK_SECRET is not configured — rejecting webhook")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook secret not configured",
        )

    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )


@router.post(
    "/api/internal/aurum-sniper-alert",
    response_model=SniperAlertResponse,
    status_code=status.HTTP_200_OK,
)
async def aurum_sniper_alert(
    payload: SniperAlertPayload,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    secret: str | None = Query(default=None),
    store: SupabaseClient = Depends(get_analysis_store),
    notifier: TelegramNotifier | None = Depends(get_analysis_notifier),
) -> SniperAlertResponse:
    # 1. Authenticate. TradingView can't send custom headers, so the
    #    `?secret=` query param is accepted as a fallback to the header.
    _verify_secret(x_webhook_secret or secret)

    # 2. Vocab is already normalized by SniperAlertPayload validators
    #    (buy/long → bullish, sell/short → bearish).

    # 3. Persist to the customers project. Realtime then broadcasts the row
    #    to /room subscribers via postgres_changes.
    try:
        inserted = await store.insert_row(
            get_settings().ANALYSIS_TABLE,
            payload.to_post_row(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to insert analysis post: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to persist analysis post",
        ) from exc

    post_id = inserted.get("id")
    if post_id is None:
        logger.error("Insert returned no id: {}", inserted)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Insert returned no post id",
        )

    logger.info(
        "Analysis post {} created ({} {} {})",
        post_id,
        payload.symbol,
        payload.timeframe,
        payload.bias,
    )

    # 4. Notify Telegram (best-effort; never fails the request).
    if notifier is not None:
        await notifier.send_analysis_alert(payload, post_id=str(post_id))

    # 5. Capture chart snapshot → Storage → UPDATE chart_image_url (Phase 5a).
    #    Runs after the notify so the Realtime INSERT broadcast and Telegram
    #    alert are already out before the up-to-25s capture. Fully graceful —
    #    any failure here must never turn the persisted post into a 5xx.
    try:
        await _attach_chart_snapshot(store, payload, str(post_id))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "chart snapshot pipeline failed: post_id={} exc={}: {}",
            post_id,
            type(exc).__name__,
            str(exc)[:200],
        )

    return SniperAlertResponse(post_id=str(post_id), broadcast=True)
