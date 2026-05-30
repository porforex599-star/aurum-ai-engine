from __future__ import annotations

import asyncio

from loguru import logger
from supabase import Client, create_client

from src.config import get_settings


class SupabaseClient:
    def __init__(self) -> None:
        self._client: Client | None = None
        self._connected: bool = False

    def connect(self) -> None:
        settings = get_settings()
        logger.info("Initializing Supabase client for {}", settings.SUPABASE_URL)
        self._client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
        self._connected = True

    async def ping(self) -> bool:
        if self._client is None:
            return False

        def _probe() -> bool:
            try:
                self._client.table("tokens").select("*").limit(0).execute()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Supabase ping failed: {}", exc)
                return False

        ok = await asyncio.to_thread(_probe)
        self._connected = ok
        return ok

    def is_connected(self) -> bool:
        return self._connected

    def get_client(self) -> Client | None:
        return self._client

    async def shutdown(self) -> None:
        self._client = None
        self._connected = False


_client: SupabaseClient | None = None


def get_supabase_client() -> SupabaseClient:
    global _client
    if _client is None:
        _client = SupabaseClient()
    return _client
