from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.position_poller import PositionPoller
from src.strategy.models import SignalSide


@pytest.mark.asyncio
async def test_fetch_all_returns_list_of_open_positions() -> None:
    conn = SimpleNamespace(
        terminal_state=SimpleNamespace(
            positions=[
                {
                    "id": "1",
                    "symbol": "XAUUSD",
                    "type": "POSITION_TYPE_BUY",
                    "volume": 0.03,
                    "openPrice": 2000.0,
                    "currentPrice": 2010.0,
                    "unrealizedProfit": 30.0,
                    "stopLoss": 1990.0,
                },
                {
                    "id": "2",
                    "symbol": "EURUSD",
                    "type": "POSITION_TYPE_SELL",
                    "volume": 0.02,
                    "openPrice": 1.1,
                    "currentPrice": 1.095,
                    "unrealizedProfit": 5.0,
                },
            ]
        )
    )
    pp = PositionPoller(conn)
    out = await pp.fetch_all()
    assert len(out) == 2
    assert out[0].symbol == "XAUUSD"
    assert out[0].side == SignalSide.BUY
    assert out[0].current_sl == 1990.0
    assert out[1].side == SignalSide.SELL
    assert out[1].current_sl is None


@pytest.mark.asyncio
async def test_fetch_all_handles_empty() -> None:
    conn = SimpleNamespace(terminal_state=SimpleNamespace(positions=[]))
    pp = PositionPoller(conn)
    assert await pp.fetch_all() == []


@pytest.mark.asyncio
async def test_fetch_all_returns_empty_on_exception() -> None:
    class Boom:
        @property
        def terminal_state(self):
            raise RuntimeError("kaboom")

    pp = PositionPoller(Boom())
    assert await pp.fetch_all() == []
