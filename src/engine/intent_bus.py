from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class IntentLogEntry:
    timestamp: datetime
    product: str
    kind: str
    payload: dict
    dry_run: bool


class IntentBus:
    def __init__(self, buffer_size: int = 100) -> None:
        self._buffer: deque[IntentLogEntry] = deque(maxlen=buffer_size)

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
