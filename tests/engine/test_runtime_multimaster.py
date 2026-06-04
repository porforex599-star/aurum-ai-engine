"""Phase 7 Stage 2 — AppRuntime per-product master routing.

Verifies the routing layer without touching MetaApi: the master_accounts service
and the MetaApi client pool are stubbed, so we assert resolution + bundle
construction/caching + the single-master fallback in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.engine.runtime import AppRuntime

DEFAULT_ID = "default-acct"


def _settings(**overrides) -> Settings:
    base = dict(
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="k",
        METAAPI_TOKEN="t",
        METAAPI_MASTER_ACCOUNT_ID=DEFAULT_ID,
        APP_ENV="development",
        LOG_LEVEL="INFO",
    )
    base.update(overrides)
    return Settings(**base)


class _StubMasters:
    """Stand-in MasterAccountService: maps product slug → account_id (or None)."""

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def get_master_for_product(self, slug: str):
        self.calls.append(slug)
        aid = self.mapping.get(slug)
        return {"metaapi_account_id": aid} if aid else None


class _FakeClient:
    def __init__(self, account_id: str) -> None:
        self._account_id = account_id
        self.connects = 0
        self._account = SimpleNamespace(get_rpc_connection=lambda: AsyncMock())

    async def connect(self) -> None:
        self.connects += 1

    def get_account(self):
        return self._account


class _FakePool:
    def __init__(self) -> None:
        self._clients: dict[str, _FakeClient] = {}

    def get_or_create(self, account_id: str) -> _FakeClient:
        c = self._clients.get(account_id)
        if c is None:
            c = _FakeClient(account_id)
            self._clients[account_id] = c
        return c


def _runtime(mapping: dict[str, str | None]) -> AppRuntime:
    rt = AppRuntime(_settings(), MagicMock(), MagicMock(), MagicMock())
    rt.master_accounts = _StubMasters(mapping)  # type: ignore[assignment]
    rt._metaapi_pool = _FakePool()  # type: ignore[assignment]
    return rt


@pytest.mark.asyncio
async def test_falls_back_to_default_when_no_master_row() -> None:
    rt = _runtime({})  # no rows at all
    assert await rt.get_account_id_for_product("gold_ai") == DEFAULT_ID
    assert await rt.get_account_id_for_product("multi_cfd_ai") == DEFAULT_ID


@pytest.mark.asyncio
async def test_resolves_assigned_account_id() -> None:
    rt = _runtime({"gold_ai": "acct-G"})
    assert await rt.get_account_id_for_product("gold_ai") == "acct-G"


@pytest.mark.asyncio
async def test_account_id_is_cached_after_first_resolve() -> None:
    rt = _runtime({"gold_ai": "acct-G"})
    await rt.get_account_id_for_product("gold_ai")
    await rt.get_account_id_for_product("gold_ai")
    # service consulted only once; second call hit the cache
    assert rt.master_accounts.calls == ["gold_ai"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_default_bundle_reuses_flat_components() -> None:
    rt = _runtime({})
    bundle = await rt.get_bundle_for_product("gold_ai")
    assert bundle.account_id == DEFAULT_ID
    # Default bundle is a live view of the runtime's single-master components.
    assert bundle.order_executor is rt.order_executor
    assert bundle.close_detector is rt.close_detector
    assert bundle.account_snapshot is rt.account_snapshot
    assert bundle.account is rt.account


@pytest.mark.asyncio
async def test_non_default_builds_and_caches_dedicated_bundle() -> None:
    rt = _runtime({"multi_cfd_ai": "acct-M"})
    bundle = await rt.get_bundle_for_product("multi_cfd_ai")
    assert bundle.account_id == "acct-M"
    # Independent components from the default master.
    assert bundle.order_executor is not rt.order_executor
    assert bundle.close_detector is not rt.close_detector
    # Cached: second resolve returns the very same bundle (no rebuild/reconnect).
    again = await rt.get_bundle_for_product("multi_cfd_ai")
    assert again is bundle


@pytest.mark.asyncio
async def test_two_products_same_master_share_one_bundle() -> None:
    rt = _runtime({"gold_ai": "acct-X", "multi_cfd_ai": "acct-X"})
    gb = await rt.get_bundle_for_product("gold_ai")
    mb = await rt.get_bundle_for_product("multi_cfd_ai")
    assert gb.account_id == mb.account_id == "acct-X"
    assert gb is mb  # deduped by account_id → shared close-detector state


@pytest.mark.asyncio
async def test_resolve_master_routing_populates_map() -> None:
    rt = _runtime({"gold_ai": "acct-G"})
    # both products exist by default in settings
    assert "gold_ai" in rt.products and "multi_cfd_ai" in rt.products
    await rt.resolve_master_routing()
    assert rt._product_account_ids["gold_ai"] == "acct-G"
    assert rt._product_account_ids["multi_cfd_ai"] == DEFAULT_ID


@pytest.mark.asyncio
async def test_get_account_for_product_returns_bundle_account() -> None:
    rt = _runtime({"multi_cfd_ai": "acct-M"})
    acct = await rt.get_account_for_product("multi_cfd_ai")
    bundle = await rt.get_bundle_for_product("multi_cfd_ai")
    assert acct is bundle.account
