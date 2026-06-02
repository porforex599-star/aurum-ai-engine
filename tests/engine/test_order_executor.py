from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.engine.order_executor import OrderExecutor
from src.products.models import CloseIntent, IntentKind, ModifySLIntent, TradeIntent
from src.strategy.models import SetupName, SignalSide


def _conn():
    conn = AsyncMock()
    conn.create_market_buy_order = AsyncMock(
        return_value={"orderId": "O1", "positionId": "P1"}
    )
    conn.create_market_sell_order = AsyncMock(
        return_value={"orderId": "O2", "positionId": "P2"}
    )
    conn.close_position = AsyncMock(return_value={"numericCode": 0})
    conn.modify_position = AsyncMock(return_value={"numericCode": 0})
    return conn


def _provider(conn):
    async def _get():
        return conn

    return _get


def _buy_intent() -> TradeIntent:
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


def _sell_intent() -> TradeIntent:
    return TradeIntent(
        kind=IntentKind.OPEN,
        symbol="EURUSD",
        side=SignalSide.SELL,
        lot=0.02,
        entry_price=None,
        sl_price=1.10,
        tp_price=1.08,
        reason="r",
        setup=None,
        confidence=0.7,
    )


@pytest.mark.asyncio
async def test_execute_open_buy_calls_create_market_buy_order() -> None:
    conn = _conn()
    ex = OrderExecutor(_provider(conn))
    result = await ex.execute_open(_buy_intent())
    conn.create_market_buy_order.assert_awaited_once()
    _, kwargs = conn.create_market_buy_order.call_args
    assert kwargs["symbol"] == "XAUUSD"
    assert kwargs["volume"] == 0.03
    assert kwargs["stop_loss"] == 1995.0
    assert kwargs["take_profit"] == 2010.0
    assert result is not None
    assert result["order_id"] == "O1"
    assert result["position_id"] == "P1"
    assert result["side"] == "buy"


@pytest.mark.asyncio
async def test_execute_open_sell_calls_create_market_sell_order() -> None:
    conn = _conn()
    ex = OrderExecutor(_provider(conn))
    result = await ex.execute_open(_sell_intent())
    conn.create_market_sell_order.assert_awaited_once()
    conn.create_market_buy_order.assert_not_called()
    assert result["order_id"] == "O2"


@pytest.mark.asyncio
async def test_execute_open_returns_none_and_sets_last_error_on_exception() -> None:
    conn = _conn()
    conn.create_market_buy_order = AsyncMock(side_effect=RuntimeError("boom"))
    ex = OrderExecutor(_provider(conn))
    result = await ex.execute_open(_buy_intent())
    assert result is None
    assert ex._last_error is not None
    assert ex._last_error["exc_type"] == "RuntimeError"
    assert "boom" in ex._last_error["exc_msg"]


@pytest.mark.asyncio
async def test_execute_close_calls_close_position() -> None:
    conn = _conn()
    ex = OrderExecutor(_provider(conn))
    ok = await ex.execute_close(CloseIntent(IntentKind.CLOSE, "P9", "x", "friday"))
    assert ok is True
    conn.close_position.assert_awaited_once_with(position_id="P9")


@pytest.mark.asyncio
async def test_execute_modify_sl_calls_modify_position_with_stop_loss() -> None:
    conn = _conn()
    ex = OrderExecutor(_provider(conn))
    ok = await ex.execute_modify_sl(
        ModifySLIntent(IntentKind.MODIFY_SL, "P3", 1990.0, "breakeven")
    )
    assert ok is True
    conn.modify_position.assert_awaited_once_with(
        position_id="P3", stop_loss=1990.0
    )
