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


def _sell_intent_with(sl, tp, symbol="SP500.v") -> TradeIntent:
    return TradeIntent(
        kind=IntentKind.OPEN,
        symbol=symbol,
        side=SignalSide.SELL,
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
    conn = _padding_conn(ask=1.16000, bid=1.15998)
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    # SL only 5 points away; TP far enough that R:R stays healthy.
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=1.15995, tp=1.16500)
    )
    assert outcome.status == "executed"
    _, kwargs = conn.create_market_buy_order.call_args
    # BUY SL is measured from the bid (close side): min_distance =
    # (10 + 10) * 0.00001 = 0.0002 → padded SL = bid - 0.0002 = 1.15978.
    assert kwargs["stop_loss"] == pytest.approx(1.15978)
    assert kwargs["take_profit"] == pytest.approx(1.16500)
    assert outcome.padding["adjusted"] is True
    assert outcome.padding["sl_original"] == 1.15995
    # entry_price recorded on the trade is the fill price (ask for a BUY).
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


# -------- Round 2: spread-aware anchoring (SP500 "Invalid stops" fix) ---------
# SP500.v: stopsLevel=100, point=0.1 → broker minimum = 10.0 index points,
# measured from the CLOSE side (bid for BUY, ask for SELL). With a real index
# spread, anchoring to the fill side left the SL within stopsLevel of the close
# side by the spread, which the broker rejected as "Invalid stops".


@pytest.mark.asyncio
async def test_buy_sl_clears_stops_level_measured_from_bid() -> None:
    # 3-point spread; tight mean_reversion BUY SL ~7 points below fill.
    conn = _padding_conn(ask=7590.0, bid=7587.0, stops_level=100, point=0.1, digits=1)
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=7583.0, tp=7620.0, symbol="SP500.v")
    )
    assert outcome.status == "executed"
    _, kwargs = conn.create_market_buy_order.call_args
    sl = kwargs["stop_loss"]
    # Broker rule: (bid - SL) >= stopsLevel * point = 10.0. The fill-anchored
    # value (7590 - 11 = 7579) would only be 8.0 from the bid → rejected.
    assert 7587.0 - sl >= 10.0
    assert sl == pytest.approx(7576.0)  # bid - (100+10)*0.1
    assert outcome.padding["adjusted"] is True
    # Recorded entry is the fill price (ask for a BUY).
    assert outcome.padding["entry_price"] == pytest.approx(7590.0)


@pytest.mark.asyncio
async def test_sell_sl_clears_stops_level_measured_from_ask() -> None:
    # 3-point spread; tight mean_reversion SELL SL ~7 points above fill.
    conn = _padding_conn(ask=7590.0, bid=7587.0, stops_level=100, point=0.1, digits=1)
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    outcome = await ex.execute_open_with_padding(
        _sell_intent_with(sl=7594.0, tp=7560.0, symbol="SP500.v")
    )
    assert outcome.status == "executed"
    _, kwargs = conn.create_market_sell_order.call_args
    sl = kwargs["stop_loss"]
    # Broker rule for a SELL: (SL - ask) >= 10.0, measured from the ask.
    assert sl - 7590.0 >= 10.0
    assert sl == pytest.approx(7601.0)  # ask + (100+10)*0.1
    assert outcome.padding["adjusted"] is True
    # Recorded entry is the fill price (bid for a SELL).
    assert outcome.padding["entry_price"] == pytest.approx(7587.0)


@pytest.mark.asyncio
async def test_wide_index_stops_unchanged_no_regression() -> None:
    # trend_continuation-style wide stops already clear stopsLevel from the
    # close side → padding must leave them untouched (no behavior change).
    conn = _padding_conn(ask=7590.0, bid=7587.0, stops_level=100, point=0.1, digits=1)
    ex = OrderExecutor(_provider(conn), spec_cache=_spec_cache(conn))
    outcome = await ex.execute_open_with_padding(
        _buy_intent_with(sl=7550.0, tp=7700.0, symbol="NAS100.v")
    )
    assert outcome.status == "executed"
    _, kwargs = conn.create_market_buy_order.call_args
    assert kwargs["stop_loss"] == pytest.approx(7550.0)
    assert kwargs["take_profit"] == pytest.approx(7700.0)
    assert outcome.padding["adjusted"] is False


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
