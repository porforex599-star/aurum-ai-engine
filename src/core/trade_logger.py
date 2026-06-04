"""Phase 6.5 — persist closed master-account trades for dashboard stats.

Thin async wrapper around the `master_closed_trades` table. Mirrors the
TokenService style: methods are async, never raise (log + swallow), and degrade
to no-ops when the Supabase client is unavailable (dev / unit tests).

Writes happen from the tick loop on every detected close, regardless of
dry_run, so paper-trade history is captured too (flagged dry_run=TRUE).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger


def _iso(value: Any) -> Any:
    """Serialize datetimes to ISO strings for the JSON insert; pass through rest."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class TradeLogger:
    _TABLE = "master_closed_trades"

    def __init__(self, supabase_client: Any) -> None:
        self.sb = supabase_client

    async def record_closed_trade(
        self,
        *,
        position_id: str,
        product: str,
        symbol: str,
        symbol_norm: str,
        pnl: float,
        closed_at: datetime,
        opened_at: datetime | None = None,
        side: str | None = None,
        lot: float | None = None,
        setup: str | None = None,
        entry_price: float | None = None,
        exit_price: float | None = None,
        gross_profit: float | None = None,
        swap: float | None = None,
        commission: float | None = None,
        duration_seconds: int | None = None,
        dry_run: bool = False,
    ) -> bool:
        """Insert one closed-trade row. Idempotent on position_id (a tick retry
        or restart re-logging the same close is a no-op)."""
        if self.sb is None:
            return False
        row = {
            "position_id": position_id,
            "product": product,
            "symbol": symbol,
            "symbol_norm": symbol_norm,
            "side": side,
            "lot": lot,
            "setup": setup,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "gross_profit": gross_profit,
            "swap": swap,
            "commission": commission,
            "opened_at": _iso(opened_at),
            "closed_at": _iso(closed_at),
            "duration_seconds": duration_seconds,
            "dry_run": dry_run,
        }
        try:
            (
                self.sb.table(self._TABLE)
                .upsert(row, on_conflict="position_id", ignore_duplicates=True)
                .execute()
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.exception("record_closed_trade failed for {}: {}", position_id, e)
            return False

    async def fetch_trades(
        self,
        product: str,
        start: datetime | None = None,
        *,
        include_dry_run: bool = False,
        limit: int | None = None,
    ) -> list[dict]:
        """Read closed trades for a product, newest first. Returns [] on failure
        or when no Supabase client is wired."""
        if self.sb is None:
            return []
        try:
            q = self.sb.table(self._TABLE).select("*").eq("product", product)
            if not include_dry_run:
                q = q.eq("dry_run", False)
            if start is not None:
                q = q.gte("closed_at", _iso(start))
            q = q.order("closed_at", desc=True)
            if limit is not None:
                q = q.limit(limit)
            resp = q.execute()
            return resp.data or []
        except Exception as e:  # noqa: BLE001
            logger.exception("fetch_trades failed for {}: {}", product, e)
            return []
