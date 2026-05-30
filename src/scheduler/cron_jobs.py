from __future__ import annotations

from loguru import logger

from src.engine.runtime import AppRuntime


async def run_friday_close(runtime: AppRuntime) -> None:
    try:
        count = await runtime.token_service.friday_close()
        runtime.intent_bus.publish(
            "scheduler",
            "friday_close",
            {"expired_count": count},
            runtime.settings.dry_run,
        )
        logger.info("friday_close: expired {} tokens", count)
    except Exception as e:  # noqa: BLE001
        logger.exception("friday_close failed: {}", e)
