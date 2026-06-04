from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from src import __version__
from src.api.health import router as health_router
from src.api.masters import router as masters_router
from src.config import get_settings
from src.core.metaapi_client import get_metaapi_client
from src.core.supabase_client import get_supabase_client


def _configure_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, enqueue=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)
    logger.info(
        "Starting Aurum AI Engine v{} (env={}, tz={})",
        __version__,
        settings.APP_ENV,
        settings.TIMEZONE,
    )

    supabase = get_supabase_client()
    supabase.connect()
    await supabase.ping()

    metaapi = get_metaapi_client()
    try:
        await metaapi.connect()
    except Exception as exc:  # noqa: BLE001
        logger.error("MetaApi connection failed at startup: {}", exc)

    try:
        yield
    finally:
        logger.info("Shutting down Aurum AI Engine")
        await metaapi.shutdown()
        await supabase.shutdown()


app = FastAPI(title="Aurum AI Engine", version=__version__, lifespan=lifespan)
app.include_router(health_router)
app.include_router(masters_router)
