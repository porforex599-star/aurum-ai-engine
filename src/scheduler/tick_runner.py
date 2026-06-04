from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from loguru import logger

from src.engine.intent_bus import serialize_intent
from src.engine.master_account import (
    is_product_position,
    normalize_symbol,
    parse_setup,
)
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


def _attribute_product(runtime: AppRuntime, symbol: str, comment: str | None) -> str | None:
    """Phase 6.4 attribution: match a closed trade to a product by symbol-set
    membership + the "AURUM_AI <setup>" comment guard. A bare "AURUM_AI" manual
    order matches nothing, so it's excluded from product stats. Returns the
    product key or None."""
    for key, product in runtime.products.items():
        config = getattr(product, "config", None)
        symbols = getattr(config, "symbols", None)
        if symbols and is_product_position(symbol, comment, symbols):
            return key
    return None


def _duration_seconds(opened_at: Any, closed_at: Any) -> int | None:
    if isinstance(opened_at, datetime) and isinstance(closed_at, datetime):
        return int((closed_at - opened_at).total_seconds())
    return None


async def _persist_closed_trade(
    runtime: AppRuntime, position_id: str, deal: dict, dry_run: bool
) -> None:
    """Phase 6.5 — log the closed trade to master_closed_trades for stats.

    Runs in both dry_run and live so paper history is captured. Attribution is
    the Phase 6.4 scheme; unattributed closes (manual orders, foreign symbols)
    are skipped. Never raises — the ledger is best-effort bookkeeping."""
    trade_logger = getattr(runtime, "trade_logger", None)
    if trade_logger is None:
        return
    symbol = deal.get("symbol", "") or ""
    product_key = _attribute_product(runtime, symbol, deal.get("comment"))
    if product_key is None:
        return
    await trade_logger.record_closed_trade(
        position_id=position_id,
        product=product_key,
        symbol=symbol,
        symbol_norm=normalize_symbol(symbol),
        pnl=deal["pnl"],
        closed_at=deal["closed_at"],
        opened_at=deal.get("opened_at"),
        side=deal.get("side"),
        lot=deal.get("lot"),
        setup=parse_setup(deal.get("comment")),
        entry_price=deal.get("entry_price"),
        exit_price=deal.get("exit_price"),
        gross_profit=deal.get("gross_profit"),
        swap=deal.get("swap"),
        commission=deal.get("commission"),
        duration_seconds=_duration_seconds(deal.get("opened_at"), deal.get("closed_at")),
        dry_run=dry_run,
    )


async def _handle_closes(
    runtime: AppRuntime, bundle: Any, positions: list, dry_run: bool, now: datetime
) -> None:
    """Detect positions that closed since the last tick and record their PnL.

    Phase 7 Stage 2: close detection is per-account (`bundle.close_detector`), so
    each master tracks its own open set. Attribution, token + ledger writes and
    the signal lock remain engine-global."""
    closed_ids = bundle.close_detector.detect_closes(positions)
    for closed_id in closed_ids:
        deal = await bundle.close_detector.fetch_deal_info(closed_id)
        if deal is None:
            continue
        symbol = deal["symbol"]
        product, key, product_code = _resolve_product(runtime, symbol)

        # Phase 6.5 — persist to the stats ledger (both dry_run and live).
        await _persist_closed_trade(runtime, closed_id, deal, dry_run)

        if product is not None:
            product.record_trade_closed(deal["pnl"])

        # Release the per-(product, symbol) open guard now that the position is
        # gone. The signal cooldown (if any) is enforced separately.
        runtime.signal_lock.release(key, symbol)

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

    bundle.close_detector.cleanup_meta(closed_ids)
    bundle.close_detector.update_open(positions)


def _flat_bundle(runtime: AppRuntime) -> Any:
    """Backward-compat bundle built from the runtime's flat attributes.

    Used when a runtime predates the per-product accessor (the existing unit
    tests, which inject single mock components). All products then share this
    one bundle, exactly reproducing the original single-master tick."""
    return SimpleNamespace(
        account_id="__default__",
        position_poller=runtime.position_poller,
        close_detector=runtime.close_detector,
        snapshot_fetcher=runtime.snapshot_fetcher,
        order_executor=runtime.order_executor,
    )


async def _bundle_for(runtime: AppRuntime, slug: str) -> Any:
    getter = getattr(runtime, "get_bundle_for_product", None)
    if getter is None:
        return _flat_bundle(runtime)
    return await getter(slug)


async def _executor_for(runtime: AppRuntime, slug: str) -> Any:
    """The order executor for `slug`'s master. Minimal attribute footprint so it
    works with the lean runtimes used by the freeze-gating unit tests."""
    getter = getattr(runtime, "get_bundle_for_product", None)
    if getter is None:
        return runtime.order_executor
    return (await getter(slug)).order_executor


async def run_tick(runtime: AppRuntime) -> None:
    now = datetime.now(timezone.utc)
    runtime.last_tick = now
    dry_run = runtime.settings.dry_run

    try:
        # Phase 7 Stage 2 — resolve each product's master, then dedup by account
        # so products sharing a master poll/close once. Single-master: exactly
        # one bundle, identical to the original tick.
        product_bundles: dict[str, Any] = {
            slug: await _bundle_for(runtime, slug) for slug in runtime.products
        }
        bundles_by_account: dict[str, Any] = {}
        for bundle in product_bundles.values():
            bundles_by_account.setdefault(bundle.account_id, bundle)
        # No products registered → still service the default account (closes/SL).
        if not bundles_by_account:
            b = await _bundle_for(runtime, "")
            bundles_by_account[b.account_id] = b

        # 1. Per account: fetch open positions + detect/handle closes.
        positions_by_account: dict[str, list] = {}
        for account_id, bundle in bundles_by_account.items():
            positions = await bundle.position_poller.fetch_all()
            positions_by_account[account_id] = positions
            await _handle_closes(runtime, bundle, positions, dry_run, now)

        # 2. Per product: evaluate strategy on its own master's data + execute.
        if "gold_ai" in runtime.products:
            gold = runtime.products["gold_ai"]
            bundle = product_bundles["gold_ai"]
            positions = positions_by_account.get(bundle.account_id, [])
            symbol = runtime.settings.gold_ai_symbol
            snap = await bundle.snapshot_fetcher.fetch(symbol)
            if snap is None:
                payload = {"reason": "snapshot_fetch_failed", "symbol": symbol}
                last_err = getattr(bundle.snapshot_fetcher, "_last_error", None)
                if last_err:
                    payload.update(last_err)
                runtime.intent_bus.publish("gold_ai", "error", payload, dry_run, now)
            else:
                result = gold.evaluate(snap, positions, now)
                await _handle_eval_result(runtime, "gold_ai", result, dry_run, now)

        if "multi_cfd_ai" in runtime.products:
            mcfd = runtime.products["multi_cfd_ai"]
            bundle = product_bundles["multi_cfd_ai"]
            positions = positions_by_account.get(bundle.account_id, [])
            symbols = runtime.settings.multi_cfd_ai_symbols
            snapshots: dict = {}
            failed_symbols: list[str] = []
            for s in symbols:
                snap = await bundle.snapshot_fetcher.fetch(s)
                if snap is not None:
                    snapshots[s] = snap
                else:
                    failed_symbols.append(s)
            if not snapshots:
                payload = {
                    "reason": "all_snapshots_failed",
                    "symbols": list(symbols),
                }
                last_err = getattr(bundle.snapshot_fetcher, "_last_error", None)
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

        # 3. Per account: SL trailing on that account's positions.
        for account_id, bundle in bundles_by_account.items():
            positions = positions_by_account.get(account_id, [])
            if not positions:
                continue
            sl_intents = runtime.position_manager.evaluate_all(
                positions, RiskParams.default()
            )
            for si in sl_intents:
                if not dry_run:
                    ok = await bundle.order_executor.execute_modify_sl(si)
                    payload = serialize_intent(si)
                    if not ok:
                        payload = {
                            **payload,
                            **(bundle.order_executor._last_error or {}),
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
    """Publish (and, when live, execute) the intents produced by a product.

    Phase 7 Stage 2: order execution routes to the product's master executor
    (resolved via its bundle); the freeze gate, signal lock and bus stay
    engine-global."""
    bus = runtime.intent_bus
    executor = await _executor_for(runtime, product)

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
            ok = await executor.execute_close(item)
            payload = serialize_intent(item)
            if not ok:
                payload = {**payload, **(executor._last_error or {})}
            bus.publish(
                product,
                "close_executed" if ok else "close_failed",
                payload,
                dry_run,
                now,
            )
        elif isinstance(item, TradeIntent):
            # Per-(product, symbol) open guard + signal cooldown. Guards against
            # the position-feed lag that let one setup fire as 8 stacked orders.
            lock_reason = runtime.signal_lock.status(product, item.symbol, now)
            if lock_reason is not None:
                bus.publish(
                    product,
                    "signal_skipped_position_locked",
                    {
                        **serialize_intent(item),
                        "reason": lock_reason,
                        "existing_position_id": runtime.signal_lock.existing_position_id(
                            product, item.symbol
                        ),
                    },
                    dry_run,
                    now,
                )
                continue

            outcome = await executor.execute_open_with_padding(item)
            if outcome.status == "executed":
                payload = {**serialize_intent(item), **(outcome.result or {})}
                if outcome.padding:
                    payload.update(outcome.padding)
                # Lock immediately on a real fill — independent of the streaming
                # position feed, which can trail this open by several ticks.
                runtime.signal_lock.record_open(
                    product, item.symbol, now, (outcome.result or {}).get("position_id")
                )
                bus.publish(product, "open_executed", payload, dry_run, now)
            elif outcome.status == "skipped_rr_too_low":
                payload = {
                    **serialize_intent(item),
                    **(outcome.padding or {}),
                    "reason": outcome.reason or "rr_too_low",
                }
                bus.publish(product, "skipped_rr_too_low", payload, dry_run, now)
            elif outcome.status == "skipped_padding_unavailable":
                # Padding inputs missing → explicit skip, NOT a raw placement.
                bus.publish(
                    product,
                    outcome.reason or "padding_unavailable",
                    {**serialize_intent(item), **(outcome.error or {})},
                    dry_run,
                    now,
                )
            else:
                bus.publish(
                    product,
                    "open_failed",
                    {**serialize_intent(item), **(outcome.error or {})},
                    dry_run,
                    now,
                )
