from __future__ import annotations

from datetime import datetime, timezone

from src.engine.intent_bus import IntentBus, serialize_intent
from src.products.models import CloseIntent, IntentKind, ModifySLIntent, TradeIntent
from src.strategy.models import SetupName, SignalSide


def test_publish_appends_to_buffer() -> None:
    bus = IntentBus(buffer_size=10)
    bus.publish("gold_ai", "open", {"x": 1}, dry_run=True)
    items = bus.recent(10)
    assert len(items) == 1
    assert items[0].product == "gold_ai"
    assert items[0].kind == "open"


def test_buffer_respects_maxlen() -> None:
    bus = IntentBus(buffer_size=3)
    for i in range(5):
        bus.publish("p", "none", {"i": i}, dry_run=True)
    items = bus.recent(10)
    assert len(items) == 3
    # recent is most-recent-first, so the newest entries are first
    assert items[0].payload == {"i": 4}
    assert items[-1].payload == {"i": 2}


def test_recent_returns_most_recent_first() -> None:
    bus = IntentBus()
    bus.publish("p", "none", {"i": 1}, dry_run=False)
    bus.publish("p", "none", {"i": 2}, dry_run=False)
    bus.publish("p", "none", {"i": 3}, dry_run=False)
    items = bus.recent(2)
    assert [i.payload["i"] for i in items] == [3, 2]


def test_clear_empties_buffer() -> None:
    bus = IntentBus()
    bus.publish("p", "none", {}, dry_run=True)
    bus.clear()
    assert bus.recent(10) == []


def test_serialize_intent_handles_enums_and_datetimes() -> None:
    trade = TradeIntent(
        kind=IntentKind.OPEN,
        symbol="XAUUSD",
        side=SignalSide.BUY,
        lot=0.03,
        entry_price=None,
        sl_price=1995.0,
        tp_price=2010.0,
        reason="r",
        setup=SetupName.ORDER_BLOCK,
        confidence=0.8,
    )
    d = serialize_intent(trade)
    assert d["kind"] == "open"
    assert d["side"] == "buy"
    assert d["setup"] == "order_block"

    close = CloseIntent(IntentKind.CLOSE, "p1", "x", "friday_close")
    assert serialize_intent(close)["kind"] == "close"

    mod = ModifySLIntent(IntentKind.MODIFY_SL, "p1", 100.0, "be")
    s = serialize_intent(mod)
    assert s["kind"] == "modify_sl"
    assert s["new_sl_price"] == 100.0


# ---------- Phase 2.6 — notifier integration ----------


class _RecordingNotifier:
    """Records every (kind, dry_run) pair passed to notify."""

    def __init__(self, skip: set[str] | None = None) -> None:
        self._skip = skip or set()
        self.calls: list[tuple[str, bool]] = []
        self.should_send_calls: list[str] = []

    def should_send(self, entry) -> bool:  # type: ignore[no-untyped-def]
        self.should_send_calls.append(entry.kind)
        return entry.kind not in self._skip

    async def notify(self, entry) -> bool:  # type: ignore[no-untyped-def]
        self.calls.append((entry.kind, entry.dry_run))
        return True


def test_publish_without_notifier_is_unchanged() -> None:
    """Backward-compat: existing call sites with no notifier must keep working."""
    bus = IntentBus(buffer_size=10)
    bus.publish("gold_ai", "open_executed", {"x": 1}, dry_run=False)
    assert len(bus.recent(10)) == 1


def test_publish_in_sync_context_skips_notifier_silently() -> None:
    """No running loop → notifier MUST NOT be scheduled and MUST NOT raise."""
    notifier = _RecordingNotifier()
    bus = IntentBus(buffer_size=10, notifier=notifier)
    bus.publish("gold_ai", "open_executed", {"x": 1}, dry_run=False)
    # should_send may or may not be checked before the loop check; what
    # matters is that .notify() was not invoked and nothing raised.
    assert notifier.calls == []
    # entry was still buffered correctly
    assert len(bus.recent(10)) == 1


def test_publish_in_async_context_dispatches_notifier() -> None:
    import asyncio

    notifier = _RecordingNotifier()
    bus = IntentBus(buffer_size=10, notifier=notifier)

    async def runner() -> None:
        bus.publish("gold_ai", "open_executed", {"x": 1}, dry_run=False)
        bus.publish("gold_ai", "none", {}, dry_run=True)  # filtered by should_send? no — recorder allows all
        # Yield once so create_task callbacks can run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(runner())
    kinds = [c[0] for c in notifier.calls]
    assert "open_executed" in kinds


def test_publish_async_respects_should_send_filter() -> None:
    import asyncio

    notifier = _RecordingNotifier(skip={"none", "modify_sl"})
    bus = IntentBus(buffer_size=10, notifier=notifier)

    async def runner() -> None:
        bus.publish("gold_ai", "none", {}, dry_run=True)
        bus.publish("gold_ai", "open_executed", {}, dry_run=False)
        bus.publish("position_manager", "modify_sl", {}, dry_run=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(runner())
    kinds = [c[0] for c in notifier.calls]
    assert kinds == ["open_executed"]
    # all three should_send checks should still have been recorded
    assert notifier.should_send_calls == ["none", "open_executed", "modify_sl"]


def test_publish_swallows_notifier_exceptions() -> None:
    import asyncio

    class _BoomNotifier:
        def should_send(self, entry) -> bool:  # type: ignore[no-untyped-def]
            return True

        async def notify(self, entry):  # type: ignore[no-untyped-def]
            raise RuntimeError("kaboom")

    bus = IntentBus(buffer_size=10, notifier=_BoomNotifier())

    async def runner() -> None:
        bus.publish("gold_ai", "open_executed", {}, dry_run=False)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # Must not raise even though notifier.notify always raises.
    asyncio.run(runner())
    assert len(bus.recent(10)) == 1
