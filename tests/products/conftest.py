from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.products.models import OpenPosition
from src.strategy.models import (
    Bar,
    MarketSnapshot,
    SetupName,
    Signal,
    SignalSide,
)

BKK = ZoneInfo("Asia/Bangkok")


def bkk_dt(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BKK).astimezone(timezone.utc)


@pytest.fixture
def gold_position():
    return OpenPosition(
        position_id="p1",
        symbol="XAUUSD",
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=2000.0,
        current_price=2010.0,
        current_pnl_usd=10.0,
        current_sl=None,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def eur_position():
    return OpenPosition(
        position_id="p2",
        symbol="EURUSD",
        side=SignalSide.SELL,
        lot=0.02,
        entry_price=1.10,
        current_price=1.099,
        current_pnl_usd=5.0,
        current_sl=None,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def empty_snapshot():
    return MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=[], bars_h4=[])


@pytest.fixture
def fake_signal():
    return Signal(
        setup=SetupName.LIQUIDITY_SWEEP,
        side=SignalSide.BUY,
        entry_price=2000.0,
        sl_price=1995.0,
        tp_price=2010.0,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def make_signal():
    def _make(symbol_unused: str = "X", side=SignalSide.BUY, conf=0.7):
        return Signal(
            setup=SetupName.ORDER_BLOCK,
            side=side,
            entry_price=100.0,
            sl_price=99.0,
            tp_price=102.0,
            confidence=conf,
            reason="m",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    return _make
