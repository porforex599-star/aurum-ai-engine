"""Phase 7 Stage 1 — master-account registry endpoints (read + admin writes).

All endpoints reuse the Phase 6 `X-Admin-Key` gate (`admin._verify_admin_key`):
503 if ADMIN_KEY isn't configured, 401 on mismatch.

Stage 1 scope: these endpoints operate purely against the `master_accounts`
table. The engine is NOT yet wired to use them — it keeps trading off the single
env-var master with the documented gold_ai fallback. Engine refactor = Stage 2.

Endpoints:
  GET    /masters                 — list all masters + assignment + status
  POST   /masters                 — register a new (standby) master
  POST   /masters/{id}/assign     — assign a product (demotes prior holder)
  POST   /masters/{id}/unassign   — clear assignment → standby
  DELETE /masters/{id}            — only when standby; else 409
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.api.admin import _verify_admin_key
from src.core.master_account_service import (
    PRODUCTS,
    MasterAccountError,
    MasterAccountService,
)
from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/masters", tags=["masters"])


def _service(rt: AppRuntime) -> MasterAccountService:
    return rt.master_accounts


class RegisterMasterBody(BaseModel):
    login: str = Field(..., min_length=1)
    broker: str = Field(..., min_length=1)
    server: str = Field(..., min_length=1)
    currency: str = Field(..., min_length=1)
    metaapi_account_id: str = Field(..., min_length=1)
    metaapi_region: str = Field(default="eu-west", min_length=1)
    notes: str | None = None


class AssignBody(BaseModel):
    product: str


def _raise_from(err: MasterAccountError) -> None:
    raise HTTPException(status_code=err.status_code, detail=err.message)


@router.get("")
async def list_masters(
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """List every registered master with its current product + status."""
    masters = await _service(rt).list_masters()
    return {"count": len(masters), "masters": masters}


@router.post("")
async def register_master(
    body: RegisterMasterBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Register a new master account. Starts in `standby` (unassigned)."""
    try:
        master = await _service(rt).register_master(
            login=body.login,
            broker=body.broker,
            server=body.server,
            currency=body.currency,
            metaapi_account_id=body.metaapi_account_id,
            metaapi_region=body.metaapi_region,
            notes=body.notes,
        )
    except MasterAccountError as err:
        _raise_from(err)
    logger.info("master registered: login={} id={}", body.login, master.get("id"))
    return master


@router.post("/{master_id}/assign")
async def assign_master(
    master_id: str,
    body: AssignBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Assign a product to this master, demoting any prior holder to standby."""
    if body.product not in PRODUCTS:
        raise HTTPException(
            status_code=400, detail=f"unknown product: {body.product!r}"
        )
    try:
        master = await _service(rt).assign(master_id, body.product)
    except MasterAccountError as err:
        _raise_from(err)
    logger.info("master {} assigned to {}", master_id, body.product)
    return master


@router.post("/{master_id}/unassign")
async def unassign_master(
    master_id: str,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Clear this master's product assignment → standby."""
    try:
        master = await _service(rt).unassign(master_id)
    except MasterAccountError as err:
        _raise_from(err)
    logger.info("master {} unassigned", master_id)
    return master


@router.delete("/{master_id}")
async def delete_master(
    master_id: str,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Delete a master — only when it is in `standby`; otherwise 409."""
    try:
        await _service(rt).delete_master(master_id)
    except MasterAccountError as err:
        _raise_from(err)
    logger.info("master {} deleted", master_id)
    return {"deleted": True, "id": master_id}
