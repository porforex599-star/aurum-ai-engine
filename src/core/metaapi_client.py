from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger
from metaapi_cloud_sdk import MetaApi
from metaapi_cloud_sdk.clients.error_handler import (
    ApiException,
    UnauthorizedException,
    ValidationException,
)

from src.config import get_settings

# Hard ceiling for deploy() + wait_connected() during provisioning.
PROVISION_TIMEOUT_SECONDS = 60.0


class ProvisioningError(Exception):
    """Raised when MetaApi provisioning fails.

    Carries the HTTP status and a stable machine-readable ``code`` so the API
    layer can surface a clear error without re-classifying the SDK exception.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProvisionedAccount:
    metaapi_account_id: str
    metaapi_region: str | None
    currency: str | None


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

    async def provision_account(
        self,
        *,
        login: str,
        password: str,
        server: str,
        name: str | None = None,
        timeout: float = PROVISION_TIMEOUT_SECONDS,
    ) -> ProvisionedAccount:
        """Create, deploy and connect a MetaApi MT5 account, then return its ids.

        The ``password`` is only ever forwarded to the MetaApi SDK — it is never
        logged, stored, or echoed back. Translates SDK failures into
        :class:`ProvisioningError` with an appropriate HTTP status.
        """
        settings = get_settings()
        if self._api is None:
            self._api = MetaApi(settings.METAAPI_TOKEN)
        api = self._api

        # NOTE: never include `password` in any log statement below.
        logger.info("Provisioning MetaApi account for login {} on server {}", login, server)

        account_dto: dict[str, Any] = {
            "name": name or f"Master {login}",
            "type": "cloud-g2",
            "login": str(login),
            "password": password,
            "server": server,
            "platform": "mt5",
            "application": "MetaApi",
            "magic": 0,
        }

        try:
            account = await api.metatrader_account_api.create_account(account_dto)
        except UnauthorizedException as exc:
            raise ProvisioningError(401, "invalid_credentials", "MetaApi rejected the credentials") from exc
        except ValidationException as exc:
            raise ProvisioningError(400, "invalid_server", "MetaApi rejected the server name") from exc
        except ApiException as exc:
            raise ProvisioningError(502, "metaapi_unreachable", "MetaApi API error while creating account") from exc
        except (asyncio.TimeoutError, OSError, ConnectionError) as exc:
            raise ProvisioningError(502, "metaapi_unreachable", "Could not reach MetaApi") from exc

        async def _deploy_and_connect() -> None:
            if account.state not in ("DEPLOYED", "DEPLOYING"):
                await account.deploy()
            await account.wait_connected()

        try:
            await asyncio.wait_for(_deploy_and_connect(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ProvisioningError(504, "provisioning_timeout", "MetaApi account did not connect within 60s") from exc
        except UnauthorizedException as exc:
            raise ProvisioningError(401, "invalid_credentials", "MetaApi rejected the credentials") from exc
        except ValidationException as exc:
            raise ProvisioningError(400, "invalid_server", "MetaApi rejected the server name") from exc
        except ApiException as exc:
            raise ProvisioningError(502, "metaapi_unreachable", "MetaApi API error while deploying account") from exc
        except (OSError, ConnectionError) as exc:
            raise ProvisioningError(502, "metaapi_unreachable", "Could not reach MetaApi") from exc

        currency = getattr(account, "base_currency", None) or getattr(account, "currency", None)
        provisioned = ProvisionedAccount(
            metaapi_account_id=account.id,
            metaapi_region=getattr(account, "region", None),
            currency=currency,
        )
        logger.info(
            "Provisioned MetaApi account {} (region={}, currency={})",
            provisioned.metaapi_account_id,
            provisioned.metaapi_region,
            provisioned.currency,
        )
        return provisioned

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
