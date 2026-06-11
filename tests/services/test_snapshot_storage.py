from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test")
os.environ.setdefault("SUPABASE_CUSTOMERS_URL", "https://customers.example.supabase.co")
os.environ.setdefault("SUPABASE_CUSTOMERS_SERVICE_ROLE_KEY", "test-customers")
os.environ.setdefault("METAAPI_TOKEN", "test")
os.environ.setdefault("METAAPI_MASTER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("APP_ENV", "development")

from src.services.snapshot_storage import BUCKET, upload_snapshot  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake"
PROJECT_URL = "https://etwlurpjrqlvrxgsbhkd.supabase.co"


class _FakeStore:
    """Mimics the subset of SupabaseClient that upload_snapshot touches."""

    def __init__(self, *, raise_on_upload: bool = False) -> None:
        self.raise_on_upload = raise_on_upload
        self.uploads: list[dict] = []

    async def upload_to_storage(
        self, bucket, path, data, *, content_type, upsert=True
    ) -> None:
        if self.raise_on_upload:
            raise RuntimeError("storage exploded")
        self.uploads.append(
            {
                "bucket": bucket,
                "path": path,
                "data": data,
                "content_type": content_type,
                "upsert": upsert,
            }
        )

    def storage_public_url(self, bucket, path) -> str:
        return f"{PROJECT_URL}/storage/v1/object/public/{bucket}/{path}"


async def test_upload_returns_public_url():
    store = _FakeStore()
    url = await upload_snapshot(store, "post-123", PNG)

    assert url == (
        f"{PROJECT_URL}/storage/v1/object/public/{BUCKET}/post-123.png"
    )
    assert len(store.uploads) == 1
    up = store.uploads[0]
    assert up["bucket"] == BUCKET
    assert up["path"] == "post-123.png"
    assert up["data"] == PNG
    assert up["content_type"] == "image/png"
    assert up["upsert"] is True


async def test_upload_returns_none_on_error():
    store = _FakeStore(raise_on_upload=True)
    url = await upload_snapshot(store, "post-123", PNG)
    assert url is None
