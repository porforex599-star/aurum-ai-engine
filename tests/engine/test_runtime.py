from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.engine.runtime import AppRuntime, get_runtime, set_runtime


def _settings(**overrides) -> Settings:
    base = dict(
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="k",
        METAAPI_TOKEN="t",
        METAAPI_MASTER_ACCOUNT_ID="00000000-0000-0000-0000-000000000000",
        APP_ENV="development",
        LOG_LEVEL="INFO",
    )
    base.update(overrides)
    return Settings(**base)


def test_app_runtime_initializes_both_products_when_enabled() -> None:
    s = _settings(enable_gold_ai=True, enable_multi_cfd_ai=True)
    rt = AppRuntime(s, MagicMock(), MagicMock(), MagicMock())
    assert "gold_ai" in rt.products
    assert "multi_cfd_ai" in rt.products


def test_app_runtime_skips_disabled_products() -> None:
    s = _settings(enable_gold_ai=True, enable_multi_cfd_ai=False)
    rt = AppRuntime(s, MagicMock(), MagicMock(), MagicMock())
    assert "gold_ai" in rt.products
    assert "multi_cfd_ai" not in rt.products


def test_get_runtime_raises_before_set() -> None:
    set_runtime(None)
    with pytest.raises(RuntimeError):
        get_runtime()


def test_set_and_get_runtime_roundtrip() -> None:
    s = _settings()
    rt = AppRuntime(s, MagicMock(), MagicMock(), MagicMock())
    set_runtime(rt)
    try:
        assert get_runtime() is rt
    finally:
        set_runtime(None)


def test_snapshot_fetcher_wired_with_account() -> None:
    s = _settings()
    account = MagicMock()
    connection = MagicMock()
    rt = AppRuntime(s, account, connection, MagicMock())
    assert rt.snapshot_fetcher.account is account
    assert rt.snapshot_fetcher.connection is connection


# ---------- Phase 2.6 — notifier wiring ----------


def test_runtime_builds_disabled_notifier_when_telegram_off() -> None:
    s = _settings(telegram_enabled=False)
    rt = AppRuntime(s, MagicMock(), MagicMock(), MagicMock())
    assert rt.notifier is not None
    assert rt.notifier.enabled is False
    # Bus should NOT have a notifier attached when disabled — avoids any
    # accidental dispatch when token/chat are blank in dev.
    assert rt.intent_bus._notifier is None  # type: ignore[attr-defined]


def test_runtime_attaches_notifier_when_telegram_enabled() -> None:
    s = _settings(
        telegram_enabled=True,
        telegram_bot_token="111:abc",
        telegram_chat_id="555",
    )
    rt = AppRuntime(s, MagicMock(), MagicMock(), MagicMock())
    assert rt.notifier.enabled is True
    assert rt.intent_bus._notifier is rt.notifier  # type: ignore[attr-defined]


def test_runtime_disables_notifier_when_token_blank_even_if_flag_on() -> None:
    """Defence in depth — telegram_enabled=True but no token should NOT wire."""
    s = _settings(
        telegram_enabled=True,
        telegram_bot_token="",
        telegram_chat_id="555",
    )
    rt = AppRuntime(s, MagicMock(), MagicMock(), MagicMock())
    assert rt.notifier.enabled is False
    assert rt.intent_bus._notifier is None  # type: ignore[attr-defined]


# ---------- Phase 6 — freeze manager wiring ----------


def test_runtime_builds_freeze_manager() -> None:
    s = _settings()
    rt = AppRuntime(s, MagicMock(), MagicMock(), MagicMock())
    assert rt.freeze_manager is not None
    # TTL flows through from settings.
    assert rt.freeze_manager._ttl == s.freeze_cache_ttl_seconds  # type: ignore[attr-defined]


def test_runtime_freeze_manager_uses_supabase_client() -> None:
    """The FreezeManager must hold the same supabase reference as the rest of the runtime."""
    s = _settings()
    sb = MagicMock()
    rt = AppRuntime(s, MagicMock(), MagicMock(), sb)
    assert rt.freeze_manager._sb is sb  # type: ignore[attr-defined]
