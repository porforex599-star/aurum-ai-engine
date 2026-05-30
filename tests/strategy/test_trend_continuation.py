from __future__ import annotations

from src.strategy.models import MarketSnapshot, SetupName, SignalSide
from src.strategy.trend_continuation import detect_trend_continuation


def _build_pullback_up_h1(make_bars):
    """30 H1 bars: flat closes then last bar pulls back to EMA20 and closes bullish."""
    prices = [(100.0, 100.5, 99.5, 100.2) for _ in range(29)]
    # Current bar: low touches near EMA20 (~100.2), bullish close above
    prices.append((99.95, 100.6, 99.9, 100.4))
    return make_bars(prices)


def _build_pullback_down_h1(make_bars):
    """Mirror: flat closes then last bar pulls up to EMA20, bearish close below."""
    prices = [(100.0, 100.5, 99.5, 99.8) for _ in range(29)]
    # Current bar: high touches EMA20 (~99.8), bearish close below
    prices.append((100.05, 100.1, 99.4, 99.6))
    return make_bars(prices)


def test_buy_on_uptrend_pullback_to_ema20(make_bars, uptrend_h4) -> None:
    h1 = _build_pullback_up_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=uptrend_h4)
    sig = detect_trend_continuation(snap)
    assert sig is not None
    assert sig.setup == SetupName.TREND_CONTINUATION
    assert sig.side == SignalSide.BUY
    assert sig.sl_price < sig.entry_price < sig.tp_price


def test_sell_on_downtrend_pullback_to_ema20(make_bars, downtrend_h4) -> None:
    h1 = _build_pullback_down_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=downtrend_h4)
    sig = detect_trend_continuation(snap)
    assert sig is not None
    assert sig.side == SignalSide.SELL
    assert sig.tp_price < sig.entry_price < sig.sl_price


def test_no_signal_on_flat_trend(make_bars, flat_h4) -> None:
    h1 = _build_pullback_up_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=flat_h4)
    assert detect_trend_continuation(snap) is None


def test_no_signal_when_price_too_far_from_ema20(make_bars, uptrend_h4) -> None:
    # Last bar's low is far above EMA20, no pullback
    prices = [(100.0, 100.5, 99.5, 100.2) for _ in range(29)]
    prices.append((110.0, 111.0, 109.8, 110.5))  # far away
    h1 = make_bars(prices)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=uptrend_h4)
    assert detect_trend_continuation(snap) is None
