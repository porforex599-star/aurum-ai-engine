from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.intents import router as intents_router  # noqa: E402
from src.engine.intent_bus import IntentBus  # noqa: E402
from src.engine.runtime import get_runtime  # noqa: E402


def _build_app(bus: IntentBus):
    app = FastAPI()
    app.include_router(intents_router)
    rt = SimpleNamespace(intent_bus=bus)
    app.dependency_overrides[get_runtime] = lambda: rt
    return app


def test_recent_intents_empty_initially() -> None:
    bus = IntentBus()
    client = TestClient(_build_app(bus))
    r = client.get("/intents/recent")
    assert r.status_code == 200
    assert r.json()["intents"] == []


def test_recent_intents_returns_last_n() -> None:
    bus = IntentBus()
    bus.publish("gold_ai", "open", {"x": 1}, dry_run=True)
    bus.publish("gold_ai", "none", {}, dry_run=True)
    bus.publish("multi_cfd_ai", "open", {"x": 2}, dry_run=True)
    client = TestClient(_build_app(bus))
    r = client.get("/intents/recent?n=2")
    items = r.json()["intents"]
    assert len(items) == 2
    # Most recent first
    assert items[0]["product"] == "multi_cfd_ai"
