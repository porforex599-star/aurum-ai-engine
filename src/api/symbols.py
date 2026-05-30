from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("")
async def list_symbols(rt: AppRuntime = Depends(get_runtime)) -> dict:
    try:
        conn = await rt.get_rpc_connection()
        symbols = await conn.get_symbols()
        return {"count": len(symbols), "symbols": sorted(symbols)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/search")
async def search_symbols(q: str, rt: AppRuntime = Depends(get_runtime)) -> dict:
    try:
        conn = await rt.get_rpc_connection()
        symbols = await conn.get_symbols()
        ql = q.lower()
        matches = [s for s in symbols if ql in s.lower()]
        return {"query": q, "count": len(matches), "matches": sorted(matches)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/spec/{symbol}")
async def get_spec(symbol: str, rt: AppRuntime = Depends(get_runtime)) -> dict:
    try:
        conn = await rt.get_rpc_connection()
        spec = await conn.get_symbol_specification(symbol)
        return spec if isinstance(spec, dict) else dict(spec)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
