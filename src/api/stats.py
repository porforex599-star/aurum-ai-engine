"""Phase 6.5 — trade history + per-product statistics endpoints.

Read-only dashboard surface over `master_closed_trades`. Aggregation runs
engine-side in Python (per the approved design) so it's unit-testable and the
read path has no SQL coupling; push to a Postgres RPC later only if perf needs
it.

All endpoints reuse the Phase 6 `X-Admin-Key` gate. The dashboard filters
dry_run=FALSE by default for "real production" stats; pass include_dry_run=true
to inspect paper history.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi import HTTPException

from src.api.admin import _PRODUCT_SLUGS, _verify_admin_key
from src.engine.runtime import AppRuntime, get_runtime

router = APIRouter(tags=["stats"])

_BANGKOK = ZoneInfo("Asia/Bangkok")
_PERIODS = ("today", "7d", "30d", "all")
# multi_cfd_ai symbols, normalized — the per-symbol breakdown reports each even
# when it has zero trades in the window, so the dashboard grid is stable.
_MULTI_SYMBOLS = ("NAS100", "US500", "EURUSD", "GBPUSD", "USDJPY", "GER40")


def period_start(period: str, now: datetime | None = None) -> datetime | None:
    """Resolve a period label to its UTC window start. `today` anchors to
    Bangkok midnight (the engine's trading tz); `all` returns None."""
    now = now or datetime.now(timezone.utc)
    if period == "all":
        return None
    if period == "today":
        local_midnight = now.astimezone(_BANGKOK).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local_midnight.astimezone(timezone.utc)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    raise HTTPException(status_code=400, detail=f"unknown period: {period}")


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _max_drawdown(pnls: list[float]) -> float:
    """Peak-to-trough of the cumulative PnL curve (returned as a positive
    magnitude). Assumes `pnls` is ordered oldest → newest."""
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 2)


def compute_stats(
    trades: list[dict], start: datetime | None = None, now: datetime | None = None
) -> dict:
    """Compute the per-product KPI block from a list of closed-trade rows.

    Pure function — `trades` may be in any order (sorted internally by
    closed_at for the drawdown curve). `start`/`now` bound the span used for
    trades_per_day; when omitted, the span runs from the earliest trade.
    """
    now = now or datetime.now(timezone.utc)
    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "biggest_win": 0.0,
            "biggest_loss": 0.0,
            "profit_factor": None,
            "max_drawdown": 0.0,
            "avg_trade_duration": None,
            "trades_per_day": 0.0,
        }

    ordered = sorted(trades, key=lambda t: _parse_dt(t.get("closed_at")) or now)
    pnls = [float(t.get("pnl", 0) or 0) for t in ordered]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    durations = [
        int(t["duration_seconds"])
        for t in ordered
        if t.get("duration_seconds") is not None
    ]

    # Span for trades_per_day: window start (if given) → now, else first close.
    span_start = start or (_parse_dt(ordered[0].get("closed_at")) or now)
    span_days = max((now - span_start).total_seconds() / 86400.0, 1.0)

    return {
        "total_trades": total,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / total, 4),
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "biggest_win": round(max(pnls), 2),
        "biggest_loss": round(min(pnls), 2),
        # None = undefined (no losing trades), which the UI renders as "∞".
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown": _max_drawdown(pnls),
        "avg_trade_duration": round(sum(durations) / len(durations))
        if durations
        else None,
        "trades_per_day": round(total / span_days, 2),
    }


def _validate(slug: str, period: str) -> None:
    if slug not in _PRODUCT_SLUGS:
        raise HTTPException(status_code=400, detail=f"unknown product slug: {slug}")
    if period not in _PERIODS:
        raise HTTPException(status_code=400, detail=f"unknown period: {period}")


@router.get("/stats/{slug}")
async def get_stats(
    slug: str,
    period: str = Query("7d"),
    include_dry_run: bool = Query(False),
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Per-product KPI block for a time window. For multi_cfd_ai, also returns a
    per-symbol breakdown over the configured symbol set."""
    _validate(slug, period)
    now = datetime.now(timezone.utc)
    start = period_start(period, now)
    trades = await rt.trade_logger.fetch_trades(
        slug, start=start, include_dry_run=include_dry_run
    )

    result = {
        "product": slug,
        "period": period,
        "include_dry_run": include_dry_run,
        "stats": compute_stats(trades, start=start, now=now),
    }

    if slug == "multi_cfd_ai":
        by_symbol: dict[str, list[dict]] = {s: [] for s in _MULTI_SYMBOLS}
        for t in trades:
            by_symbol.setdefault(t.get("symbol_norm", ""), []).append(t)
        result["per_symbol"] = {
            sym: compute_stats(rows, start=start, now=now)
            for sym, rows in by_symbol.items()
        }

    return result


@router.get("/trades/{slug}")
async def get_trades(
    slug: str,
    period: str = Query("7d"),
    limit: int = Query(50, ge=1, le=500),
    include_dry_run: bool = Query(False),
    rt: AppRuntime = Depends(get_runtime),
    _: None = Depends(_verify_admin_key),
) -> dict:
    """Recent closed trades for a product, newest first."""
    _validate(slug, period)
    start = period_start(period)
    trades = await rt.trade_logger.fetch_trades(
        slug, start=start, include_dry_run=include_dry_run, limit=limit
    )
    return {
        "product": slug,
        "period": period,
        "count": len(trades),
        "trades": trades,
    }
