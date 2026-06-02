from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.engine.freeze_manager import FreezeState
from src.engine.runtime import AppRuntime, set_runtime
from src.main import app


def _settings() -> Settings:
    return Settings(
        SUPABASE_URL="https://x.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="k",
        METAAPI_TOKEN="t",
        METAAPI_MASTER_ACCOUNT_ID="00000000-0000-0000-0000-000000000000",
        APP_ENV="development",
        LOG_LEVEL="INFO",
    )


class _StubFreeze:
    """Stub FreezeManager — records calls without touching Supabase."""

    def __init__(self) -> None:
        self._state = FreezeState.unfrozen()
        self.set_calls: list[tuple[bool, str | None, str | None]] = []

    async def get_state(self, force_refresh: bool = False) -> FreezeState:  # noqa: ARG002
        return self._state

    async def is_frozen(self) -> bool:
        return self._state.frozen

    async def set_frozen(
        self,
        frozen: bool,
        reason: str | None = None,
        by: str | None = None,
    ) -> FreezeState:
        self.set_calls.append((frozen, reason, by))
        from datetime import datetime, timezone

        if frozen:
            self._state = FreezeState(
                frozen=True,
                reason=reason,
                frozen_by=by,
                frozen_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        else:
            self._state = FreezeState.unfrozen()
        return self._state


@pytest.fixture
def client_with_runtime(monkeypatch):
    """Build a TestClient with a runtime whose FreezeManager is a stub."""
    monkeypatch.setenv("ADMIN_KEY", "test-admin-secret")
    rt = AppRuntime(_settings(), MagicMock(), MagicMock(), MagicMock())
    stub = _StubFreeze()
    rt.freeze_manager = stub  # type: ignore[assignment]
    set_runtime(rt)
    try:
        yield TestClient(app), stub
    finally:
        set_runtime(None)


# -------------------- auth gating --------------------


def test_freeze_endpoint_returns_503_when_admin_key_unset(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_KEY", raising=False)
    rt = AppRuntime(_settings(), MagicMock(), MagicMock(), MagicMock())
    rt.freeze_manager = _StubFreeze()  # type: ignore[assignment]
    set_runtime(rt)
    try:
        client = TestClient(app)
        r = client.post("/admin/freeze", json={})
        assert r.status_code == 503
    finally:
        set_runtime(None)


def test_freeze_endpoint_rejects_missing_key(client_with_runtime) -> None:
    client, _ = client_with_runtime
    r = client.post("/admin/freeze", json={})
    assert r.status_code == 401


def test_freeze_endpoint_rejects_wrong_key(client_with_runtime) -> None:
    client, _ = client_with_runtime
    r = client.post(
        "/admin/freeze",
        headers={"X-Admin-Key": "wrong"},
        json={},
    )
    assert r.status_code == 401


# -------------------- happy path --------------------


def test_post_freeze_sets_state_and_publishes_intent(client_with_runtime) -> None:
    client, stub = client_with_runtime
    r = client.post(
        "/admin/freeze",
        headers={"X-Admin-Key": "test-admin-secret"},
        json={"reason": "manual_kill", "by": "por"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["frozen"] is True
    assert body["reason"] == "manual_kill"
    assert body["frozen_by"] == "por"
    assert stub.set_calls == [(True, "manual_kill", "por")]


def test_post_unfreeze_clears_state(client_with_runtime) -> None:
    client, stub = client_with_runtime
    # freeze first
    client.post(
        "/admin/freeze",
        headers={"X-Admin-Key": "test-admin-secret"},
        json={"reason": "x"},
    )
    r = client.post(
        "/admin/unfreeze",
        headers={"X-Admin-Key": "test-admin-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["frozen"] is False
    assert stub.set_calls[-1] == (False, None, None)


def test_get_freeze_returns_current_state(client_with_runtime) -> None:
    client, stub = client_with_runtime
    # not frozen yet
    r = client.get("/admin/freeze", headers={"X-Admin-Key": "test-admin-secret"})
    assert r.status_code == 200
    assert r.json()["frozen"] is False
    # freeze and re-check
    client.post(
        "/admin/freeze",
        headers={"X-Admin-Key": "test-admin-secret"},
        json={"reason": "x", "by": "y"},
    )
    r = client.get("/admin/freeze", headers={"X-Admin-Key": "test-admin-secret"})
    assert r.json()["frozen"] is True
    assert r.json()["reason"] == "x"


def test_freeze_publishes_to_intent_bus(client_with_runtime) -> None:
    client, _ = client_with_runtime
    # Inject a recording bus.
    from src.engine.runtime import get_runtime

    rt = get_runtime()
    rt.intent_bus.clear()
    client.post(
        "/admin/freeze",
        headers={"X-Admin-Key": "test-admin-secret"},
        json={"reason": "x", "by": "y"},
    )
    recent = rt.intent_bus.recent(10)
    kinds = [e.kind for e in recent]
    assert "frozen" in kinds


def test_unfreeze_publishes_to_intent_bus(client_with_runtime) -> None:
    client, _ = client_with_runtime
    from src.engine.runtime import get_runtime

    rt = get_runtime()
    rt.intent_bus.clear()
    client.post(
        "/admin/unfreeze",
        headers={"X-Admin-Key": "test-admin-secret"},
    )
    kinds = [e.kind for e in rt.intent_bus.recent(10)]
    assert "unfrozen" in kinds
