from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("SUPABASE_CUSTOMERS_URL", "https://customers.example.supabase.co")
os.environ.setdefault("SUPABASE_CUSTOMERS_SERVICE_ROLE_KEY", "test-customers-service-role-key")
os.environ.setdefault("METAAPI_TOKEN", "test-metaapi-token")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ["AURUM_SNIPER_WEBHOOK_SECRET"] = "test-webhook-secret"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src import config as config_module  # noqa: E402
from src.core import metaapi_client as metaapi_module  # noqa: E402
from src.core import supabase_client as supabase_module  # noqa: E402
from src.core import telegram_notifier as telegram_module  # noqa: E402
from src.main import app  # noqa: E402

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


class _FakeMetaApiClient:
    async def connect(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def get_account(self):
        return None


class _FakeSupabaseClient:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []
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

    async def insert_row(self, table: str, row: dict) -> dict:
        self.rows.append((table, row))
        return {"id": "post-123", **row}


class _FakeTelegramNotifier:
    def __init__(self) -> None:
        self.sent: list = []

    def is_configured(self) -> bool:
        return True

    async def send_analysis(self, payload) -> bool:
        self.sent.append(payload)
        return True


@pytest.fixture
def wired(monkeypatch):
    """Reset the settings cache (to pick up the webhook secret) and inject fakes."""
    monkeypatch.setattr(config_module, "_settings", None)
    fake_supa = _FakeSupabaseClient()
    fake_customers = _FakeSupabaseClient()
    fake_tg = _FakeTelegramNotifier()
    monkeypatch.setattr(metaapi_module, "_client", _FakeMetaApiClient())
    monkeypatch.setattr(supabase_module, "_client", fake_supa)
    monkeypatch.setattr(supabase_module, "_customers_client", fake_customers)
    monkeypatch.setattr(telegram_module, "_notifier", fake_tg)
    with TestClient(app) as client:
        yield client, fake_customers, fake_tg


def test_missing_secret_returns_401(wired):
    client, _, _ = wired
    response = client.post(ENDPOINT, json=VALID_PAYLOAD)
    assert response.status_code == 401


def test_wrong_secret_returns_401(wired):
    client, _, _ = wired
    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": "nope"})
    assert response.status_code == 401


def test_valid_alert_persists_and_broadcasts(wired):
    client, fake_customers, fake_tg = wired
    response = client.post(ENDPOINT, json=VALID_PAYLOAD, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    body = response.json()
    assert body == {"post_id": "post-123", "broadcast": True}

    # Persisted into the customers project's analysis_posts table (public schema).
    assert len(fake_customers.rows) == 1
    table, row = fake_customers.rows[0]
    assert table == "analysis_posts"
    assert row["symbol"] == "XAUUSD"
    assert row["bias"] == "bullish"

    # Telegram notified.
    assert len(fake_tg.sent) == 1


def test_vocab_normalization_buy_to_bullish(wired):
    client, fake_customers, fake_tg = wired
    payload = {**VALID_PAYLOAD, "bias": "buy"}
    response = client.post(ENDPOINT, json=payload, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    _, row = fake_customers.rows[0]
    assert row["bias"] == "bullish"
    assert fake_tg.sent[0].bias == "bullish"


def test_vocab_normalization_short_to_bearish(wired):
    client, fake_customers, _ = wired
    payload = {**VALID_PAYLOAD, "bias": "SHORT"}
    response = client.post(ENDPOINT, json=payload, headers={"X-Webhook-Secret": SECRET})

    assert response.status_code == 200
    _, row = fake_customers.rows[0]
    assert row["bias"] == "bearish"


def test_invalid_confidence_returns_422(wired):
    client, _, _ = wired
    payload = {**VALID_PAYLOAD, "confidence": 150}
    response = client.post(ENDPOINT, json=payload, headers={"X-Webhook-Secret": SECRET})
    assert response.status_code == 422
