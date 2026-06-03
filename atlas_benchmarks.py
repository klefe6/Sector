"""
Cached subsector ETF benchmark returns for Atlas Morning Audio Source.

Reuses morning_status yfinance cache — no per-request network fan-out.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import morning_status as ms

ATLAS_SUBSECTOR_ETFS: tuple[str, ...] = (
    "SMH", "SOXX", "IGV", "WCLD", "SKYY", "CIBR", "XLK", "QQQ", "SPY",
)


def fetch_subsector_benchmark_returns(
    start_date: date,
    *,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, float | None]:
    """Return percent return from start_date to today for each ETF symbol."""
    syms = symbols or ATLAS_SUBSECTOR_ETFS
    out: dict[str, float | None] = {}
    for sym in syms:
        hist = ms._fetch_bm_hist(sym)
        p0 = ms._bm_price_on_or_after(hist, start_date)
        p1 = ms._bm_current_price(hist)
        if p0 and p1 and p0 > 0:
            out[sym] = round((p1 - p0) / p0 * 100, 2)
        else:
            out[sym] = None
    return out


def subsector_leaders_laggers(
    returns: dict[str, float | None],
    *,
    limit: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sort ETFs by return — leaders highest, laggers lowest."""
    ranked = [
        {"etf": sym, "return_pct": ret, "name": ms.BENCHMARKS.get(sym, sym)}
        for sym, ret in returns.items()
        if ret is not None
    ]
    ranked.sort(key=lambda x: x["return_pct"], reverse=True)
    leaders = ranked[:limit]
    laggers = sorted(ranked, key=lambda x: x["return_pct"])[:limit]
    return leaders, laggers
