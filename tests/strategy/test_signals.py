from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from src.strategy.models import Bar, TrendBias
from src.strategy.signals import (
    calc_atr,
    calc_bb,
    calc_ema,
    calc_rsi,
    detect_swing_highs,
    detect_swing_lows,
    htf_trend,
    last_swing_high,
    last_swing_low,
)


def _bar(i: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(datetime(2026, 1, 1) + timedelta(hours=i), o, h, l, c)


def test_atr_insufficient_bars_returns_zero() -> None:
    bars = [_bar(i, 1.0, 1.1, 0.9, 1.05) for i in range(5)]
    assert calc_atr(bars, period=14) == 0.0


def test_atr_with_enough_bars_returns_positive() -> None:
    bars = [_bar(i, 1.0 + i * 0.01, 1.1 + i * 0.01, 0.9 + i * 0.01, 1.05 + i * 0.01) for i in range(20)]
    assert calc_atr(bars, period=14) > 0.0


def test_ema_matches_manual_calc_short_series() -> None:
    bars = [_bar(i, c, c, c, c) for i, c in enumerate([10.0, 11.0, 12.0, 13.0, 14.0])]
    # EMA with span=3 adjust=False:
    # alpha = 2/(3+1)=0.5
    # ema[0]=10, ema[1]=10.5, ema[2]=11.25, ema[3]=12.125, ema[4]=13.0625
    assert calc_ema(bars, period=3) == pytest.approx(13.0625)


def test_rsi_insufficient_data_returns_50() -> None:
    bars = [_bar(i, 1.0, 1.0, 1.0, 1.0) for i in range(5)]
    assert calc_rsi(bars, period=14) == 50.0


def test_rsi_bounded_0_100() -> None:
    bars = [_bar(i, 1.0 + i * 0.1, 1.0, 1.0, 1.0 + i * 0.1) for i in range(30)]
    r = calc_rsi(bars, period=14)
    assert 0.0 <= r <= 100.0


def test_bb_upper_above_middle_above_lower() -> None:
    bars = [_bar(i, 1.0 + i * 0.01, 1.05, 0.95, 1.0 + (i % 5) * 0.05) for i in range(25)]
    lower, mid, upper = calc_bb(bars, period=20, std_dev=2.0)
    assert lower < mid < upper


def test_bb_insufficient_bars_returns_nan() -> None:
    bars = [_bar(i, 1.0, 1.0, 1.0, 1.0) for i in range(5)]
    lower, mid, upper = calc_bb(bars, period=20)
    assert math.isnan(lower) and math.isnan(mid) and math.isnan(upper)


def test_detect_swing_highs_finds_peak() -> None:
    # Build a clear peak at index 5
    highs = [1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1]
    bars = [_bar(i, h, h, h - 0.5, h) for i, h in enumerate(highs)]
    idxs = detect_swing_highs(bars, lookback=3)
    assert 5 in idxs
    assert last_swing_high(bars, lookback=3).high == 10


def test_detect_swing_lows_finds_trough() -> None:
    lows = [10, 9, 8, 7, 6, 1, 6, 7, 8, 9, 10]
    bars = [_bar(i, l + 0.5, l + 0.5, l, l + 0.5) for i, l in enumerate(lows)]
    idxs = detect_swing_lows(bars, lookback=3)
    assert 5 in idxs
    assert last_swing_low(bars, lookback=3).low == 1


def test_htf_trend_up(uptrend_h4) -> None:
    assert htf_trend(uptrend_h4) == TrendBias.UP


def test_htf_trend_down(downtrend_h4) -> None:
    assert htf_trend(downtrend_h4) == TrendBias.DOWN


def test_htf_trend_flat(flat_h4) -> None:
    assert htf_trend(flat_h4) == TrendBias.FLAT


def test_htf_trend_insufficient_returns_flat() -> None:
    bars = [_bar(i, 1.0, 1.0, 1.0, 1.0) for i in range(10)]
    assert htf_trend(bars) == TrendBias.FLAT
