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
from src.engine.freeze_manager import FreezeState  # noqa: E402
from src.engine.runtime import get_runtime  # noqa: E402


class _StubFreeze:
    def __init__(self, state: FreezeState | None = None) -> None:
        self._state = state or FreezeState.unfrozen()

    async def get_state(self, force_refresh: bool = False) -> FreezeState:  # noqa: ARG002
        return self._state


def _fake_runtime(dry_run: bool = True, freeze: FreezeState | None = None):
    return SimpleNamespace(
        settings=SimpleNamespace(dry_run=dry_run),
        last_tick=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_tick_status="ok",
        freeze_manager=_StubFreeze(freeze),
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


def test_status_includes_freeze_section_unfrozen_default() -> None:
    rt = _fake_runtime()
    client = TestClient(_build_app(rt))
    body = client.get("/status").json()
    assert body["freeze"]["frozen"] is False
    assert body["freeze"]["reason"] is None


def test_status_shows_frozen_state_with_metadata() -> None:
    state = FreezeState(
        frozen=True,
        reason="manual_kill",
        frozen_by="por",
        frozen_at=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
    )
    rt = _fake_runtime(freeze=state)
    client = TestClient(_build_app(rt))
    body = client.get("/status").json()
    assert body["freeze"]["frozen"] is True
    assert body["freeze"]["reason"] == "manual_kill"
    assert body["freeze"]["frozen_by"] == "por"
