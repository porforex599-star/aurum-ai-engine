from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.engine.signal_lock import (
    REASON_COOLDOWN,
    REASON_POSITION_OPEN,
    SignalLock,
)

_T0 = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)


def test_unlocked_by_default() -> None:
    lock = SignalLock(cooldown_seconds=300)
    assert lock.status("multi_cfd_ai", "NAS100.v", _T0) is None
    assert not lock.is_locked("multi_cfd_ai", "NAS100.v", _T0)


def test_locks_after_open_position_held() -> None:
    lock = SignalLock(cooldown_seconds=300)
    lock.record_open("multi_cfd_ai", "NAS100.v", _T0, position_id="POS-1")
    # While the position is believed open, the pair stays locked indefinitely.
    assert lock.status("multi_cfd_ai", "NAS100.v", _T0) == REASON_POSITION_OPEN
    later = _T0 + timedelta(hours=2)
    assert lock.status("multi_cfd_ai", "NAS100.v", later) == REASON_POSITION_OPEN
    assert lock.existing_position_id("multi_cfd_ai", "NAS100.v") == "POS-1"


def test_cooldown_applies_after_release() -> None:
    lock = SignalLock(cooldown_seconds=300)
    lock.record_open("multi_cfd_ai", "NAS100.v", _T0)
    lock.release("multi_cfd_ai", "NAS100.v")
    # Closed but still inside the cooldown window → blocked.
    inside = _T0 + timedelta(seconds=120)
    assert lock.status("multi_cfd_ai", "NAS100.v", inside) == REASON_COOLDOWN
    # Past the cooldown → free, and the stale entry is dropped.
    after = _T0 + timedelta(seconds=301)
    assert lock.status("multi_cfd_ai", "NAS100.v", after) is None
    assert lock.existing_position_id("multi_cfd_ai", "NAS100.v") is None


def test_lock_is_per_product_and_symbol() -> None:
    lock = SignalLock(cooldown_seconds=300)
    lock.record_open("multi_cfd_ai", "NAS100.v", _T0)
    assert lock.is_locked("multi_cfd_ai", "NAS100.v", _T0)
    assert not lock.is_locked("multi_cfd_ai", "US500.v", _T0)
    assert not lock.is_locked("gold_ai", "NAS100.v", _T0)


def test_zero_cooldown_unlocks_immediately_after_release() -> None:
    lock = SignalLock(cooldown_seconds=0)
    lock.record_open("gold_ai", "XAUUSD", _T0)
    lock.release("gold_ai", "XAUUSD")
    assert lock.status("gold_ai", "XAUUSD", _T0) is None
