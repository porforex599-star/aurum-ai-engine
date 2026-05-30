from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.snapshot_fetcher import SnapshotFetcher
from src.strategy.models import MarketSnapshot


def _fake_candle(ts: str, o: float, h: float, l: float, c: float) -> dict:
    return {"time": ts, "open": o, "high": h, "low": l, "close": c, "tickVolume": 1}


@pytest.mark.asyncio
async def test_fetch_returns_snapshot_when_candles_returned() -> None:
    conn = MagicMock()
    candles = [_fake_candle("2026-01-01T00:00:00Z", 100, 101, 99, 100.5)]
    conn.get_historical_candles = AsyncMock(return_value=candles)
    sf = SnapshotFetcher(conn)
    result = await sf.fetch("XAUUSD", bars_count=1)
    assert isinstance(result, MarketSnapshot)
    assert result.symbol == "XAUUSD"
    assert len(result.bars_h1) == 1
    assert result.bars_h1[0].close == 100.5


@pytest.mark.asyncio
async def test_fetch_returns_none_on_exception() -> None:
    conn = MagicMock()
    conn.get_historical_candles = AsyncMock(side_effect=RuntimeError("boom"))
    sf = SnapshotFetcher(conn)
    assert await sf.fetch("XAUUSD") is None


@pytest.mark.asyncio
async def test_fetch_calls_each_timeframe() -> None:
    conn = MagicMock()
    conn.get_historical_candles = AsyncMock(return_value=[])
    sf = SnapshotFetcher(conn)
    await sf.fetch("XAUUSD", bars_count=5)
    timeframes_called = [
        call.kwargs.get("timeframe") for call in conn.get_historical_candles.call_args_list
    ]
    assert set(timeframes_called) == {"4h", "1h", "15m"}
