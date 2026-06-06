"""Phase 2.6.3 — per-(product, symbol) open guard + signal cooldown.

Background
----------
Both products already dedup inside `evaluate()` via the open-positions list
(`if symbol in open_symbols: continue`). That list comes from
`PositionPoller.fetch_all()`, which reads the *streaming* connection's
`terminal_state.positions`. Orders, however, are placed through a *separate*
RPC connection, and the streaming terminal state can trail an RPC placement by
several ticks. During that window the dedup sees zero open positions for the
symbol and fires again — which is exactly how a single NAS100 setup turned into
8 stacked BUY orders on a ~$238 account.

`SignalLock` closes that gap with an in-memory guard that is set the instant an
open is executed — independent of the broker position feed. A locked
(product, symbol) pair is skipped until BOTH conditions hold:

  * the position we opened is known to have closed (released), AND
  * the cooldown window since the open has elapsed.

This gives us two protections at once:
  * position lock  — never more than one engine-opened position per
                     (product, symbol) at a time, even while the feed lags.
  * signal cooldown — after any open, suppress re-opens for that pair for at
                     least `cooldown_seconds` (default 300s).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Skip reasons surfaced to the intent bus / Telegram.
REASON_POSITION_OPEN = "position_already_open"
REASON_COOLDOWN = "cooldown_active"


@dataclass
class _LockEntry:
    opened_at: datetime
    position_id: Optional[str]
    released: bool = False


class SignalLock:
    def __init__(self, cooldown_seconds: float = 300.0) -> None:
        self._cooldown = max(0.0, float(cooldown_seconds))
        self._locks: dict[tuple[str, str], _LockEntry] = {}

    def status(self, product: str, symbol: str, now: datetime) -> Optional[str]:
        """Return a skip reason if (product, symbol) is locked, else None.

        Self-cleans: once a released lock's cooldown has elapsed the entry is
        dropped so the map can't grow without bound.
        """
        key = (product, symbol)
        entry = self._locks.get(key)
        if entry is None:
            return None
        if not entry.released:
            return REASON_POSITION_OPEN
        if (now - entry.opened_at).total_seconds() < self._cooldown:
            return REASON_COOLDOWN
        del self._locks[key]
        return None

    def is_locked(self, product: str, symbol: str, now: datetime) -> bool:
        return self.status(product, symbol, now) is not None

    def existing_position_id(self, product: str, symbol: str) -> Optional[str]:
        entry = self._locks.get((product, symbol))
        return entry.position_id if entry else None

    def record_open(
        self,
        product: str,
        symbol: str,
        now: datetime,
        position_id: Optional[str] = None,
    ) -> None:
        """Lock (product, symbol) immediately after an open is executed."""
        self._locks[(product, symbol)] = _LockEntry(
            opened_at=now, position_id=position_id
        )

    def release(self, product: str, symbol: str) -> None:
        """Mark the position closed. Cooldown (if any) still applies."""
        entry = self._locks.get((product, symbol))
        if entry is not None:
            entry.released = True
