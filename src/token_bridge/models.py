from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TokenState(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED_WIN = "expired_win"
    EXPIRED_LOSS = "expired_loss"


@dataclass(frozen=True)
class TokenInfo:
    id: str
    customer_id: str
    product_code: str
    subscription_id: str
    token_index: int
    state: TokenState
    net_pnl_usd: float
    target_win: float
    target_loss: float


@dataclass(frozen=True)
class AddTradeResult:
    ok: bool
    token_id: str | None
    net_pnl: float | None
    expired: bool
    expiry_reason: str | None
    error: str | None = None
