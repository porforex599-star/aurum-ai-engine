from __future__ import annotations

import math

from src.strategy.models import MarketSnapshot, SetupName, Signal, SignalSide, TrendBias
from src.strategy.signals import calc_atr, calc_ema, htf_trend, last_swing_high, last_swing_low


def detect_trend_continuation(snapshot: MarketSnapshot) -> Signal | None:
    bars = snapshot.bars_h1
    if len(bars) < 25:
        return None
    trend = htf_trend(snapshot.bars_h4)
    if trend == TrendBias.FLAT:
        return None

    ema20 = calc_ema(bars, period=20)
    atr = calc_atr(bars)
    if atr <= 0 or math.isnan(ema20):
        return None
    current = bars[-1]

    if trend == TrendBias.UP:
        near_ema = abs(current.low - ema20) < 0.5 * atr and current.low <= ema20
        bull_confirm = current.close > current.open and current.close > ema20
        if near_ema and bull_confirm:
            swing = last_swing_low(bars[:-1], lookback=3)
            sl_base = swing.low if swing else current.low
            entry = current.close
            sl = sl_base - 0.3 * atr
            tp = entry + 2 * (entry - sl)
            return Signal(
                SetupName.TREND_CONTINUATION,
                SignalSide.BUY,
                entry,
                sl,
                tp,
                confidence=0.72,
                reason=f"Bull pullback to EMA20 {ema20:.5f}",
                timestamp=current.timestamp,
            )

    if trend == TrendBias.DOWN:
        near_ema = abs(current.high - ema20) < 0.5 * atr and current.high >= ema20
        bear_confirm = current.close < current.open and current.close < ema20
        if near_ema and bear_confirm:
            swing = last_swing_high(bars[:-1], lookback=3)
            sl_base = swing.high if swing else current.high
            entry = current.close
            sl = sl_base + 0.3 * atr
            tp = entry - 2 * (sl - entry)
            return Signal(
                SetupName.TREND_CONTINUATION,
                SignalSide.SELL,
                entry,
                sl,
                tp,
                confidence=0.72,
                reason=f"Bear pullback to EMA20 {ema20:.5f}",
                timestamp=current.timestamp,
            )

    return None
