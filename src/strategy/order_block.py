from __future__ import annotations

from src.strategy.models import MarketSnapshot, SetupName, Signal, SignalSide, TrendBias
from src.strategy.signals import calc_atr, htf_trend


def detect_order_block(snapshot: MarketSnapshot) -> Signal | None:
    bars = snapshot.bars_h1
    if len(bars) < 30:
        return None
    trend = htf_trend(snapshot.bars_h4)
    if trend == TrendBias.FLAT:
        return None

    atr = calc_atr(bars)
    if atr <= 0:
        return None

    ob = None
    for b in reversed(bars[-25:-1]):
        body = abs(b.close - b.open)
        if body < 1.2 * atr:
            continue
        if trend == TrendBias.UP and b.close > b.open:
            ob = b
            break
        if trend == TrendBias.DOWN and b.close < b.open:
            ob = b
            break
    if ob is None:
        return None

    current = bars[-1]
    ob_high = max(ob.open, ob.close)
    ob_low = min(ob.open, ob.close)

    if trend == TrendBias.UP:
        retested = current.low <= ob_high and current.low >= ob_low - 0.2 * atr
        bouncing = current.close > current.open
        if retested and bouncing:
            entry = current.close
            sl = ob_low - 0.3 * atr
            tp = entry + 2 * (entry - sl)
            return Signal(
                SetupName.ORDER_BLOCK,
                SignalSide.BUY,
                entry,
                sl,
                tp,
                confidence=0.70,
                reason=f"Bull OB retest at {ob_low:.5f}-{ob_high:.5f}",
                timestamp=current.timestamp,
            )

    if trend == TrendBias.DOWN:
        retested = current.high >= ob_low and current.high <= ob_high + 0.2 * atr
        bouncing = current.close < current.open
        if retested and bouncing:
            entry = current.close
            sl = ob_high + 0.3 * atr
            tp = entry - 2 * (sl - entry)
            return Signal(
                SetupName.ORDER_BLOCK,
                SignalSide.SELL,
                entry,
                sl,
                tp,
                confidence=0.70,
                reason=f"Bear OB retest at {ob_low:.5f}-{ob_high:.5f}",
                timestamp=current.timestamp,
            )

    return None
