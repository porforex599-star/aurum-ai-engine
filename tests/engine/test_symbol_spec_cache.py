from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.engine.symbol_spec_cache import (
    SymbolSpecCache,
    parse_symbol_spec,
)


def test_parse_from_dict() -> None:
    spec = parse_symbol_spec(
        "EURUSD.v",
        {"stopsLevel": 10, "freezeLevel": 5, "point": 0.00001, "digits": 5},
    )
    assert spec.stops_level == 10
    assert spec.freeze_level == 5
    assert spec.point == 0.00001
    assert spec.digits == 5


def test_parse_derives_point_from_digits() -> None:
    spec = parse_symbol_spec("USDJPY.v", {"stopsLevel": 10, "digits": 3})
    assert spec.point == pytest.approx(0.001)


def test_parse_derives_digits_from_point() -> None:
    spec = parse_symbol_spec("SP500.v", {"stopsLevel": 100, "point": 0.1})
    assert spec.digits == 1


def _conn(spec_value):
    conn = AsyncMock()
    conn.get_symbol_specification = AsyncMock(return_value=spec_value)
    return conn


@pytest.mark.asyncio
async def test_cache_hits_within_ttl() -> None:
    conn = _conn({"stopsLevel": 10, "point": 0.00001, "digits": 5})

    async def provider():
        return conn

    clock = {"t": 0.0}
    cache = SymbolSpecCache(provider, ttl_seconds=300.0, time_fn=lambda: clock["t"])

    s1 = await cache.get("EURUSD.v")
    clock["t"] = 100.0
    s2 = await cache.get("EURUSD.v")

    assert s1 == s2
    conn.get_symbol_specification.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_refreshes_after_ttl() -> None:
    conn = _conn({"stopsLevel": 10, "point": 0.00001, "digits": 5})

    async def provider():
        return conn

    clock = {"t": 0.0}
    cache = SymbolSpecCache(provider, ttl_seconds=300.0, time_fn=lambda: clock["t"])

    await cache.get("EURUSD.v")
    clock["t"] = 301.0
    await cache.get("EURUSD.v")

    assert conn.get_symbol_specification.await_count == 2


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache() -> None:
    conn = _conn({"stopsLevel": 10, "point": 0.00001, "digits": 5})

    async def provider():
        return conn

    cache = SymbolSpecCache(provider, ttl_seconds=300.0, time_fn=lambda: 0.0)
    await cache.get("EURUSD.v")
    await cache.get("EURUSD.v", force_refresh=True)
    assert conn.get_symbol_specification.await_count == 2


@pytest.mark.asyncio
async def test_invalidate_clears_entry() -> None:
    conn = _conn({"stopsLevel": 10, "point": 0.00001, "digits": 5})

    async def provider():
        return conn

    cache = SymbolSpecCache(provider, ttl_seconds=300.0, time_fn=lambda: 0.0)
    await cache.get("EURUSD.v")
    cache.invalidate("EURUSD.v")
    await cache.get("EURUSD.v")
    assert conn.get_symbol_specification.await_count == 2
