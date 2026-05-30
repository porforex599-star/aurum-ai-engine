from __future__ import annotations

from datetime import datetime, timezone

from src.products.models import OpenPosition
from src.products.position_manager import PositionManager
from src.risk.models import RiskParams
from src.strategy.models import SignalSide


def _pos(
    side: SignalSide,
    pnl: float,
    current_sl: float | None,
    entry: float = 100.0,
    price: float = 100.0,
    lot: float = 0.1,
) -> OpenPosition:
    return OpenPosition(
        position_id="p",
        symbol="X",
        side=side,
        lot=lot,
        entry_price=entry,
        current_price=price,
        current_pnl_usd=pnl,
        current_sl=current_sl,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_no_sl_change_when_pnl_below_be() -> None:
    pm = PositionManager()
    p = _pos(SignalSide.BUY, pnl=5.0, current_sl=None)
    assert pm.evaluate_position(p, RiskParams.default()) is None


def test_be_move_when_pnl_at_threshold_and_no_current_sl() -> None:
    pm = PositionManager()
    p = _pos(SignalSide.BUY, pnl=15.0, current_sl=None, entry=100.0, price=100.5)
    r = pm.evaluate_position(p, RiskParams.default())
    assert r is not None
    assert r.new_sl_price == 100.0
    assert "breakeven" in r.reason.lower()


def test_be_no_backward_when_existing_sl_better() -> None:
    pm = PositionManager()
    # BUY: existing SL at 101 (already above entry). BE move to 100 would go backward.
    p = _pos(SignalSide.BUY, pnl=15.0, current_sl=101.0, entry=100.0, price=101.5)
    assert pm.evaluate_position(p, RiskParams.default()) is None


def test_trail_when_pnl_above_trail_start() -> None:
    pm = PositionManager()
    p = _pos(SignalSide.BUY, pnl=30.0, current_sl=None, entry=100.0, price=110.0, lot=0.1)
    r = pm.evaluate_position(p, RiskParams.default(), pip_value_usd=10.0)
    assert r is not None
    assert "trail" in r.reason.lower()


def test_trail_buy_sl_moves_up() -> None:
    pm = PositionManager()
    # lot=0.1, pip_value=10 → step_price = 10/(0.1*10) = 10
    # BUY price=110, current_sl=99 → new_sl = max(99, 110-10) = 100
    p = _pos(SignalSide.BUY, pnl=30.0, current_sl=99.0, entry=100.0, price=110.0, lot=0.1)
    r = pm.evaluate_position(p, RiskParams.default(), pip_value_usd=10.0)
    assert r is not None
    assert r.new_sl_price == 100.0


def test_trail_sell_sl_moves_down() -> None:
    pm = PositionManager()
    # SELL price=90, current_sl=101 → new_sl = min(101, 90+10) = 100
    p = _pos(SignalSide.SELL, pnl=30.0, current_sl=101.0, entry=100.0, price=90.0, lot=0.1)
    r = pm.evaluate_position(p, RiskParams.default(), pip_value_usd=10.0)
    assert r is not None
    assert r.new_sl_price == 100.0


def test_evaluate_all_filters_non_actions() -> None:
    pm = PositionManager()
    positions = [
        _pos(SignalSide.BUY, pnl=5.0, current_sl=None),  # no action
        _pos(SignalSide.BUY, pnl=30.0, current_sl=99.0, entry=100.0, price=110.0, lot=0.1),  # trail
    ]
    out = pm.evaluate_all(positions, RiskParams.default(), pip_value_usd=10.0)
    assert len(out) == 1
