"""Aurum Sniper alert webhook.

Receives Pine Script alert JSON, normalizes vocabulary, persists it to
`analysis_posts` in the separate `aurum-customers` Supabase project (which
Supabase Realtime broadcasts to subscribed `/room` clients via
`postgres_changes`), and pushes a Telegram notification to @AurumAIEngineBot.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from loguru import logger

from src.config import get_settings
from src.core.supabase_client import SupabaseClient, get_customers_client
from src.engine.runtime import get_runtime
from src.notifier.telegram import TelegramNotifier
from src.schemas.sniper import SniperAlertPayload, SniperAlertResponse

router = APIRouter()


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
        await notifier.send_analysis_alert(payload)

    return SniperAlertResponse(post_id=str(post_id), broadcast=True)
