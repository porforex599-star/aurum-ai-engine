from __future__ import annotations

import pytest

from src.risk.models import RiskParams
from src.risk.per_trade import (
    compute_new_trail_sl,
    should_move_to_be,
    should_start_trail,
)


@pytest.fixture
def params() -> RiskParams:
    return RiskParams.default()


def test_be_trigger_exact_threshold(params: RiskParams) -> None:
    assert should_move_to_be(15.0, params) is True


def test_be_trigger_below_threshold(params: RiskParams) -> None:
    assert should_move_to_be(14.99, params) is False


def test_trail_start_exact_threshold(params: RiskParams) -> None:
    assert should_start_trail(25.0, params) is True
    assert should_start_trail(24.99, params) is False


def test_buy_trail_sl_moves_up(params: RiskParams) -> None:
    # lot=0.1 pip_value=10 -> step_price = 10 / 1 = 10
    new_sl = compute_new_trail_sl(
        current_price=2050.0,
        current_sl=2030.0,
        side="buy",
        params=params,
        lot=0.1,
        pip_value_usd=10.0,
    )
    assert new_sl == 2040.0


def test_sell_trail_sl_moves_down(params: RiskParams) -> None:
    new_sl = compute_new_trail_sl(
        current_price=2050.0,
        current_sl=2080.0,
        side="sell",
        params=params,
        lot=0.1,
        pip_value_usd=10.0,
    )
    assert new_sl == 2060.0


def test_buy_trail_sl_never_goes_backward(params: RiskParams) -> None:
    # Current SL already higher than candidate -> keep it.
    new_sl = compute_new_trail_sl(
        current_price=2050.0,
        current_sl=2045.0,
        side="buy",
        params=params,
        lot=0.1,
        pip_value_usd=10.0,
    )
    assert new_sl == 2045.0


def test_sell_trail_sl_never_goes_backward(params: RiskParams) -> None:
    new_sl = compute_new_trail_sl(
        current_price=2050.0,
        current_sl=2055.0,
        side="sell",
        params=params,
        lot=0.1,
        pip_value_usd=10.0,
    )
    assert new_sl == 2055.0


def test_unknown_side_raises(params: RiskParams) -> None:
    with pytest.raises(ValueError):
        compute_new_trail_sl(
            current_price=1.0,
            current_sl=1.0,
            side="hold",
            params=params,
            lot=0.1,
        )
