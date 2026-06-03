from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.intent_bus import IntentBus
from src.engine.order_executor import OpenOutcome
from src.products.models import CloseIntent, IntentKind, TradeIntent
from src.scheduler.tick_runner import run_tick
from src.strategy.models import MarketSnapshot, SetupName, SignalSide


def _close_detector(closed_ids=None, deal=None):
    cd = MagicMock()
    cd.detect_closes = MagicMock(return_value=closed_ids or [])
    cd.fetch_deal_info = AsyncMock(return_value=deal)
    cd.cleanup_meta = MagicMock()
    cd.update_open = MagicMock()
    return cd


def _runtime(
    *,
    gold_evaluate=None,
    mcfd_evaluate=None,
    positions=None,
    snapshot=None,
    fetch_side_effect=None,
    multi_symbols=None,
    dry_run=True,
    order_executor=None,
    close_detector=None,
    token_service=None,
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

    if order_executor is None:
        order_executor = MagicMock(_last_error=None)
        order_executor.execute_open = AsyncMock(return_value={"order_id": "o1"})
        order_executor.execute_open_with_padding = AsyncMock(
            return_value=OpenOutcome(status="executed", result={"order_id": "o1"})
        )
        order_executor.execute_close = AsyncMock(return_value=True)
        order_executor.execute_modify_sl = AsyncMock(return_value=True)

    if token_service is None:
        token_service = SimpleNamespace(add_trade=AsyncMock())

    rt = SimpleNamespace(
        settings=SimpleNamespace(
            dry_run=dry_run,
            gold_ai_symbol="XAUUSD",
            multi_cfd_ai_symbols=multi_symbols or ["EURUSD"],
            primary_customer_id="cust-1",
        ),
        intent_bus=bus,
        position_poller=SimpleNamespace(
            fetch_all=AsyncMock(return_value=positions or [])
        ),
        snapshot_fetcher=fetcher,
        position_manager=MagicMock(evaluate_all=MagicMock(return_value=[])),
        order_executor=order_executor,
        close_detector=close_detector or _close_detector(),
        token_service=token_service,
        products={},
        last_tick=None,
        last_tick_status=None,
    )
    if gold_evaluate is not None:
        rt.products["gold_ai"] = SimpleNamespace(
            evaluate=gold_evaluate, record_trade_closed=MagicMock()
        )
    if mcfd_evaluate is not None:
        rt.products["multi_cfd_ai"] = SimpleNamespace(
            evaluate=mcfd_evaluate, record_trade_closed=MagicMock()
        )
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


@pytest.mark.asyncio
async def test_dry_run_trade_intent_publishes_open_without_executing() -> None:
    rt = _runtime(gold_evaluate=lambda *a, **k: _trade_intent(), dry_run=True)
    await run_tick(rt)
    items = rt.intent_bus.recent(50)
    opens = [i for i in items if i.product == "gold_ai" and i.kind == "open"]
    assert len(opens) == 1
    rt.order_executor.execute_open.assert_not_called()


@pytest.mark.asyncio
async def test_live_trade_intent_executes_open_and_publishes_executed() -> None:
    rt = _runtime(gold_evaluate=lambda *a, **k: _trade_intent(), dry_run=False)
    rt.order_executor.execute_open_with_padding = AsyncMock(
        return_value=OpenOutcome(
            status="executed",
            result={"order_id": "ORD-123", "position_id": "POS-9"},
        )
    )
    await run_tick(rt)
    rt.order_executor.execute_open_with_padding.assert_awaited_once()
    items = rt.intent_bus.recent(50)
    executed = [
        i for i in items if i.product == "gold_ai" and i.kind == "open_executed"
    ]
    assert len(executed) == 1
    assert executed[0].payload["order_id"] == "ORD-123"


@pytest.mark.asyncio
async def test_position_close_detected_live_calls_add_trade() -> None:
    deal = {
        "position_id": "P1",
        "symbol": "XAUUSD",
        "pnl": 42.5,
        "opened_at": datetime.now(timezone.utc),
        "closed_at": datetime.now(timezone.utc),
    }
    cd = _close_detector(closed_ids=["P1"], deal=deal)
    token_service = SimpleNamespace(
        add_trade=AsyncMock(
            return_value=SimpleNamespace(
                ok=True, expired=False, expiry_reason=None
            )
        )
    )
    rt = _runtime(
        gold_evaluate=lambda *a, **k: None,
        dry_run=False,
        close_detector=cd,
        token_service=token_service,
    )
    await run_tick(rt)
    token_service.add_trade.assert_awaited_once()
    _, kwargs = token_service.add_trade.call_args
    assert kwargs["product_code"] == "ai_xaupro"
    assert kwargs["pnl"] == 42.5
    assert kwargs["metaapi_position_id"] == "P1"
    items = rt.intent_bus.recent(50)
    assert any(i.kind == "trade_closed" for i in items)
