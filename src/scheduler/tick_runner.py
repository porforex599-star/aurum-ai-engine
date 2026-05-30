from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.engine.intent_bus import IntentBus, serialize_intent
from src.engine.runtime import AppRuntime
from src.products.models import CloseIntent
from src.risk.models import RiskParams


async def run_tick(runtime: AppRuntime) -> None:
    now = datetime.now(timezone.utc)
    runtime.last_tick = now
    dry_run = runtime.settings.dry_run

    try:
        positions = await runtime.position_poller.fetch_all()

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
                _publish_eval_result(runtime.intent_bus, "gold_ai", result, dry_run, now)

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
                _publish_eval_result(
                    runtime.intent_bus, "multi_cfd_ai", result, dry_run, now
                )

        if positions:
            sl_intents = runtime.position_manager.evaluate_all(
                positions, RiskParams.default()
            )
            for si in sl_intents:
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


def _publish_eval_result(
    bus: IntentBus, product: str, result: Any, dry_run: bool, now: datetime
) -> None:
    if result is None:
        bus.publish(product, "none", {"reason": "no_signal"}, dry_run, now)
        return
    if isinstance(result, list):
        if not result:
            bus.publish(product, "none", {"reason": "no_signal"}, dry_run, now)
            return
        for item in result:
            kind = "close" if isinstance(item, CloseIntent) else "open"
            bus.publish(product, kind, serialize_intent(item), dry_run, now)
        return
    bus.publish(product, "open", serialize_intent(result), dry_run, now)
