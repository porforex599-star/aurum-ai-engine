"""Phase 7 Stage 2 — tick_runner routes per product to the right master.

Drives run_tick against a runtime exposing get_bundle_for_product, with each
product on its own bundle (account). Asserts positions/closes are fetched per
account and orders execute on the owning master's executor.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.intent_bus import IntentBus
from src.engine.order_executor import OpenOutcome
from src.engine.signal_lock import SignalLock
from src.products.models import IntentKind, TradeIntent
from src.scheduler.tick_runner import run_tick
from src.strategy.models import MarketSnapshot, SetupName, SignalSide


def _snap(symbol: str) -> MarketSnapshot:
    return MarketSnapshot(symbol=symbol, bars_m15=[], bars_h1=[], bars_h4=[])


def _trade(symbol: str) -> TradeIntent:
    return TradeIntent(
        kind=IntentKind.OPEN,
        symbol=symbol,
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=None,
        sl_price=1.0,
        tp_price=2.0,
        reason="r",
        setup=SetupName.ORDER_BLOCK,
        confidence=0.8,
    )


def _close_detector(positions_sentinel):
    cd = MagicMock()
    cd.detect_closes = MagicMock(return_value=[])
    cd.fetch_deal_info = AsyncMock(return_value=None)
    cd.cleanup_meta = MagicMock()
    cd.update_open = MagicMock()
    return cd


def _bundle(account_id: str, symbol: str, positions: list):
    oe = MagicMock(_last_error=None)
    oe.execute_open_with_padding = AsyncMock(
        return_value=OpenOutcome(
            status="executed", result={"order_id": "o-" + account_id, "position_id": "p"}
        )
    )
    oe.execute_close = AsyncMock(return_value=True)
    oe.execute_modify_sl = AsyncMock(return_value=True)
    return SimpleNamespace(
        account_id=account_id,
        position_poller=SimpleNamespace(fetch_all=AsyncMock(return_value=positions)),
        close_detector=_close_detector(positions),
        snapshot_fetcher=SimpleNamespace(
            fetch=AsyncMock(return_value=_snap(symbol)), _last_error=None
        ),
        order_executor=oe,
    )


def _runtime(bundles: dict[str, SimpleNamespace], *, dry_run=False, multi_symbols=None):
    multi_symbols = multi_symbols or ["EURUSD"]

    async def get_bundle_for_product(slug):
        return bundles[slug]

    rt = SimpleNamespace(
        settings=SimpleNamespace(
            dry_run=dry_run,
            gold_ai_symbol="XAUUSD",
            multi_cfd_ai_symbols=multi_symbols,
            primary_customer_id="cust-1",
        ),
        intent_bus=IntentBus(buffer_size=100),
        position_manager=MagicMock(evaluate_all=MagicMock(return_value=[])),
        signal_lock=SignalLock(cooldown_seconds=300.0),
        token_service=SimpleNamespace(add_trade=AsyncMock()),
        trade_logger=SimpleNamespace(record_closed_trade=AsyncMock()),
        freeze_manager=SimpleNamespace(is_frozen=AsyncMock(return_value=False)),
        get_bundle_for_product=get_bundle_for_product,
        products={},
        last_tick=None,
        last_tick_status=None,
    )
    if "gold_ai" in bundles:
        rt.products["gold_ai"] = SimpleNamespace(
            evaluate=lambda *a, **k: _trade("XAUUSD"),
            record_trade_closed=MagicMock(),
            config=SimpleNamespace(symbols=("XAUUSD",)),
        )
    if "multi_cfd_ai" in bundles:
        rt.products["multi_cfd_ai"] = SimpleNamespace(
            evaluate=lambda *a, **k: _trade("EURUSD"),
            record_trade_closed=MagicMock(),
            config=SimpleNamespace(symbols=tuple(multi_symbols)),
        )
    return rt


@pytest.mark.asyncio
async def test_distinct_masters_poll_and_execute_independently() -> None:
    gold_positions = ["G"]
    mcfd_positions = ["M"]
    gold_b = _bundle("acct-A", "XAUUSD", gold_positions)
    mcfd_b = _bundle("acct-B", "EURUSD", mcfd_positions)
    rt = _runtime({"gold_ai": gold_b, "multi_cfd_ai": mcfd_b}, dry_run=False)

    await run_tick(rt)
    assert rt.last_tick_status == "ok"

    # Each account's positions were polled exactly once.
    gold_b.position_poller.fetch_all.assert_awaited_once()
    mcfd_b.position_poller.fetch_all.assert_awaited_once()

    # Close detection ran per account on that account's positions.
    gold_b.close_detector.detect_closes.assert_called_once_with(gold_positions)
    mcfd_b.close_detector.detect_closes.assert_called_once_with(mcfd_positions)

    # Strategy snapshots came from each product's own master.
    gold_b.snapshot_fetcher.fetch.assert_any_await("XAUUSD")
    mcfd_b.snapshot_fetcher.fetch.assert_any_await("EURUSD")

    # Orders executed on the owning master's executor only.
    gold_b.order_executor.execute_open_with_padding.assert_awaited_once()
    mcfd_b.order_executor.execute_open_with_padding.assert_awaited_once()
    gargs = gold_b.order_executor.execute_open_with_padding.call_args[0][0]
    margs = mcfd_b.order_executor.execute_open_with_padding.call_args[0][0]
    assert gargs.symbol == "XAUUSD"
    assert margs.symbol == "EURUSD"


@pytest.mark.asyncio
async def test_shared_master_polls_once() -> None:
    """Both products on the same account share one bundle → a single poll."""
    shared = _bundle("acct-X", "XAUUSD", [])
    rt = _runtime({"gold_ai": shared, "multi_cfd_ai": shared}, dry_run=False)

    await run_tick(rt)
    assert rt.last_tick_status == "ok"
    # Deduped by account_id → fetched once even with two products.
    shared.position_poller.fetch_all.assert_awaited_once()
    shared.close_detector.detect_closes.assert_called_once()
    # Both products still evaluated + executed.
    assert shared.order_executor.execute_open_with_padding.await_count == 2
