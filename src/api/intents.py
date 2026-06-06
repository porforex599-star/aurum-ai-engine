from __future__ import annotations

from fastapi import APIRouter, Depends

from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(prefix="/intents", tags=["intents"])


@router.get("/recent")
def get_recent_intents(n: int = 20, rt: AppRuntime = Depends(get_runtime)) -> dict:
    return {
        "intents": [
            {
                "timestamp": e.timestamp.isoformat(),
                "product": e.product,
                "kind": e.kind,
                "payload": e.payload,
                "dry_run": e.dry_run,
            }
            for e in rt.intent_bus.recent(n)
        ]
    }
