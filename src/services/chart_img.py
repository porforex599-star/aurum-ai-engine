"""chart-img.com PRO ($7/mo) API client.

PRO specs: 500/day, 10/sec, 1920×1080, no watermark, Layout Charts endpoint.

Used by the Aurum Sniper webhook (Phase 5a) to capture a TradingView layout
snapshot for an analysis post. Never raises — a failed capture returns ``None``
so the webhook's Realtime broadcast and Telegram notification are unaffected.
"""

from __future__ import annotations

import httpx
from loguru import logger

from src.config import get_settings

CHARTIMG_BASE = "https://api.chart-img.com/v2"


async def capture_layout_snapshot(
    symbol: str,
    interval: str,
    timeout: float = 25.0,
) -> bytes | None:
    """Capture a TradingView layout snapshot → PNG bytes. ``None`` on failure.

    Returns ``None`` (without making a request) when the chart-img credentials
    or layout id are not configured, so the feature degrades gracefully on
    deployments that haven't set CHARTIMG_API_KEY / TV_LAYOUT_ID.
    """
    settings = get_settings()
    api_key = settings.CHARTIMG_API_KEY
    layout_id = settings.TV_LAYOUT_ID

    if not api_key or not layout_id:
        logger.warning(
            "chart_img not configured (CHARTIMG_API_KEY/TV_LAYOUT_ID missing) — "
            "skipping snapshot for {} {}",
            symbol,
            interval,
        )
        return None

    url = f"{CHARTIMG_BASE}/tradingview/layout-chart"
    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
    }
    payload = {
        "layout": layout_id,
        "symbol": symbol,
        "interval": interval,
        "width": 1920,
        "height": 1080,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chart_img.capture failed: symbol={} interval={} exc={}: {}",
            symbol,
            interval,
            type(exc).__name__,
            str(exc)[:200],
        )
        return None
