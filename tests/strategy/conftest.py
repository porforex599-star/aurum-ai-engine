from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.strategy.models import Bar


@pytest.fixture
def make_bars():
    def _make(
        prices_ohlc,
        start: datetime = datetime(2026, 1, 1),
        step_minutes: int = 60,
    ) -> list[Bar]:
        return [
            Bar(
                timestamp=start + timedelta(minutes=step_minutes * i),
                open=o,
                high=h,
                low=l,
                close=c,
            )
            for i, (o, h, l, c) in enumerate(prices_ohlc)
        ]

    return _make


@pytest.fixture
def uptrend_h4(make_bars):
    """60 H4 bars in clear uptrend (price > EMA50, positive slope)."""
    prices = []
    base = 1000.0
    for i in range(60):
        base += 2.0 + (i % 3 - 1) * 0.5
        o = base - 0.5
        h = base + 1.0
        l = base - 1.0
        c = base + 0.5
        prices.append((o, h, l, c))
    return make_bars(prices, step_minutes=240)


@pytest.fixture
def downtrend_h4(make_bars):
    """60 H4 bars in clear downtrend."""
    prices = []
    base = 2000.0
    for i in range(60):
        base -= 2.0 + (i % 3 - 1) * 0.5
        o = base + 0.5
        h = base + 1.0
        l = base - 1.0
        c = base - 0.5
        prices.append((o, h, l, c))
    return make_bars(prices, step_minutes=240)


@pytest.fixture
def flat_h4(make_bars):
    """60 H4 bars range-bound around 1500."""
    prices = []
    for i in range(60):
        # Tight oscillation
        mid = 1500.0 + (0.5 if i % 2 == 0 else -0.5)
        prices.append((mid - 0.1, mid + 0.5, mid - 0.5, mid + 0.1))
    return make_bars(prices, step_minutes=240)
