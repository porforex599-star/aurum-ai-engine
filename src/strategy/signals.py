from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategy.models import Bar, TrendBias


def bars_to_df(bars: list[Bar]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.DataFrame(
        [
            {
                "timestamp": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
    ).set_index("timestamp")


def calc_atr(bars: list[Bar], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    df = bars_to_df(bars)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def calc_ema(bars: list[Bar], period: int) -> float:
    if len(bars) < period:
        return float("nan")
    closes = pd.Series([b.close for b in bars])
    return float(closes.ewm(span=period, adjust=False).mean().iloc[-1])


def calc_ema_series(bars: list[Bar], period: int) -> np.ndarray:
    closes = pd.Series([b.close for b in bars])
    return closes.ewm(span=period, adjust=False).mean().to_numpy()


def calc_rsi(bars: list[Bar], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 50.0
    closes = pd.Series([b.close for b in bars])
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def calc_bb(
    bars: list[Bar], period: int = 20, std_dev: float = 2.0
) -> tuple[float, float, float]:
    if len(bars) < period:
        return (float("nan"), float("nan"), float("nan"))
    closes = pd.Series([b.close for b in bars])
    mid = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    return (
        float(mid.iloc[-1] - std_dev * std.iloc[-1]),
        float(mid.iloc[-1]),
        float(mid.iloc[-1] + std_dev * std.iloc[-1]),
    )


def detect_swing_highs(bars: list[Bar], lookback: int = 5) -> list[int]:
    result: list[int] = []
    for i in range(lookback, len(bars) - lookback):
        window = bars[i - lookback : i + lookback + 1]
        if bars[i].high == max(b.high for b in window) and bars[i].high > bars[i - 1].high:
            result.append(i)
    return result


def detect_swing_lows(bars: list[Bar], lookback: int = 5) -> list[int]:
    result: list[int] = []
    for i in range(lookback, len(bars) - lookback):
        window = bars[i - lookback : i + lookback + 1]
        if bars[i].low == min(b.low for b in window) and bars[i].low < bars[i - 1].low:
            result.append(i)
    return result


def last_swing_high(bars: list[Bar], lookback: int = 5) -> Bar | None:
    idxs = detect_swing_highs(bars, lookback)
    return bars[idxs[-1]] if idxs else None


def last_swing_low(bars: list[Bar], lookback: int = 5) -> Bar | None:
    idxs = detect_swing_lows(bars, lookback)
    return bars[idxs[-1]] if idxs else None


def htf_trend(
    bars_h4: list[Bar], ema_period: int = 50, flat_threshold_pct: float = 0.1
) -> TrendBias:
    if len(bars_h4) < ema_period + 5:
        return TrendBias.FLAT
    ema = calc_ema_series(bars_h4, ema_period)
    price = bars_h4[-1].close
    slope_pct = (ema[-1] - ema[-5]) / ema[-5] * 100
    if price > ema[-1] and slope_pct > flat_threshold_pct:
        return TrendBias.UP
    if price < ema[-1] and slope_pct < -flat_threshold_pct:
        return TrendBias.DOWN
    return TrendBias.FLAT
