from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("METAAPI_TOKEN", "test-metaapi-token")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("ADMIN_KEY", "test-admin-key")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_LEVEL", "INFO")

from fastapi.testclient import TestClient  # noqa: E402

from src import __version__  # noqa: E402
from src.core import metaapi_client as metaapi_module  # noqa: E402
from src.core import supabase_client as supabase_module  # noqa: E402
from src.main import app  # noqa: E402


class _FakeMetaApiClient:
    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def shutdown(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_account(self):
        return None


class _FakeSupabaseClient:
    def __init__(self) -> None:
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    async def ping(self) -> bool:
        return self._connected

    def is_connected(self) -> bool:
        return self._connected

    def get_client(self):
        return object() if self._connected else None

    async def shutdown(self) -> None:
        self._connected = False


def test_health_endpoint(monkeypatch) -> None:
    fake_meta = _FakeMetaApiClient()
    fake_supa = _FakeSupabaseClient()

    monkeypatch.setattr(metaapi_module, "_client", fake_meta)
    monkeypatch.setattr(supabase_module, "_client", fake_supa)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == __version__
    assert body["metaapi_connected"] is True
    assert body["supabase_connected"] is True
    assert body["status"] == "ok"
