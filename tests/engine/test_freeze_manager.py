from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from src.engine.freeze_manager import FreezeManager, FreezeState


class _FakeQuery:
    """Chainable stub for the supabase-py query builder."""

    def __init__(self, parent: "_FakeTable") -> None:
        self._parent = parent

    def select(self, *_a, **_kw) -> "_FakeQuery":
        self._parent.select_calls += 1
        return self

    def eq(self, *_a, **_kw) -> "_FakeQuery":
        return self

    def limit(self, *_a, **_kw) -> "_FakeQuery":
        return self

    def execute(self) -> Any:
        if self._parent.error is not None:
            raise self._parent.error
        return type("Res", (), {"data": list(self._parent.rows)})()


class _FakeTable:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.upserts: list[dict] = []
        self.select_calls = 0
        self.error: Exception | None = None

    def select(self, *a, **kw) -> _FakeQuery:
        q = _FakeQuery(self)
        return q.select(*a, **kw)

    def upsert(self, payload: dict) -> "_FakeUpsert":
        self.upserts.append(payload)
        # Mirror the row so a subsequent SELECT sees the write.
        self.rows = [payload]
        return _FakeUpsert(self)


class _FakeUpsert:
    def __init__(self, parent: _FakeTable) -> None:
        self._parent = parent

    def execute(self) -> Any:
        return type("Res", (), {"data": [self._parent.rows[-1]]})()


class _FakeClient:
    def __init__(self) -> None:
        self._table = _FakeTable()

    def table(self, name: str) -> _FakeTable:
        assert name == "engine_config"
        return self._table


# -------------------- get_state / cache --------------------


async def test_unfrozen_when_no_row_in_db() -> None:
    client = _FakeClient()
    fm = FreezeManager(client)
    state = await fm.get_state()
    assert state.frozen is False
    assert state.reason is None
    assert state.cached is False


async def test_returns_row_when_present() -> None:
    client = _FakeClient()
    client._table.rows = [
        {
            "id": "global",
            "frozen": True,
            "frozen_reason": "manual_kill",
            "frozen_at": "2026-06-02T10:00:00+00:00",
            "frozen_by": "por",
            "updated_at": "2026-06-02T10:00:00+00:00",
        }
    ]
    fm = FreezeManager(client)
    state = await fm.get_state()
    assert state.frozen is True
    assert state.reason == "manual_kill"
    assert state.frozen_by == "por"
    assert isinstance(state.frozen_at, datetime)
    assert state.frozen_at.tzinfo is not None


async def test_cache_avoids_repeat_db_reads() -> None:
    client = _FakeClient()
    client._table.rows = [{"id": "global", "frozen": True}]
    fm = FreezeManager(client, cache_ttl_seconds=60.0)
    await fm.get_state()
    first_calls = client._table.select_calls
    # Three more reads — all should be served from cache.
    for _ in range(3):
        s = await fm.get_state()
        assert s.frozen is True
        assert s.cached is True
    assert client._table.select_calls == first_calls


async def test_force_refresh_bypasses_cache() -> None:
    client = _FakeClient()
    client._table.rows = [{"id": "global", "frozen": False}]
    fm = FreezeManager(client, cache_ttl_seconds=600.0)
    await fm.get_state()
    # Now mutate DB and force refresh.
    client._table.rows = [{"id": "global", "frozen": True, "frozen_reason": "x"}]
    state = await fm.get_state(force_refresh=True)
    assert state.frozen is True
    assert state.reason == "x"
    assert state.cached is False


async def test_db_error_returns_last_known_state() -> None:
    """Fail-open: on a transient DB error, return cached state, not unfrozen."""
    client = _FakeClient()
    client._table.rows = [{"id": "global", "frozen": True}]
    fm = FreezeManager(client, cache_ttl_seconds=0.0)  # always-stale cache
    first = await fm.get_state()
    assert first.frozen is True
    # Now break the DB.
    client._table.error = RuntimeError("supabase down")
    second = await fm.get_state(force_refresh=True)
    assert second.frozen is True  # cached value preserved
    assert second.cached is True


async def test_db_error_with_no_cache_defaults_unfrozen() -> None:
    client = _FakeClient()
    client._table.error = ConnectionError("no network")
    fm = FreezeManager(client)
    state = await fm.get_state()
    assert state.frozen is False


# -------------------- is_frozen shortcut --------------------


async def test_is_frozen_shortcut_true_and_false() -> None:
    client = _FakeClient()
    client._table.rows = [{"id": "global", "frozen": True}]
    fm = FreezeManager(client)
    assert await fm.is_frozen() is True
    client._table.rows = [{"id": "global", "frozen": False}]
    assert await fm.is_frozen() is True  # still cached True
    fm._cache = None  # type: ignore[attr-defined]
    fm._cache_expires_at = 0.0  # type: ignore[attr-defined]
    assert await fm.is_frozen() is False


# -------------------- set_frozen --------------------


async def test_set_frozen_upserts_and_invalidates_cache() -> None:
    client = _FakeClient()
    fm = FreezeManager(client, cache_ttl_seconds=600.0)
    # Prime cache as unfrozen.
    await fm.get_state()
    state = await fm.set_frozen(True, reason="kill", by="por")
    assert state.frozen is True
    assert state.reason == "kill"
    assert state.frozen_by == "por"
    # Upsert payload reflects the freeze.
    assert client._table.upserts[-1]["frozen"] is True
    assert client._table.upserts[-1]["frozen_reason"] == "kill"
    assert client._table.upserts[-1]["frozen_by"] == "por"


async def test_set_unfrozen_clears_metadata_columns() -> None:
    client = _FakeClient()
    fm = FreezeManager(client)
    await fm.set_frozen(True, reason="x", by="y")
    state = await fm.set_frozen(False)
    assert state.frozen is False
    last = client._table.upserts[-1]
    assert last["frozen"] is False
    assert last["frozen_reason"] is None
    assert last["frozen_at"] is None
    assert last["frozen_by"] is None


async def test_set_frozen_raises_when_client_missing() -> None:
    class _NoneSb:
        def get_client(self):  # type: ignore[no-untyped-def]
            return None

    fm = FreezeManager(_NoneSb())
    with pytest.raises(RuntimeError):
        await fm.set_frozen(True)


# -------------------- supports either wrapper or raw client --------------------


async def test_accepts_supabase_wrapper_with_get_client() -> None:
    raw = _FakeClient()
    raw._table.rows = [{"id": "global", "frozen": False}]

    class _Wrapper:
        def get_client(self):  # type: ignore[no-untyped-def]
            return raw

    fm = FreezeManager(_Wrapper())
    state = await fm.get_state()
    assert state.frozen is False


# -------------------- unfrozen() factory --------------------


def test_freeze_state_unfrozen_factory() -> None:
    s = FreezeState.unfrozen()
    assert s.frozen is False
    assert s.reason is None
    assert s.frozen_at is None
