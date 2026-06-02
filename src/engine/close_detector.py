from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from loguru import logger


class CloseDetector:
    """Tracks open positions between ticks; emits close events with PnL."""

    def __init__(self, rpc_connection_provider: Callable[[], Any]) -> None:
        self._get_conn = rpc_connection_provider
        self._prev_position_ids: set[str] = set()
        self._position_meta: dict[str, dict] = {}  # position_id -> {symbol, opened_at}

    def update_open(self, current_positions: list) -> None:
        """Call each tick AFTER checking closes. Snapshots metadata for new ids."""
        for p in current_positions:
            if p.position_id not in self._position_meta:
                self._position_meta[p.position_id] = {
                    "symbol": p.symbol,
                    "opened_at": p.opened_at,
                }

    def detect_closes(self, current_positions: list) -> list[str]:
        """Compare current vs previously tracked positions. Returns closed ids."""
        current_ids = {p.position_id for p in current_positions}
        closed_ids = list(self._prev_position_ids - current_ids)
        self._prev_position_ids = current_ids
        return closed_ids

    @staticmethod
    def _field(deal: Any, key: str, default: Any = None) -> Any:
        if isinstance(deal, dict):
            return deal.get(key, default)
        return getattr(deal, key, default)

    async def fetch_deal_info(self, position_id: str) -> dict | None:
        """Get deal info for a closed position via get_deals_by_time_range."""
        try:
            conn = await self._get_conn()
            meta = self._position_meta.get(position_id, {})
            opened_at = meta.get(
                "opened_at", datetime.now(timezone.utc) - timedelta(days=1)
            )
            end_time = datetime.now(timezone.utc) + timedelta(minutes=5)
            raw = await conn.get_deals_by_time_range(
                start_time=opened_at, end_time=end_time
            )
            # get_deals_by_time_range returns {"deals": [...]} (MetatraderDeals);
            # tolerate a plain list too.
            deals = raw.get("deals", []) if isinstance(raw, dict) else (raw or [])
            matching = [
                d
                for d in deals
                if str(self._field(d, "positionId", "")) == position_id
            ]
            if not matching:
                return None
            total_profit = sum(
                float(self._field(d, "profit", 0) or 0) for d in matching
            )
            total_swap = sum(float(self._field(d, "swap", 0) or 0) for d in matching)
            total_commission = sum(
                float(self._field(d, "commission", 0) or 0) for d in matching
            )
            net_pnl = total_profit + total_swap + total_commission
            return {
                "position_id": position_id,
                "symbol": meta.get("symbol", ""),
                "pnl": net_pnl,
                "opened_at": opened_at,
                "closed_at": datetime.now(timezone.utc),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception(f"fetch_deal_info failed for {position_id}: {e}")
            return None

    def cleanup_meta(self, closed_ids: list[str]) -> None:
        for pid in closed_ids:
            self._position_meta.pop(pid, None)
