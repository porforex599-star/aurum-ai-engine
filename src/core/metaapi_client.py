from __future__ import annotations

import asyncio
import traceback
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from loguru import logger
from metaapi_cloud_sdk import MetaApi

from src.config import get_settings

try:
    _SDK_VERSION = version("metaapi-cloud-sdk")
except PackageNotFoundError:
    _SDK_VERSION = "unknown"

logger.info("metaapi-cloud-sdk version: {}", _SDK_VERSION)


class MetaApiClient:
    def __init__(self) -> None:
        self._api: MetaApi | None = None
        self._account: Any | None = None
        self._connected: bool = False

    async def connect(self) -> None:
        settings = get_settings()
        logger.info(
            "Connecting to MetaApi master account {}", settings.METAAPI_MASTER_ACCOUNT_ID
        )

        try:
            self._api = MetaApi(settings.METAAPI_TOKEN)
            account = await self._api.metatrader_account_api.get_account(
                settings.METAAPI_MASTER_ACCOUNT_ID
            )

            region = getattr(account, "region", "?")
            state = getattr(account, "state", "?")
            connection_status = getattr(account, "connection_status", "?")
            logger.info(
                "MetaApi account fetched: region={}, state={}, connection_status={}",
                region,
                state,
                connection_status,
            )

            if state in ("UNDEPLOYED", "DRAFT"):
                logger.info("Account state is {}; calling account.deploy()", state)
                await account.deploy()
                logger.info("account.deploy() returned; awaiting wait_connected(timeout=60s)")

            try:
                await asyncio.wait_for(account.wait_connected(), timeout=60)
                logger.info("account.wait_connected() succeeded")
            except asyncio.TimeoutError:
                logger.error("account.wait_connected() timed out after 60s")
                raise

            self._account = account
            self._connected = True
            logger.info(
                "MetaApi master account connected (state={}, connection_status={})",
                getattr(account, "state", "?"),
                getattr(account, "connection_status", "?"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("MetaApi connect failed: {}", exc)
            logger.error("Full traceback:\n{}", traceback.format_exc())
            self._connected = False
        finally:
            logger.info("MetaApi connect() finished: self._connected={}", self._connected)

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
        if not self._connected or self._account is None:
            return False
        connection_status = getattr(self._account, "connection_status", None)
        if connection_status is not None and connection_status != "CONNECTED":
            return False
        return True

    def get_account(self) -> Any:
        return self._account


_client: MetaApiClient | None = None


def get_metaapi_client() -> MetaApiClient:
    global _client
    if _client is None:
        _client = MetaApiClient()
    return _client
