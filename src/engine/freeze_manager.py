"""Phase 6 — Engine freeze/unfreeze state.

Reads a single row from the `engine_config` table in Supabase (id='global').
Result is cached for `cache_ttl_seconds` so we don't hit the DB on every tick.

`is_frozen()` is intentionally robust: any DB / network error logs a warning
and returns the LAST KNOWN STATE (cached), or `False` if we have no cache yet.
Fail-open during transient DB outages — the alternative (fail-closed) would
silently halt trading every time Supabase hiccups, which is worse than the
miss-window risk of fail-open.

`set_frozen(...)` is used by the admin endpoints and invalidates the cache so
the next tick picks up the change within seconds.

Schema (apply via Supabase SQL editor — see `migrations/phase_6_engine_config.sql`):

    CREATE TABLE engine_config (
      id TEXT PRIMARY KEY,
      frozen BOOLEAN NOT NULL DEFAULT FALSE,
      frozen_reason TEXT,
      frozen_at TIMESTAMPTZ,
      frozen_by TEXT,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class FreezeState:
    frozen: bool
    reason: str | None = None
    frozen_at: datetime | None = None
    frozen_by: str | None = None
    updated_at: datetime | None = None
    cached: bool = False  # True when returned from cache without a fresh DB read

    @classmethod
    def unfrozen(cls) -> "FreezeState":
        return cls(frozen=False)


class FreezeManager:
    TABLE_NAME = "engine_config"
    GLOBAL_KEY = "global"

    def __init__(self, supabase_client: Any, cache_ttl_seconds: float = 30.0) -> None:
        """`supabase_client` may be the SupabaseClient wrapper or a raw client."""
        self._sb = supabase_client
        self._ttl = float(cache_ttl_seconds)
        self._cache: FreezeState | None = None
        self._cache_expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def _client(self) -> Any:
        sb = self._sb
        if hasattr(sb, "get_client"):
            return sb.get_client()
        return sb

    async def get_state(self, force_refresh: bool = False) -> FreezeState:
        """Return the freeze state, hitting Supabase only when the cache expires."""
        async with self._lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._cache is not None
                and now < self._cache_expires_at
            ):
                return replace(self._cache, cached=True)

            fresh = await self._fetch_from_db()
            if fresh is None:
                # DB read failed. Return last known state if we have one — better
                # than flipping to unfrozen on a transient network blip.
                if self._cache is not None:
                    return replace(self._cache, cached=True)
                return FreezeState.unfrozen()

            self._cache = fresh
            self._cache_expires_at = now + self._ttl
            return fresh

    async def is_frozen(self) -> bool:
        return (await self.get_state()).frozen

    async def set_frozen(
        self,
        frozen: bool,
        reason: str | None = None,
        by: str | None = None,
    ) -> FreezeState:
        """Upsert the freeze row, invalidate cache, return the new state."""
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": self.GLOBAL_KEY,
            "frozen": bool(frozen),
            "frozen_reason": reason if frozen else None,
            "frozen_at": now_iso if frozen else None,
            "frozen_by": by if frozen else None,
            "updated_at": now_iso,
        }

        def _upsert() -> None:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            client.table(self.TABLE_NAME).upsert(payload).execute()

        try:
            await asyncio.to_thread(_upsert)
        except Exception as exc:  # noqa: BLE001
            logger.exception("freeze_manager: upsert failed: {}", exc)
            raise

        # Invalidate cache and re-read from DB to confirm.
        self._cache = None
        self._cache_expires_at = 0.0
        return await self.get_state(force_refresh=True)

    async def _fetch_from_db(self) -> FreezeState | None:
        def _query() -> list[dict]:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            result = (
                client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", self.GLOBAL_KEY)
                .limit(1)
                .execute()
            )
            return result.data or []

        try:
            rows = await asyncio.to_thread(_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "freeze_manager: fetch failed ({}); returning last-known state",
                exc,
            )
            return None

        if not rows:
            return FreezeState.unfrozen()

        row = rows[0]
        return FreezeState(
            frozen=bool(row.get("frozen", False)),
            reason=row.get("frozen_reason"),
            frozen_at=_parse_dt(row.get("frozen_at")),
            frozen_by=row.get("frozen_by"),
            updated_at=_parse_dt(row.get("updated_at")),
        )


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
