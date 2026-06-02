from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.engine.intent_bus import serialize_intent
from src.engine.runtime import AppRuntime
from src.products.models import CloseIntent, TradeIntent
from src.risk.models import RiskParams

# Map an internal product key to its external token product code.
_TOKEN_PRODUCT_CODE = {
    "gold_ai": "ai_xaupro",
    "multi_cfd_ai": "ai_multicfd",
}


def _resolve_product(runtime: AppRuntime, symbol: str) -> tuple[Any, str, str]:
    """Return (product, product_key, token_product_code) for a closed symbol."""
    if "XAU" in symbol.upper() or "GOLD" in symbol.upper():
        key = "gold_ai"
    else:
        key = "multi_cfd_ai"
    return runtime.products.get(key), key, _TOKEN_PRODUCT_CODE[key]


async def _handle_closes(runtime: AppRuntime, positions: list, dry_run: bool, now: datetime) -> None:
    """Detect positions that closed since the last tick and record their PnL."""
    closed_ids = runtime.close_detector.detect_closes(positions)
    for closed_id in closed_ids:
        deal = await runtime.close_detector.fetch_deal_info(closed_id)
        if deal is None:
            continue
        symbol = deal["symbol"]
        product, _key, product_code = _resolve_product(runtime, symbol)

        if product is not None:
            product.record_trade_closed(deal["pnl"])

        if not dry_run:
            result = await runtime.token_service.add_trade(
                customer_id=runtime.settings.primary_customer_id,
                product_code=product_code,
                metaapi_position_id=closed_id,
                symbol=symbol,
                pnl=deal["pnl"],
                opened_at=deal["opened_at"],
                closed_at=deal["closed_at"],
            )
            runtime.intent_bus.publish(
                product_code,
                "trade_closed",
                {
                    "position_id": closed_id,
                    "pnl": deal["pnl"],
                    "token_updated": result.ok,
                    "expired": result.expired,
                    "expiry_reason": result.expiry_reason,
                },
                dry_run,
                now,
            )
        else:
            runtime.intent_bus.publish(
                product_code,
                "trade_closed_dryrun",
                {"position_id": closed_id, "pnl": deal["pnl"]},
                dry_run,
                now,
            )

    runtime.close_detector.cleanup_meta(closed_ids)
    runtime.close_detector.update_open(positions)


async def run_tick(runtime: AppRuntime) -> None:
    now = datetime.now(timezone.utc)
    runtime.last_tick = now
    dry_run = runtime.settings.dry_run

    try:
        positions = await runtime.position_poller.fetch_all()

        # Detect closes that happened since the last tick (PnL -> tokens).
        await _handle_closes(runtime, positions, dry_run, now)

        if "gold_ai" in runtime.products:
            gold = runtime.products["gold_ai"]
            symbol = runtime.settings.gold_ai_symbol
            snap = await runtime.snapshot_fetcher.fetch(symbol)
            if snap is None:
                payload = {"reason": "snapshot_fetch_failed", "symbol": symbol}
                last_err = getattr(runtime.snapshot_fetcher, "_last_error", None)
                if last_err:
                    payload.update(last_err)
                runtime.intent_bus.publish("gold_ai", "error", payload, dry_run, now)
            else:
                result = gold.evaluate(snap, positions, now)
                await _handle_eval_result(runtime, "gold_ai", result, dry_run, now)

        if "multi_cfd_ai" in runtime.products:
            mcfd = runtime.products["multi_cfd_ai"]
            symbols = runtime.settings.multi_cfd_ai_symbols
            snapshots: dict = {}
            failed_symbols: list[str] = []
            for s in symbols:
                snap = await runtime.snapshot_fetcher.fetch(s)
                if snap is not None:
                    snapshots[s] = snap
                else:
                    failed_symbols.append(s)
            if not snapshots:
                payload = {
                    "reason": "all_snapshots_failed",
                    "symbols": list(symbols),
                }
                last_err = getattr(runtime.snapshot_fetcher, "_last_error", None)
                if last_err:
                    payload.update(last_err)
                runtime.intent_bus.publish(
                    "multi_cfd_ai", "error", payload, dry_run, now
                )
            else:
                result = mcfd.evaluate(snapshots, positions, now)
                await _handle_eval_result(
                    runtime, "multi_cfd_ai", result, dry_run, now
                )

        if positions:
            sl_intents = runtime.position_manager.evaluate_all(
                positions, RiskParams.default()
            )
            for si in sl_intents:
                if not dry_run:
                    ok = await runtime.order_executor.execute_modify_sl(si)
                    payload = serialize_intent(si)
                    if not ok:
                        payload = {
                            **payload,
                            **(runtime.order_executor._last_error or {}),
                        }
                    runtime.intent_bus.publish(
                        "position_manager",
                        "modify_sl_executed" if ok else "modify_sl_failed",
                        payload,
                        dry_run,
                        now,
                    )
                else:
                    runtime.intent_bus.publish(
                        "position_manager",
                        "modify_sl",
                        serialize_intent(si),
                        dry_run,
                        now,
                    )

        runtime.last_tick_status = "ok"
    except Exception as e:  # noqa: BLE001
        logger.exception("tick failed: {}", e)
        runtime.last_tick_status = f"error: {e}"


async def _handle_eval_result(
    runtime: AppRuntime, product: str, result: Any, dry_run: bool, now: datetime
) -> None:
    """Publish (and, when live, execute) the intents produced by a product."""
    bus = runtime.intent_bus

    if result is None:
        bus.publish(product, "none", {"reason": "no_signal"}, dry_run, now)
        return

    items = result if isinstance(result, list) else [result]
    if not items:
        bus.publish(product, "none", {"reason": "no_signal"}, dry_run, now)
        return

    # Phase 6 — when frozen, swallow NEW opens but let closes through.
    # is_frozen() is cached for ~30s; never raises (returns False on failure).
    try:
        frozen = await runtime.freeze_manager.is_frozen()
    except Exception as exc:  # noqa: BLE001
        logger.warning("freeze check raised (defaulting to unfrozen): {}", exc)
        frozen = False

    for item in items:
        if frozen and isinstance(item, TradeIntent):
            bus.publish(
                product,
                "frozen_skip",
                {**serialize_intent(item), "reason": "engine_frozen"},
                dry_run,
                now,
            )
            continue

        if dry_run:
            kind = "close" if isinstance(item, CloseIntent) else "open"
            bus.publish(product, kind, serialize_intent(item), dry_run, now)
            continue

        if isinstance(item, CloseIntent):
            ok = await runtime.order_executor.execute_close(item)
            payload = serialize_intent(item)
            if not ok:
                payload = {**payload, **(runtime.order_executor._last_error or {})}
            bus.publish(
                product,
                "close_executed" if ok else "close_failed",
                payload,
                dry_run,
                now,
            )
        elif isinstance(item, TradeIntent):
            res = await runtime.order_executor.execute_open(item)
            if res is not None:
                bus.publish(
                    product,
                    "open_executed",
                    {**serialize_intent(item), **res},
                    dry_run,
                    now,
                )
            else:
                bus.publish(
                    product,
                    "open_failed",
                    {
                        **serialize_intent(item),
                        **(runtime.order_executor._last_error or {}),
                    },
                    dry_run,
                    now,
                )
