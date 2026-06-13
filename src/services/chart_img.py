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

# chart-img layout-chart intervals (case-sensitive: "1m"=minute vs "1M"=month).
_CHARTIMG_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "45m",
    "1h", "2h", "3h", "4h",
    "1D", "1W", "1M",
}

# TradingView / Pine vocab → chart-img interval. Covers Pine "M5"/"H4" style
# and the numeric resolution style ("5", "240") emitted by alerts and the
# admin test-capture body.
_TF_ALIASES = {
    "M1": "1m", "M3": "3m", "M5": "5m", "M15": "15m", "M30": "30m", "M45": "45m",
    "H1": "1h", "H2": "2h", "H3": "3h", "H4": "4h",
    "D1": "1D", "W1": "1W", "MN1": "1M",
    "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "45": "45m",
    "60": "1h", "120": "2h", "180": "3h", "240": "4h",
    "D": "1D", "W": "1W", "M": "1M",
}


def normalize_interval(tf: str) -> str:
    """Normalize any TV/Pine timeframe to a chart-img interval (default ``15m``).

    Idempotent: an already-valid chart-img interval (e.g. ``"5m"``) passes
    through unchanged, so it's safe to call even on values that another layer
    has already mapped. This is the single source of truth for the chart-img
    interval vocabulary — both the Sniper webhook and the admin test-capture
    endpoint go through it, so a raw ``"5"`` never reaches chart-img as ``"5"``
    (which the API rejects with HTTP 422).
    """
    s = str(tf).strip()
    if s in _CHARTIMG_INTERVALS:
        return s
    return _TF_ALIASES.get(s.upper(), "15m")


async def capture_layout_snapshot(
    symbol: str,
    interval: str,
    layout_id: str | None = None,
    timeout: float = 60.0,
) -> bytes | None:
    """Capture a TradingView layout snapshot → PNG bytes. ``None`` on failure.

    Returns ``None`` (without making a request) when the chart-img credentials
    or layout id are not configured, so the feature degrades gracefully on
    deployments that haven't set CHARTIMG_API_KEY / TV_LAYOUT_ID.

    ``layout_id`` overrides the configured ``TV_LAYOUT_ID`` for the call, letting
    callers (e.g. the admin test-capture endpoint) target an arbitrary shared
    layout without changing global config.

    ``interval`` is normalized to chart-img's vocabulary via
    :func:`normalize_interval`, so callers may pass any TV/Pine style value.
    """
    settings = get_settings()
    api_key = settings.CHARTIMG_API_KEY
    layout_id = layout_id or settings.TV_LAYOUT_ID
    interval = normalize_interval(interval)

    if not api_key or not layout_id:
        logger.warning(
            "chart_img not configured (CHARTIMG_API_KEY/TV_LAYOUT_ID missing) — "
            "skipping snapshot for {} {}",
            symbol,
            interval,
        )
        return None

    # chart-img.com Shared Layout endpoint: the layout id goes in the URL path
    # (POST /v2/tradingview/layout-chart/<LAYOUT_ID>), not the request body.
    url = f"{CHARTIMG_BASE}/tradingview/layout-chart/{layout_id}"
    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
    }
    payload = {
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
    except httpx.HTTPStatusError as exc:
        # Surface chart-img's own validation message (it explains *which* field
        # the request got wrong) instead of just the generic status line.
        resp = exc.response
        logger.warning(
            "chart_img.capture failed: status={} body={} symbol={} interval={} "
            "exc={}: {}",
            resp.status_code,
            resp.text[:1000],
            symbol,
            interval,
            type(exc).__name__,
            str(exc)[:200],
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chart_img.capture failed: symbol={} interval={} exc={}: {}",
            symbol,
            interval,
            type(exc).__name__,
            str(exc)[:200],
        )
        return None
