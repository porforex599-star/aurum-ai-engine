from __future__ import annotations

from datetime import datetime

from src.strategy.ares import AresStrategy
from src.strategy.models import MarketSnapshot, SetupName, Signal, SignalSide


def _sig(setup: SetupName, conf: float) -> Signal:
    return Signal(
        setup=setup,
        side=SignalSide.BUY,
        entry_price=100.0,
        sl_price=99.0,
        tp_price=102.0,
        confidence=conf,
        reason="test",
        timestamp=datetime(2026, 1, 1),
    )


def _empty_snapshot() -> MarketSnapshot:
    return MarketSnapshot(symbol="XAUUSD", bars_m15=[], bars_h1=[], bars_h4=[])


def test_evaluate_returns_none_when_no_setups_fire() -> None:
    ares = AresStrategy()
    # All real detectors return None on empty snapshot.
    assert ares.evaluate(_empty_snapshot()) is None


def test_evaluate_returns_single_signal_when_one_fires() -> None:
    ares = AresStrategy()
    ares._detectors = [
        lambda s: None,
        lambda s: _sig(SetupName.ORDER_BLOCK, 0.7),
        lambda s: None,
        lambda s: None,
    ]
    result = ares.evaluate(_empty_snapshot())
    assert result is not None
    assert result.setup == SetupName.ORDER_BLOCK
    assert result.confidence == 0.7


def test_evaluate_picks_highest_confidence_when_multiple_fire() -> None:
    ares = AresStrategy()
    ares._detectors = [
        lambda s: _sig(SetupName.LIQUIDITY_SWEEP, 0.60),
        lambda s: _sig(SetupName.ORDER_BLOCK, 0.85),
        lambda s: _sig(SetupName.MEAN_REVERSION, 0.70),
        lambda s: _sig(SetupName.TREND_CONTINUATION, 0.75),
    ]
    result = ares.evaluate(_empty_snapshot())
    assert result is not None
    assert result.setup == SetupName.ORDER_BLOCK
    assert result.confidence == 0.85


def test_evaluate_all_returns_all_firing_signals() -> None:
    ares = AresStrategy()
    ares._detectors = [
        lambda s: _sig(SetupName.LIQUIDITY_SWEEP, 0.6),
        lambda s: None,
        lambda s: _sig(SetupName.MEAN_REVERSION, 0.7),
        lambda s: None,
    ]
    results = ares.evaluate_all(_empty_snapshot())
    assert len(results) == 2
    assert {r.setup for r in results} == {SetupName.LIQUIDITY_SWEEP, SetupName.MEAN_REVERSION}


def test_evaluate_swallows_detector_exceptions() -> None:
    ares = AresStrategy()

    def boom(_snap):
        raise RuntimeError("kaboom")

    ares._detectors = [boom, lambda s: _sig(SetupName.ORDER_BLOCK, 0.5)]
    result = ares.evaluate(_empty_snapshot())
    assert result is not None
    assert result.setup == SetupName.ORDER_BLOCK
