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
    """Connects to a single MetaApi account.

    Phase 7 Stage 2: `account_id` is optional — when omitted the client connects
    to `METAAPI_MASTER_ACCOUNT_ID` (the original single-master behavior). Passing
    an explicit id lets the `MetaApiClientPool` manage one client per master so
    each product can trade on its own MT5 account.
    """

    def __init__(self, account_id: str | None = None) -> None:
        self._account_id = account_id
        self._api: MetaApi | None = None
        self._account: Any | None = None
        self._connected: bool = False

    @property
    def account_id(self) -> str:
        return self._account_id or get_settings().METAAPI_MASTER_ACCOUNT_ID

    async def connect(self) -> None:
        # Idempotent: a pooled client may be connect()-ed again on reuse.
        if self._connected and self._account is not None:
            return

        settings = get_settings()
        account_id = self._account_id or settings.METAAPI_MASTER_ACCOUNT_ID
        logger.info("Connecting to MetaApi master account {}", account_id)

        try:
            self._api = MetaApi(settings.METAAPI_TOKEN)
            account = await self._api.metatrader_account_api.get_account(account_id)

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


class MetaApiClientPool:
    """Lazily creates and caches one `MetaApiClient` per metaapi_account_id.

    Phase 7 Stage 2 factory: products assigned to different masters each get
    their own connected client; products sharing a master share one client.
    """

    def __init__(self) -> None:
        self._clients: dict[str, MetaApiClient] = {}

    def get_or_create(self, account_id: str) -> MetaApiClient:
        client = self._clients.get(account_id)
        if client is None:
            client = MetaApiClient(account_id=account_id)
            self._clients[account_id] = client
        return client

    def get(self, account_id: str) -> MetaApiClient | None:
        return self._clients.get(account_id)

    def all(self) -> list[MetaApiClient]:
        return list(self._clients.values())


_client: MetaApiClient | None = None


def get_metaapi_client() -> MetaApiClient:
    """The default single-master client (env-var account). Kept as the engine's
    fallback so single-master deployments behave exactly as before."""
    global _client
    if _client is None:
        _client = MetaApiClient()
    return _client
