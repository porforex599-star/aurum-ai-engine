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
    rt = AppRuntime(s, MagicMock(), MagicMock())
    assert "gold_ai" in rt.products
    assert "multi_cfd_ai" in rt.products


def test_app_runtime_skips_disabled_products() -> None:
    s = _settings(enable_gold_ai=True, enable_multi_cfd_ai=False)
    rt = AppRuntime(s, MagicMock(), MagicMock())
    assert "gold_ai" in rt.products
    assert "multi_cfd_ai" not in rt.products


def test_get_runtime_raises_before_set() -> None:
    set_runtime(None)
    with pytest.raises(RuntimeError):
        get_runtime()


def test_set_and_get_runtime_roundtrip() -> None:
    s = _settings()
    rt = AppRuntime(s, MagicMock(), MagicMock())
    set_runtime(rt)
    try:
        assert get_runtime() is rt
    finally:
        set_runtime(None)
