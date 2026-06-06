from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.snapshot_fetcher import SnapshotFetcher
from src.strategy.models import MarketSnapshot


def _fake_candle(ts: str, o: float, h: float, l: float, c: float) -> dict:
    return {"time": ts, "open": o, "high": h, "low": l, "close": c, "tickVolume": 1}


@pytest.mark.asyncio
async def test_fetch_returns_snapshot_when_account_returns_candles() -> None:
    account = MagicMock()
    candles = [_fake_candle("2026-01-01T00:00:00Z", 100, 101, 99, 100.5)]
    account.get_historical_candles = AsyncMock(return_value=candles)
    sf = SnapshotFetcher(account=account)
    result = await sf.fetch("XAUUSD", bars_count=1)
    assert isinstance(result, MarketSnapshot)
    assert result.symbol == "XAUUSD"
    assert len(result.bars_h1) == 1
    assert result.bars_h1[0].close == 100.5
    assert sf._last_error is None


@pytest.mark.asyncio
async def test_fetch_returns_none_and_records_last_error_on_exception() -> None:
    account = MagicMock()
    account.get_historical_candles = AsyncMock(side_effect=RuntimeError("boom-msg"))
    sf = SnapshotFetcher(account=account)
    result = await sf.fetch("XAUUSD")
    assert result is None
    assert sf._last_error is not None
    assert sf._last_error["exc_type"] == "RuntimeError"
    assert "boom-msg" in sf._last_error["exc_msg"]


@pytest.mark.asyncio
async def test_fetch_calls_each_timeframe_on_account() -> None:
    account = MagicMock()
    account.get_historical_candles = AsyncMock(return_value=[])
    sf = SnapshotFetcher(account=account)
    await sf.fetch("XAUUSD", bars_count=5)
    timeframes_called = [
        call.kwargs.get("timeframe")
        for call in account.get_historical_candles.call_args_list
    ]
    assert set(timeframes_called) == {"4h", "1h", "15m"}
