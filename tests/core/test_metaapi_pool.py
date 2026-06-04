"""Phase 7 Stage 2 — MetaApiClientPool + per-account MetaApiClient."""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "default-acct-id")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_LEVEL", "INFO")

from src.config import get_settings  # noqa: E402
from src.core.metaapi_client import MetaApiClient, MetaApiClientPool  # noqa: E402


def test_pool_returns_same_client_per_account_id() -> None:
    pool = MetaApiClientPool()
    a1 = pool.get_or_create("acct-A")
    a2 = pool.get_or_create("acct-A")
    assert a1 is a2


def test_pool_creates_distinct_clients_per_account() -> None:
    pool = MetaApiClientPool()
    a = pool.get_or_create("acct-A")
    b = pool.get_or_create("acct-B")
    assert a is not b
    assert a.account_id == "acct-A"
    assert b.account_id == "acct-B"
    assert {c.account_id for c in pool.all()} == {"acct-A", "acct-B"}


def test_pool_get_returns_none_for_unknown() -> None:
    pool = MetaApiClientPool()
    assert pool.get("nope") is None


def test_client_account_id_defaults_to_env_master() -> None:
    # No explicit id → resolves to the configured master account id.
    assert MetaApiClient().account_id == get_settings().METAAPI_MASTER_ACCOUNT_ID
    # An explicit id always wins.
    assert MetaApiClient(account_id="x").account_id == "x"
