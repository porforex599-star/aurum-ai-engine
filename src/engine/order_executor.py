from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from loguru import logger

from src.engine.stops_padding import pad_stops_for_broker
from src.engine.symbol_spec_cache import SymbolSpecCache
from src.products.models import CloseIntent, ModifySLIntent, TradeIntent
from src.strategy.models import SignalSide


@dataclass(frozen=True)
class OpenOutcome:
    """Result of a padding-aware open attempt.

    status:
      - "executed"                   → order placed; `result` holds order metadata.
      - "skipped_rr_too_low"         → padding degraded R:R below the floor; not placed.
      - "skipped_padding_unavailable"→ the inputs padding needs (live price /
                                        symbol spec) were unavailable, so the
                                        order was NOT placed. `reason` carries the
                                        specific cause. We deliberately do NOT
                                        fall back to a raw placement: the broker
                                        rejects raw stops with "Invalid stops"
                                        anyway, so a raw fallback is both useless
                                        and a silent bypass of the safety system.
      - "failed"                     → placement/geometry error; `error` set.
    """

    status: str
    result: dict | None = None
    error: dict | None = None
    padding: dict | None = None
    reason: str | None = None


class OrderExecutor:
    """Routes intents to a MetaApi RPC connection. Records last_error on failure."""

    def __init__(
        self,
        rpc_connection_provider: Callable[[], Any],
        spec_cache: SymbolSpecCache | None = None,
        safety_buffer_points: int = 10,
        min_padded_rr: float = 1.2,
    ) -> None:
        """rpc_connection_provider: async callable returning a connected
        RpcMetaApiConnectionInstance (use runtime.get_rpc_connection).

        spec_cache: SymbolSpecCache used by execute_open_with_padding. When
        None, padding is skipped and orders are placed raw."""
        self._get_conn = rpc_connection_provider
        self._spec_cache = spec_cache
        self._safety_buffer_points = safety_buffer_points
        self._min_padded_rr = min_padded_rr
        self._last_error: dict | None = None

    @staticmethod
    def _field(result: Any, key: str) -> str:
        if isinstance(result, dict):
            return str(result.get(key) or "")
        return str(getattr(result, key, "") or "")

    @staticmethod
    def _price_field(price: Any, key: str) -> float:
        if isinstance(price, dict):
            return float(price.get(key) or 0.0)
        return float(getattr(price, key, 0.0) or 0.0)

    async def execute_open(self, intent: TradeIntent) -> dict | None:
        """Open a market order with the intent's SL/TP exactly as given (raw).

        Returns dict with order_id + metadata, or None. Does NOT pad — used by
        the manual test-trade endpoint and as the final placement step of
        execute_open_with_padding."""
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

    async def execute_open_with_padding(self, intent: TradeIntent) -> OpenOutcome:
        """Re-anchor SL/TP to the live price, pad to the broker minimum stop
        distance, enforce an R:R floor, then place the order.

        If the inputs padding requires (live price or symbol spec) are
        unavailable, the order is SKIPPED rather than placed raw — a raw
        placement just gets rejected by the broker ("Invalid stops") while
        silently bypassing the safety system."""
        self._last_error = None

        # 1. Live price for re-anchoring (ask for BUY, bid for SELL).
        try:
            conn = await self._get_conn()
            price = await conn.get_symbol_price(symbol=intent.symbol)
        except Exception as e:  # noqa: BLE001
            return self._padding_unavailable(
                intent,
                "padding_unavailable_price_fetch",
                exc=e,
            )

        entry = (
            self._price_field(price, "ask")
            if intent.side == SignalSide.BUY
            else self._price_field(price, "bid")
        )
        if entry <= 0:
            return self._padding_unavailable(
                intent,
                "padding_unavailable_price_fetch",
                detail=f"non-positive live price ({entry})",
            )

        # 2. Symbol spec (cached). No cache configured → no padding possible.
        #    Production always wires a SymbolSpecCache (see AppRuntime), so this
        #    branch is only reachable in the manual/test path that opts out of
        #    padding entirely; there we keep the raw placement.
        if self._spec_cache is None:
            return self._raw_outcome(await self.execute_open(intent))
        try:
            spec = await self._spec_cache.get(intent.symbol)
        except Exception as e:  # noqa: BLE001
            return self._padding_unavailable(
                intent,
                "padding_unavailable_spec_miss",
                exc=e,
            )

        if spec.point <= 0:
            return self._padding_unavailable(
                intent,
                "padding_unavailable_spec_miss",
                detail=f"invalid point ({spec.point})",
            )

        # 3. Pad outward to the broker minimum stop distance.
        try:
            padded = pad_stops_for_broker(
                side=intent.side.value,
                entry_price=entry,
                sl=intent.sl_price,
                tp=intent.tp_price,
                stops_level_points=spec.stops_level,
                point=spec.point,
                safety_buffer_points=self._safety_buffer_points,
            )
        except ValueError as e:
            self._last_error = {"exc_type": "ValueError", "exc_msg": str(e)[:300]}
            logger.warning("padding: invalid geometry for {}: {}", intent.symbol, e)
            return OpenOutcome(status="failed", error=self._last_error)

        digits = spec.digits if spec.digits > 0 else 5
        padded_sl = round(padded.sl, digits)
        padded_tp = round(padded.tp, digits) if padded.tp is not None else None

        padding_meta: dict[str, Any] = {
            "entry_price": entry,
            "sl_price": padded_sl,
            "tp_price": padded_tp,
            "adjusted": padded.adjusted,
        }
        if padded.adjusted:
            padding_meta["sl_original"] = intent.sl_price
            padding_meta["tp_original"] = intent.tp_price
        if padded.rr is not None:
            padding_meta["padded_rr"] = round(padded.rr, 3)
            padding_meta["min_rr"] = self._min_padded_rr

        # 4. R:R floor — don't fire terrible-quality orders just because we
        #    widened the stops.
        if padded.rr is not None and padded.rr < self._min_padded_rr:
            logger.info(
                "padding: {} R:R {:.3f} < floor {} after padding — skipping",
                intent.symbol,
                padded.rr,
                self._min_padded_rr,
            )
            return OpenOutcome(
                status="skipped_rr_too_low",
                reason="rr_too_low",
                padding=padding_meta,
            )

        if padded.adjusted:
            logger.info(
                "padding: {} {} SL {}->{} TP {}->{} (entry={}, stopsLevel={})",
                intent.symbol,
                intent.side.value,
                intent.sl_price,
                padded_sl,
                intent.tp_price,
                padded_tp,
                entry,
                spec.stops_level,
            )

        # 5. Place with padded values (live entry anchor recorded too).
        padded_intent = replace(
            intent, entry_price=entry, sl_price=padded_sl, tp_price=padded_tp
        )
        result = await self.execute_open(padded_intent)
        if result is None:
            return OpenOutcome(status="failed", error=self._last_error)
        return OpenOutcome(status="executed", result=result, padding=padding_meta)

    def _raw_outcome(self, result: dict | None) -> OpenOutcome:
        if result is None:
            return OpenOutcome(status="failed", error=self._last_error)
        return OpenOutcome(status="executed", result=result)

    def _padding_unavailable(
        self,
        intent: TradeIntent,
        reason: str,
        exc: Exception | None = None,
        detail: str | None = None,
    ) -> OpenOutcome:
        """Skip the open because padding inputs were unavailable. Never places
        a raw order — surfaces a categorized skip so Telegram shows e.g.
        `padding_unavailable_price_fetch` instead of `open_failed Invalid stops`."""
        error: dict[str, str] = {}
        if exc is not None:
            error = {"exc_type": type(exc).__name__, "exc_msg": str(exc)[:300]}
        elif detail is not None:
            error = {"exc_msg": detail}
        self._last_error = error or None
        logger.warning(
            "padding unavailable for {} ({}): {} — skipping (no raw placement)",
            intent.symbol,
            reason,
            error.get("exc_msg", ""),
        )
        return OpenOutcome(
            status="skipped_padding_unavailable",
            reason=reason,
            error=error or None,
        )

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
