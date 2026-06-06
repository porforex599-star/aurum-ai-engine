from __future__ import annotations

from src.strategy.models import MarketSnapshot, SetupName, Signal, SignalSide, TrendBias
from src.strategy.signals import calc_atr, htf_trend, last_swing_high, last_swing_low


def detect_liquidity_sweep(snapshot: MarketSnapshot) -> Signal | None:
    bars_h1 = snapshot.bars_h1
    if len(bars_h1) < 20:
        return None
    trend = htf_trend(snapshot.bars_h4)
    if trend == TrendBias.FLAT:
        return None

    current = bars_h1[-1]
    prior = bars_h1[:-1]
    atr = calc_atr(bars_h1)
    if atr <= 0:
        return None

    if trend == TrendBias.UP:
        swing = last_swing_low(prior, lookback=5)
        if swing is None:
            return None
        swept = current.low < swing.low and current.close > swing.low
        rng = current.high - current.low
        bull_reversal = (
            current.close > current.open
            and rng > 0
            and (current.close - current.low) > 0.6 * rng
        )
        if swept and bull_reversal:
            entry = current.close
            sl = current.low - 0.3 * atr
            tp = entry + 2 * (entry - sl)
            return Signal(
                SetupName.LIQUIDITY_SWEEP,
                SignalSide.BUY,
                entry,
                sl,
                tp,
                confidence=0.75,
                reason=f"Bull sweep of swing-low {swing.low:.5f}",
                timestamp=current.timestamp,
            )

    if trend == TrendBias.DOWN:
        swing = last_swing_high(prior, lookback=5)
        if swing is None:
            return None
        swept = current.high > swing.high and current.close < swing.high
        rng = current.high - current.low
        bear_reversal = (
            current.close < current.open
            and rng > 0
            and (current.high - current.close) > 0.6 * rng
        )
        if swept and bear_reversal:
            entry = current.close
            sl = current.high + 0.3 * atr
            tp = entry - 2 * (sl - entry)
            return Signal(
                SetupName.LIQUIDITY_SWEEP,
                SignalSide.SELL,
                entry,
                sl,
                tp,
                confidence=0.75,
                reason=f"Bear sweep of swing-high {swing.high:.5f}",
                timestamp=current.timestamp,
            )

    return None
