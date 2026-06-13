"""Admin manual analysis publish (POST /admin/analysis/publish).

Lets an admin publish a new analysis_posts row to /room without a Pine alert,
reusing the Sniper webhook's persist→capture→update flow. chart-img and
Supabase Storage are mocked so the tests never hit the network.
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
from src.api.admin import ROOM_URL, get_chart_store  # noqa: E402
from src.config import reset_settings  # noqa: E402
from src.main import app  # noqa: E402

ENDPOINT = "/admin/analysis/publish"
ADMIN_KEY = "test-admin-secret"
_HDR = {"X-Admin-Key": ADMIN_KEY}
PNG = b"\x89PNG\r\n\x1a\nfake"


class _FakeStore:
    """Records inserts/updates/uploads against analysis_posts without I/O."""

    def __init__(self) -> None:
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []
        self.uploads: list[tuple[str, str, bytes]] = []

    async def insert_row(self, table: str, row: dict) -> dict:
        self.inserts.append((table, row))
        return {"id": "post-abc", **row}

    async def update_row(self, table: str, values: dict, *, match: dict) -> dict:
        self.updates.append((table, values, match))
        return {"id": match.get("id"), **values}

    async def upload_to_storage(self, bucket, path, data, *, content_type, upsert=True):
        self.uploads.append((bucket, path, data))

    def storage_public_url(self, bucket, path) -> str:
        return f"https://customers.example.supabase.co/storage/v1/object/public/{bucket}/{path}"


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", ADMIN_KEY)
    reset_settings()

    async def _fake_capture(*, symbol, interval, layout_id=None, **_k):
        return PNG

    monkeypatch.setattr(admin, "capture_layout_snapshot", _fake_capture)

    store = _FakeStore()
    app.dependency_overrides[get_chart_store] = lambda: store
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.pop(get_chart_store, None)
        reset_settings()


# -------------------- auth gating --------------------


def test_rejects_missing_admin_key(wired) -> None:
    client, _store = wired
    r = client.post(ENDPOINT, json={"direction": "bull", "conviction": 4})
    assert r.status_code == 401


def test_rejects_wrong_admin_key(wired) -> None:
    client, _store = wired
    r = client.post(
        ENDPOINT,
        headers={"X-Admin-Key": "nope"},
        json={"direction": "bull", "conviction": 4},
    )
    assert r.status_code == 401


def test_returns_503_when_admin_key_unset(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    store = _FakeStore()
    app.dependency_overrides[get_chart_store] = lambda: store
    try:
        r = TestClient(app).post(
            ENDPOINT, headers=_HDR, json={"direction": "bull", "conviction": 4}
        )
        assert r.status_code == 503
    finally:
        app.dependency_overrides.pop(get_chart_store, None)


# -------------------- validation --------------------


def test_rejects_bad_direction(wired) -> None:
    client, _store = wired
    r = client.post(ENDPOINT, headers=_HDR, json={"direction": "sideways", "conviction": 4})
    assert r.status_code == 422


def test_rejects_missing_direction(wired) -> None:
    client, _store = wired
    r = client.post(ENDPOINT, headers=_HDR, json={"conviction": 4})
    assert r.status_code == 422


@pytest.mark.parametrize("bad", [2, 6, 0])
def test_rejects_conviction_out_of_range(wired, bad) -> None:
    client, _store = wired
    r = client.post(ENDPOINT, headers=_HDR, json={"direction": "bull", "conviction": bad})
    assert r.status_code == 422


# -------------------- happy path --------------------


def test_publish_inserts_row_and_updates_chart(wired) -> None:
    client, store = wired
    r = client.post(
        ENDPOINT, headers=_HDR, json={"direction": "bear", "conviction": 5}
    )
    assert r.status_code == 200
    body = r.json()

    # Response shape.
    assert body["post_id"] == "post-abc"
    assert body["chart_image_url"].endswith("/analysis-snapshots/post-abc.png")
    assert isinstance(body["latency_ms"], int)
    assert body["room_url"] == ROOM_URL

    # INSERT: direction→bias, conviction→confidence%, source stamped.
    assert len(store.inserts) == 1
    table, row = store.inserts[0]
    assert table == "analysis_posts"
    assert row == {
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "bias": "bearish",
        "key_level": 0.0,
        "risk_level": "medium",
        "confidence": 100,  # 5 * 20
        "source": "admin_manual",
    }
    # chart_image_url is NOT inserted up front — it's filled by the UPDATE.
    assert "chart_image_url" not in row

    # Uploaded to {post_id}.png and chart_image_url updated.
    assert store.uploads[0][1] == "post-abc.png"
    assert len(store.updates) == 1
    utable, values, match = store.updates[0]
    assert utable == "analysis_posts"
    assert match == {"id": "post-abc"}
    assert values["chart_image_url"] == body["chart_image_url"]
    assert "chart_image_generated_at" in values


def test_bull_maps_to_bullish_and_conviction_scales(wired) -> None:
    client, store = wired
    r = client.post(ENDPOINT, headers=_HDR, json={"direction": "bull", "conviction": 3})
    assert r.status_code == 200
    _table, row = store.inserts[0]
    assert row["bias"] == "bullish"
    assert row["confidence"] == 60  # 3 * 20


def test_publish_succeeds_without_chart_when_capture_fails(wired, monkeypatch) -> None:
    """Capture returns None → post still published, no UPDATE, 200 w/ null url."""
    client, store = wired

    async def _capture_none(**_k):
        return None

    monkeypatch.setattr(admin, "capture_layout_snapshot", _capture_none)
    r = client.post(ENDPOINT, headers=_HDR, json={"direction": "bull", "conviction": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["post_id"] == "post-abc"
    assert body["chart_image_url"] is None
    # Row was inserted (post is live on /room) but no chart UPDATE fired.
    assert len(store.inserts) == 1
    assert store.updates == []


# -------------------- end-to-end chart-img body guard --------------------


class _FakeHTTPResponse:
    def __init__(self, content: bytes) -> None:
        self.status_code = 200
        self.content = content

    def raise_for_status(self) -> None:  # pragma: no cover - 200 path
        return None


class _FakeHTTPClient:
    posted: dict = {}

    def __init__(self, **_k) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeHTTPClient.posted = {"url": url, "json": json}
        return _FakeHTTPResponse(PNG)


def test_endpoint_sends_chartimg_accepted_body(monkeypatch):
    """Regression guard: chart_interval "5" must reach chart-img as "5m"."""
    from src.services import chart_img

    monkeypatch.setenv("ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("CHARTIMG_API_KEY", "test-key")
    monkeypatch.setenv("TV_LAYOUT_ID", "fallback-layout")
    reset_settings()
    # Do NOT mock capture — exercise the real chart-img body construction.
    monkeypatch.setattr(chart_img.httpx, "AsyncClient", _FakeHTTPClient)

    store = _FakeStore()
    app.dependency_overrides[get_chart_store] = lambda: store
    try:
        r = TestClient(app).post(
            ENDPOINT, headers=_HDR, json={"direction": "bull", "conviction": 4}
        )
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_chart_store, None)
        reset_settings()

    body = _FakeHTTPClient.posted["json"]
    assert body == {
        "symbol": "OANDA:XAUUSD",
        "interval": "5m",
        "width": 1920,
        "height": 1080,
    }
    assert _FakeHTTPClient.posted["url"].endswith("/tradingview/layout-chart/uoSX32t7")
