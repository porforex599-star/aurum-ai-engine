"""CopyFactory client stub — wired in Phase 3."""

from __future__ import annotations

from loguru import logger


class CopyFactoryClient:
    def __init__(self) -> None:
        self._ready: bool = False

    async def connect(self) -> None:
        logger.debug("CopyFactoryClient.connect() — stub, no-op until Phase 3")

    async def shutdown(self) -> None:
        logger.debug("CopyFactoryClient.shutdown() — stub, no-op until Phase 3")

    def is_ready(self) -> bool:
        return self._ready


_client: CopyFactoryClient | None = None


def get_copyfactory_client() -> CopyFactoryClient:
    global _client
    if _client is None:
        _client = CopyFactoryClient()
    return _client
