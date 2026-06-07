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
from src.notifier.telegram import (
    TelegramNotifier,
    format_analysis_message,
    format_message,
)
from src.schemas.sniper import SniperAlertPayload


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


def test_format_open_executed_shows_padded_from_annotation() -> None:
    e = _entry(
        "open_executed",
        product="multi_cfd_ai",
        payload={
            "symbol": "EURUSD.v",
            "side": "buy",
            "lot": 0.02,
            "entry_price": 1.16000,
            "sl_price": 1.15980,
            "tp_price": 1.16210,
            "sl_original": 1.16000,
            "tp_original": 1.16210,
            "setup": "mean_reversion",
            "confidence": 0.65,
        },
    )
    msg = format_message(e)
    assert "padded from" in msg
    # SL was adjusted (1.16000 -> 1.15980) so its annotation must appear.
    assert "1.1600" in msg


def test_format_skipped_rr_too_low_renders_reason() -> None:
    e = _entry(
        "skipped_rr_too_low",
        product="multi_cfd_ai",
        payload={
            "symbol": "SP500.v",
            "side": "buy",
            "lot": 0.02,
            "sl_price": 7599.0,
            "tp_price": 7621.0,
            "sl_original": 7603.0,
            "tp_original": 7617.0,
            "padded_rr": 1.0,
            "min_rr": 1.2,
            "setup": "mean_reversion",
        },
    )
    msg = format_message(e)
    assert "🚫" in msg
    assert "SP500.v" in msg
    assert "skipped" in msg
    assert "mean_reversion" in msg


def test_skipped_rr_too_low_is_not_skipped_by_default() -> None:
    n = TelegramNotifier(token="t", chat_id="123", enabled=True)
    assert n.should_send(_entry("skipped_rr_too_low")) is True


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


# --- Aurum Sniper analysis alerts -------------------------------------------


def _analysis_payload(**overrides) -> SniperAlertPayload:
    data = {
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "bias": "bullish",
        "key_level": 2345.67,
        "target_zones": [{"id": "Z1", "price": 2350.0}],
        "risk_level": "medium",
        "confidence": 85,
        "note": "ทดสอบ",
    }
    data.update(overrides)
    return SniperAlertPayload(**data)


def test_format_analysis_message_renders_fields() -> None:
    msg = format_analysis_message(_analysis_payload())
    assert "Aurum Sniper" in msg
    assert "XAUUSD" in msg
    assert "M5" in msg
    assert "BULLISH" in msg
    assert "Z1@" in msg
    assert "85%" in msg
    assert "ทดสอบ" in msg


def test_format_analysis_message_renders_invalidation_and_rr_when_present() -> None:
    msg = format_analysis_message(
        _analysis_payload(invalidation_price=2330.5, rr_ratio=2.8)
    )
    assert "Invalidation" in msg
    assert "2,330.50" in msg or "2330.50" in msg
    assert "R:R" in msg
    assert "2.8" in msg


def test_format_analysis_message_omits_invalidation_and_rr_when_absent() -> None:
    msg = format_analysis_message(_analysis_payload())
    assert "Invalidation" not in msg
    assert "R:R" not in msg
    assert "N/A" not in msg


def test_format_analysis_message_renders_phase4_counts_when_present() -> None:
    msg = format_analysis_message(
        _analysis_payload(
            pattern_markers=[
                {"time": 1717689600, "kind": "3ls_bull", "price": 2346.0},
                {"time": 1717693200, "kind": "engulf_bull", "price": 2347.5},
            ],
            sd_zones=[
                {"tf": "2H", "type": "demand", "high": 2344.0, "low": 2340.0},
                {"tf": "30M", "type": "supply", "high": 2360.0, "low": 2358.0},
                {"tf": "5M", "type": "demand", "high": 2342.0, "low": 2341.0},
            ],
        )
    )
    assert "Patterns: 2" in msg
    assert "Zones: 3" in msg


def test_format_analysis_message_omits_phase4_counts_when_absent() -> None:
    msg = format_analysis_message(_analysis_payload())
    assert "Patterns:" not in msg
    assert "Zones:" not in msg


def test_format_analysis_message_renders_deeplink_when_post_id_present() -> None:
    msg = format_analysis_message(_analysis_payload(), post_id="abc-123")
    assert (
        "🔗 ดู chart สดๆ: https://aurum-signals-ecru.vercel.app/room?post_id=abc-123"
        in msg
    )


def test_format_analysis_message_deeplink_precedes_phase4_counts() -> None:
    msg = format_analysis_message(
        _analysis_payload(
            pattern_markers=[{"time": 1717689600, "kind": "3ls_bull", "price": 2346.0}],
            sd_zones=[{"tf": "2H", "type": "demand", "high": 2344.0, "low": 2340.0}],
        ),
        post_id="abc-123",
    )
    assert msg.index("🔗 ดู chart สดๆ") < msg.index("Patterns:")
    assert msg.index("🔗 ดู chart สดๆ") < msg.index("Zones:")


def test_format_analysis_message_omits_deeplink_when_post_id_absent() -> None:
    msg = format_analysis_message(_analysis_payload())
    assert "🔗" not in msg
    assert "/room?post_id=" not in msg


@pytest.mark.asyncio
async def test_send_analysis_alert_happy_path() -> None:
    fake = _FakeClient(_FakeResponse(200))
    n = TelegramNotifier(token="t", chat_id="123", enabled=True, client=fake)
    ok = await n.send_analysis_alert(_analysis_payload())
    assert ok is True
    assert fake.call_count == 1
    assert "/bott/sendMessage" in fake.last_url
    assert fake.last_body["parse_mode"] == "HTML"
    assert fake.last_body["chat_id"] == "123"


@pytest.mark.asyncio
async def test_send_analysis_alert_includes_deeplink_with_post_id() -> None:
    fake = _FakeClient(_FakeResponse(200))
    n = TelegramNotifier(token="t", chat_id="123", enabled=True, client=fake)
    ok = await n.send_analysis_alert(_analysis_payload(), post_id="post-42")
    assert ok is True
    assert "/room?post_id=post-42" in fake.last_body["text"]


@pytest.mark.asyncio
async def test_send_analysis_alert_disabled_makes_no_call() -> None:
    fake = _FakeClient(_FakeResponse(200))
    n = TelegramNotifier(token="", chat_id="", enabled=False, client=fake)
    ok = await n.send_analysis_alert(_analysis_payload())
    assert ok is False
    assert fake.call_count == 0


@pytest.mark.asyncio
async def test_send_analysis_alert_http_error_returns_false() -> None:
    fake = _FakeClient(_FakeResponse(500, text="boom"))
    n = TelegramNotifier(token="t", chat_id="123", enabled=True, client=fake)
    ok = await n.send_analysis_alert(_analysis_payload())
    assert ok is False


@pytest.mark.asyncio
async def test_send_analysis_alert_swallows_exceptions() -> None:
    fake = _FakeClient(raise_exc=RuntimeError("network down"))
    n = TelegramNotifier(token="t", chat_id="123", enabled=True, client=fake)
    ok = await n.send_analysis_alert(_analysis_payload())
    assert ok is False
