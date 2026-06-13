"""Upload chart-img PNG bytes → Supabase Storage ``analysis-snapshots`` bucket.

The bucket lives in the customer-facing Supabase project (same project as the
``analysis_posts`` table), so the upload reuses that project's service-role
``SupabaseClient``. Never raises — a failed upload returns ``None`` so the
webhook's Realtime broadcast and Telegram notification are unaffected.
"""

from __future__ import annotations

from loguru import logger

from src.core.supabase_client import SupabaseClient

BUCKET = "analysis-snapshots"


async def upload_snapshot_to_path(
    store: SupabaseClient,
    file_path: str,
    png_bytes: bytes,
) -> str | None:
    """Upload ``png_bytes`` to ``file_path`` in the bucket → public URL.

    ``None`` on failure. Uses upsert so retries overwrite any partial/previous
    object at the same path.
    """
    try:
        await store.upload_to_storage(
            BUCKET,
            file_path,
            png_bytes,
            content_type="image/png",
            upsert=True,
        )
        return store.storage_public_url(BUCKET, file_path)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "snapshot_storage.upload failed: path={} exc={}: {}",
            file_path,
            type(exc).__name__,
            str(exc)[:200],
        )
        return None


async def upload_snapshot(
    store: SupabaseClient,
    post_id: str,
    png_bytes: bytes,
) -> str | None:
    """Upload ``{post_id}.png`` → return its public URL. ``None`` on failure.

    Uses upsert so retries overwrite any partial/previous object for the post.
    """
    return await upload_snapshot_to_path(store, f"{post_id}.png", png_bytes)
