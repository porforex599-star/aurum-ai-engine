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


async def upload_snapshot(
    store: SupabaseClient,
    post_id: str,
    png_bytes: bytes,
) -> str | None:
    """Upload ``{post_id}.png`` → return its public URL. ``None`` on failure.

    Uses upsert so retries overwrite any partial/previous object for the post.
    """
    file_path = f"{post_id}.png"
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
            "snapshot_storage.upload failed: post_id={} exc={}: {}",
            post_id,
            type(exc).__name__,
            str(exc)[:200],
        )
        return None
