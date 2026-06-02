"""Phase 6 — tick_runner freeze gating.

When `freeze_manager.is_frozen()` returns True, the tick loop must skip any
TradeIntent (new opens) but continue to process CloseIntent / ModifySLIntent
so that existing positions can still wind down.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any


from src.engine.intent_bus import IntentBus
from src.products.models import CloseIntent, IntentKind, TradeIntent
from src.scheduler.tick_runner import _handle_eval_result
from src.strategy.models import SetupName, SignalSide


class _StubFreeze:
    def __init__(self, frozen: bool = False) -> None:
        self._frozen = frozen
        self.checks = 0

    async def is_frozen(self) -> bool:
        self.checks += 1
        return self._frozen


def _runtime(frozen: bool) -> Any:
    return SimpleNamespace(
        intent_bus=IntentBus(buffer_size=20),
        freeze_manager=_StubFreeze(frozen=frozen),
        order_executor=SimpleNamespace(),
        settings=SimpleNamespace(),
    )


def _trade() -> TradeIntent:
    return TradeIntent(
        kind=IntentKind.OPEN,
        symbol="XAUUSD.v",
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=None,
        sl_price=2010.0,
        tp_price=2030.0,
        reason="r",
        setup=SetupName.ORDER_BLOCK,
        confidence=0.7,
    )


def _close(position_id: str = "p1") -> CloseIntent:
    return CloseIntent(IntentKind.CLOSE, position_id, "trail", "manual")


async def test_open_publishes_normally_when_unfrozen() -> None:
    rt = _runtime(frozen=False)
    await _handle_eval_result(rt, "gold_ai", _trade(), dry_run=True,
                              now=datetime.now(timezone.utc))
    kinds = [e.kind for e in rt.intent_bus.recent(10)]
    assert kinds == ["open"]


async def test_open_skipped_with_frozen_skip_kind_when_frozen() -> None:
    rt = _runtime(frozen=True)
    await _handle_eval_result(rt, "gold_ai", _trade(), dry_run=True,
                              now=datetime.now(timezone.utc))
    entries = rt.intent_bus.recent(10)
    assert len(entries) == 1
    assert entries[0].kind == "frozen_skip"
    assert entries[0].payload["reason"] == "engine_frozen"
    assert entries[0].payload["symbol"] == "XAUUSD.v"


async def test_close_intent_passes_through_freeze() -> None:
    """CloseIntent must NOT be blocked by freeze — positions still need to wind down."""
    rt = _runtime(frozen=True)
    await _handle_eval_result(rt, "gold_ai", _close(), dry_run=True,
                              now=datetime.now(timezone.utc))
    kinds = [e.kind for e in rt.intent_bus.recent(10)]
    assert kinds == ["close"]


async def test_mixed_batch_only_blocks_opens() -> None:
    rt = _runtime(frozen=True)
    await _handle_eval_result(
        rt, "gold_ai", [_close("p1"), _trade(), _close("p2")],
        dry_run=True, now=datetime.now(timezone.utc),
    )
    kinds = [e.kind for e in rt.intent_bus.recent(10)]
    # recent() returns newest first; we want the per-event order, so reverse.
    assert list(reversed(kinds)) == ["close", "frozen_skip", "close"]


async def test_freeze_check_failure_defaults_unfrozen() -> None:
    """If freeze_manager itself raises, we keep trading rather than silently halt."""

    class _BoomFreeze:
        async def is_frozen(self) -> bool:
            raise RuntimeError("supabase down")

    rt = SimpleNamespace(
        intent_bus=IntentBus(buffer_size=20),
        freeze_manager=_BoomFreeze(),
        order_executor=SimpleNamespace(),
        settings=SimpleNamespace(),
    )
    await _handle_eval_result(rt, "gold_ai", _trade(), dry_run=True,
                              now=datetime.now(timezone.utc))
    kinds = [e.kind for e in rt.intent_bus.recent(10)]
    # Defaulted to unfrozen → open published normally.
    assert kinds == ["open"]


async def test_none_result_publishes_none_regardless_of_freeze() -> None:
    rt = _runtime(frozen=True)
    await _handle_eval_result(rt, "gold_ai", None, dry_run=True,
                              now=datetime.now(timezone.utc))
    kinds = [e.kind for e in rt.intent_bus.recent(10)]
    assert kinds == ["none"]


async def test_empty_list_result_publishes_none() -> None:
    rt = _runtime(frozen=False)
    await _handle_eval_result(rt, "gold_ai", [], dry_run=True,
                              now=datetime.now(timezone.utc))
    kinds = [e.kind for e in rt.intent_bus.recent(10)]
    assert kinds == ["none"]
