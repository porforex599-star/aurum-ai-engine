from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from loguru import logger
from pydantic import BaseModel

from src.engine.runtime import AppRuntime, get_runtime
from src.products.models import CloseIntent, IntentKind, TradeIntent
from src.strategy.models import SignalSide

router = APIRouter(prefix="/test/trade", tags=["test"])


def _verify_test_key(x_test_key: str | None = Header(default=None)) -> None:
    """Gate test-trade endpoints behind the TEST_TRADE_KEY env secret."""
    expected = os.environ.get("TEST_TRADE_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="TEST_TRADE_KEY not configured")
    if x_test_key != expected:
        raise HTTPException(status_code=401, detail="invalid X-Test-Key")


class OpenBody(BaseModel):
    symbol: str = "XAUUSD.v"
    side: str = "BUY"
    lot: float = 0.01
    sl_distance: float = 5.0
    tp_distance: float = 5.0


class CloseBody(BaseModel):
    position_id: str


def _price_field(price: Any, key: str) -> float:
    if isinstance(price, dict):
        return float(price.get(key) or 0.0)
    return float(getattr(price, key, 0.0) or 0.0)


@router.post("/open")
async def test_open(
    body: OpenBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_test_key),
) -> dict:
    side_str = body.side.strip().upper()
    if side_str not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    side = SignalSide.BUY if side_str == "BUY" else SignalSide.SELL

    try:
        conn = await rt.get_rpc_connection()
        price = await conn.get_symbol_price(symbol=body.symbol)
    except Exception as e:  # noqa: BLE001
        logger.exception("test_open: price fetch failed for {}: {}", body.symbol, e)
        return {
            "success": False,
            "symbol": body.symbol,
            "side": side.value,
            "lot": body.lot,
            "error": f"price_fetch_failed: {type(e).__name__}: {e}"[:300],
        }

    ask = _price_field(price, "ask")
    bid = _price_field(price, "bid")

    if side == SignalSide.BUY:
        entry = ask
        sl = entry - body.sl_distance
        tp = entry + body.tp_distance
    else:
        entry = bid
        sl = entry + body.sl_distance
        tp = entry - body.tp_distance

    intent = TradeIntent(
        kind=IntentKind.OPEN,
        symbol=body.symbol,
        side=side,
        lot=body.lot,
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
        reason="MANUAL_TEST",
        setup=None,
        confidence=1.0,
    )

    # BYPASSES dry_run gate — execute_open always sends the order.
    result = await rt.order_executor.execute_open(intent)

    if result is None:
        return {
            "success": False,
            "symbol": body.symbol,
            "side": side.value,
            "lot": body.lot,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "error": (rt.order_executor._last_error or {}).get("exc_msg", "unknown"),
        }

    return {
        "success": True,
        "order_id": result.get("order_id"),
        "position_id": result.get("position_id"),
        "symbol": body.symbol,
        "side": side.value,
        "lot": body.lot,
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


@router.post("/close")
async def test_close(
    body: CloseBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_test_key),
) -> dict:
    intent = CloseIntent(
        kind=IntentKind.CLOSE,
        position_id=body.position_id,
        reason="manual_test",
        code="manual_test",
    )
    ok = await rt.order_executor.execute_close(intent)
    out: dict = {"success": ok, "position_id": body.position_id}
    if not ok:
        out["error"] = (rt.order_executor._last_error or {}).get("exc_msg", "unknown")
    return out


@router.get("/positions")
async def test_positions(
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_test_key),
) -> dict:
    positions = await rt.position_poller.fetch_all()
    return {
        "count": len(positions),
        "positions": [
            {
                "position_id": p.position_id,
                "symbol": p.symbol,
                "side": p.side.value,
                "lot": p.lot,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "current_pnl_usd": p.current_pnl_usd,
                "current_sl": p.current_sl,
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            }
            for p in positions
        ],
    }
