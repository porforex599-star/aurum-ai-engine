from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.products.models import OpenPosition
from src.strategy.models import SignalSide


class PositionPoller:
    def __init__(self, metaapi_connection: Any) -> None:
        self.conn = metaapi_connection

    async def fetch_all(self) -> list[OpenPosition]:
        try:
            raw_positions = (
                self.conn.terminal_state.positions
                if hasattr(self.conn, "terminal_state")
                else []
            )
            result: list[OpenPosition] = []
            for p in raw_positions:
                result.append(self._parse(p))
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("position_poller failed: {}", e)
            return []

    @staticmethod
    def _parse(p: Any) -> OpenPosition:
        def g(key, default=None):
            if isinstance(p, dict):
                return p.get(key, default)
            return getattr(p, key, default)

        side_str = str(g("type", "") or "").lower()
        side = SignalSide.BUY if "buy" in side_str else SignalSide.SELL
        sl_raw = g("stopLoss", None)
        current_sl = float(sl_raw) if sl_raw else None
        return OpenPosition(
            position_id=str(g("id", "")),
            symbol=str(g("symbol", "")),
            side=side,
            lot=float(g("volume", 0) or 0),
            entry_price=float(g("openPrice", 0) or 0),
            current_price=float(g("currentPrice", 0) or 0),
            current_pnl_usd=float(g("unrealizedProfit", 0) or 0),
            current_sl=current_sl,
            opened_at=datetime.now(timezone.utc),
        )
