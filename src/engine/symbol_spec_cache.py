"""Phase 2.6.2 — Symbol specification cache.

The order executor needs the broker's `stopsLevel`/`point`/`digits` per symbol
to pad SL/TP before placing an order. These values are static per symbol, so
hitting MetaApi's `get_symbol_specification` on every tick would be wasteful.

`SymbolSpecCache` wraps the RPC connection and caches the parsed spec per
symbol for a TTL (default 5 minutes).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    stops_level: int
    freeze_level: int
    point: float
    digits: int


def _field(spec: Any, key: str) -> Any:
    if isinstance(spec, dict):
        return spec.get(key)
    return getattr(spec, key, None)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_symbol_spec(symbol: str, raw: Any) -> SymbolSpec:
    """Parse a MetaApi symbol specification (dict or object) into SymbolSpec.

    `point` and `digits` are cross-derived when one is missing, since they are
    mathematically linked (point == 10 ** -digits).
    """
    stops_level = _to_int(_field(raw, "stopsLevel"), 0)
    freeze_level = _to_int(_field(raw, "freezeLevel"), 0)
    point = _to_float(_field(raw, "point"), 0.0)
    digits = _to_int(_field(raw, "digits"), 0)

    if point <= 0 and digits > 0:
        point = 10 ** (-digits)
    if digits <= 0 and point > 0:
        digits = max(0, round(-math.log10(point)))

    return SymbolSpec(
        symbol=symbol,
        stops_level=stops_level,
        freeze_level=freeze_level,
        point=point,
        digits=digits,
    )


class SymbolSpecCache:
    """Caches broker symbol specifications per symbol with a TTL."""

    def __init__(
        self,
        conn_provider: Callable[[], Awaitable[Any]],
        ttl_seconds: float = 300.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._get_conn = conn_provider
        self._ttl = ttl_seconds
        self._now = time_fn
        self._cache: dict[str, tuple[float, SymbolSpec]] = {}

    async def get(self, symbol: str, force_refresh: bool = False) -> SymbolSpec:
        if not force_refresh:
            cached = self._cache.get(symbol)
            if cached is not None:
                ts, spec = cached
                if (self._now() - ts) < self._ttl:
                    return spec
        conn = await self._get_conn()
        raw = await conn.get_symbol_specification(symbol)
        spec = parse_symbol_spec(symbol, raw)
        self._cache[symbol] = (self._now(), spec)
        return spec

    def invalidate(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._cache.clear()
        else:
            self._cache.pop(symbol, None)
