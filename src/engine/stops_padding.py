"""Phase 2.6.2 — Broker stop-distance padding.

ARES strategy setups (esp. `mean_reversion`) compute SL/TP in absolute price
terms derived from ATR / Bollinger structure. For naturally-tight setups the
resulting SL/TP can sit closer to the live price than the broker's minimum
stop distance (`stopsLevel`), which makes MetaApi reject the order with
"Invalid stops in the request".

`pad_stops_for_broker` widens SL/TP *outward* (never inward) so they satisfy
`(stopsLevel + safety_buffer) * point` distance from the live entry price.
It is intentionally pure — no MetaApi, no caching, no logging — so it is
trivial to unit-test and never affects the strategy layer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaddedStops:
    """Result of padding. `sl`/`tp` are the broker-safe values to send."""

    sl: float
    tp: float | None
    sl_adjusted: bool
    tp_adjusted: bool
    rr: float | None  # post-padding reward:risk ratio (None when no TP)

    @property
    def adjusted(self) -> bool:
        return self.sl_adjusted or self.tp_adjusted


def _compute_rr(entry: float, sl: float, tp: float | None) -> float | None:
    if tp is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    reward = abs(tp - entry)
    return reward / risk


def pad_stops_for_broker(
    *,
    side: str,
    entry_price: float,
    sl: float,
    tp: float | None,
    stops_level_points: int,
    point: float,
    safety_buffer_points: int = 10,
) -> PaddedStops:
    """Pad SL/TP outward so they satisfy the broker's minimum stop distance.

    Args:
        side: "BUY" or "SELL" (case-insensitive).
        entry_price: the LIVE market price the order will fill at (ask for BUY,
            bid for SELL) — NOT a stale bar close.
        sl: strategy-computed stop loss (absolute price).
        tp: strategy-computed take profit (absolute price), or None.
        stops_level_points: broker `stopsLevel` for the symbol (in points).
        point: broker `point` value for the symbol (e.g. 0.00001 for EURUSD).
        safety_buffer_points: extra cushion added above the broker minimum.

    Returns:
        PaddedStops with broker-safe values, adjustment flags, and post-pad R:R.

    Raises:
        ValueError: if `side` is unknown, or if the stops are on the wrong side
            of entry (would invert the order geometry) — a malformed/stale
            intent we must not silently "fix".
    """
    min_distance = (stops_level_points + safety_buffer_points) * point
    s = side.strip().upper()

    if s == "BUY":
        if sl >= entry_price:
            raise ValueError(
                f"BUY SL {sl} is not below entry {entry_price} (inverted stops)"
            )
        if tp is not None and tp <= entry_price:
            raise ValueError(
                f"BUY TP {tp} is not above entry {entry_price} (inverted stops)"
            )
        max_sl = entry_price - min_distance
        padded_sl = min(sl, max_sl)
        if tp is not None:
            min_tp = entry_price + min_distance
            padded_tp: float | None = max(tp, min_tp)
        else:
            padded_tp = None
    elif s == "SELL":
        if sl <= entry_price:
            raise ValueError(
                f"SELL SL {sl} is not above entry {entry_price} (inverted stops)"
            )
        if tp is not None and tp >= entry_price:
            raise ValueError(
                f"SELL TP {tp} is not below entry {entry_price} (inverted stops)"
            )
        min_sl = entry_price + min_distance
        padded_sl = max(sl, min_sl)
        if tp is not None:
            max_tp = entry_price - min_distance
            padded_tp = min(tp, max_tp)
        else:
            padded_tp = None
    else:
        raise ValueError(f"Unknown side: {side}")

    return PaddedStops(
        sl=padded_sl,
        tp=padded_tp,
        sl_adjusted=padded_sl != sl,
        tp_adjusted=padded_tp != tp,
        rr=_compute_rr(entry_price, padded_sl, padded_tp),
    )
