from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from loguru import logger
from pydantic import BaseModel, Field, model_validator

from src.config import get_settings
from src.core.metaapi_client import ProvisioningError, get_metaapi_client
from src.core.supabase_client import get_supabase_client

router = APIRouter(prefix="/api", tags=["masters"])


def require_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    """Guard admin-only endpoints with the shared ADMIN_KEY secret."""
    expected = get_settings().ADMIN_KEY
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


class CreateMasterRequest(BaseModel):
    login: str = Field(..., min_length=1)
    broker: str = Field(..., min_length=1)
    server: str = Field(..., min_length=1)

    # Write-only: forwarded to the MetaApi SDK during provisioning and never
    # persisted, logged, or returned. `exclude=True` keeps it out of any dump.
    password: str | None = Field(default=None, exclude=True, repr=False)

    # Server-populated after provisioning. May still be supplied directly to
    # skip provisioning and adopt a pre-existing MetaApi account (migration
    # period back-compat).
    metaapi_account_id: str | None = None
    metaapi_region: str | None = None

    assigned_product: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _require_password_or_account_id(self) -> "CreateMasterRequest":
        if not self.metaapi_account_id and not self.password:
            raise ValueError("password is required when metaapi_account_id is absent")
        return self


@router.post("/masters", status_code=201, dependencies=[Depends(require_admin)])
async def create_master(payload: CreateMasterRequest) -> dict[str, Any]:
    supabase = get_supabase_client()
    client = supabase.get_client()
    if client is None:
        raise HTTPException(status_code=503, detail={"error": "supabase_unavailable"})

    metaapi_account_id = payload.metaapi_account_id
    metaapi_region = payload.metaapi_region
    currency: str | None = None

    if not metaapi_account_id:
        # No account id supplied -> provision one from MT5 credentials.
        metaapi = get_metaapi_client()
        try:
            provisioned = await metaapi.provision_account(
                login=payload.login,
                password=payload.password or "",
                server=payload.server,
            )
        except ProvisioningError as exc:
            logger.warning(
                "Provisioning failed for login {} ({}): {}",
                payload.login,
                exc.code,
                exc.message,
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail={"error": exc.code, "message": exc.message},
            ) from exc

        metaapi_account_id = provisioned.metaapi_account_id
        metaapi_region = provisioned.metaapi_region
        currency = provisioned.currency

    row: dict[str, Any] = {
        "login": payload.login,
        "broker": payload.broker,
        "server": payload.server,
        "metaapi_account_id": metaapi_account_id,
    }
    if metaapi_region is not None:
        row["metaapi_region"] = metaapi_region
    if currency is not None:
        row["currency"] = currency
    if payload.assigned_product is not None:
        row["assigned_product"] = payload.assigned_product
    if payload.notes is not None:
        row["notes"] = payload.notes

    def _insert() -> Any:
        return client.table("master_accounts").insert(row).execute()

    try:
        result = await asyncio.to_thread(_insert)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist master_accounts row for login {}: {}", payload.login, exc)
        raise HTTPException(status_code=500, detail={"error": "persist_failed"}) from exc

    data = getattr(result, "data", None) or []
    return data[0] if data else {}
