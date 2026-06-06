from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.symbols import router as symbols_router  # noqa: E402
from src.engine.runtime import get_runtime  # noqa: E402


def _fake_runtime(symbols: list[str], spec: dict | None = None):
    rpc_conn = SimpleNamespace(
        get_symbols=AsyncMock(return_value=symbols),
        get_symbol_specification=AsyncMock(return_value=spec or {}),
    )
    return SimpleNamespace(get_rpc_connection=AsyncMock(return_value=rpc_conn))


def _build_app(rt):
    app = FastAPI()
    app.include_router(symbols_router)
    app.dependency_overrides[get_runtime] = lambda: rt
    return app


def test_list_symbols_returns_sorted_list() -> None:
    rt = _fake_runtime(["GBPUSD", "EURUSD", "XAUUSD.s"])
    client = TestClient(_build_app(rt))
    r = client.get("/symbols")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert body["symbols"] == ["EURUSD", "GBPUSD", "XAUUSD.s"]


def test_search_symbols_filters_case_insensitively() -> None:
    rt = _fake_runtime(["GBPUSD", "EURUSD", "XAUUSD.s", "XAGUSD"])
    client = TestClient(_build_app(rt))
    r = client.get("/symbols/search?q=xau")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "xau"
    assert body["matches"] == ["XAUUSD.s"]


def test_get_spec_returns_dict() -> None:
    spec = {"symbol": "EURUSD", "digits": 5, "contractSize": 100000}
    rt = _fake_runtime([], spec=spec)
    client = TestClient(_build_app(rt))
    r = client.get("/symbols/spec/EURUSD")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "EURUSD"
    assert body["digits"] == 5
