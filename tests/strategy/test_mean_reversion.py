from __future__ import annotations

from src.strategy.mean_reversion import detect_mean_reversion
from src.strategy.models import MarketSnapshot, SetupName, SignalSide


def _build_falling_h1(make_bars):
    """25 bars: oscillating then sharp decline → close below BB lower, RSI < 30."""
    prices = []
    # Slight oscillation to avoid all-equal closes that would NaN the RSI gain/loss.
    for i in range(15):
        c = 100.0 + (0.3 if i % 2 == 0 else -0.3)
        prices.append((c, c + 0.5, c - 0.5, c))
    c = 100.0
    for _ in range(10):
        c -= 3.0
        prices.append((c + 1.5, c + 1.6, c - 0.5, c))
    return make_bars(prices)


def _build_rising_h1(make_bars):
    """25 bars: oscillating then sharp rise → close above BB upper, RSI > 70."""
    prices = []
    for i in range(15):
        c = 100.0 + (0.3 if i % 2 == 0 else -0.3)
        prices.append((c, c + 0.5, c - 0.5, c))
    c = 100.0
    for _ in range(10):
        c += 3.0
        prices.append((c - 1.5, c + 0.5, c - 1.6, c))
    return make_bars(prices)


def test_buy_at_bb_lower_with_rsi_under_30(make_bars, flat_h4) -> None:
    h1 = _build_falling_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=flat_h4)
    sig = detect_mean_reversion(snap)
    assert sig is not None
    assert sig.setup == SetupName.MEAN_REVERSION
    assert sig.side == SignalSide.BUY


def test_sell_at_bb_upper_with_rsi_over_70(make_bars, flat_h4) -> None:
    h1 = _build_rising_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=flat_h4)
    sig = detect_mean_reversion(snap)
    assert sig is not None
    assert sig.side == SignalSide.SELL


def test_no_signal_when_htf_strongly_aligned_down(make_bars, downtrend_h4) -> None:
    # falling H1 wants BUY, but trend != DOWN is required → blocked
    h1 = _build_falling_h1(make_bars)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=downtrend_h4)
    assert detect_mean_reversion(snap) is None


def test_no_signal_when_rsi_moderate(make_bars, flat_h4) -> None:
    prices = [(100.0, 100.5, 99.5, 100.0) for _ in range(25)]
    h1 = make_bars(prices)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=flat_h4)
    assert detect_mean_reversion(snap) is None


def test_no_signal_on_insufficient_bars(make_bars, flat_h4) -> None:
    prices = [(100.0, 100.5, 99.5, 100.0) for _ in range(10)]
    h1 = make_bars(prices)
    snap = MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=h1, bars_h4=flat_h4)
    assert detect_mean_reversion(snap) is None
