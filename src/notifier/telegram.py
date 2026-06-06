"""Telegram notifier for engine intents.

Hooks into IntentBus.publish() as a fire-and-forget side effect: every intent
that survives the kind filter is delivered to a single Telegram chat via the
Bot API. Failures (network, 4xx, 5xx, missing credentials, disabled) never
propagate — the engine MUST NOT slow down or crash because of notifications.

Design notes:
- Skip kinds default to {"none", "modify_sl"} — these are per-tick noise.
  All "*_executed", "*_failed", "trade_closed", "error", "friday_close"
  kinds pass through.
- DRY-RUN safety: every message is suffixed with [DRY-RUN] or [LIVE]
  so a glance at the chat tells you what mode the engine is in.
- HTML parse mode used for bold/emoji rendering. Payload values are
  HTML-escaped to defend against payload strings containing < > &.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any, Iterable

import httpx
from loguru import logger

from src.engine.intent_bus import IntentLogEntry

if TYPE_CHECKING:
    from src.schemas.sniper import SniperAlertPayload

_API_BASE = "https://api.telegram.org"

# Per-kind emoji prefixes for at-a-glance scanning.
_KIND_EMOJI: dict[str, str] = {
    "open": "🟢",
    "close": "🔵",
    "open_executed": "🟢",
    "open_failed": "⚠️",
    "close_executed": "✅",
    "close_failed": "⚠️",
    "modify_sl_executed": "🟦",
    "modify_sl_failed": "⚠️",
    "trade_closed": "💰",
    "trade_closed_dryrun": "💭",
    "skipped_rr_too_low": "🚫",
    # Phase 2.6.3 — signal-dedup + padding-unavailable skips
    "signal_skipped_position_locked": "🔒",
    "padding_unavailable_price_fetch": "🚧",
    "padding_unavailable_spec_miss": "🚧",
    "padding_unavailable_exception": "🚧",
    "error": "❌",
    "friday_close": "🗓️",
    # Phase 6 — freeze state
    "frozen": "🧊",
    "unfrozen": "🟩",
    "frozen_skip": "⏸️",
    # Phase 6.4 — admin position controls
    "admin_close_all": "🚨",
    "admin_close_position": "🚨",
}

# Kinds we never forward to Telegram — per-tick "no signal" noise plus
# the dry-run modify_sl intents (which fire often without value).
_DEFAULT_SKIP_KINDS: frozenset[str] = frozenset({"none", "modify_sl"})


def _esc(value: Any) -> str:
    """HTML-escape a payload value for Telegram HTML parse mode."""
    return html.escape(str(value), quote=False)


def _fmt_price(value: Any) -> str:
    """Format a numeric payload field with reasonable precision, else passthrough."""
    if value is None or value == "":
        return "-"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    if abs(f) >= 100:
        return f"{f:,.2f}"
    if abs(f) >= 1:
        return f"{f:.4f}"
    return f"{f:.5f}"


def _fmt_pnl(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _esc(value)
    sign = "+" if f >= 0 else ""
    return f"{sign}{f:,.2f}"


def _fmt_sl_tp_line(p: dict) -> str:
    """Render the SL / TP line, annotating "(padded from X)" when the order
    executor widened a stop to satisfy the broker's minimum distance."""
    sl, tp = p.get("sl_price"), p.get("tp_price")
    sl_orig, tp_orig = p.get("sl_original"), p.get("tp_original")
    sl_txt = _fmt_price(sl)
    if sl_orig is not None and sl_orig != sl:
        sl_txt += f" (padded from {_fmt_price(sl_orig)})"
    tp_txt = _fmt_price(tp)
    if tp_orig is not None and tp_orig != tp:
        tp_txt += f" (padded from {_fmt_price(tp_orig)})"
    return f"SL {sl_txt} / TP {tp_txt}"


def format_message(entry: IntentLogEntry) -> str:
    """Render an IntentLogEntry as a short HTML-parsed Telegram message."""
    p = entry.payload or {}
    emoji = _KIND_EMOJI.get(entry.kind, "•")
    mode = "DRY-RUN" if entry.dry_run else "LIVE"
    header = (
        f"{emoji} <b>{_esc(entry.product)}</b> · <code>{_esc(entry.kind)}</code>"
    )

    lines: list[str] = [header]

    # Body shape depends on intent kind.
    if entry.kind in {"open", "open_executed", "open_failed"}:
        symbol = p.get("symbol", "?")
        side = str(p.get("side", "")).upper()
        lot = p.get("lot", "?")
        line = f"{_esc(symbol)} {_esc(side)} {_esc(lot)}"
        entry_px = p.get("entry_price")
        if entry_px not in (None, ""):
            line += f" @ {_fmt_price(entry_px)}"
        lines.append(line)
        sl, tp = p.get("sl_price"), p.get("tp_price")
        if sl is not None or tp is not None:
            lines.append(_fmt_sl_tp_line(p))
        setup = p.get("setup")
        conf = p.get("confidence")
        if setup or conf is not None:
            extras: list[str] = []
            if setup:
                extras.append(f"setup={_esc(setup)}")
            if conf is not None:
                try:
                    extras.append(f"conf={float(conf):.2f}")
                except (TypeError, ValueError):
                    extras.append(f"conf={_esc(conf)}")
            lines.append(" ".join(extras))
        if entry.kind == "open_executed" and p.get("position_id"):
            lines.append(f"pos=<code>{_esc(p['position_id'])}</code>")
        if entry.kind == "open_failed":
            err = p.get("exc_msg") or p.get("reason")
            if err:
                lines.append(f"err: {_esc(err)}")

    elif entry.kind in {"close", "close_executed", "close_failed"}:
        pid = p.get("position_id", "?")
        reason = p.get("reason", "")
        lines.append(f"pos=<code>{_esc(pid)}</code>")
        if reason:
            lines.append(f"reason={_esc(reason)}")
        if entry.kind == "close_failed":
            err = p.get("exc_msg")
            if err:
                lines.append(f"err: {_esc(err)}")

    elif entry.kind in {"trade_closed", "trade_closed_dryrun"}:
        pid = p.get("position_id", "?")
        pnl = p.get("pnl")
        lines.append(f"pos=<code>{_esc(pid)}</code>  PnL <b>{_fmt_pnl(pnl)}</b>")
        extras = []
        if "token_updated" in p:
            extras.append(f"token_updated={_esc(p['token_updated'])}")
        if p.get("expired"):
            extras.append(f"expired={_esc(p.get('expiry_reason', 'yes'))}")
        if extras:
            lines.append(" ".join(extras))

    elif entry.kind in {"modify_sl_executed", "modify_sl_failed"}:
        pid = p.get("position_id", "?")
        new_sl = p.get("new_sl_price")
        reason = p.get("reason", "")
        line = f"pos=<code>{_esc(pid)}</code>"
        if new_sl is not None:
            line += f" → SL {_fmt_price(new_sl)}"
        lines.append(line)
        if reason:
            lines.append(f"reason={_esc(reason)}")
        if entry.kind == "modify_sl_failed":
            err = p.get("exc_msg")
            if err:
                lines.append(f"err: {_esc(err)}")

    elif entry.kind == "error":
        reason = p.get("reason", "")
        sym = p.get("symbol") or p.get("symbols")
        if reason:
            lines.append(f"reason={_esc(reason)}")
        if sym:
            lines.append(f"symbol={_esc(sym)}")
        exc_type = p.get("exc_type")
        exc_msg = p.get("exc_msg")
        if exc_type or exc_msg:
            lines.append(f"{_esc(exc_type or 'error')}: {_esc(exc_msg or '')}")

    elif entry.kind == "friday_close":
        count = p.get("expired_count", "?")
        lines.append(f"expired_tokens={_esc(count)}")

    elif entry.kind == "frozen":
        reason = p.get("reason")
        by = p.get("by")
        lines.append("⚠️ <b>Engine frozen — no new opens</b>")
        if reason:
            lines.append(f"reason: {_esc(reason)}")
        if by:
            lines.append(f"by: {_esc(by)}")

    elif entry.kind == "unfrozen":
        lines.append("✅ <b>Engine unfrozen — opens resumed</b>")

    elif entry.kind == "frozen_skip":
        symbol = p.get("symbol", "?")
        side = str(p.get("side", "")).upper()
        lot = p.get("lot", "?")
        lines.append(
            f"skipped <code>{_esc(symbol)} {_esc(side)} {_esc(lot)}</code>"
        )
        setup = p.get("setup")
        if setup:
            lines.append(f"setup={_esc(setup)}")

    elif entry.kind == "skipped_rr_too_low":
        symbol = p.get("symbol", "?")
        side = str(p.get("side", "")).upper()
        lot = p.get("lot", "?")
        lines.append(f"{_esc(symbol)} {_esc(side)} {_esc(lot)} — skipped")
        if p.get("sl_price") is not None or p.get("tp_price") is not None:
            lines.append(_fmt_sl_tp_line(p))
        padded_rr = p.get("padded_rr")
        min_rr = p.get("min_rr")
        if padded_rr is not None and min_rr is not None:
            lines.append(f"R:R {_esc(padded_rr)} &lt; floor {_esc(min_rr)}")
        setup = p.get("setup")
        if setup:
            lines.append(f"setup={_esc(setup)}")

    elif entry.kind == "signal_skipped_position_locked":
        symbol = p.get("symbol", "?")
        side = str(p.get("side", "")).upper()
        lot = p.get("lot", "?")
        lines.append(f"{_esc(symbol)} {_esc(side)} {_esc(lot)} — skipped (locked)")
        reason = p.get("reason")
        if reason:
            lines.append(f"reason={_esc(reason)}")
        existing = p.get("existing_position_id")
        if existing:
            lines.append(f"open pos=<code>{_esc(existing)}</code>")

    elif entry.kind in {
        "padding_unavailable_price_fetch",
        "padding_unavailable_spec_miss",
        "padding_unavailable_exception",
    }:
        symbol = p.get("symbol", "?")
        side = str(p.get("side", "")).upper()
        lot = p.get("lot", "?")
        lines.append(f"{_esc(symbol)} {_esc(side)} {_esc(lot)} — skipped (no padding)")
        err = p.get("exc_msg") or p.get("exc_type")
        if err:
            lines.append(f"reason: {_esc(err)}")

    elif entry.kind == "admin_close_all":
        slug = p.get("slug", "?")
        closed = p.get("positions_closed", 0)
        failed = p.get("positions_failed", 0)
        pnl = p.get("total_pnl")
        lines.append(f"<b>close_all</b> {_esc(slug)}")
        line = f"{_esc(closed)} closed"
        if failed:
            line += f", {_esc(failed)} failed"
        if pnl is not None:
            line += f" · PnL <b>{_fmt_pnl(pnl)}</b>"
        lines.append(line)
        if p.get("by"):
            lines.append(f"by: {_esc(p['by'])}")

    elif entry.kind == "admin_close_position":
        slug = p.get("slug", "?")
        pid = p.get("position_id", "?")
        status = p.get("status", "?")
        pnl = p.get("pnl")
        lines.append(f"<b>close_position</b> {_esc(slug)}")
        line = f"pos=<code>{_esc(pid)}</code> · {_esc(status)}"
        if pnl is not None:
            line += f" · PnL <b>{_fmt_pnl(pnl)}</b>"
        lines.append(line)
        if p.get("by"):
            lines.append(f"by: {_esc(p['by'])}")

    else:
        # Unknown kind — dump a compact payload preview.
        if p:
            preview = ", ".join(f"{k}={_esc(v)}" for k, v in list(p.items())[:4])
            lines.append(preview)

    lines.append(f"<i>[{mode}]</i>")
    return "\n".join(lines)


# Aurum Sniper analysis-post emoji palettes.
_BIAS_EMOJI: dict[str, str] = {"bullish": "🟢", "bearish": "🔴"}
_RISK_EMOJI: dict[str, str] = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def format_analysis_message(payload: "SniperAlertPayload") -> str:
    """Render an Aurum Sniper analysis post as an HTML-parsed Telegram message."""
    bias = str(payload.bias)
    risk = str(payload.risk_level)
    lines = [
        f"🎯 <b>Aurum Sniper</b> — {_esc(payload.symbol)} · {_esc(payload.timeframe)}",
        f"{_BIAS_EMOJI.get(bias, '🎯')} Bias: <b>{_esc(bias.upper())}</b>",
        f"📍 Key level: <code>{_fmt_price(payload.key_level)}</code>",
    ]
    if payload.target_zones:
        zones = "  ".join(
            f"{_esc(z.id)}@{_fmt_price(z.price)}" for z in payload.target_zones
        )
        lines.append(f"🎯 Targets: {zones}")
    lines.append(
        f"{_RISK_EMOJI.get(risk, '')} Risk: {_esc(risk)} · "
        f"Confidence: {_esc(payload.confidence)}%"
    )
    if payload.note:
        lines.append(f"📝 {_esc(payload.note)}")
    return "\n".join(lines)


class TelegramNotifier:
    """Sends formatted IntentLogEntry messages to a Telegram chat.

    Never raises. Network/HTTP errors are logged and swallowed so a misbehaving
    Telegram endpoint can't impact the tick loop.
    """

    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        enabled: bool = True,
        skip_kinds: Iterable[str] | None = None,
        timeout: float = 10.0,
        api_base: str = _API_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token or ""
        self._chat_id = str(chat_id) if chat_id is not None else ""
        self._enabled = bool(enabled and self._token and self._chat_id)
        self._skip = (
            frozenset(skip_kinds) if skip_kinds is not None else _DEFAULT_SKIP_KINDS
        )
        self._timeout = timeout
        self._api_base = api_base.rstrip("/")
        self._client = client  # injectable for tests; None => new client per call

    @property
    def enabled(self) -> bool:
        return self._enabled

    def should_send(self, entry: IntentLogEntry) -> bool:
        return self._enabled and entry.kind not in self._skip

    async def notify(self, entry: IntentLogEntry) -> bool:
        """Send entry to Telegram. Returns True on success, False otherwise.

        Always returns — never raises. Disabled / skipped kinds return False
        without making an HTTP call.
        """
        if not self.should_send(entry):
            return False
        url = f"{self._api_base}/bot{self._token}/sendMessage"
        body = {
            "chat_id": self._chat_id,
            "text": format_message(entry),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=body, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=body)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "telegram notify exception: kind={} exc={}: {}",
                entry.kind,
                type(e).__name__,
                str(e)[:200],
            )
            return False
        if resp.status_code >= 400:
            # Truncate body — Telegram errors are usually short JSON.
            try:
                body_text = resp.text
            except Exception:  # noqa: BLE001
                body_text = ""
            logger.warning(
                "telegram notify HTTP {}: kind={} body={}",
                resp.status_code,
                entry.kind,
                body_text[:200],
            )
            return False
        return True

    async def send_analysis_alert(self, payload: "SniperAlertPayload") -> bool:
        """Send an Aurum Sniper analysis post to Telegram.

        Mirrors notify(): never raises, returns False when disabled or on any
        network/HTTP error. Independent of the per-tick intent skip-kind filter.
        """
        if not self._enabled:
            return False
        url = f"{self._api_base}/bot{self._token}/sendMessage"
        body = {
            "chat_id": self._chat_id,
            "text": format_analysis_message(payload),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=body, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=body)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "telegram analysis alert exception: {}: {}",
                type(e).__name__,
                str(e)[:200],
            )
            return False
        if resp.status_code >= 400:
            try:
                body_text = resp.text
            except Exception:  # noqa: BLE001
                body_text = ""
            logger.warning(
                "telegram analysis alert HTTP {}: body={}",
                resp.status_code,
                body_text[:200],
            )
            return False
        return True
