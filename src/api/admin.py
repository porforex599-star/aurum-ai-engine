"""Phase 6 — Admin endpoints for engine freeze/unfreeze.

All endpoints require the `X-Admin-Key` header to match the `ADMIN_KEY` env
var. If `ADMIN_KEY` isn't set, the endpoints return 503 — that's intentional;
the engine refuses to admit it has admin endpoints at all if no key is wired.

A freeze stops NEW open intents from being executed by the tick loop. Closes
and SL trails still run — that's by design so positions can wind down safely.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/admin", tags=["admin"])


def _verify_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ADMIN_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_KEY not configured")
    if x_admin_key != expected:
        raise HTTPException(status_code=401, detail="invalid X-Admin-Key")


class FreezeBody(BaseModel):
    reason: str | None = None
    by: str | None = None


def _state_to_dict(state) -> dict:  # type: ignore[no-untyped-def]
    return {
        "frozen": state.frozen,
        "reason": state.reason,
        "frozen_at": state.frozen_at.isoformat() if state.frozen_at else None,
        "frozen_by": state.frozen_by,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
        "cached": state.cached,
    }


@router.get("/freeze")
async def get_freeze(
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Return the current freeze state, forcing a fresh DB read."""
    state = await rt.freeze_manager.get_state(force_refresh=True)
    return _state_to_dict(state)


@router.post("/freeze")
async def post_freeze(
    body: FreezeBody,
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Freeze the engine — new opens skipped, closes still run."""
    state = await rt.freeze_manager.set_frozen(
        frozen=True, reason=body.reason, by=body.by
    )
    rt.intent_bus.publish(
        "freeze_manager",
        "frozen",
        {"reason": body.reason, "by": body.by},
        rt.settings.dry_run,
    )
    return _state_to_dict(state)


@router.post("/unfreeze")
async def post_unfreeze(
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Unfreeze — opens resume on the next tick."""
    state = await rt.freeze_manager.set_frozen(frozen=False)
    rt.intent_bus.publish(
        "freeze_manager",
        "unfrozen",
        {},
        rt.settings.dry_run,
    )
    return _state_to_dict(state)
