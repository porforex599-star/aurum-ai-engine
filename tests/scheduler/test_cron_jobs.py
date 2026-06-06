from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.engine.intent_bus import IntentBus
from src.scheduler.cron_jobs import run_friday_close


def _runtime(friday_close_impl):
    return SimpleNamespace(
        settings=SimpleNamespace(dry_run=True),
        intent_bus=IntentBus(buffer_size=10),
        token_service=SimpleNamespace(friday_close=friday_close_impl),
    )


@pytest.mark.asyncio
async def test_friday_close_calls_token_service_and_publishes() -> None:
    rt = _runtime(AsyncMock(return_value=4))
    await run_friday_close(rt)
    items = rt.intent_bus.recent(5)
    assert len(items) == 1
    assert items[0].kind == "friday_close"
    assert items[0].payload["expired_count"] == 4


@pytest.mark.asyncio
async def test_friday_close_swallows_exception() -> None:
    rt = _runtime(AsyncMock(side_effect=RuntimeError("db")))
    # Should not raise
    await run_friday_close(rt)
    # No intent published when exception thrown
    assert rt.intent_bus.recent(5) == []
