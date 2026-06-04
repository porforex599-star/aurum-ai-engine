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

from src.core import metaapi_client as metaapi_module  # noqa: E402
from src.core import supabase_client as supabase_module  # noqa: E402
from src.main import app  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


# --- Fakes for the MetaApi SDK -------------------------------------------------


class _FakeAccount:
    """Stand-in for a MetatraderAccount returned by create_account()."""

    def __init__(self) -> None:
        self.id = "acc-123"
        self.region = "london"
        self.base_currency = "USD"
        self.state = "CREATED"
        self.deployed = False
        self.connected = False
        self.password_seen: str | None = None

    async def deploy(self) -> None:
        self.deployed = True
        self.state = "DEPLOYED"

    async def wait_connected(self, timeout_in_seconds: int = 300, interval_in_milliseconds: int = 1000) -> None:
        self.connected = True


class _FakeAccountApi:
    def __init__(self, account: _FakeAccount) -> None:
        self._account = account
        self.create_calls: list[dict] = []

    async def create_account(self, dto: dict) -> _FakeAccount:
        self.create_calls.append(dto)
        self._account.password_seen = dto.get("password")
        return self._account


class _FakeMetaApiSdk:
    """Stand-in for metaapi_cloud_sdk.MetaApi(token)."""

    def __init__(self, account: _FakeAccount) -> None:
        self.metatrader_account_api = _FakeAccountApi(account)


# --- Fake Supabase -------------------------------------------------------------


class _FakeResult:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeTable:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    def insert(self, row: dict) -> "_FakeTable":
        self._sink["row"] = row
        return self

    def execute(self) -> _FakeResult:
        # Echo the inserted row back with a generated id, like a real insert.
        return _FakeResult([{"id": "row-1", **self._sink["row"]}])


class _FakeSupabaseClient:
    """Mimics SupabaseClient: get_client() returns a thing with .table()."""

    def __init__(self) -> None:
        self.inserted: dict = {}

    def connect(self) -> None:
        return None

    def get_client(self):
        sink = self.inserted

        class _Client:
            def table(self, name: str) -> _FakeTable:
                assert name == "master_accounts"
                return _FakeTable(sink)

        return _Client()

    async def ping(self) -> bool:
        return True

    async def shutdown(self) -> None:
        return None


def _install_fakes(monkeypatch, account: _FakeAccount) -> tuple:
    """Wire a fresh MetaApiClient (with a mocked SDK) and fake Supabase into app state."""
    fake_supa = _FakeSupabaseClient()
    monkeypatch.setattr(supabase_module, "_client", fake_supa)

    # Patch the SDK class used inside MetaApiClient.provision_account so the real
    # provisioning logic (deploy + wait_connected + extraction) runs end-to-end.
    sdk = _FakeMetaApiSdk(account)
    monkeypatch.setattr(metaapi_module, "MetaApi", lambda token: sdk)

    fresh = metaapi_module.MetaApiClient()
    monkeypatch.setattr(metaapi_module, "_client", fresh)
    return fake_supa, sdk


def test_create_master_provisions_account(monkeypatch) -> None:
    account = _FakeAccount()
    fake_supa, sdk = _install_fakes(monkeypatch, account)

    with TestClient(app) as client:
        resp = client.post(
            "/api/masters",
            headers=ADMIN_HEADERS,
            json={
                "login": "5001234",
                "broker": "KVB Prime",
                "server": "KVBPrime-Live",
                "password": "s3cr3t-mt5-pw",
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Auto-populated from the connected account.
    assert body["metaapi_account_id"] == "acc-123"
    assert body["metaapi_region"] == "london"
    assert body["currency"] == "USD"
    assert body["login"] == "5001234"
    assert body["broker"] == "KVB Prime"
    assert body["server"] == "KVBPrime-Live"

    # SDK got the right DTO and the account was deployed + connected.
    assert sdk.metatrader_account_api.create_calls[0]["type"] == "cloud-g2"
    assert sdk.metatrader_account_api.create_calls[0]["platform"] == "mt5"
    assert account.deployed is True
    assert account.connected is True

    # Password is forwarded to the SDK but never persisted or returned.
    assert account.password_seen == "s3cr3t-mt5-pw"
    assert "password" not in body
    assert "password" not in fake_supa.inserted["row"]


def test_create_master_invalid_credentials(monkeypatch) -> None:
    from metaapi_cloud_sdk.clients.error_handler import UnauthorizedException

    account = _FakeAccount()
    _, sdk = _install_fakes(monkeypatch, account)

    async def _boom(dto: dict):
        raise UnauthorizedException("bad creds")

    monkeypatch.setattr(sdk.metatrader_account_api, "create_account", _boom)

    with TestClient(app) as client:
        resp = client.post(
            "/api/masters",
            headers=ADMIN_HEADERS,
            json={
                "login": "5001234",
                "broker": "KVB Prime",
                "server": "KVBPrime-Live",
                "password": "wrong-pw",
            },
        )

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "invalid_credentials"


def test_create_master_backward_compat_skips_provisioning(monkeypatch) -> None:
    account = _FakeAccount()
    fake_supa, sdk = _install_fakes(monkeypatch, account)

    with TestClient(app) as client:
        resp = client.post(
            "/api/masters",
            headers=ADMIN_HEADERS,
            json={
                "login": "5009999",
                "broker": "KCM Trade",
                "server": "KCM-Live",
                "metaapi_account_id": "pre-existing-id",
                "metaapi_region": "new-york",
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["metaapi_account_id"] == "pre-existing-id"
    assert body["metaapi_region"] == "new-york"
    # Provisioning must be skipped entirely.
    assert sdk.metatrader_account_api.create_calls == []


def test_create_master_requires_admin_key() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/masters",
            json={
                "login": "5001234",
                "broker": "KVB Prime",
                "server": "KVBPrime-Live",
                "password": "x",
            },
        )
    assert resp.status_code == 401


def test_create_master_requires_password_or_account_id() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/masters",
            headers=ADMIN_HEADERS,
            json={
                "login": "5001234",
                "broker": "KVB Prime",
                "server": "KVBPrime-Live",
            },
        )
    assert resp.status_code == 422
