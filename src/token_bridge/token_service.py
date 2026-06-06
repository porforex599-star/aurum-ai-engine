from __future__ import annotations

from datetime import datetime

from loguru import logger

from src.token_bridge.models import AddTradeResult, TokenInfo, TokenState


class TokenService:
    """Thin async wrapper around aurum-customers tokens_* RPCs."""

    def __init__(self, supabase_client) -> None:
        self.sb = supabase_client

    async def activate_next(self, customer_id: str, product_code: str) -> str | None:
        try:
            resp = self.sb.rpc(
                "tokens_activate_next",
                {"p_customer_id": customer_id, "p_product_code": product_code},
            ).execute()
            return resp.data if resp.data else None
        except Exception as e:  # noqa: BLE001
            logger.exception("tokens_activate_next failed: {}", e)
            return None

    async def add_trade(
        self,
        customer_id: str,
        product_code: str,
        metaapi_position_id: str,
        symbol: str,
        pnl: float,
        opened_at: datetime,
        closed_at: datetime,
    ) -> AddTradeResult:
        try:
            resp = self.sb.rpc(
                "tokens_add_trade",
                {
                    "p_customer_id": customer_id,
                    "p_product_code": product_code,
                    "p_metaapi_position_id": metaapi_position_id,
                    "p_symbol": symbol,
                    "p_pnl": pnl,
                    "p_opened_at": opened_at.isoformat(),
                    "p_closed_at": closed_at.isoformat(),
                },
            ).execute()
            d = resp.data or {}
            return AddTradeResult(
                ok=bool(d.get("ok")),
                token_id=d.get("token_id"),
                net_pnl=d.get("net_pnl"),
                expired=bool(d.get("expired")),
                expiry_reason=d.get("expiry_reason"),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("tokens_add_trade failed: {}", e)
            return AddTradeResult(
                ok=False,
                token_id=None,
                net_pnl=None,
                expired=False,
                expiry_reason=None,
                error=str(e),
            )

    async def friday_close(self) -> int:
        try:
            resp = self.sb.rpc("tokens_friday_close", {}).execute()
            return int(resp.data or 0)
        except Exception as e:  # noqa: BLE001
            logger.exception("tokens_friday_close failed: {}", e)
            return 0

    async def get_active_token(
        self, customer_id: str, product_code: str
    ) -> TokenInfo | None:
        try:
            resp = (
                self.sb.table("tokens")
                .select("*")
                .eq("customer_id", customer_id)
                .eq("product_code", product_code)
                .eq("state", "active")
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            if not rows:
                return None
            r = rows[0]
            return TokenInfo(
                id=r["id"],
                customer_id=r["customer_id"],
                product_code=r["product_code"],
                subscription_id=r["subscription_id"],
                token_index=r["token_index"],
                state=TokenState(r["state"]),
                net_pnl_usd=float(r.get("net_pnl", 0)),
                target_win=float(r.get("target_win", 95)),
                target_loss=float(r.get("target_loss", 70)),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("get_active_token failed: {}", e)
            return None
