from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.engine.order_executor import OrderExecutor
from src.engine.symbol_spec_cache import SymbolSpecCache
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


# ---------------------- Phase 2.6.2 — padding-aware open ----------------------


def _padding_conn(*, ask=1.16000, bid=1.15998, stops_level=10, point=0.00001, digits=5):
    conn = _conn()
    conn.get_symbol_price = AsyncMock(return_value={"ask": ask, "bid": bid})
    conn.get_symbol_specification = AsyncMock(
        return_value={
            "stopsLevel": stops_level,
            "freezeLevel": 0,
            "point": point,
            "digits": digits,
        }
    )
    return conn


def _spec_cache(conn):
    return SymbolSpecCache(_provider(conn), ttl_seconds=300.0, time_fn=lambda: 0.0)


def _buy_intent_with(sl, tp, symbol="EURUSD.v") -> TradeIntent:
    return TradeIntent(
        kind=IntentKind.OPEN,
        symbol=symbol,
        side=SignalSide.BUY,
        lot=0.02,
        entry_price=None,
        sl_price=sl,
        tp_price=tp,
        reason="r",
        setup=SetupName.MEAN_REVERSION,
        confidence=0.65,
    )


@pytest.mark.asyncio
async def test_padding_widens_tight_buy_stops() -> None:
    conn = _padding_conn(ask=1.16000)
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    # SL only 5 points away; TP far enough that R:R stays healthy.
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.15995, tp=1.16500)
    )
    assert outcome.status == "executed"
    _, kwargs = conn.create_market_buy_order.call_args
    # min_distance = (10 + 10) * 0.00001 = 0.0002 → padded SL = 1.1598
    assert kwargs["stop_loss"] == pytest.approx(1.15980)
    assert kwargs["take_profit"] == pytest.approx(1.16500)
    assert outcome.padding["adjusted"] is True
    assert outcome.padding["sl_original"] == 1.15995
    assert outcome.padding["entry_price"] == pytest.approx(1.16000)


@pytest.mark.asyncio
async def test_padding_skips_when_rr_too_low() -> None:
    conn = _padding_conn(ask=1.16000)
    ex = OrderExecutor(
        _provider(conn), spec_cache=_spec_cache(conn), min_padded_rr=1.2
    )
    # Both SL and TP within minimum → both widened to 20 points → R:R ~1.0.
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.15995, tp=1.16010)
    )
    assert outcome.status == "skipped_rr_too_low"
    assert outcome.reason == "rr_too_low"
    conn.create_market_buy_order.assert_not_called()
    assert outcome.padding["padded_rr"] < 1.2


@pytest.mark.asyncio
async def test_padding_leaves_wide_stops_unchanged() -> None:
    conn = _padding_conn(ask=1.16000)
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.15000, tp=1.18000)
    )
    assert outcome.status == "executed"
    _, kwargs = conn.create_market_buy_order.call_args
    assert kwargs["stop_loss"] == pytest.approx(1.15000)
    assert kwargs["take_profit"] == pytest.approx(1.18000)
    assert outcome.padding["adjusted"] is False


@pytest.mark.asyncio
async def test_padding_falls_back_to_raw_without_spec_cache() -> None:
    conn = _padding_conn(ask=1.16000)
    ex = OrderExecutor(_provider(conn), spec_cache=None)
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.15995, tp=1.16500)
    )
    assert outcome.status == "executed"
    _, kwargs = conn.create_market_buy_order.call_args
    # No padding → raw values sent.
    assert kwargs["stop_loss"] == pytest.approx(1.15995)


@pytest.mark.asyncio
async def test_padding_skips_when_price_fetch_fails() -> None:
    conn = _padding_conn()
    conn.get_symbol_price = AsyncMock(side_effect=RuntimeError("no price"))
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.15995, tp=1.16500)
    )
    # No raw placement — broker would just reject "Invalid stops" anyway.
    assert outcome.status == "skipped_padding_unavailable"
    assert outcome.reason == "padding_unavailable_price_fetch"
    conn.create_market_buy_order.assert_not_called()


@pytest.mark.asyncio
async def test_padding_skips_when_spec_fetch_fails() -> None:
    conn = _padding_conn(ask=1.16000)
    conn.get_symbol_specification = AsyncMock(side_effect=RuntimeError("no spec"))
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.15995, tp=1.16500)
    )
    assert outcome.status == "skipped_padding_unavailable"
    assert outcome.reason == "padding_unavailable_spec_miss"
    conn.create_market_buy_order.assert_not_called()


@pytest.mark.asyncio
async def test_padding_fails_on_inverted_geometry() -> None:
    conn = _padding_conn(ask=1.16000)
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    # Live price drifted above the SL → SL now above entry for a BUY.
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.16100, tp=1.16500)
    )
    assert outcome.status == "failed"
    assert outcome.error["exc_type"] == "ValueError"
    conn.create_market_buy_order.assert_not_called()
