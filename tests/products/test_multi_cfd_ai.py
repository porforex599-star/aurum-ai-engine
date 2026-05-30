from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.products.models import CloseIntent, OpenPosition, TradeIntent
from src.products.multi_cfd_ai import MultiCfdAIProduct
from src.strategy.models import (
    MarketSnapshot,
    SetupName,
    Signal,
    SignalSide,
)

BKK = ZoneInfo("Asia/Bangkok")


def _bkk(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BKK).astimezone(timezone.utc)


def _snap(sym: str) -> MarketSnapshot:
    return MarketSnapshot(symbol=sym, bars_m15=[], bars_h1=[], bars_h4=[])


def _sig(side: SignalSide, conf: float) -> Signal:
    return Signal(
        setup=SetupName.ORDER_BLOCK,
        side=side,
        entry_price=100.0,
        sl_price=99.0,
        tp_price=102.0,
        confidence=conf,
        reason="m",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_returns_empty_on_saturday() -> None:
    p = MultiCfdAIProduct("cust", "w1")
    now = _bkk(2026, 1, 3, 12, 0)  # Saturday
    result = p.evaluate({"EURUSD": _snap("EURUSD")}, [], now)
    assert result == []


def test_returns_top_n_intents_ranked_by_confidence() -> None:
    p = MultiCfdAIProduct("cust", "w1")
    sigs = {
        "EURUSD": _sig(SignalSide.BUY, 0.60),
        "GBPUSD": _sig(SignalSide.SELL, 0.85),
        "USDJPY": _sig(SignalSide.BUY, 0.70),
        "US500": _sig(SignalSide.BUY, 0.55),
    }
    p.strategy.evaluate = lambda snap: sigs.get(snap.symbol)  # type: ignore[assignment]
    snapshots = {s: _snap(s) for s in sigs.keys()}
    now = _bkk(2026, 1, 5, 12, 0)
    result = p.evaluate(snapshots, [], now)
    assert all(isinstance(r, TradeIntent) for r in result)
    assert len(result) == 3  # max_positions
    assert [r.symbol for r in result] == ["GBPUSD", "USDJPY", "EURUSD"]


def test_skips_symbols_not_in_config() -> None:
    p = MultiCfdAIProduct("cust", "w1")
    p.strategy.evaluate = lambda snap: _sig(SignalSide.BUY, 0.9)  # type: ignore[assignment]
    snapshots = {"BTCUSD": _snap("BTCUSD"), "EURUSD": _snap("EURUSD")}
    result = p.evaluate(snapshots, [], _bkk(2026, 1, 5, 12, 0))
    assert [r.symbol for r in result] == ["EURUSD"]


def test_skips_xau_xag_defensively() -> None:
    p = MultiCfdAIProduct("cust", "w1")
    # Force XAUUSD into snapshots (it's not in config so it's already skipped, but
    # also exercise the explicit guard by adding it to the symbol set).
    p.config = p.config.__class__(
        product=p.config.product,
        symbols=p.config.symbols + ("XAUUSD", "XAGUSD"),
        lot=p.config.lot,
        max_positions=p.config.max_positions,
        trading_hours=p.config.trading_hours,
        risk_params=p.config.risk_params,
    )
    p.strategy.evaluate = lambda snap: _sig(SignalSide.BUY, 0.9)  # type: ignore[assignment]
    snapshots = {"XAUUSD": _snap("XAUUSD"), "XAGUSD": _snap("XAGUSD"), "EURUSD": _snap("EURUSD")}
    result = p.evaluate(snapshots, [], _bkk(2026, 1, 5, 12, 0))
    assert [r.symbol for r in result] == ["EURUSD"]


def test_skips_already_open_symbols() -> None:
    p = MultiCfdAIProduct("cust", "w1")
    p.strategy.evaluate = lambda snap: _sig(SignalSide.BUY, 0.9)  # type: ignore[assignment]
    open_pos = [
        OpenPosition(
            position_id="p1",
            symbol="EURUSD",
            side=SignalSide.BUY,
            lot=0.02,
            entry_price=1.1,
            current_price=1.1,
            current_pnl_usd=0.0,
            current_sl=None,
            opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]
    snapshots = {"EURUSD": _snap("EURUSD"), "GBPUSD": _snap("GBPUSD")}
    result = p.evaluate(snapshots, open_pos, _bkk(2026, 1, 5, 12, 0))
    assert [r.symbol for r in result] == ["GBPUSD"]


def test_symbols_override_propagates_and_accepts_broker_codes() -> None:
    p = MultiCfdAIProduct("cust", "w1", symbols=("EURUSD.v", "GBPUSD.v", "SP500.v"))
    assert p.config.symbols == ("EURUSD.v", "GBPUSD.v", "SP500.v")
    p.strategy.evaluate = lambda snap: _sig(SignalSide.BUY, 0.8)  # type: ignore[assignment]
    snapshots = {
        "EURUSD.v": _snap("EURUSD.v"),
        "GBPUSD.v": _snap("GBPUSD.v"),
        "EURUSD": _snap("EURUSD"),  # not in overridden config — should be skipped
    }
    result = p.evaluate(snapshots, [], _bkk(2026, 1, 5, 12, 0))
    symbols_emitted = sorted(r.symbol for r in result)
    assert symbols_emitted == ["EURUSD.v", "GBPUSD.v"]


def test_returns_close_intents_on_week_target_hit() -> None:
    p = MultiCfdAIProduct("cust", "w1")
    p.week_tracker.record_pnl_delta(-80.0)  # below -70 → expired_loss
    open_pos = [
        OpenPosition(
            position_id="p1",
            symbol="EURUSD",
            side=SignalSide.BUY,
            lot=0.02,
            entry_price=1.1,
            current_price=1.1,
            current_pnl_usd=0.0,
            current_sl=None,
            opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]
    now = _bkk(2026, 1, 5, 12, 0)
    result = p.evaluate({}, open_pos, now)
    assert len(result) == 1
    assert isinstance(result[0], CloseIntent)
    assert result[0].code == "week_target_loss"
