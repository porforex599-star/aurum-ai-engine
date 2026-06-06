from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from src.strategy.models import Bar, MarketSnapshot


class SnapshotFetcher:
    """Fetches OHLC candles from MetaApi for H4/H1/M15.

    Primary data source is the MetatraderAccount object (which exposes
    `get_historical_candles`). The streaming connection is kept around for
    future use (spread/tick queries) but not required for candle fetches.
    """

    def __init__(self, account: Any, connection: Any | None = None) -> None:
        self.account = account
        self.connection = connection
        self._last_error: dict | None = None

    async def fetch(self, symbol: str, bars_count: int = 80) -> MarketSnapshot | None:
        try:
            bars_h4 = await self._fetch_tf(symbol, "4h", bars_count)
            bars_h1 = await self._fetch_tf(symbol, "1h", bars_count)
            bars_m15 = await self._fetch_tf(symbol, "15m", bars_count)
            self._last_error = None
            return MarketSnapshot(
                symbol=symbol,
                bars_m15=bars_m15,
                bars_h1=bars_h1,
                bars_h4=bars_h4,
                current_spread_points=0.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("snapshot_fetcher failed for {}: {}", symbol, e)
            self._last_error = {
                "exc_type": type(e).__name__,
                "exc_msg": str(e)[:300],
            }
            return None

    async def _fetch_tf(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        candles = await self.account.get_historical_candles(
            symbol=symbol,
            timeframe=timeframe,
            start_time=None,
            limit=count,
        )
        bars: list[Bar] = []
        for c in candles or []:
            ts = c.get("time") if isinstance(c, dict) else getattr(c, "time", None)
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            bars.append(
                Bar(
                    timestamp=ts,
                    open=float(c.get("open") if isinstance(c, dict) else c.open),
                    high=float(c.get("high") if isinstance(c, dict) else c.high),
                    low=float(c.get("low") if isinstance(c, dict) else c.low),
                    close=float(c.get("close") if isinstance(c, dict) else c.close),
                    volume=float(
                        c.get("tickVolume", 0)
                        if isinstance(c, dict)
                        else getattr(c, "tickVolume", 0)
                    ),
                )
            )
        return bars
