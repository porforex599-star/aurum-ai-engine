from __future__ import annotations

import pandas as pd

from src.strategy.models import MarketSnapshot, SetupName, Signal, SignalSide, TrendBias
from src.strategy.signals import calc_atr, calc_bb, calc_rsi, htf_trend


def detect_mean_reversion(snapshot: MarketSnapshot) -> Signal | None:
    bars = snapshot.bars_h1
    if len(bars) < 22:
        return None

    lower, mid, upper = calc_bb(bars, period=20, std_dev=2.0)
    if any(pd.isna(x) for x in (lower, mid, upper)):
        return None
    rsi = calc_rsi(bars, period=14)
    atr = calc_atr(bars)
    if atr <= 0:
        return None
    current = bars[-1]
    trend = htf_trend(snapshot.bars_h4)

    if current.close > upper and rsi > 70 and trend != TrendBias.UP:
        entry = current.close
        sl = upper + 0.5 * atr
        tp = mid
        return Signal(
            SetupName.MEAN_REVERSION,
            SignalSide.SELL,
            entry,
            sl,
            tp,
            confidence=0.65,
            reason=f"BB upper + RSI {rsi:.1f}",
            timestamp=current.timestamp,
        )

    if current.close < lower and rsi < 30 and trend != TrendBias.DOWN:
        entry = current.close
        sl = lower - 0.5 * atr
        tp = mid
        return Signal(
            SetupName.MEAN_REVERSION,
            SignalSide.BUY,
            entry,
            sl,
            tp,
            confidence=0.65,
            reason=f"BB lower + RSI {rsi:.1f}",
            timestamp=current.timestamp,
        )

    return None
