from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.trade_logger import TradeLogger


def _sb_with_capture():
    """Return (client, captured) where captured records the upsert row/args."""
    captured: dict = {}

    def upsert(row, on_conflict=None, ignore_duplicates=None):
        captured["row"] = row
        captured["on_conflict"] = on_conflict
        captured["ignore_duplicates"] = ignore_duplicates
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[row]))

    table = MagicMock()
    table.upsert = upsert
    client = MagicMock()
    client.table = MagicMock(return_value=table)
    return client, captured


@pytest.mark.asyncio
async def test_record_closed_trade_inserts_serialized_row() -> None:
    client, captured = _sb_with_capture()
    tl = TradeLogger(client)
    opened = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 6, 3, 11, 0, tzinfo=timezone.utc)
    ok = await tl.record_closed_trade(
        position_id="P1",
        product="gold_ai",
        symbol="XAUUSD.v",
        symbol_norm="XAUUSD",
        pnl=42.5,
        closed_at=closed,
        opened_at=opened,
        side="BUY",
        lot=0.03,
        setup="order_block",
        duration_seconds=3600,
        dry_run=True,
    )
    assert ok is True
    row = captured["row"]
    assert row["position_id"] == "P1"
    assert row["product"] == "gold_ai"
    assert row["symbol_norm"] == "XAUUSD"
    assert row["pnl"] == 42.5
    assert row["dry_run"] is True
    # datetimes serialized to ISO strings for the JSON insert
    assert row["opened_at"] == opened.isoformat()
    assert row["closed_at"] == closed.isoformat()
    # idempotent upsert config
    assert captured["on_conflict"] == "position_id"
    assert captured["ignore_duplicates"] is True


@pytest.mark.asyncio
async def test_record_closed_trade_noop_without_client() -> None:
    tl = TradeLogger(None)
    ok = await tl.record_closed_trade(
        position_id="P1",
        product="gold_ai",
        symbol="XAUUSD",
        symbol_norm="XAUUSD",
        pnl=1.0,
        closed_at=datetime.now(timezone.utc),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_record_closed_trade_swallows_errors() -> None:
    client = MagicMock()
    client.table = MagicMock(side_effect=RuntimeError("db down"))
    tl = TradeLogger(client)
    ok = await tl.record_closed_trade(
        position_id="P1",
        product="gold_ai",
        symbol="XAUUSD",
        symbol_norm="XAUUSD",
        pnl=1.0,
        closed_at=datetime.now(timezone.utc),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_fetch_trades_builds_query_and_returns_rows() -> None:
    rows = [{"position_id": "P1", "pnl": 5.0}]

    class _Query:
        def __init__(self):
            self.calls = []

        def select(self, *a):
            self.calls.append(("select", a))
            return self

        def eq(self, *a):
            self.calls.append(("eq", a))
            return self

        def gte(self, *a):
            self.calls.append(("gte", a))
            return self

        def order(self, *a, **k):
            self.calls.append(("order", a, k))
            return self

        def limit(self, n):
            self.calls.append(("limit", n))
            return self

        def execute(self):
            return SimpleNamespace(data=rows)

    q = _Query()
    client = MagicMock()
    client.table = MagicMock(return_value=q)
    tl = TradeLogger(client)
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    out = await tl.fetch_trades("gold_ai", start=start, limit=10)
    assert out == rows
    # default excludes dry_run
    assert ("eq", ("dry_run", False)) in q.calls
    assert ("eq", ("product", "gold_ai")) in q.calls
    assert any(c[0] == "gte" for c in q.calls)
    assert ("limit", 10) in q.calls


@pytest.mark.asyncio
async def test_fetch_trades_include_dry_run_skips_filter() -> None:
    class _Query:
        def __init__(self):
            self.eq_calls = []

        def select(self, *a):
            return self

        def eq(self, *a):
            self.eq_calls.append(a)
            return self

        def order(self, *a, **k):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    q = _Query()
    client = MagicMock()
    client.table = MagicMock(return_value=q)
    tl = TradeLogger(client)
    await tl.fetch_trades("multi_cfd_ai", include_dry_run=True)
    assert ("dry_run", False) not in q.eq_calls


@pytest.mark.asyncio
async def test_fetch_trades_noop_without_client() -> None:
    tl = TradeLogger(None)
    assert await tl.fetch_trades("gold_ai") == []
