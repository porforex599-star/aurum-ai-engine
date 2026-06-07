from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("SUPABASE_CUSTOMERS_URL", "https://customers.example.supabase.co")
os.environ.setdefault("SUPABASE_CUSTOMERS_SERVICE_ROLE_KEY", "test-customers")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ["AURUM_SNIPER_WEBHOOK_SECRET"] = "test-webhook-secret"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

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
    "target_zones": [{"id": "Z1", "price": 2350.00}, {"id": "Z2", "price": 2355.00}],
    "risk_level": "medium",
    "confidence": 85,
    "note": "ทดสอบ",
    "timestamp_utc": "2026-06-06T12:00:00Z",
}


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    async def insert_row(self, table: str, row: dict) -> dict:
        self.rows.append((table, row))
        return {"id": "post-123", **row}


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_analysis_alert(self, payload) -> bool:
        self.sent.append(payload)
        return True


def _build_app(store, notifier):
    reset_settings()  # pick up AURUM_SNIPER_WEBHOOK_SECRET from env
    app = FastAPI()
    app.include_router(sniper_router)
    app.dependency_overrides[get_analysis_store] = lambda: store
    app.dependency_overrides[get_analysis_notifier] = lambda: notifier
    return app


@pytest.fixture
def wired():
    store = _FakeStore()
    notifier = _FakeNotifier()
    client = TestClient(_build_app(store, notifier))
    return client, store, notifier


def test_missing_secret_returns_401(wired):
    client, _, _ = wired
    response = client.post(ENDPOINT, json=VALID_PAYLOAD)
    assert response.status_code == 401


def test_wrong_secret_returns_401(wired):
    client, _, _ = wired
    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": "nope"})
    assert response.status_code == 401


def test_secret_via_query_param_succeeds(wired):
    """TradingView can't send custom headers — ?secret= must work as a fallback."""
    client, store, _ = wired
    response = client.post(f"{ENDPOINT}?secret={SECRET}", json=VALID_PAYLOAD)
    assert response.status_code == 200
    assert response.json() == {"post_id": "post-123", "broadcast": True}
    assert len(store.rows) == 1


def test_wrong_query_param_secret_returns_401(wired):
    client, _, _ = wired
    response = client.post(f"{ENDPOINT}?secret=nope", json=VALID_PAYLOAD)
    assert response.status_code == 401


def test_header_takes_precedence_over_query_param(wired):
    """A valid header authenticates even if the query param is wrong/absent."""
    client, _, _ = wired
    response = client.post(
        f"{ENDPOINT}?secret=nope", json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET}
    )
    assert response.status_code == 200


def test_valid_alert_persists_and_broadcasts(wired):
    client, store, notifier = wired
    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    assert response.json() == {"post_id": "post-123", "broadcast": True}

    # Persisted into the customers project's analysis_posts table (public schema).
    assert len(store.rows) == 1
    table, row = store.rows[0]
    assert table == "analysis_posts"
    assert row["symbol"] == "XAUUSD"
    assert row["bias"] == "bullish"

    # Telegram notified.
    assert len(notifier.sent) == 1


def test_vocab_normalization_buy_to_bullish(wired):
    client, store, notifier = wired
    payload = {**VALID_PAYLOAD, "bias": "buy"}
    response = client.post(ENDPOINT, json=payload, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    _, row = store.rows[0]
    assert row["bias"] == "bullish"
    assert notifier.sent[0].bias == "bullish"


def test_vocab_normalization_short_to_bearish(wired):
    client, store, _ = wired
    payload = {**VALID_PAYLOAD, "bias": "SHORT"}
    response = client.post(ENDPOINT, json=payload, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    _, row = store.rows[0]
    assert row["bias"] == "bearish"


def test_invalid_confidence_returns_422(wired):
    client, _, _ = wired
    payload = {**VALID_PAYLOAD, "confidence": 150}
    response = client.post(ENDPOINT, json=payload, headers={"X-Webhook-Secret": SECRET})
    assert response.status_code == 422


def test_notification_failure_does_not_fail_request(wired):
    """A missing/skipped notifier (None) must not fail the webhook."""
    client, store, _ = wired
    client.app.dependency_overrides[get_analysis_notifier] = lambda: None
    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET})
    assert response.status_code == 200
    assert len(store.rows) == 1


def test_webhook_accepts_invalidation_and_rr_fields(wired):
    """invalidation_price and rr_ratio are accepted and persisted when provided."""
    client, store, notifier = wired
    payload = {**VALID_PAYLOAD, "invalidation_price": 2330.5, "rr_ratio": 2.8}
    response = client.post(ENDPOINT, json=payload, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    assert len(store.rows) == 1
    _, row = store.rows[0]
    assert row["invalidation_price"] == 2330.5
    assert row["rr_ratio"] == 2.8

    # The validated payload handed to the notifier carries the new fields too.
    assert notifier.sent[0].invalidation_price == 2330.5
    assert notifier.sent[0].rr_ratio == 2.8


def test_webhook_backward_compat_without_new_fields(wired):
    """Phase 3 callers that omit the new fields still succeed.

    The fields default to None and are dropped from the persisted row
    (to_post_row uses exclude_none), so DB column defaults still apply.
    """
    client, store, notifier = wired
    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    assert len(store.rows) == 1
    _, row = store.rows[0]
    assert "invalidation_price" not in row
    assert "rr_ratio" not in row

    # Defaults are None on the parsed payload.
    assert notifier.sent[0].invalidation_price is None
    assert notifier.sent[0].rr_ratio is None
