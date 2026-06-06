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


@pytest.mark.asyncio
async def test_fetch_deal_info_extracts_entry_exit_side_and_comment() -> None:
    entry_time = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 6, 3, 12, 30, tzinfo=timezone.utc)
    conn = AsyncMock()
    conn.get_deals_by_time_range = AsyncMock(
        return_value={
            "deals": [
                {
                    "positionId": "9",
                    "entryType": "DEAL_ENTRY_IN",
                    "type": "DEAL_TYPE_BUY",
                    "symbol": "US500.v",
                    "comment": "AURUM_AI order_block",
                    "price": 5000.0,
                    "volume": 0.02,
                    "time": entry_time,
                    "profit": 0.0,
                    "swap": 0.0,
                    "commission": -1.0,
                },
                {
                    "positionId": "9",
                    "entryType": "DEAL_ENTRY_OUT",
                    "type": "DEAL_TYPE_SELL",
                    "symbol": "US500.v",
                    "comment": "AURUM_AI order_block",
                    "price": 5025.0,
                    "volume": 0.02,
                    "time": exit_time,
                    "profit": 50.0,
                    "swap": -2.0,
                    "commission": -1.0,
                },
            ]
        }
    )
    cd = CloseDetector(_provider(conn))
    cd.update_open([_pos("9", "US500.v")])
    info = await cd.fetch_deal_info("9")
    assert info is not None
    assert info["side"] == "BUY"
    assert info["symbol"] == "US500.v"
    assert info["comment"] == "AURUM_AI order_block"
    assert info["entry_price"] == 5000.0
    assert info["exit_price"] == 5025.0
    assert info["lot"] == 0.02
    # Real broker times, not the tick-poll sighting time.
    assert info["opened_at"] == entry_time
    assert info["closed_at"] == exit_time
    # net = 50 + (-2 swap) + (-1 -1 commission) = 46
    assert info["pnl"] == pytest.approx(46.0)
    assert info["gross_profit"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_fetch_deal_info_falls_back_when_no_entry_type() -> None:
    """Brokers that omit entryType still yield PnL + meta symbol; new fields
    degrade to None rather than raising."""
    conn = AsyncMock()
    conn.get_deals_by_time_range = AsyncMock(
        return_value={"deals": [{"positionId": "7", "profit": 10.0}]}
    )
    cd = CloseDetector(_provider(conn))
    cd.update_open([_pos("7", "EURUSD")])
    info = await cd.fetch_deal_info("7")
    assert info is not None
    assert info["symbol"] == "EURUSD"
    assert info["pnl"] == pytest.approx(10.0)
    assert info["exit_price"] is None


def test_cleanup_meta_removes_tracked_positions() -> None:
    cd = CloseDetector(_provider(AsyncMock()))
    cd.update_open([_pos("1"), _pos("2")])
    assert "1" in cd._position_meta and "2" in cd._position_meta
    cd.cleanup_meta(["1"])
    assert "1" not in cd._position_meta
    assert "2" in cd._position_meta
