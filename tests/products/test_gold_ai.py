from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.products.gold_ai import GoldAIProduct
from src.products.models import CloseIntent, IntentKind, OpenPosition, TradeIntent
from src.strategy.models import (
    MarketSnapshot,
    SetupName,
    Signal,
    SignalSide,
)

BKK = ZoneInfo("Asia/Bangkok")


def _bkk(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BKK).astimezone(timezone.utc)


def _empty_snap() -> MarketSnapshot:
    return MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=[], bars_h4=[])


def _signal() -> Signal:
    return Signal(
        setup=SetupName.LIQUIDITY_SWEEP,
        side=SignalSide.BUY,
        entry_price=2000.0,
        sl_price=1995.0,
        tp_price=2010.0,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_within_trading_hours_mon_morning() -> None:
    g = GoldAIProduct("cust", "w1")
    # Monday Jan 5, 2026 at 07:00 BKK
    now = _bkk(2026, 1, 5, 7, 0)
    assert g.is_within_trading_hours(now) is True


def test_outside_trading_hours_mon_early() -> None:
    g = GoldAIProduct("cust", "w1")
    now = _bkk(2026, 1, 5, 5, 0)
    assert g.is_within_trading_hours(now) is False


def test_outside_trading_hours_saturday() -> None:
    g = GoldAIProduct("cust", "w1")
    # Saturday Jan 3, 2026 at 12:00 BKK
    now = _bkk(2026, 1, 3, 12, 0)
    assert g.is_within_trading_hours(now) is False


def test_friday_close_time_at_17() -> None:
    g = GoldAIProduct("cust", "w1")
    # Friday Jan 2, 2026 at 17:00 BKK
    now = _bkk(2026, 1, 2, 17, 0)
    assert g.is_friday_close_time(now) is True


def test_friday_close_time_at_16() -> None:
    g = GoldAIProduct("cust", "w1")
    now = _bkk(2026, 1, 2, 16, 0)
    assert g.is_friday_close_time(now) is False


def test_evaluate_returns_none_outside_trading_hours() -> None:
    g = GoldAIProduct("cust", "w1")
    now = _bkk(2026, 1, 3, 12, 0)  # Saturday
    assert g.evaluate(_empty_snap(), [], now) is None


def test_evaluate_returns_close_intents_on_friday_close() -> None:
    g = GoldAIProduct("cust", "w1")
    pos = OpenPosition(
        position_id="p1",
        symbol="XAUUSD",
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=2000.0,
        current_price=2010.0,
        current_pnl_usd=10.0,
        current_sl=None,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    now = _bkk(2026, 1, 2, 17, 30)  # Fri 17:30 BKK
    result = g.evaluate(_empty_snap(), [pos], now)
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], CloseIntent)
    assert result[0].code == "friday_close"


def test_evaluate_returns_close_intents_on_week_target_hit() -> None:
    g = GoldAIProduct("cust", "w1")
    g.week_tracker.record_pnl_delta(100.0)  # > target_win_usd=95
    pos = OpenPosition(
        position_id="p1",
        symbol="XAUUSD",
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=2000.0,
        current_price=2010.0,
        current_pnl_usd=10.0,
        current_sl=None,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    now = _bkk(2026, 1, 5, 10, 0)  # Mon during hours
    result = g.evaluate(_empty_snap(), [pos], now)
    assert isinstance(result, list)
    assert result[0].code == "week_target_win"


def test_evaluate_returns_none_when_max_positions_hit() -> None:
    g = GoldAIProduct("cust", "w1")
    pos = OpenPosition(
        position_id="p1",
        symbol="XAUUSD",
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=2000.0,
        current_price=2010.0,
        current_pnl_usd=10.0,
        current_sl=None,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    now = _bkk(2026, 1, 5, 10, 0)
    assert g.evaluate(_empty_snap(), [pos], now) is None


def test_evaluate_returns_trade_intent_when_signal_fires() -> None:
    g = GoldAIProduct("cust", "w1")
    g.strategy.evaluate = lambda snap: _signal()  # type: ignore[assignment]
    now = _bkk(2026, 1, 5, 10, 0)
    result = g.evaluate(_empty_snap(), [], now)
    assert isinstance(result, TradeIntent)
    assert result.kind == IntentKind.OPEN
    assert result.symbol == "XAUUSD"
    assert result.lot == 0.03
    assert result.side == SignalSide.BUY


def test_symbol_override_propagates_to_config_and_intent() -> None:
    g = GoldAIProduct("cust", "w1", symbol="XAUUSD.v")
    assert g.config.symbols == ("XAUUSD.v",)
    g.strategy.evaluate = lambda snap: _signal()  # type: ignore[assignment]
    now = _bkk(2026, 1, 5, 10, 0)
    result = g.evaluate(_empty_snap(), [], now)
    assert result is not None
    # TradeIntent should carry the broker-specific symbol
    assert result.symbol == "XAUUSD.v"  # type: ignore[union-attr]


def test_evaluate_returns_none_when_daily_loss_limit_hit() -> None:
    g = GoldAIProduct("cust", "w1")
    now = _bkk(2026, 1, 5, 10, 0)
    # Align DayTracker date with the test 'now' so maybe_reset doesn't wipe state.
    g.day_tracker.state.date = now.astimezone(BKK).date()
    g.day_tracker.record_trade_close(-60.0)
    g.strategy.evaluate = lambda snap: _signal()  # type: ignore[assignment]
    assert g.evaluate(_empty_snap(), [], now) is None
