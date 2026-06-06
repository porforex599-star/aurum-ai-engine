from __future__ import annotations

import asyncio

from loguru import logger
from supabase import Client, create_client

from src.config import get_settings


class SupabaseClient:
    """Thin async-friendly wrapper around a service-role Supabase client.

    A single Aurum deployment talks to more than one Supabase project (the
    engine's own project and the separate customer-facing project), so this
    class is parametrized with the credentials it should use.
    """

    def __init__(
        self, url: str, key: str, *, label: str = "supabase", ping_table: str = "tokens"
    ) -> None:
        self._url = url
        self._key = key
        self._label = label
        self._ping_table = ping_table
        self._client: Client | None = None
        self._connected: bool = False

    def connect(self) -> None:
        logger.info("Initializing Supabase client [{}] for {}", self._label, self._url)
        self._client = create_client(self._url, self._key)
        self._connected = True

    async def ping(self) -> bool:
        if self._client is None:
            return False

        def _probe() -> bool:
            try:
                self._client.table(self._ping_table).select("*").limit(0).execute()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Supabase ping failed [{}]: {}", self._label, exc)
                return False

        ok = await asyncio.to_thread(_probe)
        self._connected = ok
        return ok

    def is_connected(self) -> bool:
        return self._connected

    def get_client(self) -> Client | None:
        return self._client

    async def insert_row(self, table: str, row: dict) -> dict:
        """Insert a single row into a public-schema table and return it.

        The synchronous postgrest call is offloaded to a worker thread so it
        never blocks the event loop. The inserted row (including any
        DB-generated columns such as `id`) is returned.
        """
        if self._client is None:
            raise RuntimeError("Supabase client is not initialized")

        def _insert() -> list[dict]:
            response = self._client.table(table).insert(row).execute()
            return response.data or []

        data = await asyncio.to_thread(_insert)
        return data[0] if data else {}

    async def shutdown(self) -> None:
        self._client = None
        self._connected = False


_client: SupabaseClient | None = None
_customers_client: SupabaseClient | None = None


def get_supabase_client() -> SupabaseClient:
    """Client for the engine's own Supabase project."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = SupabaseClient(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,
            label="engine",
            ping_table="tokens",
        )
    return _client


def get_customers_client() -> SupabaseClient:
    """Client for the separate `aurum-customers` Supabase project."""
    global _customers_client
    if _customers_client is None:
        settings = get_settings()
        _customers_client = SupabaseClient(
            settings.SUPABASE_CUSTOMERS_URL,
            settings.SUPABASE_CUSTOMERS_SERVICE_ROLE_KEY,
            label="customers",
            ping_table="analysis_posts",
        )
    return _customers_client
