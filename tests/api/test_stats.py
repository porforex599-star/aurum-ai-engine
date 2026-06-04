from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.stats import compute_stats, period_start
from src.config import Settings
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


def _trade(pnl, closed_at, symbol_norm="XAUUSD", duration=3600):
    return {
        "position_id": f"p{pnl}",
        "pnl": pnl,
        "closed_at": closed_at.isoformat(),
        "symbol_norm": symbol_norm,
        "duration_seconds": duration,
    }


# -------------------- compute_stats (pure) --------------------


def test_compute_stats_empty() -> None:
    s = compute_stats([])
    assert s["total_trades"] == 0
    assert s["win_rate"] == 0.0
    assert s["profit_factor"] is None
    assert s["avg_trade_duration"] is None


def test_compute_stats_kpis() -> None:
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    trades = [
        _trade(100.0, now - timedelta(days=1)),
        _trade(-40.0, now - timedelta(hours=12)),
        _trade(60.0, now - timedelta(hours=6)),
        _trade(-20.0, now - timedelta(hours=1)),
    ]
    s = compute_stats(trades, start=now - timedelta(days=2), now=now)
    assert s["total_trades"] == 4
    assert s["win_count"] == 2
    assert s["loss_count"] == 2
    assert s["win_rate"] == 0.5
    assert s["total_pnl"] == 100.0
    assert s["avg_win"] == 80.0
    assert s["avg_loss"] == -30.0
    assert s["biggest_win"] == 100.0
    assert s["biggest_loss"] == -40.0
    # gross win 160 / gross loss 60
    assert s["profit_factor"] == pytest.approx(2.67, abs=0.01)
    assert s["avg_trade_duration"] == 3600


def test_compute_stats_profit_factor_none_without_losses() -> None:
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    s = compute_stats([_trade(10.0, now), _trade(5.0, now)], now=now)
    assert s["profit_factor"] is None


def test_compute_stats_max_drawdown() -> None:
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    # curve: +100 -> 60 (dd 40) -> 160 -> 110 (peak 160, trough 110 dd 50)
    trades = [
        _trade(100.0, now - timedelta(hours=4)),
        _trade(-40.0, now - timedelta(hours=3)),
        _trade(100.0, now - timedelta(hours=2)),
        _trade(-50.0, now - timedelta(hours=1)),
    ]
    s = compute_stats(trades, now=now)
    assert s["max_drawdown"] == 50.0


def test_period_start_all_is_none() -> None:
    assert period_start("all") is None


def test_period_start_7d() -> None:
    now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    assert period_start("7d", now) == now - timedelta(days=7)


# -------------------- endpoints --------------------


def _runtime_with_trades(monkeypatch, trades, *, fetch=None):
    monkeypatch.setenv("ADMIN_KEY", "test-admin-secret")
    rt = AppRuntime(_settings(), MagicMock(), MagicMock(), MagicMock())
    tl = SimpleNamespace(fetch_trades=fetch or AsyncMock(return_value=trades))
    rt.trade_logger = tl  # type: ignore[assignment]
    set_runtime(rt)
    return rt


_HDR = {"X-Admin-Key": "test-admin-secret"}


def test_stats_requires_admin_key(monkeypatch) -> None:
    _runtime_with_trades(monkeypatch, [])
    try:
        r = TestClient(app).get("/stats/gold_ai")
        assert r.status_code == 401
    finally:
        set_runtime(None)


def test_stats_rejects_unknown_slug(monkeypatch) -> None:
    _runtime_with_trades(monkeypatch, [])
    try:
        r = TestClient(app).get("/stats/bogus", headers=_HDR)
        assert r.status_code == 400
    finally:
        set_runtime(None)


def test_stats_rejects_unknown_period(monkeypatch) -> None:
    _runtime_with_trades(monkeypatch, [])
    try:
        r = TestClient(app).get("/stats/gold_ai?period=99y", headers=_HDR)
        assert r.status_code == 400
    finally:
        set_runtime(None)


def test_stats_gold_returns_kpis(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    trades = [_trade(10.0, now), _trade(-5.0, now)]
    _runtime_with_trades(monkeypatch, trades)
    try:
        r = TestClient(app).get("/stats/gold_ai?period=7d", headers=_HDR)
        assert r.status_code == 200
        body = r.json()
        assert body["product"] == "gold_ai"
        assert body["period"] == "7d"
        assert body["stats"]["total_trades"] == 2
        assert "per_symbol" not in body
    finally:
        set_runtime(None)


def test_stats_multi_includes_per_symbol_breakdown(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    trades = [
        _trade(10.0, now, symbol_norm="NAS100"),
        _trade(-3.0, now, symbol_norm="EURUSD"),
    ]
    _runtime_with_trades(monkeypatch, trades)
    try:
        r = TestClient(app).get("/stats/multi_cfd_ai?period=30d", headers=_HDR)
        assert r.status_code == 200
        body = r.json()
        assert "per_symbol" in body
        # all configured symbols present, even zero-trade ones
        assert set(body["per_symbol"].keys()) >= {
            "NAS100", "US500", "EURUSD", "GBPUSD", "USDJPY", "GER40"
        }
        assert body["per_symbol"]["NAS100"]["total_trades"] == 1
        assert body["per_symbol"]["GER40"]["total_trades"] == 0
    finally:
        set_runtime(None)


def test_stats_passes_include_dry_run(monkeypatch) -> None:
    fetch = AsyncMock(return_value=[])
    _runtime_with_trades(monkeypatch, [], fetch=fetch)
    try:
        TestClient(app).get(
            "/stats/gold_ai?period=all&include_dry_run=true", headers=_HDR
        )
        _, kwargs = fetch.call_args
        assert kwargs["include_dry_run"] is True
        assert kwargs["start"] is None
    finally:
        set_runtime(None)


def test_trades_endpoint_returns_rows(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    trades = [_trade(10.0, now), _trade(-5.0, now)]
    fetch = AsyncMock(return_value=trades)
    _runtime_with_trades(monkeypatch, trades, fetch=fetch)
    try:
        r = TestClient(app).get("/trades/gold_ai?period=7d&limit=25", headers=_HDR)
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert body["trades"] == trades
        _, kwargs = fetch.call_args
        assert kwargs["limit"] == 25
    finally:
        set_runtime(None)
