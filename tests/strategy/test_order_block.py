from __future__ import annotations

from src.strategy.models import MarketSnapshot, SetupName, SignalSide
from src.strategy.order_block import detect_order_block


def _build_bull_ob_h1(make_bars):
    """30 bars: large bullish OB at index 15, current bar retests it."""
    prices = []
    # Bars 0-14: gentle rise, small bodies
    for i in range(15):
        c = 99.0 + i * 0.05
        prices.append((c - 0.1, c + 0.3, c - 0.3, c + 0.05))
    # Bar 15: large bullish OB (body=3)
    prices.append((100.0, 104.0, 99.5, 103.0))
    # Bars 16-27: small bodies, drift down toward OB zone
    starts = [103.2, 103.3, 103.1, 103.4, 103.2, 103.0, 102.7, 102.5, 102.3, 102.0, 101.8, 101.6]
    for c in starts:
        prices.append((c - 0.1, c + 0.3, c - 0.3, c + 0.05))
    # Bar 28: deeper pullback
    prices.append((101.5, 101.8, 101.0, 101.3))
    # Bar 29 (current): low touches OB (100..103), bullish close
    prices.append((101.0, 102.2, 100.5, 102.0))
    assert len(prices) == 30
    return make_bars(prices)


def _build_bear_ob_h1(make_bars):
    prices = []
    for i in range(15):
        c = 101.0 - i * 0.05
        prices.append((c + 0.1, c + 0.3, c - 0.3, c - 0.05))
    # Bar 15: large bearish OB (body=3)
    prices.append((100.0, 100.5, 96.0, 97.0))
    starts = [96.8, 96.7, 96.9, 96.6, 96.8, 97.0, 97.3, 97.5, 97.7, 98.0, 98.2, 98.4]
    for c in starts:
        prices.append((c + 0.1, c + 0.3, c - 0.3, c - 0.05))
    prices.append((98.5, 99.0, 98.2, 98.7))
    # Bar 29 (current): high touches OB (97..100), bearish close
    prices.append((99.0, 99.5, 97.8, 98.0))
    assert len(prices) == 30
    return make_bars(prices)


def test_returns_none_on_flat_htf(make_bars, flat_h4) -> None:
    h1 = _build_bull_ob_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=flat_h4)
    assert detect_order_block(snap) is None


def test_buy_signal_on_bull_ob_retest(make_bars, uptrend_h4) -> None:
    h1 = _build_bull_ob_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=uptrend_h4)
    sig = detect_order_block(snap)
    assert sig is not None
    assert sig.setup == SetupName.ORDER_BLOCK
    assert sig.side == SignalSide.BUY


def test_sell_signal_on_bear_ob_retest(make_bars, downtrend_h4) -> None:
    h1 = _build_bear_ob_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=downtrend_h4)
    sig = detect_order_block(snap)
    assert sig is not None
    assert sig.side == SignalSide.SELL


def test_returns_none_when_no_large_body_present(make_bars, uptrend_h4) -> None:
    # All small bodies
    prices = [(100.0, 100.4, 99.6, 100.1) for _ in range(30)]
    h1 = make_bars(prices)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=uptrend_h4)
    assert detect_order_block(snap) is None
