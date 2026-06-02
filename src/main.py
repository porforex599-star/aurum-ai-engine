from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from loguru import logger

from src import __version__
from src.api.health import router as health_router
from src.api.intents import router as intents_router
from src.api.positions import router as positions_router
from src.api.status import router as status_router
from src.api.symbols import router as symbols_router
from src.api.test_trade import router as test_trade_router
from src.config import get_settings
from src.core.metaapi_client import get_metaapi_client
from src.core.supabase_client import get_supabase_client
from src.engine.runtime import AppRuntime, set_runtime
from src.scheduler.cron_jobs import run_friday_close
from src.scheduler.tick_runner import run_tick


def _configure_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, enqueue=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)
    logger.info(
        "Starting Aurum AI Engine v{} (env={}, tz={}, dry_run={})",
        __version__,
        settings.APP_ENV,
        settings.TIMEZONE,
        settings.dry_run,
    )

    supabase = get_supabase_client()
    supabase.connect()
    await supabase.ping()

    metaapi = get_metaapi_client()
    try:
        await metaapi.connect()
    except Exception as exc:  # noqa: BLE001
        logger.error("MetaApi connection failed at startup: {}", exc)

    scheduler: AsyncIOScheduler | None = None
    try:
        account = metaapi.get_account() if hasattr(metaapi, "get_account") else None
        connection = (
            metaapi.get_connection() if hasattr(metaapi, "get_connection") else None
        )
        runtime = AppRuntime(settings, account, connection, supabase)
        set_runtime(runtime)

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            run_tick,
            IntervalTrigger(seconds=settings.tick_interval_seconds),
            args=[runtime],
            id="tick",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_friday_close,
            CronTrigger(day_of_week="fri", hour=10, minute=0, timezone="UTC"),
            args=[runtime],
            id="friday_close",
        )
        scheduler.start()
        app.state.scheduler = scheduler
        app.state.runtime = runtime
        logger.info(
            "Scheduler started: tick every {}s", settings.tick_interval_seconds
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Runtime/scheduler init failed: {}", exc)

    try:
        yield
    finally:
        logger.info("Shutting down Aurum AI Engine")
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduler shutdown error: {}", exc)
        set_runtime(None)
        await metaapi.shutdown()
        await supabase.shutdown()


app = FastAPI(title="Aurum AI Engine", version=__version__, lifespan=lifespan)
app.include_router(health_router)
app.include_router(status_router)
app.include_router(positions_router)
app.include_router(intents_router)
app.include_router(symbols_router)
app.include_router(test_trade_router)
