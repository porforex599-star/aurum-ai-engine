from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.config import Settings
from src.engine.intent_bus import IntentBus
from src.engine.position_poller import PositionPoller
from src.engine.snapshot_fetcher import SnapshotFetcher
from src.products.gold_ai import GoldAIProduct
from src.products.multi_cfd_ai import MultiCfdAIProduct
from src.products.position_manager import PositionManager
from src.token_bridge.token_service import TokenService


class AppRuntime:
    def __init__(
        self,
        settings: Settings,
        account: Any,
        connection: Any,
        supabase_client: Any,
    ) -> None:
        self.settings = settings
        self.account = account
        self.connection = connection
        self.supabase = supabase_client
        self.intent_bus = IntentBus(buffer_size=settings.intent_buffer_size)
        self.snapshot_fetcher = SnapshotFetcher(account=account, connection=connection)
        self.position_poller = PositionPoller(connection)
        self.position_manager = PositionManager()

        sb_raw = (
            supabase_client.get_client()
            if hasattr(supabase_client, "get_client")
            else supabase_client
        )
        self.token_service = TokenService(sb_raw)

        self.products: dict[str, Any] = {}
        self.last_tick: datetime | None = None
        self.last_tick_status: str | None = None
        self._init_products()

    def _init_products(self) -> None:
        if self.settings.enable_gold_ai:
            self.products["gold_ai"] = GoldAIProduct(
                customer_id=self.settings.primary_customer_id,
                week_cycle_id=str(uuid.uuid4()),
            )
        if self.settings.enable_multi_cfd_ai:
            self.products["multi_cfd_ai"] = MultiCfdAIProduct(
                customer_id=self.settings.primary_customer_id,
                week_cycle_id=str(uuid.uuid4()),
            )


_runtime: AppRuntime | None = None


def get_runtime() -> AppRuntime:
    if _runtime is None:
        raise RuntimeError("AppRuntime not initialized")
    return _runtime


def set_runtime(rt: AppRuntime | None) -> None:
    global _runtime
    _runtime = rt
