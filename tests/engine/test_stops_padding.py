from __future__ import annotations

import pytest

from src.engine.stops_padding import pad_stops_for_broker


# EURUSD-like: point=0.00001, stopsLevel=10, buffer=10 → min_distance=0.0002


def test_buy_sl_too_close_is_pushed_away() -> None:
    entry = 1.16000
    # SL only 5 points away, well inside the 20-point minimum.
    res = pad_stops_for_broker(
        side="BUY",
        entry_price=entry,
        sl=1.15995,
        tp=1.16300,
        stops_level_points=10,
        point=0.00001,
        safety_buffer_points=10,
    )
    assert res.sl == pytest.approx(entry - 0.0002)
    assert res.sl_adjusted is True
    # TP was already far enough → unchanged.
    assert res.tp == pytest.approx(1.16300)
    assert res.tp_adjusted is False


def test_sell_tp_too_close_is_pushed_away() -> None:
    entry = 1.16000
    res = pad_stops_for_broker(
        side="SELL",
        entry_price=entry,
        sl=1.16300,  # far enough above
        tp=1.15995,  # only 5 points below → too close
        stops_level_points=10,
        point=0.00001,
        safety_buffer_points=10,
    )
    assert res.tp == pytest.approx(entry - 0.0002)
    assert res.tp_adjusted is True
    assert res.sl == pytest.approx(1.16300)
    assert res.sl_adjusted is False


def test_already_wide_stops_unchanged() -> None:
    entry = 1.16000
    res = pad_stops_for_broker(
        side="BUY",
        entry_price=entry,
        sl=1.15000,
        tp=1.17000,
        stops_level_points=10,
        point=0.00001,
        safety_buffer_points=10,
    )
    assert res.sl == pytest.approx(1.15000)
    assert res.tp == pytest.approx(1.17000)
    assert res.adjusted is False


def test_index_padding_sp500_like() -> None:
    # SP500.v: point=0.1, stopsLevel=100 → min_distance (with buffer 10) = 11.0
    entry = 7610.0
    res = pad_stops_for_broker(
        side="BUY",
        entry_price=entry,
        sl=7603.0,  # 7 points away → inside 11-point minimum
        tp=7617.0,  # 7 points away → inside minimum
        stops_level_points=100,
        point=0.1,
        safety_buffer_points=10,
    )
    assert res.sl == pytest.approx(entry - 11.0)
    assert res.tp == pytest.approx(entry + 11.0)
    assert res.adjusted is True


def test_buy_inverted_sl_raises() -> None:
    with pytest.raises(ValueError):
        pad_stops_for_broker(
            side="BUY",
            entry_price=1.16000,
            sl=1.16100,  # SL above entry → inverted for a BUY
            tp=1.16300,
            stops_level_points=10,
            point=0.00001,
        )


def test_sell_inverted_tp_raises() -> None:
    with pytest.raises(ValueError):
        pad_stops_for_broker(
            side="SELL",
            entry_price=1.16000,
            sl=1.16300,
            tp=1.16100,  # TP above entry → inverted for a SELL
            stops_level_points=10,
            point=0.00001,
        )


def test_unknown_side_raises() -> None:
    with pytest.raises(ValueError):
        pad_stops_for_broker(
            side="LONG",
            entry_price=1.0,
            sl=0.9,
            tp=1.1,
            stops_level_points=10,
            point=0.00001,
        )


def test_rr_computed_after_padding() -> None:
    entry = 1.16000
    res = pad_stops_for_broker(
        side="BUY",
        entry_price=entry,
        sl=1.15800,  # 20 points risk
        tp=1.16400,  # 40 points reward
        stops_level_points=10,
        point=0.00001,
        safety_buffer_points=10,
    )
    # Nothing padded (both already beyond minimum) → R:R = 40/20 = 2.0
    assert res.adjusted is False
    assert res.rr == pytest.approx(2.0)


def test_rr_degrades_when_sl_widened() -> None:
    entry = 1.16000
    res = pad_stops_for_broker(
        side="BUY",
        entry_price=entry,
        sl=1.15995,  # 5 points → widened to 20 points (risk grows)
        tp=1.16010,  # 10 points reward... widened to 20 too
        stops_level_points=10,
        point=0.00001,
        safety_buffer_points=10,
    )
    # Both widened to 20 points → R:R collapses to ~1.0
    assert res.adjusted is True
    assert res.rr == pytest.approx(1.0)


def test_tp_none_is_handled() -> None:
    res = pad_stops_for_broker(
        side="BUY",
        entry_price=1.16000,
        sl=1.15995,
        tp=None,
        stops_level_points=10,
        point=0.00001,
        safety_buffer_points=10,
    )
    assert res.tp is None
    assert res.rr is None
    assert res.sl_adjusted is True
