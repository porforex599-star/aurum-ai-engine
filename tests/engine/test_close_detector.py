from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine.close_detector import CloseDetector


def _pos(pid: str, symbol: str = "XAUUSD"):
    return SimpleNamespace(
        position_id=pid, symbol=symbol, opened_at=datetime.now(timezone.utc)
    )


def _provider(conn):
    async def _get():
        return conn

    return _get


def test_detect_closes_returns_closed_ids_when_position_disappears() -> None:
    cd = CloseDetector(_provider(AsyncMock()))
    p1, p2 = _pos("1"), _pos("2")
    # First tick: two positions open.
    assert cd.detect_closes([p1, p2]) == []
    cd.update_open([p1, p2])
    # Second tick: p2 gone.
    closed = cd.detect_closes([p1])
    assert closed == ["2"]


def test_detect_closes_returns_empty_when_no_closes() -> None:
    cd = CloseDetector(_provider(AsyncMock()))
    p1 = _pos("1")
    cd.detect_closes([p1])
    cd.update_open([p1])
    assert cd.detect_closes([p1]) == []


@pytest.mark.asyncio
async def test_fetch_deal_info_aggregates_pnl_from_deals() -> None:
    conn = AsyncMock()
    conn.get_deals_by_time_range = AsyncMock(
        return_value={
            "deals": [
                {"positionId": "7", "profit": 100.0, "swap": -2.0, "commission": -3.0},
                {"positionId": "7", "profit": 5.0, "swap": 0.0, "commission": -1.0},
                {"positionId": "8", "profit": 999.0},  # different position
            ]
        }
    )
    cd = CloseDetector(_provider(conn))
    cd.update_open([_pos("7", "EURUSD")])
    info = await cd.fetch_deal_info("7")
    assert info is not None
    assert info["symbol"] == "EURUSD"
    assert info["pnl"] == pytest.approx(99.0)
    assert info["position_id"] == "7"


def test_cleanup_meta_removes_tracked_positions() -> None:
    cd = CloseDetector(_provider(AsyncMock()))
    cd.update_open([_pos("1"), _pos("2")])
    assert "1" in cd._position_meta and "2" in cd._position_meta
    cd.cleanup_meta(["1"])
    assert "1" not in cd._position_meta
    assert "2" in cd._position_meta
