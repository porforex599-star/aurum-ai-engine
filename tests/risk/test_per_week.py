from __future__ import annotations

import pytest

from src.risk.models import ProductCode, RiskParams
from src.risk.per_week import WeekTracker


@pytest.fixture
def tracker() -> WeekTracker:
    return WeekTracker("cycle-1", ProductCode.GOLD_AI, RiskParams.default())


def test_pnl_progression_accumulates(tracker: WeekTracker) -> None:
    tracker.record_pnl_delta(10.0)
    tracker.record_pnl_delta(20.0)
    tracker.record_pnl_delta(-5.0)
    assert tracker.state.net_pnl_usd == 25.0
    assert tracker.state.state == "active"


def test_exact_win_target_expires_week(tracker: WeekTracker) -> None:
    tracker.record_pnl_delta(95.0)
    assert tracker.state.state == "expired_win"
    assert tracker.is_expired() is True


def test_exact_loss_target_expires_week(tracker: WeekTracker) -> None:
    tracker.record_pnl_delta(-70.0)
    assert tracker.state.state == "expired_loss"
    assert tracker.is_expired() is True


def test_just_below_win_stays_active(tracker: WeekTracker) -> None:
    tracker.record_pnl_delta(94.0)
    assert tracker.state.state == "active"
    assert tracker.is_expired() is False


def test_check_target_force_close_on_win(tracker: WeekTracker) -> None:
    tracker.record_pnl_delta(120.0)
    d = tracker.check_target()
    assert d.allowed is False
    assert d.force_close is True
    assert d.code == "week_target_win"


def test_check_target_force_close_on_loss(tracker: WeekTracker) -> None:
    tracker.record_pnl_delta(-80.0)
    d = tracker.check_target()
    assert d.allowed is False
    assert d.force_close is True
    assert d.code == "week_target_loss"


def test_check_target_active_allows(tracker: WeekTracker) -> None:
    tracker.record_pnl_delta(10.0)
    d = tracker.check_target()
    assert d.allowed is True


def test_record_trade_closed_increments_count(tracker: WeekTracker) -> None:
    tracker.record_trade_closed(5.0)
    tracker.record_trade_closed(-2.0)
    assert tracker.state.trades_in_cycle == 2
    assert tracker.state.net_pnl_usd == 3.0
