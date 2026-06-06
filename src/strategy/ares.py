from __future__ import annotations

from loguru import logger

from src.strategy.liquidity_sweep import detect_liquidity_sweep
from src.strategy.mean_reversion import detect_mean_reversion
from src.strategy.models import MarketSnapshot, Signal
from src.strategy.order_block import detect_order_block
from src.strategy.trend_continuation import detect_trend_continuation


class AresStrategy:
    """Orchestrator: runs all 4 setups, picks highest-confidence signal."""

    def __init__(self) -> None:
        self._detectors = [
            detect_liquidity_sweep,
            detect_order_block,
            detect_mean_reversion,
            detect_trend_continuation,
        ]

    def evaluate(self, snapshot: MarketSnapshot) -> Signal | None:
        candidates: list[Signal] = []
        for fn in self._detectors:
            try:
                sig = fn(snapshot)
                if sig is not None:
                    candidates.append(sig)
            except Exception as e:  # noqa: BLE001
                logger.warning("Setup {} raised: {}", fn.__name__, e)
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.confidence)

    def evaluate_all(self, snapshot: MarketSnapshot) -> list[Signal]:
        out: list[Signal] = []
        for fn in self._detectors:
            try:
                sig = fn(snapshot)
                if sig is not None:
                    out.append(sig)
            except Exception:  # noqa: BLE001
                pass
        return out
