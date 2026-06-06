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

    @staticmethod
    def _deal_dt(deal: Any) -> datetime | None:
        """Coerce a deal `time` (datetime or ISO string) to a datetime."""
        t = CloseDetector._field(deal, "time")
        if isinstance(t, datetime):
            return t
        if isinstance(t, str) and t:
            try:
                return datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    async def fetch_deal_info(self, position_id: str) -> dict | None:
        """Get deal info for a closed position via get_deals_by_time_range.

        Returns net PnL plus the fields the Phase 6.5 stats ledger needs: real
        broker entry time (not the tick-poll sighting time), entry/exit prices,
        side, volume, comment (the "AURUM_AI <setup>" tag) and the PnL split.
        """
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

            # Split entry (DEAL_ENTRY_IN) from exit (DEAL_ENTRY_OUT) deals so we
            # can recover real open time + entry/exit prices. Fall back
            # gracefully when a broker omits entryType.
            entries = [
                d
                for d in matching
                if str(self._field(d, "entryType", "") or "").upper().endswith("_IN")
            ]
            exits = [
                d
                for d in matching
                if str(self._field(d, "entryType", "") or "").upper().endswith("_OUT")
            ]
            entry_deal = entries[0] if entries else matching[0]
            exit_deal = exits[-1] if exits else None

            side_raw = str(self._field(entry_deal, "type", "") or "").lower()
            side = "BUY" if "buy" in side_raw else "SELL" if "sell" in side_raw else None

            real_opened = self._deal_dt(entry_deal) or (
                opened_at if isinstance(opened_at, datetime) else None
            )
            real_closed = self._deal_dt(exit_deal) if exit_deal else None
            closed_at = real_closed or datetime.now(timezone.utc)

            comment = self._field(entry_deal, "comment") or (
                self._field(exit_deal, "comment") if exit_deal else None
            )
            symbol = (
                str(self._field(entry_deal, "symbol", "") or "")
                or meta.get("symbol", "")
            )
            entry_price = self._field(entry_deal, "price")
            exit_price = self._field(exit_deal, "price") if exit_deal else None
            lot = self._field(entry_deal, "volume")

            return {
                "position_id": position_id,
                "symbol": symbol,
                "pnl": net_pnl,
                "opened_at": real_opened or opened_at,
                "closed_at": closed_at,
                "side": side,
                "lot": float(lot) if lot is not None else None,
                "comment": comment,
                "entry_price": float(entry_price) if entry_price is not None else None,
                "exit_price": float(exit_price) if exit_price is not None else None,
                "gross_profit": total_profit,
                "swap": total_swap,
                "commission": total_commission,
            }
        except Exception as e:  # noqa: BLE001
            logger.exception(f"fetch_deal_info failed for {position_id}: {e}")
            return None

    def cleanup_meta(self, closed_ids: list[str]) -> None:
        for pid in closed_ids:
            self._position_meta.pop(pid, None)
