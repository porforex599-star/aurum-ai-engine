"""Telegram notifier — pushes analysis alerts to @AurumAIEngineBot.

Sends are best-effort: a delivery failure is logged but never propagated,
so a flaky Telegram API can't fail a webhook whose data is already persisted.
"""

from __future__ import annotations

import httpx
from loguru import logger

from src.config import get_settings
from src.schemas.sniper import SniperAlertPayload

_API_BASE = "https://api.telegram.org"
_BIAS_EMOJI = {"bullish": "🟢", "bearish": "🔴"}
_RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def _format_message(payload: SniperAlertPayload) -> str:
    bias_icon = _BIAS_EMOJI.get(payload.bias, "")
    risk_icon = _RISK_EMOJI.get(payload.risk_level, "")

    lines = [
        f"🎯 *Aurum Sniper* — {payload.symbol} · {payload.timeframe}",
        f"{bias_icon} Bias: *{payload.bias.upper()}*",
        f"📍 Key level: `{payload.key_level}`",
    ]

    if payload.target_zones:
        zones = "  ".join(f"{z.id}@{z.price}" for z in payload.target_zones)
        lines.append(f"🎯 Targets: {zones}")

    lines.append(f"{risk_icon} Risk: {payload.risk_level} · Confidence: {payload.confidence}%")

    if payload.note:
        lines.append(f"📝 {payload.note}")

    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.TELEGRAM_BOT_TOKEN
        self._chat_id = settings.TELEGRAM_CHAT_ID

    def is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send_analysis(self, payload: SniperAlertPayload) -> bool:
        """Send a formatted analysis alert. Returns True on a successful send."""
        if not self.is_configured():
            logger.warning("Telegram notifier not configured — skipping send_analysis")
            return False

        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        body = {
            "chat_id": self._chat_id,
            "text": _format_message(payload),
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
            logger.info("Telegram analysis alert sent for {} {}", payload.symbol, payload.timeframe)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram send_analysis failed: {}", exc)
            return False


_notifier: TelegramNotifier | None = None


def get_telegram_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
