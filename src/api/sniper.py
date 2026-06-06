"""Aurum Sniper alert webhook.

Receives Pine Script alert JSON, normalizes vocabulary, persists it to
`aurum-customers.analysis_posts` (which Supabase Realtime broadcasts to
subscribed `/room` clients via `postgres_changes`), and pushes a Telegram
notification to @AurumAIEngineBot.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, HTTPException, status
from loguru import logger

from src.config import get_settings
from src.core.supabase_client import get_supabase_client
from src.core.telegram_notifier import get_telegram_notifier
from src.schemas.sniper import SniperAlertPayload, SniperAlertResponse

router = APIRouter()


def _verify_secret(provided: str | None) -> None:
    settings = get_settings()
    expected = settings.AURUM_SNIPER_WEBHOOK_SECRET

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
) -> SniperAlertResponse:
    # 1. Authenticate.
    _verify_secret(x_webhook_secret)

    # 2. Vocab is already normalized by SniperAlertPayload validators
    #    (buy/long → bullish, sell/short → bearish).
    settings = get_settings()

    # 3. Persist. Realtime then broadcasts the row to /room subscribers.
    supabase = get_supabase_client()
    try:
        inserted = await supabase.insert_row(
            settings.ANALYSIS_SCHEMA,
            settings.ANALYSIS_TABLE,
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
    await get_telegram_notifier().send_analysis(payload)

    return SniperAlertResponse(post_id=str(post_id), broadcast=True)
