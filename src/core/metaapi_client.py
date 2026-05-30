from __future__ import annotations

from typing import Any

from loguru import logger
from metaapi_cloud_sdk import MetaApi

from src.config import get_settings


class MetaApiClient:
    def __init__(self) -> None:
        self._api: MetaApi | None = None
        self._account: Any | None = None
        self._connected: bool = False

    async def connect(self) -> None:
        settings = get_settings()
        logger.info("Connecting to MetaApi master account {}", settings.METAAPI_MASTER_ACCOUNT_ID)

        self._api = MetaApi(settings.METAAPI_TOKEN)
        account = await self._api.metatrader_account_api.get_account(
            settings.METAAPI_MASTER_ACCOUNT_ID
        )

        if account.state not in ("DEPLOYED", "DEPLOYING"):
            await account.deploy()
        await account.wait_connected()

        self._account = account
        self._connected = True
        logger.info("MetaApi master account connected (state={})", account.state)

    async def shutdown(self) -> None:
        if self._account is not None:
            try:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("MetaApi shutdown error: {}", exc)
        self._connected = False
        self._account = None
        self._api = None

    def is_connected(self) -> bool:
        return self._connected

    def get_account(self) -> Any:
        return self._account


_client: MetaApiClient | None = None


def get_metaapi_client() -> MetaApiClient:
    global _client
    if _client is None:
        _client = MetaApiClient()
    return _client
