"""Phase 5a — webhook resilience around the chart-img snapshot pipeline.

The snapshot pipeline (capture → upload → UPDATE chart_image_url) is appended
to the Sniper webhook *after* the Realtime INSERT broadcast and the Telegram
notify. These tests assert that any failure in that pipeline still yields a
200 response with the post persisted and Telegram notified.
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
os.environ["AURUM_SNIPER_WEBHOOK_SECRET"] = "test-webhook-secret"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api import sniper  # noqa: E402
from src.api.sniper import (  # noqa: E402
    get_analysis_notifier,
    get_analysis_store,
    router as sniper_router,
)
from src.config import reset_settings  # noqa: E402

ENDPOINT = "/api/internal/aurum-sniper-alert"
SECRET = "test-webhook-secret"

VALID_PAYLOAD = {
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "bias": "bullish",
    "key_level": 2345.67,
    "target_zones": [{"id": "Z1", "price": 2350.00}],
    "risk_level": "medium",
    "confidence": 85,
}


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []

    async def insert_row(self, table: str, row: dict) -> dict:
        self.rows.append((table, row))
        return {"id": "post-123", **row}

    async def update_row(self, table: str, values: dict, *, match: dict) -> dict:
        self.updates.append((table, values, match))
        return {"id": "post-123", **values}

    async def upload_to_storage(self, *a, **k) -> None:  # pragma: no cover
        return None

    def storage_public_url(self, bucket, path) -> str:
        return f"https://customers.example.supabase.co/storage/v1/object/public/{bucket}/{path}"


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent_post_ids: list = []

    async def send_analysis_alert(self, payload, post_id=None) -> bool:
        self.sent_post_ids.append(post_id)
        return True


def _build(store, notifier):
    reset_settings()
    app = FastAPI()
    app.include_router(sniper_router)
    app.dependency_overrides[get_analysis_store] = lambda: store
    app.dependency_overrides[get_analysis_notifier] = lambda: notifier
    return TestClient(app)


@pytest.fixture
def wired():
    store = _FakeStore()
    notifier = _FakeNotifier()
    return _build(store, notifier), store, notifier


async def _fake_capture_ok(*_a, **_k):
    return b"\x89PNG\r\n\x1a\nfake"


async def _fake_capture_none(*_a, **_k):
    return None


async def _fake_upload_none(*_a, **_k):
    return None


async def _fake_upload_ok(store, post_id, png_bytes):
    return f"https://x/storage/v1/object/public/analysis-snapshots/{post_id}.png"


def test_webhook_continues_when_chart_img_fails(wired, monkeypatch):
    """chart-img capture returns None → still 200, persisted, Telegram notified."""
    client, store, notifier = wired
    monkeypatch.setattr(sniper, "capture_layout_snapshot", _fake_capture_none)

    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    assert response.json() == {"post_id": "post-123", "broadcast": True}
    assert len(store.rows) == 1
    assert notifier.sent_post_ids == ["post-123"]
    # No snapshot → no chart_image_url UPDATE.
    assert store.updates == []


def test_webhook_continues_when_storage_fails(wired, monkeypatch):
    """Capture succeeds but Storage upload returns None → still 200, no UPDATE."""
    client, store, notifier = wired
    monkeypatch.setattr(sniper, "capture_layout_snapshot", _fake_capture_ok)
    monkeypatch.setattr(sniper, "upload_snapshot", _fake_upload_none)

    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    assert len(store.rows) == 1
    assert notifier.sent_post_ids == ["post-123"]
    assert store.updates == []


def test_webhook_updates_chart_image_url_on_success(wired, monkeypatch):
    """Full happy path → chart_image_url + chart_image_generated_at UPDATE fires."""
    client, store, notifier = wired
    monkeypatch.setattr(sniper, "capture_layout_snapshot", _fake_capture_ok)
    monkeypatch.setattr(sniper, "upload_snapshot", _fake_upload_ok)

    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    assert len(store.updates) == 1
    table, values, match = store.updates[0]
    assert table == "analysis_posts"
    assert match == {"id": "post-123"}
    assert values["chart_image_url"].endswith("/analysis-snapshots/post-123.png")
    assert "chart_image_generated_at" in values
