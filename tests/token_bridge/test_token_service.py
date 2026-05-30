from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.token_bridge.models import TokenState
from src.token_bridge.token_service import TokenService


def _rpc_returning(data):
    sb = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=data)
    sb.rpc.return_value = chain
    return sb


def _table_returning(rows):
    sb = MagicMock()
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=rows)
    # Build sb.table(...).select(...).eq(...).eq(...).eq(...).limit(...)
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value = (
        chain
    )
    return sb


@pytest.mark.asyncio
async def test_activate_next_returns_token_id() -> None:
    svc = TokenService(_rpc_returning("token-uuid-123"))
    result = await svc.activate_next("cust", "gold_ai")
    assert result == "token-uuid-123"


@pytest.mark.asyncio
async def test_activate_next_returns_none_when_rpc_returns_none() -> None:
    svc = TokenService(_rpc_returning(None))
    result = await svc.activate_next("cust", "gold_ai")
    assert result is None


@pytest.mark.asyncio
async def test_activate_next_returns_none_on_exception() -> None:
    sb = MagicMock()
    sb.rpc.side_effect = RuntimeError("boom")
    svc = TokenService(sb)
    assert await svc.activate_next("cust", "gold_ai") is None


@pytest.mark.asyncio
async def test_add_trade_returns_ok_result() -> None:
    sb = _rpc_returning(
        {"ok": True, "token_id": "tk-1", "net_pnl": 23.5, "expired": False, "expiry_reason": None}
    )
    svc = TokenService(sb)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = await svc.add_trade("cust", "gold_ai", "pos1", "XAUUSD", 23.5, now, now)
    assert r.ok is True
    assert r.token_id == "tk-1"
    assert r.net_pnl == 23.5
    assert r.expired is False


@pytest.mark.asyncio
async def test_add_trade_returns_error_on_exception() -> None:
    sb = MagicMock()
    sb.rpc.side_effect = RuntimeError("db down")
    svc = TokenService(sb)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    r = await svc.add_trade("cust", "gold_ai", "pos1", "XAUUSD", 1.0, now, now)
    assert r.ok is False
    assert r.error is not None
    assert "db down" in r.error


@pytest.mark.asyncio
async def test_friday_close_returns_int() -> None:
    svc = TokenService(_rpc_returning(7))
    assert await svc.friday_close() == 7


@pytest.mark.asyncio
async def test_friday_close_returns_zero_on_exception() -> None:
    sb = MagicMock()
    sb.rpc.side_effect = RuntimeError("nope")
    svc = TokenService(sb)
    assert await svc.friday_close() == 0


@pytest.mark.asyncio
async def test_get_active_token_returns_info() -> None:
    row = {
        "id": "tk-9",
        "customer_id": "cust",
        "product_code": "gold_ai",
        "subscription_id": "sub-1",
        "token_index": 2,
        "state": "active",
        "net_pnl": 12.0,
        "target_win": 95,
        "target_loss": 70,
    }
    svc = TokenService(_table_returning([row]))
    info = await svc.get_active_token("cust", "gold_ai")
    assert info is not None
    assert info.id == "tk-9"
    assert info.state == TokenState.ACTIVE
    assert info.token_index == 2


@pytest.mark.asyncio
async def test_get_active_token_returns_none_on_empty() -> None:
    svc = TokenService(_table_returning([]))
    assert await svc.get_active_token("cust", "gold_ai") is None
