from fastapi import APIRouter

from src import __version__
from src.core.metaapi_client import get_metaapi_client
from src.core.supabase_client import get_supabase_client

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    metaapi = get_metaapi_client()
    supabase = get_supabase_client()

    metaapi_connected = metaapi.is_connected()
    supabase_connected = await supabase.ping() if supabase.get_client() is not None else False

    status = "ok" if metaapi_connected and supabase_connected else "degraded"

    return {
        "status": status,
        "metaapi_connected": metaapi_connected,
        "supabase_connected": supabase_connected,
        "version": __version__,
    }
