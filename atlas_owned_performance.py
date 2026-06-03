"""
Owned portfolio performance paths for Atlas Morning Audio Source.

Uses config/current_portfolio.json and cached yfinance history — no invented holdings.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

import atlas_portfolio_export as ape
import atlas_sector_etf_mapping as asem
import morning_status as ms

PriceFetcher = Callable[[str], Any]


def _parse_date(ds: str | None) -> date | None:
    if not ds:
        return None
    try:
        return datetime.strptime(ds, "%Y-%m-%d").date()
    except Exception:
        return None


def _default_price_fetcher(ticker: str):
    return ms._fetch_history_cached(ticker)


def _return_pct(p0: float | None, p1: float | None) -> float | None:
    if p0 and p1 and p0 > 0:
        return round((p1 - p0) / p0 * 100, 2)
    return None


def _return_over_trading_days(hist, days: int) -> float | None:
    if hist is None or getattr(hist, "empty", True) or len(hist) < days + 1:
        return None
    p_end = float(hist["Close"].iloc[-1])
    p_start = float(hist["Close"].iloc[-(days + 1)])
    return _return_pct(p_start, p_end)


def _benchmark_return(symbol: str, start: date, fetch_bm: Callable[[str], Any]) -> float | None:
    hist = fetch_bm(symbol)
    p0 = ms._get_price_on_or_after(hist, start)
    p1 = ms._bm_current_price(hist)
    return _return_pct(p0, p1)


def _resolve_sector_etf(position: dict) -> str:
    sector = (position.get("sector") or "").strip().upper()
    if sector:
        return sector
    inferred = asem.infer_sector_etf(
        sector_text=position.get("sector_raw") or "",
        industry_text=position.get("sector_raw") or "",
    )
    return inferred.get("sector_etf") or ""


def _classify_owned_row(
    row: dict[str, Any],
    *,
    primary_sector_etf: str,
) -> str | None:
    """Return candidate bucket: avoid_adding, hold, trim_review, or None."""
    if row.get("data_status") != "ok" or row.get("return_pct") is None:
        return None
    ret = row["return_pct"]
    vs_spy = row.get("vs_spy")
    vs_sector = row.get("vs_sector_etf")
    vs_primary = row.get("vs_primary_sector_etf")

    sector_ref = vs_sector if vs_sector is not None else vs_primary
    if vs_spy is not None and sector_ref is not None:
        if ret < 0 and vs_spy < 0 and sector_ref < 0:
            return "avoid_adding"
        if vs_spy < -1.0 and sector_ref < -1.0:
            return "trim_review"
        if vs_spy >= 0 or (sector_ref is not None and sector_ref >= -0.5):
            return "hold"
    elif vs_spy is not None:
        if vs_spy < -1.0:
            return "trim_review"
        if vs_spy >= 0:
            return "hold"
    return None


def compute_owned_position_performance(
    *,
    baseline_date: str | None,
    primary_sector_etf: str = "",
    alert_tickers: set[str] | None = None,
    portfolio_path: str | None = None,
    price_fetcher: PriceFetcher | None = None,
    bm_fetcher: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """
    Build owned ticker performance from portfolio export.

    Returns owned_position_performance contract dict.
    """
    alert_tickers = {t.upper() for t in (alert_tickers or set())}
    path = portfolio_path or ape.CONFIG_EXPORT_PATH
    positions = ape._load_json_positions(path) if path else []
    source = "config/current_portfolio.json" if path == ape.CONFIG_EXPORT_PATH else path or "none"

    baseline = _parse_date(baseline_date) or (date.today() - timedelta(days=10))
    baseline_str = baseline.strftime("%Y-%m-%d")
    fetch_price = price_fetcher or _default_price_fetcher
    fetch_bm = bm_fetcher or ms._fetch_bm_hist

    spy_ret = _benchmark_return("SPY", baseline, fetch_bm)
    qqq_ret = _benchmark_return("QQQ", baseline, fetch_bm)
    primary_ret = (
        _benchmark_return(primary_sector_etf, baseline, fetch_bm)
        if primary_sector_etf else None
    )

    sector_ret_cache: dict[str, float | None] = {}
    owned_rows: list[dict[str, Any]] = []
    missing: list[str] = []

    for pos in positions:
        ticker = (pos.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        sector_etf = _resolve_sector_etf(pos)
        if sector_etf and sector_etf not in sector_ret_cache:
            sector_ret_cache[sector_etf] = _benchmark_return(sector_etf, baseline, fetch_bm)
        sector_ret = sector_ret_cache.get(sector_etf)

        hist = fetch_price(ticker)
        p0 = ms._get_price_on_or_after(hist, baseline)
        p1 = ms._bm_current_price(hist)
        ret = _return_pct(p0, p1)

        if ret is None:
            missing.append(ticker)
            owned_rows.append({
                "ticker": ticker,
                "sector_etf": sector_etf,
                "in_alert_paths": ticker in alert_tickers,
                "baseline_date": baseline_str,
                "return_pct": None,
                "return_1d": None,
                "return_5d": None,
                "return_10d": None,
                "vs_spy": None,
                "vs_qqq": None,
                "vs_primary_sector_etf": None,
                "vs_sector_etf": None,
                "data_status": "missing",
            })
            continue

        row = {
            "ticker": ticker,
            "sector_etf": sector_etf,
            "in_alert_paths": ticker in alert_tickers,
            "baseline_date": baseline_str,
            "return_pct": ret,
            "return_1d": _return_over_trading_days(hist, 1),
            "return_5d": _return_over_trading_days(hist, 5),
            "return_10d": _return_over_trading_days(hist, 10),
            "vs_spy": round(ret - spy_ret, 2) if spy_ret is not None else None,
            "vs_qqq": round(ret - qqq_ret, 2) if qqq_ret is not None else None,
            "vs_primary_sector_etf": round(ret - primary_ret, 2) if primary_ret is not None else None,
            "vs_sector_etf": round(ret - sector_ret, 2) if sector_ret is not None else None,
            "data_status": "ok",
        }
        owned_rows.append(row)

    with_data = [r for r in owned_rows if r["data_status"] == "ok"]
    returns = [r["return_pct"] for r in with_data if r["return_pct"] is not None]
    owned_avg = round(sum(returns) / len(returns), 2) if returns else None

    ranked = sorted(with_data, key=lambda r: r["return_pct"] or -999)
    laggards = [
        {"ticker": r["ticker"], "return_pct": r["return_pct"], "vs_spy": r["vs_spy"],
         "sector_etf": r["sector_etf"]}
        for r in ranked[:3]
    ]
    leaders = [
        {"ticker": r["ticker"], "return_pct": r["return_pct"], "vs_spy": r["vs_spy"],
         "sector_etf": r["sector_etf"]}
        for r in sorted(with_data, key=lambda r: r["return_pct"] or -999, reverse=True)[:3]
    ]

    avoid_adding: list[dict] = []
    hold_candidates: list[dict] = []
    trim_review: list[dict] = []
    for row in with_data:
        bucket = _classify_owned_row(row, primary_sector_etf=primary_sector_etf)
        entry = {"ticker": row["ticker"], "return_pct": row["return_pct"],
                 "vs_spy": row["vs_spy"], "sector_etf": row["sector_etf"]}
        if bucket == "avoid_adding":
            avoid_adding.append(entry)
        elif bucket == "hold":
            hold_candidates.append(entry)
        elif bucket == "trim_review":
            trim_review.append(entry)

    total = len(positions)
    with_count = len(with_data)
    if total == 0:
        data_status = "missing"
    elif with_count == 0:
        data_status = "missing"
    elif with_count < total:
        data_status = "partial"
    else:
        data_status = "ok"

    return {
        "source": source,
        "baseline_date": baseline_str,
        "positions_total": total,
        "positions_with_price_data": with_count,
        "positions_missing_price_data": missing,
        "owned_rows": owned_rows,
        "owned_average_return": owned_avg,
        "owned_vs_spy": round(owned_avg - spy_ret, 2) if owned_avg is not None and spy_ret is not None else None,
        "owned_vs_qqq": round(owned_avg - qqq_ret, 2) if owned_avg is not None and qqq_ret is not None else None,
        "owned_vs_primary_sector_etf": (
            round(owned_avg - primary_ret, 2) if owned_avg is not None and primary_ret is not None else None
        ),
        "laggards": laggards,
        "leaders": leaders,
        "avoid_adding_candidates": avoid_adding,
        "hold_candidates": hold_candidates,
        "trim_review_candidates": trim_review,
        "data_status": data_status,
    }
