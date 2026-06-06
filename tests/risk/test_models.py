from __future__ import annotations

from datetime import date, datetime

from src.risk.models import (
    DayState,
    ProductCode,
    RiskDecision,
    RiskParams,
    TradeContext,
    WeekState,
)


def test_risk_params_default_v10() -> None:
    p = RiskParams.default()
    assert p.target_win_usd == 95.0
    assert p.target_loss_usd == 70.0
    assert p.daily_loss_limit_usd == 50.0
    assert p.daily_max_trades == 5
    assert p.be_offset_usd == 15.0
    assert p.trail_start_usd == 25.0
    assert p.trail_step_usd == 10.0


def test_risk_params_is_frozen() -> None:
    import dataclasses

    p = RiskParams.default()
    try:
        p.target_win_usd = 1.0  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("RiskParams should be frozen")


def test_day_state_total_pnl() -> None:
    s = DayState(date=date(2026, 5, 30), realized_pnl_usd=10.0, floating_pnl_usd=-3.5)
    assert s.total_pnl_usd == 6.5


def test_week_state_defaults() -> None:
    s = WeekState(cycle_id="w1", product=ProductCode.GOLD_AI)
    assert s.net_pnl_usd == 0.0
    assert s.trades_in_cycle == 0
    assert s.state == "active"


def test_product_code_values() -> None:
    assert ProductCode.GOLD_AI.value == "gold_ai"
    assert ProductCode.MULTI_CFD_AI.value == "multi_cfd_ai"


def test_trade_context_holds_fields() -> None:
    now = datetime(2026, 5, 30, 12, 0, 0)
    tc = TradeContext(
        trade_id="t1",
        symbol="XAUUSD",
        product=ProductCode.GOLD_AI,
        side="buy",
        lot=0.1,
        entry_price=2000.0,
        current_price=2010.0,
        current_pnl_usd=10.0,
        opened_at=now,
    )
    assert tc.current_sl is None
    assert tc.side == "buy"


def test_risk_decision_defaults() -> None:
    d = RiskDecision(True)
    assert d.allowed is True
    assert d.reason is None
    assert d.code is None
    assert d.force_close is False
