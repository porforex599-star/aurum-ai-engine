"""Phase 7 Stage 1 — master-account registry service.

Async wrapper around the `master_accounts` table (aurum-customers,
project etwlurpjrqlvrxgsbhkd). This is the DB foundation for multi-master
support: each product (gold_ai / multi_cfd_ai) is assigned to at most one MT5
master account at a time; unassigned masters sit in `standby`.

Style mirrors `FreezeManager` / `TradeLogger`:
  * sync supabase calls are run in `asyncio.to_thread`,
  * read methods log-and-swallow (return [] / None on failure),
  * write methods raise on DB error so the API surfaces a 5xx.

IMPORTANT — Stage 1 does NOT wire the engine to this table. The engine keeps
trading off the single env-var master (METAAPI_MASTER_ACCOUNT_ID). Engine
refactor is Stage 2.

FALLBACK CONTRACT (for Stage 2, documented here next to the lookup helper):
  Today a single master (#97038939) serves BOTH products but only the gold_ai
  row is seeded. `get_master_for_product()` therefore falls back to the gold_ai
  master when a product has no row of its own, so the engine never ends up with
  *no* master during the transition. Once Por registers a dedicated
  multi_cfd_ai master via the UI, that row wins and the fallback stops firing
  for multi_cfd_ai.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

# Products that can own a master. Kept local (not imported from admin.py) so the
# registry has no dependency on the engine runtime.
PRODUCTS = ("gold_ai", "multi_cfd_ai")
_FALLBACK_PRODUCT = "gold_ai"

# Columns returned to API clients (everything — there are no secret columns on
# this table; the metaapi_account_id is not a credential, just a routing id).
_SELECT = "*"


class MasterAccountError(Exception):
    """Raised for business-rule violations the API maps to 4xx (e.g. 409)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class MasterAccountService:
    TABLE_NAME = "master_accounts"

    def __init__(self, supabase_client: Any) -> None:
        """`supabase_client` may be the SupabaseClient wrapper or a raw client."""
        self._sb = supabase_client

    def _client(self) -> Any:
        sb = self._sb
        if hasattr(sb, "get_client"):
            return sb.get_client()
        return sb

    # -------------------- reads --------------------

    async def list_masters(self) -> list[dict]:
        """All masters, oldest first. Returns [] on failure / no client."""

        def _query() -> list[dict]:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            res = (
                client.table(self.TABLE_NAME)
                .select(_SELECT)
                .order("created_at", desc=False)
                .execute()
            )
            return res.data or []

        try:
            return await asyncio.to_thread(_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("master_accounts list failed: {}", exc)
            return []

    async def get_master(self, master_id: str) -> dict | None:
        """One master by id, or None if missing / on failure."""

        def _query() -> list[dict]:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            res = (
                client.table(self.TABLE_NAME)
                .select(_SELECT)
                .eq("id", master_id)
                .limit(1)
                .execute()
            )
            return res.data or []

        try:
            rows = await asyncio.to_thread(_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("master_accounts get failed: {}", exc)
            return None
        return rows[0] if rows else None

    async def get_master_for_product(self, product: str) -> dict | None:
        """Stage 2 hot-path lookup: the master assigned to `product`.

        Applies the documented transition fallback — if `product` has no
        assigned master, fall back to the `gold_ai` master so the engine always
        resolves *some* master. Not yet called by the engine in Stage 1; lives
        here so the fallback is implemented and unit-tested in one place.
        """

        def _query(p: str) -> list[dict]:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            res = (
                client.table(self.TABLE_NAME)
                .select(_SELECT)
                .eq("assigned_product", p)
                .limit(1)
                .execute()
            )
            return res.data or []

        try:
            rows = await asyncio.to_thread(_query, product)
            if rows:
                return rows[0]
            if product != _FALLBACK_PRODUCT:
                fb = await asyncio.to_thread(_query, _FALLBACK_PRODUCT)
                if fb:
                    logger.info(
                        "master lookup: no master for {}; falling back to {} master",
                        product,
                        _FALLBACK_PRODUCT,
                    )
                    return fb[0]
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("master_accounts product lookup failed: {}", exc)
            return None

    # -------------------- writes --------------------

    async def register_master(
        self,
        *,
        login: str,
        broker: str,
        server: str,
        currency: str,
        metaapi_account_id: str,
        metaapi_region: str = "eu-west",
        notes: str | None = None,
    ) -> dict:
        """Insert a new master in `standby` (unassigned). Raises on DB error.

        A duplicate `login` (the table's UNIQUE column) surfaces as a 409.
        """
        row = {
            "login": login,
            "broker": broker,
            "server": server,
            "currency": currency,
            "metaapi_account_id": metaapi_account_id,
            "metaapi_region": metaapi_region or "eu-west",
            "assigned_product": None,
            "status": "standby",
            "notes": notes,
        }

        def _insert() -> list[dict]:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            res = client.table(self.TABLE_NAME).insert(row).execute()
            return res.data or []

        try:
            data = await asyncio.to_thread(_insert)
        except Exception as exc:  # noqa: BLE001
            if _is_unique_violation(exc):
                raise MasterAccountError(
                    f"a master with login {login!r} already exists", status_code=409
                ) from exc
            logger.exception("master_accounts register failed: {}", exc)
            raise
        return data[0] if data else row

    async def assign(self, master_id: str, product: str) -> dict:
        """Assign `product` to a master: set it live, and demote any OTHER master
        currently holding that product to standby (assigned_product=NULL).

        Enforces one-master-per-product. We clear the previous holder first so we
        never transiently violate the partial-unique index on assigned_product.
        """
        if product not in PRODUCTS:
            raise MasterAccountError(f"unknown product: {product!r}")

        master = await self.get_master(master_id)
        if master is None:
            raise MasterAccountError("master not found", status_code=404)

        # Demote whoever currently owns this product (could be this same master —
        # a no-op re-assign — which we skip).
        previous = await self._find_by_product(product)
        for prev in previous:
            if prev["id"] != master_id:
                await self._update(
                    prev["id"], {"assigned_product": None, "status": "standby"}
                )

        return await self._update(
            master_id, {"assigned_product": product, "status": "live"}
        )

    async def unassign(self, master_id: str) -> dict:
        """Clear a master's product assignment and drop it to standby."""
        master = await self.get_master(master_id)
        if master is None:
            raise MasterAccountError("master not found", status_code=404)
        return await self._update(
            master_id, {"assigned_product": None, "status": "standby"}
        )

    async def delete_master(self, master_id: str) -> None:
        """Delete a master — only when it is in `standby`.

        A `live`/`disconnected` (assigned) master is refused with 409. Because a
        standby master is unassigned and has no live engine connection in Stage 1,
        the "no open positions attributed to it" guard is satisfied by the status
        check; the engine is not yet trading from arbitrary masters.
        """
        master = await self.get_master(master_id)
        if master is None:
            raise MasterAccountError("master not found", status_code=404)
        if master.get("status") != "standby":
            raise MasterAccountError(
                f"master is {master.get('status')!r} (assigned to "
                f"{master.get('assigned_product')!r}); unassign it before deleting",
                status_code=409,
            )

        def _delete() -> None:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            client.table(self.TABLE_NAME).delete().eq("id", master_id).execute()

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:  # noqa: BLE001
            logger.exception("master_accounts delete failed: {}", exc)
            raise

    # -------------------- internals --------------------

    async def _find_by_product(self, product: str) -> list[dict]:
        def _query() -> list[dict]:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            res = (
                client.table(self.TABLE_NAME)
                .select(_SELECT)
                .eq("assigned_product", product)
                .execute()
            )
            return res.data or []

        return await asyncio.to_thread(_query)

    async def _update(self, master_id: str, payload: dict) -> dict:
        def _do() -> list[dict]:
            client = self._client()
            if client is None:
                raise RuntimeError("supabase client not initialized")
            res = (
                client.table(self.TABLE_NAME)
                .update(payload)
                .eq("id", master_id)
                .execute()
            )
            return res.data or []

        try:
            data = await asyncio.to_thread(_do)
        except Exception as exc:  # noqa: BLE001
            if _is_unique_violation(exc):
                raise MasterAccountError(
                    "that product is already assigned to another master",
                    status_code=409,
                ) from exc
            logger.exception("master_accounts update failed: {}", exc)
            raise
        # Re-read so the API always returns the full, current row even if the
        # client's update() response doesn't echo all columns.
        fresh = await self.get_master(master_id)
        return fresh or (data[0] if data else {"id": master_id, **payload})


def _is_unique_violation(exc: Exception) -> bool:
    """Best-effort detection of a Postgres unique-violation (SQLSTATE 23505)
    across the shapes the supabase client raises it as."""
    code = getattr(exc, "code", None)
    if code == "23505":
        return True
    text = str(getattr(exc, "message", "") or "") + " " + str(exc)
    text = text.lower()
    return "23505" in text or "duplicate key" in text or "already exists" in text
