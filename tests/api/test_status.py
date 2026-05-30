from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_LEVEL", "INFO")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.status import router as status_router  # noqa: E402
from src.engine.runtime import get_runtime  # noqa: E402


def _fake_runtime(dry_run: bool = True):
    return SimpleNamespace(
        settings=SimpleNamespace(dry_run=dry_run),
        last_tick=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_tick_status="ok",
        products={
            "gold_ai": SimpleNamespace(
                day_tracker=SimpleNamespace(
                    state=SimpleNamespace(
                        total_pnl_usd=10.0, trades_opened=2
                    )
                ),
                week_tracker=SimpleNamespace(
                    state=SimpleNamespace(
                        net_pnl_usd=50.0, state="active", cycle_id="w1"
                    )
                ),
            )
        },
    )


def _build_app(rt):
    app = FastAPI()
    app.include_router(status_router)
    app.dependency_overrides[get_runtime] = lambda: rt
    return app


def test_status_returns_expected_shape() -> None:
    rt = _fake_runtime(dry_run=True)
    client = TestClient(_build_app(rt))
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["last_tick_status"] == "ok"
    assert "gold_ai" in body["products"]
    assert body["products"]["gold_ai"]["week_state"] == "active"


def test_status_reflects_dry_run_false() -> None:
    rt = _fake_runtime(dry_run=False)
    client = TestClient(_build_app(rt))
    r = client.get("/status")
    assert r.json()["dry_run"] is False
