from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.intent_bus import IntentBus
from src.products.models import CloseIntent, IntentKind, TradeIntent
from src.scheduler.tick_runner import run_tick
from src.strategy.models import MarketSnapshot, SetupName, SignalSide


def _runtime(
    *,
    gold_evaluate=None,
    mcfd_evaluate=None,
    positions=None,
    snapshot=None,
    fetch_side_effect=None,
    multi_symbols=None,
):
    snap = snapshot or MarketSnapshot(
        symbol="XAUUSD", bars_m15=[], bars_h1=[], bars_h4=[]
    )
    bus = IntentBus(buffer_size=100)
    fetcher = SimpleNamespace(_last_error=None)
    if fetch_side_effect is not None:
        fetcher.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        fetcher.fetch = AsyncMock(return_value=snap)

    rt = SimpleNamespace(
        settings=SimpleNamespace(
            dry_run=True,
            gold_ai_symbol="XAUUSD",
            multi_cfd_ai_symbols=multi_symbols or ["EURUSD"],
        ),
        intent_bus=bus,
        position_poller=SimpleNamespace(
            fetch_all=AsyncMock(return_value=positions or [])
        ),
        snapshot_fetcher=fetcher,
        position_manager=MagicMock(evaluate_all=MagicMock(return_value=[])),
        products={},
        last_tick=None,
        last_tick_status=None,
    )
    if gold_evaluate is not None:
        rt.products["gold_ai"] = SimpleNamespace(evaluate=gold_evaluate)
    if mcfd_evaluate is not None:
        rt.products["multi_cfd_ai"] = SimpleNamespace(evaluate=mcfd_evaluate)
    return rt


def _trade_intent() -> TradeIntent:
    return TradeIntent(
        kind=IntentKind.OPEN,
        symbol="XAUUSD",
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=None,
        sl_price=1995.0,
        tp_price=2010.0,
        reason="r",
        setup=SetupName.ORDER_BLOCK,
        confidence=0.8,
    )


@pytest.mark.asyncio
async def test_run_tick_updates_last_tick() -> None:
    rt = _runtime(gold_evaluate=lambda *a, **k: None)
    await run_tick(rt)
    assert rt.last_tick is not None
    assert rt.last_tick_status == "ok"


@pytest.mark.asyncio
async def test_run_tick_publishes_none_when_no_signal() -> None:
    rt = _runtime(gold_evaluate=lambda *a, **k: None)
    await run_tick(rt)
    items = rt.intent_bus.recent(50)
    assert any(i.product == "gold_ai" and i.kind == "none" for i in items)


@pytest.mark.asyncio
async def test_run_tick_publishes_open_for_trade_intent() -> None:
    rt = _runtime(gold_evaluate=lambda *a, **k: _trade_intent())
    await run_tick(rt)
    items = rt.intent_bus.recent(50)
    opens = [i for i in items if i.product == "gold_ai" and i.kind == "open"]
    assert len(opens) == 1
    assert opens[0].payload["symbol"] == "XAUUSD"


@pytest.mark.asyncio
async def test_run_tick_publishes_close_intents_when_list_returned() -> None:
    closes = [
        CloseIntent(IntentKind.CLOSE, "p1", "x", "friday_close"),
        CloseIntent(IntentKind.CLOSE, "p2", "x", "friday_close"),
    ]
    rt = _runtime(gold_evaluate=lambda *a, **k: closes)
    await run_tick(rt)
    items = rt.intent_bus.recent(50)
    close_items = [i for i in items if i.product == "gold_ai" and i.kind == "close"]
    assert len(close_items) == 2


@pytest.mark.asyncio
async def test_run_tick_sets_error_status_on_exception() -> None:
    rt = _runtime(gold_evaluate=lambda *a, **k: None)
    rt.position_poller.fetch_all = AsyncMock(side_effect=RuntimeError("kaboom"))
    await run_tick(rt)
    assert rt.last_tick_status is not None
    assert rt.last_tick_status.startswith("error")


@pytest.mark.asyncio
async def test_run_tick_snapshot_failure_includes_exc_info() -> None:
    rt = _runtime(gold_evaluate=lambda *a, **k: None, fetch_side_effect=None)
    rt.snapshot_fetcher.fetch = AsyncMock(return_value=None)
    rt.snapshot_fetcher._last_error = {
        "exc_type": "AttributeError",
        "exc_msg": "no such method",
    }
    await run_tick(rt)
    items = rt.intent_bus.recent(50)
    err = next(i for i in items if i.product == "gold_ai" and i.kind == "error")
    assert err.payload["exc_type"] == "AttributeError"
    assert err.payload["exc_msg"] == "no such method"
    assert err.payload["symbol"] == "XAUUSD"


@pytest.mark.asyncio
async def test_multi_cfd_all_snapshots_failed_publishes_summary_error() -> None:
    rt = _runtime(
        mcfd_evaluate=lambda *a, **k: [],
        multi_symbols=["EURUSD", "GBPUSD"],
    )
    # gold_ai not registered (no gold_evaluate)
    rt.snapshot_fetcher.fetch = AsyncMock(return_value=None)
    rt.snapshot_fetcher._last_error = {"exc_type": "RuntimeError", "exc_msg": "x"}
    await run_tick(rt)
    items = rt.intent_bus.recent(50)
    err = next(
        i for i in items if i.product == "multi_cfd_ai" and i.kind == "error"
    )
    assert err.payload["reason"] == "all_snapshots_failed"
    assert err.payload["symbols"] == ["EURUSD", "GBPUSD"]
    assert err.payload["exc_type"] == "RuntimeError"
