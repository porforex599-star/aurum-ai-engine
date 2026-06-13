"""Admin manual chart-img capture endpoint (POST /admin/chart/test-capture).

Exercises the reuse of the Sniper capture/upload helpers behind an admin-key
gate: capture → Supabase Storage upload → optional analysis_posts UPDATE. Both
chart-img and Supabase Storage are mocked so the tests never hit the network.
"""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("SUPABASE_CUSTOMERS_URL", "https://customers.example.supabase.co")
os.environ.setdefault("SUPABASE_CUSTOMERS_SERVICE_ROLE_KEY", "test-customers")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("APP_ENV", "development")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import admin  # noqa: E402
from src.api.admin import get_chart_store  # noqa: E402
from src.config import reset_settings  # noqa: E402
from src.main import app  # noqa: E402

ENDPOINT = "/admin/chart/test-capture"
ADMIN_KEY = "test-admin-secret"
_HDR = {"X-Admin-Key": ADMIN_KEY}
PNG = b"\x89PNG\r\n\x1a\nfake"


class _FakeStore:
    """Records storage uploads and analysis_posts updates without network I/O."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, bytes]] = []
        self.updates: list[tuple[str, dict, dict]] = []

    async def upload_to_storage(self, bucket, path, data, *, content_type, upsert=True):
        self.uploads.append((bucket, path, data))

    def storage_public_url(self, bucket, path) -> str:
        return f"https://customers.example.supabase.co/storage/v1/object/public/{bucket}/{path}"

    async def update_row(self, table: str, values: dict, *, match: dict) -> dict:
        self.updates.append((table, values, match))
        return {"id": match.get("id"), **values}


@pytest.fixture
def wired(monkeypatch):
    """TestClient with ADMIN_KEY set, chart-img mocked, store overridden."""
    monkeypatch.setenv("ADMIN_KEY", ADMIN_KEY)
    reset_settings()

    capture_calls: list[dict] = []

    async def _fake_capture(*, symbol, interval, layout_id=None, **_k):
        capture_calls.append(
            {"symbol": symbol, "interval": interval, "layout_id": layout_id}
        )
        return PNG

    monkeypatch.setattr(admin, "capture_layout_snapshot", _fake_capture)

    store = _FakeStore()
    app.dependency_overrides[get_chart_store] = lambda: store
    try:
        yield TestClient(app), store, capture_calls
    finally:
        app.dependency_overrides.pop(get_chart_store, None)
        reset_settings()


# -------------------- auth gating --------------------


def test_rejects_missing_admin_key(wired) -> None:
    client, _store, _calls = wired
    r = client.post(ENDPOINT, json={})
    assert r.status_code == 401


def test_rejects_wrong_admin_key(wired) -> None:
    client, _store, _calls = wired
    r = client.post(ENDPOINT, headers={"X-Admin-Key": "nope"}, json={})
    assert r.status_code == 401


def test_returns_503_when_admin_key_unset(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    store = _FakeStore()
    app.dependency_overrides[get_chart_store] = lambda: store
    try:
        r = TestClient(app).post(ENDPOINT, headers=_HDR, json={})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.pop(get_chart_store, None)


# -------------------- happy path: no post_id (test path, no DB) --------------------


def test_capture_without_post_id_uses_test_path_and_skips_db(wired) -> None:
    client, store, calls = wired
    r = client.post(ENDPOINT, headers=_HDR, json={})
    assert r.status_code == 200
    body = r.json()

    # Defaults applied (Gold Panel V.2 layout, 5m XAUUSD).
    assert calls == [{"symbol": "OANDA:XAUUSD", "interval": "5", "layout_id": "uoSX32t7"}]

    assert body["storage_path"].startswith("test/")
    assert body["storage_path"].endswith(".png")
    assert body["chart_image_url"].endswith(body["storage_path"])
    assert body["post_id_updated"] is None
    assert isinstance(body["latency_ms"], int)

    # Uploaded to the analysis-snapshots bucket at the test path; DB untouched.
    assert len(store.uploads) == 1
    bucket, path, data = store.uploads[0]
    assert bucket == "analysis-snapshots"
    assert path == body["storage_path"]
    assert data == PNG
    assert store.updates == []


def test_custom_layout_and_symbol_passed_through(wired) -> None:
    client, _store, calls = wired
    r = client.post(
        ENDPOINT,
        headers=_HDR,
        json={"layout_id": "ABC123", "symbol": "OANDA:EURUSD", "interval": "15"},
    )
    assert r.status_code == 200
    assert calls == [{"symbol": "OANDA:EURUSD", "interval": "15", "layout_id": "ABC123"}]


# -------------------- happy path: post_id provided (overwrite + DB UPDATE) -----


def test_capture_with_post_id_overwrites_and_updates_db(wired) -> None:
    client, store, _calls = wired
    r = client.post(ENDPOINT, headers=_HDR, json={"post_id": "post-123"})
    assert r.status_code == 200
    body = r.json()

    assert body["storage_path"] == "post-123.png"
    assert body["chart_image_url"].endswith("/analysis-snapshots/post-123.png")
    assert body["post_id_updated"] == "post-123"

    assert store.uploads[0][1] == "post-123.png"
    assert len(store.updates) == 1
    table, values, match = store.updates[0]
    assert table == "analysis_posts"
    assert match == {"id": "post-123"}
    assert values["chart_image_url"] == body["chart_image_url"]
    assert "chart_image_generated_at" in values


# -------------------- failure paths --------------------


def test_returns_502_when_capture_fails(wired, monkeypatch) -> None:
    client, store, _calls = wired

    async def _capture_none(**_k):
        return None

    monkeypatch.setattr(admin, "capture_layout_snapshot", _capture_none)
    r = client.post(ENDPOINT, headers=_HDR, json={})
    assert r.status_code == 502
    assert store.uploads == []


def test_returns_502_when_upload_fails(wired, monkeypatch) -> None:
    client, store, _calls = wired

    async def _upload_boom(*_a, **_k):
        raise RuntimeError("storage down")

    # Patch the bound method on the fake store so upload_snapshot_to_path's
    # try/except converts it to None → endpoint raises 502.
    monkeypatch.setattr(store, "upload_to_storage", _upload_boom)
    r = client.post(ENDPOINT, headers=_HDR, json={})
    assert r.status_code == 502
    assert store.updates == []
