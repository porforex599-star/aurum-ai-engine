from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable


@dataclass(frozen=True)
class FilterResult:
    allowed: bool
    reason: str | None = None
    code: str | None = None


@dataclass(frozen=True)
class NewsEvent:
    currency: str
    impact: str
    at: datetime


class SpreadFilter:
    def __init__(self, max_spread_points: dict[str, float]) -> None:
        self.max = max_spread_points

    def check(self, symbol: str, current_spread_points: float) -> FilterResult:
        cap = self.max.get(symbol)
        if cap is None:
            return FilterResult(True)
        if current_spread_points > cap:
            return FilterResult(
                False,
                f"Spread {current_spread_points} > {cap} for {symbol}",
                "spread_too_wide",
            )
        return FilterResult(True)


class VolatilityFilter:
    def __init__(self, max_atr_multiplier: float = 2.5) -> None:
        self.mult = max_atr_multiplier

    def check(self, symbol: str, current_atr: float, avg_atr: float) -> FilterResult:
        if avg_atr <= 0:
            return FilterResult(True)
        if current_atr > avg_atr * self.mult:
            return FilterResult(
                False,
                f"ATR spike {current_atr:.2f} > {avg_atr * self.mult:.2f}",
                "volatility_spike",
            )
        return FilterResult(True)


class NewsFilter:
    SYMBOL_CURRENCIES: dict[str, list[str]] = {
        "XAUUSD": ["USD"],
        "EURUSD": ["EUR", "USD"],
        "GBPUSD": ["GBP", "USD"],
        "USDJPY": ["USD", "JPY"],
        "US500": ["USD"],
        "NAS100": ["USD"],
        "GER40": ["EUR"],
    }

    def __init__(self, before_min: int = 15, after_min: int = 15) -> None:
        self.before = timedelta(minutes=before_min)
        self.after = timedelta(minutes=after_min)

    def check(self, symbol: str, now: datetime, events: list[NewsEvent]) -> FilterResult:
        currencies = set(self.SYMBOL_CURRENCIES.get(symbol, []))
        for ev in events:
            if ev.impact != "high":
                continue
            if ev.currency not in currencies:
                continue
            if ev.at - self.before <= now <= ev.at + self.after:
                return FilterResult(
                    False,
                    f"High-impact {ev.currency} news at {ev.at.isoformat()}",
                    "news_blackout",
                )
        return FilterResult(True)


def run_all_filters(checks: list[Callable[[], FilterResult]]) -> FilterResult:
    """Each item in checks is a callable returning FilterResult. Returns first block, or allow."""
    for c in checks:
        r = c()
        if not r.allowed:
            return r
    return FilterResult(True)
