from __future__ import annotations

from datetime import datetime, timezone

from src.risk.filters import (
    FilterResult,
    NewsEvent,
    NewsFilter,
    SpreadFilter,
    VolatilityFilter,
    run_all_filters,
)


def test_spread_block_above_cap() -> None:
    f = SpreadFilter({"XAUUSD": 50.0})
    r = f.check("XAUUSD", 51.0)
    assert r.allowed is False
    assert r.code == "spread_too_wide"


def test_spread_allow_at_cap() -> None:
    f = SpreadFilter({"XAUUSD": 50.0})
    r = f.check("XAUUSD", 50.0)
    assert r.allowed is True


def test_spread_allow_when_symbol_not_configured() -> None:
    f = SpreadFilter({"XAUUSD": 50.0})
    r = f.check("EURUSD", 9999.0)
    assert r.allowed is True


def test_volatility_block_when_3x_avg() -> None:
    f = VolatilityFilter(max_atr_multiplier=2.5)
    r = f.check("XAUUSD", current_atr=3.0, avg_atr=1.0)
    assert r.allowed is False
    assert r.code == "volatility_spike"


def test_volatility_allow_when_at_threshold() -> None:
    f = VolatilityFilter(max_atr_multiplier=2.5)
    r = f.check("XAUUSD", current_atr=2.5, avg_atr=1.0)
    assert r.allowed is True


def test_volatility_allow_when_avg_zero() -> None:
    f = VolatilityFilter()
    r = f.check("XAUUSD", current_atr=10.0, avg_atr=0.0)
    assert r.allowed is True


def test_news_block_at_window_start_edge() -> None:
    f = NewsFilter(before_min=15, after_min=15)
    event_at = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 30, 11, 45, tzinfo=timezone.utc)
    r = f.check("XAUUSD", now, [NewsEvent("USD", "high", event_at)])
    assert r.allowed is False
    assert r.code == "news_blackout"


def test_news_block_at_window_end_edge() -> None:
    f = NewsFilter(before_min=15, after_min=15)
    event_at = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 30, 12, 15, tzinfo=timezone.utc)
    r = f.check("XAUUSD", now, [NewsEvent("USD", "high", event_at)])
    assert r.allowed is False


def test_news_allow_outside_window() -> None:
    f = NewsFilter(before_min=15, after_min=15)
    event_at = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 30, 11, 30, tzinfo=timezone.utc)
    r = f.check("XAUUSD", now, [NewsEvent("USD", "high", event_at)])
    assert r.allowed is True


def test_news_allow_medium_impact() -> None:
    f = NewsFilter()
    event_at = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    r = f.check("XAUUSD", now, [NewsEvent("USD", "medium", event_at)])
    assert r.allowed is True


def test_news_allow_unrelated_currency() -> None:
    f = NewsFilter()
    event_at = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    # GER40 only cares about EUR; USD event should not block.
    r = f.check("GER40", now, [NewsEvent("USD", "high", event_at)])
    assert r.allowed is True


def test_news_allow_unknown_symbol() -> None:
    f = NewsFilter()
    event_at = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    r = f.check("BTCUSD", now, [NewsEvent("USD", "high", event_at)])
    assert r.allowed is True


def test_run_all_filters_returns_first_block_in_order() -> None:
    def allow() -> FilterResult:
        return FilterResult(True)

    def block_a() -> FilterResult:
        return FilterResult(False, "A blocked", "a")

    def block_b() -> FilterResult:
        return FilterResult(False, "B blocked", "b")

    r = run_all_filters([allow, block_a, block_b])
    assert r.allowed is False
    assert r.code == "a"


def test_run_all_filters_all_allow() -> None:
    r = run_all_filters([lambda: FilterResult(True), lambda: FilterResult(True)])
    assert r.allowed is True
