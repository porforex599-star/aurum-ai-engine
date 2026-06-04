"""Phase 7 Stage 2 — per-account engine component bundle.

An `AccountBundle` groups everything the tick loop needs to trade ONE MetaApi
master account. Bundles are deduped by `account_id` in `AppRuntime`, so two
products assigned to the same master share a single bundle — and therefore a
single `CloseDetector` state, exactly mirroring the original single-master
engine. When a product is assigned its own master, it gets its own bundle with
an independent RPC connection, snapshot fetcher, executor and close detector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.engine.close_detector import CloseDetector
from src.engine.master_account import AccountSnapshotCache
from src.engine.order_executor import OrderExecutor
from src.engine.position_poller import PositionPoller
from src.engine.snapshot_fetcher import SnapshotFetcher
from src.engine.symbol_spec_cache import SymbolSpecCache


@dataclass
class AccountBundle:
    account_id: str
    account: Any
    get_rpc_connection: Callable[[], Awaitable[Any]]
    symbol_spec_cache: SymbolSpecCache
    snapshot_fetcher: SnapshotFetcher
    position_poller: PositionPoller
    order_executor: OrderExecutor
    close_detector: CloseDetector
    account_snapshot: AccountSnapshotCache
