from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from loguru import logger

from src.config import Settings
from src.core.master_account_service import MasterAccountService
from src.core.metaapi_client import MetaApiClientPool
from src.engine.account_bundle import AccountBundle
from src.engine.close_detector import CloseDetector
from src.engine.freeze_manager import FreezeManager
from src.engine.intent_bus import IntentBus
from src.engine.master_account import AccountSnapshotCache
from src.engine.order_executor import OrderExecutor
from src.engine.position_poller import PositionPoller
from src.engine.signal_lock import SignalLock
from src.engine.snapshot_fetcher import SnapshotFetcher
from src.engine.symbol_spec_cache import SymbolSpecCache
from src.core.trade_logger import TradeLogger
from src.notifier.telegram import TelegramNotifier
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
        self.notifier = TelegramNotifier(
            token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            enabled=settings.telegram_enabled,
        )
        self.intent_bus = IntentBus(
            buffer_size=settings.intent_buffer_size,
            notifier=self.notifier if self.notifier.enabled else None,
        )
        self.freeze_manager = FreezeManager(
            supabase_client=supabase_client,
            cache_ttl_seconds=settings.freeze_cache_ttl_seconds,
        )
        # Phase 7 Stage 1 — master-account registry (read + admin writes). The
        # engine is NOT yet wired to this; it stays on the env-var master.
        self.master_accounts = MasterAccountService(supabase_client)
        self.snapshot_fetcher = SnapshotFetcher(account=account, connection=connection)
        self.position_poller = PositionPoller(connection)
        self.position_manager = PositionManager()
        self.symbol_spec_cache = SymbolSpecCache(
            self.get_rpc_connection,
            ttl_seconds=settings.symbol_spec_cache_ttl_seconds,
        )
        self.order_executor = OrderExecutor(
            self.get_rpc_connection,
            spec_cache=self.symbol_spec_cache,
            safety_buffer_points=settings.stop_safety_buffer_points,
            min_padded_rr=settings.min_padded_rr,
        )
        self.close_detector = CloseDetector(self.get_rpc_connection)
        self.signal_lock = SignalLock(
            cooldown_seconds=settings.signal_cooldown_seconds
        )
        # Phase 6.4 — master account + positions snapshot for the admin
        # dashboard. Cached ~8s so /status polling (every ~10s) doesn't hammer
        # the RPC connection. TTL is a constant (no new env var).
        self.account_snapshot = AccountSnapshotCache(
            self.get_rpc_connection,
            ttl_seconds=8.0,
            on_account_info=self._make_currency_backfill(
                settings.METAAPI_MASTER_ACCOUNT_ID
            ),
        )

        sb_raw = (
            supabase_client.get_client()
            if hasattr(supabase_client, "get_client")
            else supabase_client
        )
        self.token_service = TokenService(sb_raw)
        # Phase 6.5 — closed-trade ledger feeding the dashboard stats endpoints.
        self.trade_logger = TradeLogger(sb_raw)

        self.products: dict[str, Any] = {}
        self.last_tick: datetime | None = None
        self.last_tick_status: str | None = None
        self._rpc_conn: Any = None

        # Phase 7 Stage 2 — multi-master routing. The flat components above are
        # the DEFAULT bundle (env-var master); additional masters get their own
        # bundle on demand. Routing reads master_accounts; missing rows fall back
        # to the default account, so a single-master deployment is unchanged.
        self._default_account_id: str = settings.METAAPI_MASTER_ACCOUNT_ID
        self._metaapi_pool = MetaApiClientPool()
        self._account_bundles: dict[str, AccountBundle] = {}
        self._product_account_ids: dict[str, str] = {}
        self._bundle_lock = asyncio.Lock()

        self._init_products()

    async def get_rpc_connection(self) -> Any:
        """Lazy-init the RPC connection used for symbol metadata + spec queries."""
        if self._rpc_conn is not None:
            return self._rpc_conn
        if self.account is None or not hasattr(self.account, "get_rpc_connection"):
            raise RuntimeError("MetatraderAccount not available for RPC connection")
        conn = self.account.get_rpc_connection()
        try:
            await conn.connect()
        except Exception:  # noqa: BLE001
            pass
        try:
            await conn.wait_synchronized(timeout_in_seconds=30)
        except Exception:  # noqa: BLE001
            pass
        self._rpc_conn = conn
        return conn

    # ------------------------------------------------------------------
    # Phase 7 Stage 2 — per-product master account routing
    # ------------------------------------------------------------------

    def _make_currency_backfill(self, account_id: str):
        """Build the AccountSnapshotCache hook that auto-fills a master's currency
        from MetaApi on first connect. The hook reads the currency MT5 reports in
        account info and asks the registry to set it on the matching row when that
        row's currency is still empty. Never raises; returns True once resolved so
        the cache stops firing it."""

        async def _hook(account: dict) -> bool:
            currency = (account or {}).get("currency")
            if not currency:
                return False  # MetaApi hasn't reported a currency yet — retry later
            svc = getattr(self, "master_accounts", None)
            if svc is None:
                return True  # no registry to write to; don't keep retrying
            try:
                return await svc.backfill_currency(account_id, currency)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "currency backfill hook failed for {}: {}", account_id, exc
                )
                return False

        return _hook

    def _make_rpc_provider(self, account: Any):
        """Build a lazy, per-account RPC connection provider (one connection per
        account, mirroring get_rpc_connection for the default account)."""
        holder: dict[str, Any] = {}

        async def _get() -> Any:
            if holder.get("conn") is not None:
                return holder["conn"]
            if account is None or not hasattr(account, "get_rpc_connection"):
                raise RuntimeError("MetatraderAccount not available for RPC connection")
            conn = account.get_rpc_connection()
            try:
                await conn.connect()
            except Exception:  # noqa: BLE001
                pass
            try:
                await conn.wait_synchronized(timeout_in_seconds=30)
            except Exception:  # noqa: BLE001
                pass
            holder["conn"] = conn
            return conn

        return _get

    def _default_bundle(self) -> AccountBundle:
        """A live view of the default (env-var) master's components.

        Built fresh each call so it reflects the current flat attributes — this
        is what keeps endpoints/tests that swap `rt.order_executor` /
        `rt.account_snapshot` working unchanged.
        """
        return AccountBundle(
            account_id=self._default_account_id,
            account=self.account,
            get_rpc_connection=self.get_rpc_connection,
            symbol_spec_cache=self.symbol_spec_cache,
            snapshot_fetcher=self.snapshot_fetcher,
            position_poller=self.position_poller,
            order_executor=self.order_executor,
            close_detector=self.close_detector,
            account_snapshot=self.account_snapshot,
        )

    async def provision_master_account(
        self, *, login: str, password: str, server: str
    ) -> Any:
        """Provision a brand-new MetaApi account for a master via the pool.

        Delegates to `MetaApiClientPool.provision_account`; returns a
        `ProvisionedAccount`. The password is forwarded to the SDK only — it is
        never logged, stored, or returned by this runtime.
        """
        return await self._metaapi_pool.provision_account(
            login=login, password=password, server=server
        )

    async def get_account_id_for_product(self, slug: str) -> str:
        """Resolve the metaapi_account_id serving `slug`.

        Reads `master_accounts` (with the Stage 1 gold_ai fallback baked into
        the service). Any miss / error → the default env-var master, so the
        engine never ends up without an account. Cached after first resolve.
        """
        cached = self._product_account_ids.get(slug)
        if cached:
            return cached

        account_id = self._default_account_id
        svc = getattr(self, "master_accounts", None)
        if svc is not None:
            try:
                master = await svc.get_master_for_product(slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning("master routing lookup failed for {}: {}", slug, exc)
                master = None
            if isinstance(master, dict):
                mid = master.get("metaapi_account_id")
                if isinstance(mid, str) and mid:
                    account_id = mid

        self._product_account_ids[slug] = account_id
        return account_id

    async def get_bundle_for_product(self, slug: str) -> AccountBundle:
        """Return the AccountBundle serving `slug`. Default account → the live
        default bundle; any other → a lazily built, cached, connected bundle."""
        account_id = await self.get_account_id_for_product(slug)
        if account_id == self._default_account_id:
            return self._default_bundle()
        async with self._bundle_lock:
            bundle = self._account_bundles.get(account_id)
            if bundle is None:
                bundle = await self._build_bundle(account_id)
                self._account_bundles[account_id] = bundle
            return bundle

    async def get_account_for_product(self, slug: str) -> Any:
        return (await self.get_bundle_for_product(slug)).account

    async def _build_bundle(self, account_id: str) -> AccountBundle:
        """Connect a non-default master and wire its own engine components."""
        logger.info("building engine bundle for master account {}", account_id)
        client = self._metaapi_pool.get_or_create(account_id)
        await client.connect()
        account = client.get_account()
        get_conn = self._make_rpc_provider(account)
        spec_cache = SymbolSpecCache(
            get_conn, ttl_seconds=self.settings.symbol_spec_cache_ttl_seconds
        )
        return AccountBundle(
            account_id=account_id,
            account=account,
            get_rpc_connection=get_conn,
            symbol_spec_cache=spec_cache,
            snapshot_fetcher=SnapshotFetcher(account=account, connection=None),
            position_poller=PositionPoller(None),
            order_executor=OrderExecutor(
                get_conn,
                spec_cache=spec_cache,
                safety_buffer_points=self.settings.stop_safety_buffer_points,
                min_padded_rr=self.settings.min_padded_rr,
            ),
            close_detector=CloseDetector(get_conn),
            account_snapshot=AccountSnapshotCache(
                get_conn,
                ttl_seconds=8.0,
                on_account_info=self._make_currency_backfill(account_id),
            ),
        )

    async def resolve_master_routing(self) -> None:
        """Resolve each product's master at startup and warm non-default bundles.
        Never raises — a routing/connection failure leaves that product on the
        default master via the documented fallback."""
        for slug in list(self.products.keys()):
            try:
                account_id = await self.get_account_id_for_product(slug)
                if account_id != self._default_account_id:
                    await self.get_bundle_for_product(slug)
                logger.info("master routing: {} -> account {}", slug, account_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("master routing resolve failed for {}: {}", slug, exc)

    def _init_products(self) -> None:
        if self.settings.enable_gold_ai:
            self.products["gold_ai"] = GoldAIProduct(
                customer_id=self.settings.primary_customer_id,
                week_cycle_id=str(uuid.uuid4()),
                symbol=self.settings.gold_ai_symbol,
            )
        if self.settings.enable_multi_cfd_ai:
            self.products["multi_cfd_ai"] = MultiCfdAIProduct(
                customer_id=self.settings.primary_customer_id,
                week_cycle_id=str(uuid.uuid4()),
                symbols=tuple(self.settings.multi_cfd_ai_symbols),
            )


_runtime: AppRuntime | None = None


def get_runtime() -> AppRuntime:
    if _runtime is None:
        raise RuntimeError("AppRuntime not initialized")
    return _runtime


def set_runtime(rt: AppRuntime | None) -> None:
    global _runtime
    _runtime = rt
