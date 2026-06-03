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


class _StubSnapshot:
    def __init__(self, account=None, positions=None, raises: bool = False) -> None:
        self._account = account
        self._positions = positions or []
        self._raises = raises

    async def get(self, force_refresh: bool = False):  # noqa: ARG002
        if self._raises:
            raise RuntimeError("rpc down")
        return SimpleNamespace(
            account=self._account, positions=self._positions, fetched_at=0.0
        )


def _pos(symbol, comment, pid="1", pnl=1.0):
    return {
        "position_id": pid,
        "symbol": symbol,
        "side": "BUY",
        "lot": 0.02,
        "open_price": 100.0,
        "current_price": 101.0,
        "floating_pnl": pnl,
        "opened_at": "2026-06-03T13:43:00+00:00",
        "comment": comment,
        "magic": 0,
    }


def _fake_runtime(
    dry_run: bool = True,
    freeze: FreezeState | None = None,
    account=None,
    positions=None,
    snapshot_raises: bool = False,
):
    return SimpleNamespace(
        settings=SimpleNamespace(dry_run=dry_run),
        last_tick=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_tick_status="ok",
        freeze_manager=_StubFreeze(freeze),
        account_snapshot=_StubSnapshot(account, positions, snapshot_raises),
        products={
            "gold_ai": SimpleNamespace(
                config=SimpleNamespace(symbols=("XAUUSD.v",)),
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
            ),
            "multi_cfd_ai": SimpleNamespace(
                config=SimpleNamespace(
                    symbols=("EURUSD.v", "NAS100.v", "US500.v")
                ),
                day_tracker=SimpleNamespace(
                    state=SimpleNamespace(total_pnl_usd=0.0, trades_opened=0)
                ),
                week_tracker=SimpleNamespace(
                    state=SimpleNamespace(
                        net_pnl_usd=0.0, state="active", cycle_id="w2"
                    )
                ),
            ),
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


def test_status_includes_master_account_when_rpc_up() -> None:
    account = {
        "login": 97038939,
        "broker": "InterStellar",
        "server": "InterStellarFinancial-Server",
        "balance": 238.67,
        "equity": 317.65,
        "margin_used": 318.95,
        "margin_free": -1.30,
        "margin_level": 99.59,
        "currency": "USC",
    }
    rt = _fake_runtime(account=account)
    body = TestClient(_build_app(rt)).get("/status").json()
    assert body["master_account"]["login"] == 97038939
    assert body["master_account"]["margin_level"] == 99.59
    assert body["master_account"]["currency"] == "USC"


def test_status_master_account_null_when_rpc_down() -> None:
    # snapshot.get() raises → /status degrades gracefully, does not 500.
    rt = _fake_runtime(snapshot_raises=True)
    r = TestClient(_build_app(rt)).get("/status")
    assert r.status_code == 200
    assert r.json()["master_account"] is None


def test_status_attributes_positions_by_symbol_and_comment() -> None:
    positions = [
        _pos("XAUUSD.v", "AURUM_AI order_block", pid="g1", pnl=5.2),
        _pos("XAUUSD.v", "AURUM_AI", pid="manual1", pnl=2.0),  # manual → excluded
        _pos("NAS100.v", "AURUM_AI trend_continuation", pid="m1", pnl=3.0),
        _pos("GER40.v", "AURUM_AI mean_reversion", pid="x1"),  # not configured → excluded
    ]
    rt = _fake_runtime(positions=positions)
    body = TestClient(_build_app(rt)).get("/status").json()

    gold = body["products"]["gold_ai"]
    assert gold["symbols"] == ["XAUUSD.v"]
    assert gold["magic_number"] is None
    assert gold["open_positions_count"] == 1
    assert gold["open_positions"][0]["position_id"] == "g1"
    # internal fields must not leak to the client
    assert "comment" not in gold["open_positions"][0]
    assert "magic" not in gold["open_positions"][0]

    multi = body["products"]["multi_cfd_ai"]
    assert multi["open_positions_count"] == 1
    assert multi["open_positions"][0]["symbol"] == "NAS100.v"


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
