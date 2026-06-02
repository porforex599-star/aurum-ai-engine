from __future__ import annotations

from typing import Any, Callable

from loguru import logger

from src.products.models import CloseIntent, ModifySLIntent, TradeIntent
from src.strategy.models import SignalSide


class OrderExecutor:
    """Routes intents to a MetaApi RPC connection. Records last_error on failure."""

    def __init__(self, rpc_connection_provider: Callable[[], Any]) -> None:
        """rpc_connection_provider: async callable returning a connected
        RpcMetaApiConnectionInstance (use runtime.get_rpc_connection)."""
        self._get_conn = rpc_connection_provider
        self._last_error: dict | None = None

    @staticmethod
    def _field(result: Any, key: str) -> str:
        if isinstance(result, dict):
            return str(result.get(key) or "")
        return str(getattr(result, key, "") or "")

    async def execute_open(self, intent: TradeIntent) -> dict | None:
        """Open a market order. Returns dict with order_id + metadata, or None."""
        self._last_error = None
        try:
            conn = await self._get_conn()
            comment = f"AURUM_AI {intent.setup.value if intent.setup else ''}"[:31]
            if intent.side == SignalSide.BUY:
                result = await conn.create_market_buy_order(
                    symbol=intent.symbol,
                    volume=intent.lot,
                    stop_loss=intent.sl_price,
                    take_profit=intent.tp_price,
                    options={"comment": comment},
                )
            else:
                result = await conn.create_market_sell_order(
                    symbol=intent.symbol,
                    volume=intent.lot,
                    stop_loss=intent.sl_price,
                    take_profit=intent.tp_price,
                    options={"comment": comment},
                )
            return {
                "order_id": self._field(result, "orderId"),
                "position_id": self._field(result, "positionId"),
                "symbol": intent.symbol,
                "side": intent.side.value,
                "lot": intent.lot,
            }
        except Exception as e:  # noqa: BLE001
            self._last_error = {"exc_type": type(e).__name__, "exc_msg": str(e)[:300]}
            logger.exception(f"execute_open failed for {intent.symbol}: {e}")
            return None

    async def execute_close(self, intent: CloseIntent) -> bool:
        self._last_error = None
        try:
            conn = await self._get_conn()
            await conn.close_position(position_id=intent.position_id)
            return True
        except Exception as e:  # noqa: BLE001
            self._last_error = {"exc_type": type(e).__name__, "exc_msg": str(e)[:300]}
            logger.exception(f"execute_close failed for {intent.position_id}: {e}")
            return False

    async def execute_modify_sl(self, intent: ModifySLIntent) -> bool:
        self._last_error = None
        try:
            conn = await self._get_conn()
            await conn.modify_position(
                position_id=intent.position_id, stop_loss=intent.new_sl_price
            )
            return True
        except Exception as e:  # noqa: BLE001
            self._last_error = {"exc_type": type(e).__name__, "exc_msg": str(e)[:300]}
            logger.exception(
                f"execute_modify_sl failed for {intent.position_id}: {e}"
            )
            return False
