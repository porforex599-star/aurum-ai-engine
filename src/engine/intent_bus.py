from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from loguru import logger


@dataclass(frozen=True)
class IntentLogEntry:
    timestamp: datetime
    product: str
    kind: str
    payload: dict
    dry_run: bool


class IntentNotifier(Protocol):
    """Anything that wants to react to published intents (e.g. TelegramNotifier).

    `notify` is awaitable and MUST never raise — implementations swallow their
    own errors. `should_send` lets the bus short-circuit before scheduling.
    """

    def should_send(self, entry: IntentLogEntry) -> bool: ...

    async def notify(self, entry: IntentLogEntry) -> Any: ...


class IntentBus:
    def __init__(
        self,
        buffer_size: int = 100,
        notifier: IntentNotifier | None = None,
    ) -> None:
        self._buffer: deque[IntentLogEntry] = deque(maxlen=buffer_size)
        self._notifier = notifier

    def publish(
        self,
        product: str,
        kind: str,
        payload: dict,
        dry_run: bool,
        ts: datetime | None = None,
    ) -> None:
        entry = IntentLogEntry(
            timestamp=ts or datetime.now(timezone.utc),
            product=product,
            kind=kind,
            payload=payload,
            dry_run=dry_run,
        )
        self._buffer.append(entry)
        logger.info(
            "[INTENT] product={} kind={} dry_run={} payload={}",
            product,
            kind,
            dry_run,
            payload,
        )
        self._dispatch_notifier(entry)

    def _dispatch_notifier(self, entry: IntentLogEntry) -> None:
        """Fire-and-forget the notifier inside the running event loop.

        Sync test contexts (no running loop) silently skip — backward-compatible
        with all the existing intent_bus tests. Production runs inside the
        scheduler's event loop, so the create_task path is the normal one.
        """
        notifier = self._notifier
        if notifier is None:
            return
        try:
            if not notifier.should_send(entry):
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("notifier should_send raised: {}", exc)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — typical in unit tests; skip silently.
            return
        try:
            loop.create_task(self._safe_notify(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("notifier task schedule failed: {}", exc)

    async def _safe_notify(self, entry: IntentLogEntry) -> None:
        try:
            await self._notifier.notify(entry)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("notifier raised (swallowed): {}", exc)

    def recent(self, n: int = 20) -> list[IntentLogEntry]:
        items = list(self._buffer)
        return items[-n:][::-1]

    def clear(self) -> None:
        self._buffer.clear()


def serialize_intent(intent: Any) -> dict:
    """Convert TradeIntent/CloseIntent/ModifySLIntent to a JSON-serializable dict."""
    if intent is None:
        return {}
    d = asdict(intent)
    return _coerce(d)


def _coerce(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value
