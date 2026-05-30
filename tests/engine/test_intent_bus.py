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
