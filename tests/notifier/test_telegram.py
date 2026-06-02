"""Tests for the Phase 2.6 Telegram notifier.

Covers:
- Disabled-state short-circuit (no HTTP)
- Skip-kind filter (no HTTP)
- Happy path POST shape + URL
- Network exception → False, no raise
- HTTP 4xx/5xx → False, no raise
- format_message renders each intent shape we publish in tick_runner
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from src.engine.intent_bus import IntentLogEntry
from src.notifier.telegram import TelegramNotifier, format_message


def _entry(kind: str, product: str = "gold_ai", payload: dict | None = None,
           dry_run: bool = False) -> IntentLogEntry:
    return IntentLogEntry(
        timestamp=datetime(2026, 6, 2, 5, 0, 0, tzinfo=timezone.utc),
        product=product,
        kind=kind,
        payload=payload or {},
        dry_run=dry_run,
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = '{"ok":true}') -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient — records the last post call."""

    def __init__(self, response: _FakeResponse | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._response = response or _FakeResponse()
        self._raise = raise_exc
        self.last_url: str | None = None
        self.last_body: dict | None = None
        self.call_count = 0

    async def post(self, url: str, json: dict, timeout: float | None = None) -> Any:
        self.call_count += 1
        self.last_url = url
        self.last_body = json
        if self._raise is not None:
            raise self._raise
        return self._response


# ---------------------- enabled / disabled / skip-kinds ----------------------


def test_disabled_when_token_missing() -> None:
    n = TelegramNotifier(token="", chat_id="123", enabled=True)
    assert n.enabled is False


def test_disabled_when_chat_missing() -> None:
    n = TelegramNotifier(token="t", chat_id="", enabled=True)
    assert n.enabled is False


def test_disabled_when_flag_false() -> None:
    n = TelegramNotifier(token="t", chat_id="123", enabled=False)
    assert n.enabled is False


def test_enabled_when_all_set() -> None:
    n = TelegramNotifier(token="t", chat_id="123", enabled=True)
    assert n.enabled is True


def test_should_send_skips_none_by_default() -> None:
    n = TelegramNotifier(token="t", chat_id="123", enabled=True)
    assert n.should_send(_entry("none")) is False
    assert n.should_send(_entry("modify_sl")) is False
    assert n.should_send(_entry("open_executed")) is True


def test_custom_skip_kinds_override_defaults() -> None:
    n = TelegramNotifier(
        token="t", chat_id="123", enabled=True, skip_kinds={"open_executed"}
    )
    assert n.should_send(_entry("none")) is True  # not skipped anymore
    assert n.should_send(_entry("open_executed")) is False


@pytest.mark.asyncio
async def test_notify_returns_false_when_disabled() -> None:
    client = _FakeClient()
    n = TelegramNotifier(token="", chat_id="", enabled=True, client=client)
    result = await n.notify(_entry("open_executed"))
    assert result is False
    assert client.call_count == 0


@pytest.mark.asyncio
async def test_notify_returns_false_for_skip_kind() -> None:
    client = _FakeClient()
    n = TelegramNotifier(token="t", chat_id="123", enabled=True, client=client)
    result = await n.notify(_entry("none"))
    assert result is False
    assert client.call_count == 0


# ---------------------- happy path ----------------------


@pytest.mark.asyncio
async def test_notify_posts_expected_url_and_body() -> None:
    client = _FakeClient()
    n = TelegramNotifier(
        token="111:abc", chat_id="555", enabled=True, client=client
    )
    entry = _entry(
        "open_executed",
        product="gold_ai",
        payload={
            "symbol": "XAUUSD.v",
            "side": "buy",
            "lot": 0.03,
            "sl_price": 2015.0,
            "tp_price": 2025.0,
            "setup": "liquidity_sweep",
            "confidence": 0.78,
            "position_id": "pos-1",
        },
    )
    ok = await n.notify(entry)
    assert ok is True
    assert client.call_count == 1
    assert client.last_url == "https://api.telegram.org/bot111:abc/sendMessage"
    body = client.last_body or {}
    assert body["chat_id"] == "555"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True
    text = body["text"]
    assert "gold_ai" in text
    assert "open_executed" in text
    assert "XAUUSD.v" in text
    assert "BUY" in text
    assert "[LIVE]" in text


@pytest.mark.asyncio
async def test_notify_marks_dryrun_mode() -> None:
    client = _FakeClient()
    n = TelegramNotifier(token="t", chat_id="555", enabled=True, client=client)
    await n.notify(_entry("open", payload={"symbol": "EURUSD.v",
                                           "side": "sell", "lot": 0.02},
                          dry_run=True))
    assert "[DRY-RUN]" in (client.last_body or {}).get("text", "")


# ---------------------- failure modes ----------------------


@pytest.mark.asyncio
async def test_notify_swallows_network_exception() -> None:
    client = _FakeClient(raise_exc=httpx.ConnectError("boom"))
    n = TelegramNotifier(token="t", chat_id="555", enabled=True, client=client)
    ok = await n.notify(_entry("open_executed", payload={"symbol": "X", "side": "buy", "lot": 0.01}))
    assert ok is False  # no raise


@pytest.mark.asyncio
async def test_notify_treats_4xx_as_failure_no_raise() -> None:
    client = _FakeClient(_FakeResponse(status_code=400, text='{"ok":false}'))
    n = TelegramNotifier(token="t", chat_id="555", enabled=True, client=client)
    ok = await n.notify(_entry("open_executed", payload={"symbol": "X", "side": "buy", "lot": 0.01}))
    assert ok is False


@pytest.mark.asyncio
async def test_notify_treats_5xx_as_failure_no_raise() -> None:
    client = _FakeClient(_FakeResponse(status_code=500, text="oops"))
    n = TelegramNotifier(token="t", chat_id="555", enabled=True, client=client)
    ok = await n.notify(_entry("error", payload={"reason": "snapshot_fetch_failed"}))
    assert ok is False


# ---------------------- formatter coverage ----------------------


def test_format_open_executed_renders_key_fields() -> None:
    e = _entry(
        "open_executed",
        payload={
            "symbol": "XAUUSD.v",
            "side": "buy",
            "lot": 0.03,
            "entry_price": 2018.50,
            "sl_price": 2015.0,
            "tp_price": 2025.0,
            "setup": "order_block",
            "confidence": 0.72,
            "position_id": "p-1",
        },
    )
    msg = format_message(e)
    assert "XAUUSD.v" in msg
    assert "BUY" in msg
    assert "0.03" in msg
    assert "2,018.50" in msg or "2018.50" in msg
    assert "order_block" in msg
    assert "conf=0.72" in msg
    assert "p-1" in msg
    assert "[LIVE]" in msg


def test_format_open_failed_shows_error() -> None:
    e = _entry(
        "open_failed",
        payload={"symbol": "EURUSD.v", "side": "sell", "lot": 0.02,
                 "exc_msg": "MetaApi rejected"},
    )
    msg = format_message(e)
    assert "MetaApi rejected" in msg
    assert "EURUSD.v" in msg


def test_format_close_executed_renders_position() -> None:
    e = _entry(
        "close_executed",
        payload={"position_id": "pos-7", "reason": "friday_close"},
    )
    msg = format_message(e)
    assert "pos-7" in msg
    assert "friday_close" in msg


def test_format_trade_closed_renders_pnl_sign() -> None:
    win = _entry("trade_closed", payload={"position_id": "p1", "pnl": 42.55})
    loss = _entry("trade_closed", payload={"position_id": "p2", "pnl": -17.20})
    assert "+42.55" in format_message(win)
    assert "-17.20" in format_message(loss)


def test_format_error_renders_reason_and_exc() -> None:
    e = _entry(
        "error",
        product="multi_cfd_ai",
        payload={
            "reason": "all_snapshots_failed",
            "symbols": ["EURUSD.v", "GBPUSD.v"],
            "exc_type": "TimeoutError",
            "exc_msg": "metaapi timed out",
        },
    )
    msg = format_message(e)
    assert "all_snapshots_failed" in msg
    assert "TimeoutError" in msg
    assert "metaapi timed out" in msg


def test_format_friday_close_renders_count() -> None:
    e = _entry("friday_close", product="scheduler",
               payload={"expired_count": 3})
    msg = format_message(e)
    assert "expired_tokens=3" in msg


def test_format_escapes_html_in_payload() -> None:
    e = _entry(
        "error",
        payload={"reason": "bad <script>", "exc_msg": "x & y > z"},
    )
    msg = format_message(e)
    assert "<script>" not in msg  # raw tag must not appear
    assert "&lt;script&gt;" in msg
    assert "&amp;" in msg


def test_format_unknown_kind_dumps_payload_preview() -> None:
    e = _entry("brand_new_kind", payload={"a": 1, "b": "two"})
    msg = format_message(e)
    assert "brand_new_kind" in msg
    assert "a=1" in msg
    assert "b=two" in msg


# ---------- Phase 6 — freeze/unfreeze formatting ----------


def test_format_frozen_renders_reason_and_by() -> None:
    e = _entry(
        "frozen",
        product="freeze_manager",
        payload={"reason": "manual_kill", "by": "por"},
    )
    msg = format_message(e)
    assert "🧊" in msg
    assert "Engine frozen" in msg
    assert "manual_kill" in msg
    assert "por" in msg


def test_format_unfrozen_renders_banner() -> None:
    e = _entry("unfrozen", product="freeze_manager", payload={})
    msg = format_message(e)
    assert "Engine unfrozen" in msg


def test_format_frozen_skip_renders_trade_details() -> None:
    e = _entry(
        "frozen_skip",
        product="gold_ai",
        payload={
            "symbol": "XAUUSD.v",
            "side": "buy",
            "lot": 0.03,
            "setup": "order_block",
            "reason": "engine_frozen",
        },
    )
    msg = format_message(e)
    assert "⏸️" in msg
    assert "XAUUSD.v" in msg
    assert "BUY" in msg
    assert "order_block" in msg


def test_default_skip_does_not_filter_freeze_kinds() -> None:
    """frozen / unfrozen / frozen_skip must reach Telegram (not in skip set)."""
    n = TelegramNotifier(token="t", chat_id="123", enabled=True)
    assert n.should_send(_entry("frozen")) is True
    assert n.should_send(_entry("unfrozen")) is True
    assert n.should_send(_entry("frozen_skip")) is True
