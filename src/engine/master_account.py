"""Phase 6.4 — master-account snapshot + product position attribution.

Two concerns live here, both feeding the admin dashboard endpoints:

1. `AccountSnapshotCache` — wraps the RPC connection and caches the master
   account information + open positions for a few seconds, so the dashboard can
   poll `/status` every ~10s without hammering MetaApi. RPC failures degrade
   gracefully: the snapshot returns `account=None` / `positions=[]` and logs a
   warning rather than breaking `/status`.

2. Position attribution. The engine has **no magic numbers** — orders carry only
   a comment (`"AURUM_AI {setup}"`). Products are therefore attributed exactly
   the way the rest of the engine does it: by **symbol membership** in the
   product's configured set, plus a **comment guard** so a manual `/test/trade`
   order (comment is a bare `"AURUM_AI"`, no setup) on a product symbol is NOT
   mistaken for a strategy position and swept up by close-all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from loguru import logger

# All engine orders are tagged with this comment prefix; strategy orders append
# a setup name ("AURUM_AI order_block"), manual /test/trade orders do not.
_AURUM_PREFIX = "AURUM_AI"


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_account(ai: Any) -> dict:
    """Map a MetatraderAccountInformation (object or dict) to the dashboard shape."""
    return {
        "login": int(_field(ai, "login", 0) or 0),
        "broker": _field(ai, "broker"),
        "server": _field(ai, "server"),
        "balance": float(_field(ai, "balance", 0.0) or 0.0),
        "equity": float(_field(ai, "equity", 0.0) or 0.0),
        "margin_used": float(_field(ai, "margin", 0.0) or 0.0),
        "margin_free": float(_field(ai, "freeMargin", 0.0) or 0.0),
        "margin_level": float(_field(ai, "marginLevel", 0.0) or 0.0),
        "currency": _field(ai, "currency"),
    }


def parse_position(p: Any) -> dict:
    """Map a MetatraderPosition (object or dict) to a normalized dict.

    Includes `comment`/`magic` (used internally for attribution); callers that
    render to the dashboard drop those via `public_position`.
    """
    side_raw = str(_field(p, "type", "") or "").lower()
    side = "BUY" if "buy" in side_raw else "SELL"
    opened = _field(p, "time")
    if hasattr(opened, "isoformat"):
        opened_at: str | None = opened.isoformat()
    elif opened:
        opened_at = str(opened)
    else:
        opened_at = None
    return {
        "position_id": str(_field(p, "id", "") or ""),
        "symbol": str(_field(p, "symbol", "") or ""),
        "side": side,
        "lot": float(_field(p, "volume", 0.0) or 0.0),
        "open_price": float(_field(p, "openPrice", 0.0) or 0.0),
        "current_price": float(_field(p, "currentPrice", 0.0) or 0.0),
        "floating_pnl": float(_field(p, "unrealizedProfit", 0.0) or 0.0),
        "opened_at": opened_at,
        "comment": _field(p, "comment"),
        "magic": int(_field(p, "magic", 0) or 0),
    }


_PUBLIC_FIELDS = (
    "position_id",
    "symbol",
    "side",
    "lot",
    "open_price",
    "current_price",
    "floating_pnl",
    "opened_at",
)


def public_position(p: dict) -> dict:
    """Strip internal-only fields (comment/magic) before returning to clients."""
    return {k: p[k] for k in _PUBLIC_FIELDS}


def is_product_position(
    symbol: str, comment: str | None, product_symbols: Iterable[str]
) -> bool:
    """True if a position belongs to a product: its symbol is in the product's
    configured set AND its comment is a strategy tag (not a bare "AURUM_AI"
    manual order)."""
    if symbol not in set(product_symbols):
        return False
    c = (comment or "").strip()
    return c.startswith(_AURUM_PREFIX) and c != _AURUM_PREFIX


def normalize_symbol(symbol: str) -> str:
    """Strip the broker suffix so per-symbol stats roll up cleanly.

    Brokers expose suffixed variants ("US500.v", "XAUUSD.m"); the part before
    the first dot is the standard symbol the dashboard groups by.
    """
    s = (symbol or "").strip()
    return s.split(".", 1)[0] if "." in s else s


def parse_setup(comment: str | None) -> str | None:
    """Extract the setup name from a strategy comment ("AURUM_AI order_block"
    → "order_block"). A bare "AURUM_AI" (manual order) or non-tag returns None.
    """
    c = (comment or "").strip()
    if not c.startswith(_AURUM_PREFIX):
        return None
    setup = c[len(_AURUM_PREFIX):].strip()
    return setup or None


@dataclass
class MasterSnapshot:
    account: dict | None
    positions: list[dict]
    fetched_at: float


class AccountSnapshotCache:
    """Caches the master account info + open positions for a short TTL."""

    def __init__(
        self,
        conn_provider: Callable[[], Awaitable[Any]],
        ttl_seconds: float = 8.0,
        time_fn: Callable[[], float] = time.monotonic,
        on_account_info: Callable[[dict], Awaitable[bool]] | None = None,
    ) -> None:
        self._get_conn = conn_provider
        self._ttl = ttl_seconds
        self._now = time_fn
        self._cache: MasterSnapshot | None = None
        # Optional one-shot hook fired with the parsed account on the first
        # successful account-info fetch (used to auto-fill master_accounts.currency
        # from MetaApi). Returning True marks it done so it never fires again.
        self._on_account_info = on_account_info
        self._account_hook_done = False

    async def get(self, force_refresh: bool = False) -> MasterSnapshot:
        if (
            not force_refresh
            and self._cache is not None
            and (self._now() - self._cache.fetched_at) < self._ttl
        ):
            return self._cache

        account: dict | None = None
        positions: list[dict] = []
        try:
            conn = await self._get_conn()
        except Exception as e:  # noqa: BLE001
            logger.warning("master snapshot: RPC connection unavailable: {}", e)
            snap = MasterSnapshot(None, [], self._now())
            self._cache = snap
            return snap

        try:
            ai = await conn.get_account_information()
            account = parse_account(ai)
        except Exception as e:  # noqa: BLE001
            logger.warning("master snapshot: account info fetch failed: {}", e)

        if (
            account is not None
            and self._on_account_info is not None
            and not self._account_hook_done
        ):
            try:
                if await self._on_account_info(account):
                    self._account_hook_done = True
            except Exception as e:  # noqa: BLE001
                logger.warning("master snapshot: account-info hook failed: {}", e)

        try:
            raw = await conn.get_positions()
            positions = [parse_position(p) for p in (raw or [])]
        except Exception as e:  # noqa: BLE001
            logger.warning("master snapshot: positions fetch failed: {}", e)
            positions = []

        snap = MasterSnapshot(account=account, positions=positions, fetched_at=self._now())
        self._cache = snap
        return snap
