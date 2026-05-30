from __future__ import annotations

from src.strategy.liquidity_sweep import detect_liquidity_sweep
from src.strategy.models import MarketSnapshot, SetupName, SignalSide


def _build_bull_sweep_h1(make_bars):
    """20 bars, swing-low at index 10, current bar sweeps and reverses bullishly."""
    prices = []
    # Bars 0-9: rising base around 100-103, with bar 9 low at 100.5
    for i in range(10):
        c = 102.0 + i * 0.05
        prices.append((c - 0.2, c + 0.5, c - 0.3, c + 0.1))
    # Force bar 9 to have a low of 100.5 (above swing-low to follow)
    prices[9] = (102.5, 102.8, 100.5, 102.6)
    # Bar 10: swing-low at 99
    prices.append((102.0, 102.5, 99.0, 100.0))
    # Bars 11-18: range above 100
    for i in range(11, 19):
        c = 101.0 + (i - 11) * 0.05
        prices.append((c - 0.1, c + 0.4, c - 0.2, c + 0.1))
    # Bar 19 (current): sweeps below 99, closes back above with long lower wick
    prices.append((99.5, 100.2, 98.0, 100.0))
    return make_bars(prices)


def _build_bear_sweep_h1(make_bars):
    prices = []
    for i in range(10):
        c = 100.0 - i * 0.05
        prices.append((c + 0.2, c + 0.3, c - 0.5, c - 0.1))
    prices[9] = (99.5, 99.5, 99.2, 99.4)
    # Bar 10: swing-high at 101
    prices.append((100.0, 101.0, 99.5, 100.0))
    for i in range(11, 19):
        c = 100.0 - (i - 11) * 0.05
        prices.append((c + 0.1, c + 0.2, c - 0.4, c - 0.1))
    # Bar 19 (current): sweeps above 101, closes back below with long upper wick
    prices.append((100.5, 102.0, 99.8, 100.0))
    return make_bars(prices)


def test_returns_none_on_flat_htf(make_bars, flat_h4) -> None:
    h1 = _build_bull_sweep_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=flat_h4)
    assert detect_liquidity_sweep(snap) is None


def test_buy_signal_when_htf_up_and_bull_sweep(make_bars, uptrend_h4) -> None:
    h1 = _build_bull_sweep_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=uptrend_h4)
    sig = detect_liquidity_sweep(snap)
    assert sig is not None
    assert sig.setup == SetupName.LIQUIDITY_SWEEP
    assert sig.side == SignalSide.BUY
    assert sig.sl_price < sig.entry_price < sig.tp_price


def test_sell_signal_when_htf_down_and_bear_sweep(make_bars, downtrend_h4) -> None:
    h1 = _build_bear_sweep_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=downtrend_h4)
    sig = detect_liquidity_sweep(snap)
    assert sig is not None
    assert sig.side == SignalSide.SELL
    assert sig.tp_price < sig.entry_price < sig.sl_price


def test_returns_none_when_sweep_but_no_reversal(make_bars, uptrend_h4) -> None:
    h1 = _build_bull_sweep_h1(make_bars)
    # Replace current bar with bearish close (no reversal)
    last = h1[-1]
    from src.strategy.models import Bar

    h1[-1] = Bar(last.timestamp, 100.0, 100.5, 98.0, 98.5)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=uptrend_h4)
    assert detect_liquidity_sweep(snap) is None


def test_returns_none_on_insufficient_bars(make_bars, uptrend_h4) -> None:
    h1 = make_bars([(100, 101, 99, 100) for _ in range(10)])
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=uptrend_h4)
    assert detect_liquidity_sweep(snap) is None
