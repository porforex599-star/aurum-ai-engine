from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.risk.models import RiskParams
from src.risk.per_day import BKK, DayTracker


@pytest.fixture
def tracker() -> DayTracker:
    return DayTracker(RiskParams.default())


def test_fresh_tracker_allows_open(tracker: DayTracker) -> None:
    d = tracker.can_open_new_trade()
    assert d.allowed is True


def test_block_at_max_trades(tracker: DayTracker) -> None:
    for _ in range(5):
        tracker.record_trade_open()
    d = tracker.can_open_new_trade()
    assert d.allowed is False
    assert d.code == "daily_max_trades"


def test_block_when_realized_loss_at_limit(tracker: DayTracker) -> None:
    tracker.record_trade_close(-50.0)
    d = tracker.can_open_new_trade()
    assert d.allowed is False
    assert d.code == "daily_loss_limit"


def test_block_when_floating_loss_at_limit(tracker: DayTracker) -> None:
    tracker.update_floating(-50.0)
    d = tracker.can_open_new_trade()
    assert d.allowed is False
    assert d.code == "daily_loss_limit"


def test_block_when_combined_loss_at_limit(tracker: DayTracker) -> None:
    tracker.record_trade_close(-30.0)
    tracker.update_floating(-20.0)
    d = tracker.can_open_new_trade()
    assert d.allowed is False
    assert d.code == "daily_loss_limit"


def test_allow_after_midnight_reset(tracker: DayTracker) -> None:
    tracker.record_trade_close(-60.0)
    assert tracker.can_open_new_trade().allowed is False

    tomorrow = datetime.now(BKK) + timedelta(days=1)
    tracker.maybe_reset(tomorrow)

    d = tracker.can_open_new_trade()
    assert d.allowed is True
    assert tracker.state.realized_pnl_usd == 0.0
    assert tracker.state.trades_opened == 0


def test_multiple_opens_closes_preserve_counts(tracker: DayTracker) -> None:
    tracker.record_trade_open()
    tracker.record_trade_open()
    tracker.record_trade_close(10.0)
    tracker.record_trade_close(-3.0)

    assert tracker.state.trades_opened == 2
    assert tracker.state.trades_closed == 2
    assert tracker.state.realized_pnl_usd == 7.0


def test_maybe_reset_same_day_no_change(tracker: DayTracker) -> None:
    tracker.record_trade_close(-10.0)
    now = datetime.now(BKK)
    tracker.maybe_reset(now)
    assert tracker.state.realized_pnl_usd == -10.0
