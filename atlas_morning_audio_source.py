"""
AtlasInvestor Morning Analysis — audio source readiness contract.

Builds a deterministic JSON payload for future DawnBrief / AtlasInvestor spoken
audio. Does NOT generate audio or place trades.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import atlas_benchmarks as ab
import atlas_owned_performance as aop
import atlas_portfolio_export as ape
import atlas_sector_etf_mapping as asem
import morning_status as ms
import warren_loader as wl
from selector import SECTOR_NAMES

_HERE = os.path.dirname(os.path.abspath(__file__))

CONTRACT_VERSION = "atlas_morning_audio_source_v4"

# Readiness thresholds
COVERAGE_NOT_READY_PCT = 30.0
COVERAGE_WARNING_PCT = 60.0
WARREN_STALE_DAYS = 2
POST_ALERT_EXPECTED_LAG_TRADING_DAYS = 5


def _warren_context_from_result(w: wl.WarrenResult) -> dict[str, Any]:
    return {
        "ticker_sector": dict(w.ticker_sector),
        "sector_reasons": dict(w.sector_reasons),
        "top_sectors": list(w.top_sectors),
    }


def _load_warren_context_by_date(days: int = 60) -> dict[str, dict[str, Any]]:
    """Build per-scan-date Warren context for sector inference."""
    ctx: dict[str, dict[str, Any]] = {}
    today = date.today()
    for delta in range(1, days + 1):
        d = today - timedelta(days=delta)
        ds = d.strftime("%Y-%m-%d")
        folder = os.path.join(wl.WARREN_OUTPUT_DIR, ds)
        summary_path = os.path.join(folder, "run_summary.json")
        if not os.path.isfile(summary_path):
            continue
        try:
            w = wl.load_warren_from_folder(folder)
            if w.success:
                ctx[ds] = _warren_context_from_result(w)
        except Exception:
            continue
    return ctx


def _positions_lookup(active_positions: list[dict] | None) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for p in active_positions or []:
        tk = (p.get("ticker") or "").strip().upper()
        if tk:
            lookup[tk] = p
    return lookup


def _rows_from_pa_data(
    pa_data: dict | None,
    *,
    positions_lookup: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract alert row dicts from post-alert-data-store."""
    if not pa_data:
        return []
    positions_lookup = positions_lookup or {}
    series = pa_data.get("series") or []
    rows: list[dict[str, Any]] = []
    for s in series:
        tk = (s.get("ticker") or "").strip().upper()
        pos = positions_lookup.get(tk) or {}
        rows.append({
            "ticker": s.get("ticker"),
            "scan_date": s.get("scan_date"),
            "sector_etf": s.get("sector_etf") or "",
            "setup": s.get("setup") or "",
            "portfolio_status": s.get("portfolio_status") or "",
            "rank": s.get("rank") or 0,
            "sector": s.get("sector") or pos.get("sector") or "",
            "ticker_sector": s.get("ticker_sector") or pos.get("sector_raw") or "",
            "industry": s.get("industry") or "",
            "notes": s.get("notes") or pos.get("notes") or "",
            "thesis": s.get("thesis") or "",
            "alert_text": s.get("alert_text") or s.get("alert") or "",
        })
    return rows


def _latest_post_alert_date(rows: list[dict[str, Any]]) -> str | None:
    dates = sorted({r.get("scan_date") for r in rows if r.get("scan_date")}, reverse=True)
    return dates[0] if dates else None


def _parse_date(ds: str | None) -> date | None:
    if not ds:
        return None
    try:
        return datetime.strptime(ds, "%Y-%m-%d").date()
    except Exception:
        return None


def _is_stale_date(ds: str | None, max_days: int) -> bool:
    d = _parse_date(ds)
    if not d:
        return True
    return (date.today() - d).days > max_days


def _business_days_between(earlier: date, later: date) -> int:
    """Count Mon–Fri days strictly between earlier and later (exclusive of earlier)."""
    if earlier > later:
        earlier, later = later, earlier
    days = 0
    cur = earlier
    while cur < later:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def _classify_post_alert_lag(
    *,
    latest_scan_date: str | None,
    post_alert_latest_date: str | None,
) -> dict[str, Any]:
    scan_d = _parse_date(latest_scan_date)
    pa_d = _parse_date(post_alert_latest_date)
    if not pa_d:
        return {
            "post_alert_latest_date": post_alert_latest_date,
            "latest_scan_date": latest_scan_date,
            "lag_days": None,
            "lag_status": "missing",
        }
    ref = scan_d or date.today()
    lag_days = _business_days_between(pa_d, ref)
    if lag_days <= POST_ALERT_EXPECTED_LAG_TRADING_DAYS:
        lag_status = "expected_lag"
    else:
        lag_status = "stale_lag"
    return {
        "post_alert_latest_date": post_alert_latest_date,
        "latest_scan_date": latest_scan_date,
        "lag_days": lag_days,
        "lag_status": lag_status,
    }


def _portfolio_discipline_summary(
    *,
    allocation_check: dict,
    active_positions: list[dict] | None,
    w: wl.WarrenResult,
    active_tickers: set[str],
    watchlist_tickers: set[str],
) -> dict[str, Any]:
    decision = allocation_check.get("decision") or {}
    owned_ret = allocation_check.get("owned_ret")
    new_ret = allocation_check.get("new_ret")

    suggested = ms.classify_suggested_tickers(w, active_tickers, watchlist_tickers)
    research_count = sum(
        1 for s in suggested
        if s.portfolio_status in ("New", "Watchlist")
        and "Avoid" not in s.verdict
    )
    avoid_count = sum(1 for s in suggested if "Avoid" in s.verdict)

    avoid_adding_count = 0
    if decision.get("owned_signal") == "avoid_adding":
        avoid_adding_count = allocation_check.get("n_owned") or 0

    trim_count = hold_count = watch_count = 0
    if active_positions:
        try:
            from trim_monitor import analyze_portfolio

            trim_results = analyze_portfolio(active_positions)
            for tr in trim_results:
                v = (tr.verdict or "").lower()
                if "strong trim" in v or "trim candidate" in v:
                    trim_count += 1
                elif v == "hold":
                    hold_count += 1
                elif v == "watch":
                    watch_count += 1
        except Exception:
            pass

    return {
        "owned_average_return": owned_ret,
        "new_average_return": new_ret,
        "owned_signal": decision.get("owned_signal", "ok"),
        "new_signal": decision.get("new_signal", "ok"),
        "avoid_adding_count": avoid_adding_count,
        "avoid_adding_scope": "alert_basket_rows" if avoid_adding_count else None,
        "alert_basket_owned_row_count": allocation_check.get("n_owned") or 0,
        "owned_holdings_count": len(active_tickers),
        "research_count": research_count,
        "avoid_verdict_count": avoid_count,
        "trim_or_hold_count": trim_count + hold_count,
        "trim_candidate_count": trim_count,
        "hold_count": hold_count,
        "watch_count": watch_count,
    }


def _sector_strength_summary(
    w: wl.WarrenResult,
    allocation_check: dict,
    enriched_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    primary_etf = allocation_check.get("primary_etf") or ""
    if not primary_etf and w.top_sectors:
        primary_etf = w.top_sectors[0]

    etf_counts: dict[str, int] = {}
    for r in enriched_rows:
        etf = (r.get("sector_etf") or "").strip().upper()
        if etf:
            etf_counts[etf] = etf_counts.get(etf, 0) + 1
    if not primary_etf and etf_counts:
        primary_etf = max(etf_counts, key=etf_counts.get)

    return {
        "primary_sector_etf": primary_etf,
        "primary_sector_name": SECTOR_NAMES.get(primary_etf) or asem.ETF_DISPLAY_NAMES.get(primary_etf, primary_etf),
        "warren_top_sectors": list(w.top_sectors),
        "etf_alert_counts": etf_counts,
        "etf_return": allocation_check.get("etf_ret"),
        "stock_basket_return": allocation_check.get("primary_ret"),
        "etf_advantage": (
            round((allocation_check.get("etf_ret") or 0) - (allocation_check.get("primary_ret") or 0), 2)
            if allocation_check.get("etf_ret") is not None
            and allocation_check.get("primary_ret") is not None
            else None
        ),
        "suggested_vehicle": (allocation_check.get("decision") or {}).get("vehicle_label"),
    }


def _build_warnings_and_notes(
    *,
    w: wl.WarrenResult,
    pa_rows: list[dict],
    coverage: dict,
    allocation_check: dict,
    portfolio_ready: bool,
    lag_status: str,
    owned_perf: dict,
    owned_tickers: set[str],
    alert_tickers: set[str],
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    notes: list[str] = []

    if not w.date or w.freshness in ("NONE", "FAILED"):
        warnings.append("no_current_scan")
    elif w.freshness == "STALE":
        warnings.append("warren_ai_stale")
    elif _is_stale_date(w.date, WARREN_STALE_DAYS):
        warnings.append("warren_ai_stale")

    if not pa_rows:
        warnings.append("post_alert_data_missing")
    elif lag_status == "stale_lag":
        warnings.append("post_alert_lag_stale")
    elif lag_status == "missing":
        warnings.append("post_alert_data_missing")

    cov = coverage.get("coverage_percent", 0)
    if cov < COVERAGE_WARNING_PCT:
        warnings.append("sector_etf_coverage_too_low")

    if allocation_check.get("etf_ret") is None:
        warnings.append("etf_benchmark_missing")

    if not portfolio_ready:
        warnings.append("portfolio_export_missing")
    elif owned_tickers:
        perf_ok = owned_perf.get("data_status") in ("ok", "partial") and (
            owned_perf.get("positions_with_price_data", 0) > 0
        )
        if not perf_ok:
            warnings.append("owned_position_performance_missing")
        elif owned_tickers - alert_tickers:
            notes.append("owned_positions_not_in_alert_paths")

    return warnings, notes


def _overall_readiness(warnings: list[str], coverage: dict) -> str:
    critical = {"post_alert_data_missing", "no_current_scan"}
    if any(w in critical for w in warnings):
        return "not_ready"
    if coverage.get("coverage_percent", 0) < COVERAGE_NOT_READY_PCT:
        return "not_ready"
    if warnings:
        return "ready_with_warnings"
    return "ready"


def _action_rule_today(
    vehicle: str,
    owned_signal: str,
    new_signal: str,
) -> str:
    if vehicle == "Sector ETF":
        base = "Favor the sector ETF over individual stock alerts today."
    elif vehicle == "Stock Alerts":
        base = "Favor individual stock alerts over the sector ETF today."
    elif vehicle == "Cash / Wait":
        base = "Stay patient — neither stocks nor the sector ETF have a clear edge today."
    else:
        base = "Follow the allocation check vehicle for today."

    if owned_signal == "avoid_adding":
        base += " Do not add to owned laggards."
    if new_signal == "prefer_new":
        base += " Prioritize new setups over existing positions."
    return base


def _suggested_audio_bullets(
    *,
    readiness: str,
    sector_summary: dict,
    discipline: dict,
    w: wl.WarrenResult,
    portfolio: dict,
    owned_perf: dict,
    tech_summary: dict,
    lag: dict,
    warnings: list[str],
    notes: list[str],
    coverage: dict,
) -> list[str]:
    if readiness == "not_ready":
        gap = warnings[0].replace("_", " ") if warnings else "data gaps"
        return [f"Morning audio source is not ready — resolve {gap} before recording."]

    bullets: list[str] = []
    vehicle = sector_summary.get("suggested_vehicle") or "Stock Alerts"
    etf = sector_summary.get("primary_sector_etf") or "sector ETF"
    etf_name = sector_summary.get("primary_sector_name") or etf
    etf_adv = sector_summary.get("etf_advantage")

    if vehicle == "Sector ETF" and etf_adv is not None and etf_adv > 3:
        bullets.append(f"Sector ETF allocation is preferred — {etf_name} leads alerts by {etf_adv:.1f} percent.")
    elif vehicle == "Cash / Wait":
        bullets.append("Neither stocks nor the sector ETF have a clear edge — wait or use broad index only.")
    elif vehicle == "Stock Alerts":
        bullets.append(f"Individual stock alerts are preferred over {etf} today.")
    else:
        bullets.append(f"Suggested vehicle today is {vehicle}.")

    owned_avg = owned_perf.get("owned_average_return")
    vs_spy = owned_perf.get("owned_vs_spy")
    if owned_avg is not None and vs_spy is not None:
        if vs_spy < 0:
            bullets.append(
                f"Owned portfolio averages {owned_avg:.1f} percent since the scan, "
                f"trailing SPY by {abs(vs_spy):.1f} points."
            )
        else:
            bullets.append(
                f"Owned portfolio averages {owned_avg:.1f} percent since the scan, "
                f"holding up versus SPY by {vs_spy:.1f} points."
            )
    elif portfolio.get("portfolio_position_count", 0):
        bullets.append(f"Tracking {portfolio['portfolio_position_count']} owned positions from portfolio export.")

    avoid = owned_perf.get("avoid_adding_candidates") or []
    if avoid:
        names = ", ".join(a["ticker"] for a in avoid[:3])
        bullets.append(f"Do not add capital to {names} — owned laggards versus SPY and sector ETF.")
    elif owned_perf.get("hold_candidates"):
        holds = ", ".join(h["ticker"] for h in owned_perf["hold_candidates"][:3])
        bullets.append(f"Acceptable holds include {holds}.")

    if discipline.get("new_signal") == "prefer_new":
        bullets.append("New alert setups deserve capital over owned laggards today.")

    leaders = tech_summary.get("subsector_leaders") or []
    if leaders:
        top = leaders[0]
        bullets.append(
            f"Leading tech subsector ETF is {top.get('etf')} at {top.get('return_pct'):.1f} percent."
        )

    if warnings:
        bullets.append(f"Blocking gap: {warnings[0].replace('_', ' ')}.")
    elif "owned_positions_not_in_alert_paths" in notes:
        bullets.append(
            "Owned holdings use separate price paths — not in today's Warren alert list, but performance is tracked."
        )

    owned_sig = discipline.get("owned_signal", "ok")
    bullets.append(_action_rule_today(vehicle, owned_sig, discipline.get("new_signal", "ok")))
    return bullets[:8]


def _source_status_summary(
    *,
    readiness: str,
    warnings: list[str],
    notes: list[str],
    portfolio: dict,
    owned_perf: dict,
    lag: dict,
    coverage: dict,
    sector_summary: dict,
) -> str:
    parts = [f"Readiness: {readiness.replace('_', ' ')}."]
    if portfolio.get("portfolio_ready"):
        parts.append(
            f"Portfolio: {portfolio.get('portfolio_position_count', 0)} positions "
            f"via {portfolio.get('portfolio_source', 'unknown')}."
        )
    else:
        parts.append("Portfolio export not ready.")
    if owned_perf.get("data_status") in ("ok", "partial"):
        parts.append(
            f"Owned performance: {owned_perf.get('positions_with_price_data', 0)}/"
            f"{owned_perf.get('positions_total', 0)} with price data."
        )
    lag_st = lag.get("lag_status", "missing")
    if lag_st == "expected_lag":
        parts.append(f"Post-alert lag {lag.get('lag_days', 0)} trading days — expected.")
    elif lag_st == "stale_lag":
        parts.append(f"Post-alert lag {lag.get('lag_days', 0)} trading days — stale.")
    parts.append(f"Sector ETF coverage {coverage.get('coverage_percent', 0):.0f} percent.")
    etf = sector_summary.get("primary_sector_etf")
    if etf:
        parts.append(f"Primary sector {etf}.")
    if warnings:
        parts.append(f"{len(warnings)} blocking warning(s).")
    elif notes:
        parts.append(f"{len(notes)} informational note(s).")
    return " ".join(parts)


def _action_category_label(s: ms.SuggestedTicker) -> str:
    if "Avoid" in (s.verdict or ""):
        return "avoid adding"
    if s.portfolio_status == "Already owned":
        return "hold discipline"
    if s.portfolio_status == "Watchlist":
        return "watch"
    if s.portfolio_status == "New":
        return "new setup" if s.is_top3 else "research"
    return "research"


def _selected_stock_ideas(
    suggested: list[ms.SuggestedTicker],
    enriched_rows: list[dict[str, Any]],
    w: wl.WarrenResult,
    *,
    max_ideas: int = 5,
) -> list[dict[str, Any]]:
    """Top website-selected stock ideas for spoken audio (deterministic, no network)."""
    row_by_ticker: dict[str, dict[str, Any]] = {}
    for r in enriched_rows:
        tk = (r.get("ticker") or "").strip().upper()
        if tk:
            row_by_ticker[tk] = r

    top3_tickers = {(t.get("ticker") or "").upper() for t in w.top3_trades}
    ordered: list[ms.SuggestedTicker] = []
    seen: set[str] = set()

    def _push(s: ms.SuggestedTicker) -> None:
        tk = s.ticker.upper()
        if tk in seen:
            return
        seen.add(tk)
        ordered.append(s)

    for s in suggested:
        if s.ticker.upper() in top3_tickers:
            _push(s)
    for s in suggested:
        if s.portfolio_status in ("New", "Watchlist") and "Avoid" not in (s.verdict or ""):
            _push(s)
    for s in suggested:
        if s.portfolio_status == "Already owned":
            _push(s)

    ideas: list[dict[str, Any]] = []
    for s in ordered[:max_ideas]:
        row = row_by_ticker.get(s.ticker.upper(), {})
        reason = (s.setup or row.get("thesis") or row.get("alert_text") or "").strip()
        if len(reason) > 160:
            reason = reason[:157] + "..."

        risk_note: str | None = None
        if s.sl_disp and s.sl_disp not in ("", "—"):
            risk_note = f"Invalidation reference {s.sl_disp}"
        elif row.get("notes"):
            notes = str(row["notes"]).strip()
            if notes:
                risk_note = notes[:120]

        ideas.append({
            "ticker": s.ticker,
            "sector_etf": s.sector_etf or row.get("sector_etf") or "",
            "sector": row.get("sector") or row.get("ticker_sector") or "",
            "selection_reason": reason or None,
            "action_category": _action_category_label(s),
            "risk_note": risk_note,
            "portfolio_status": s.portfolio_status,
            "verdict": s.verdict,
            "is_top3": s.is_top3,
            "vs_sector_etf": row.get("vs_sector_etf"),
            "rank": s.rank,
        })
    return ideas


def _market_sentiment_summary(
    w: wl.WarrenResult,
    allocation_check: dict,
    sector_summary: dict,
) -> str | None:
    parts: list[str] = []
    if w.regime_one_liner:
        parts.append(w.regime_one_liner.strip())
    elif w.regime:
        parts.append(f"Market regime: {w.regime.strip()}.")

    decision = allocation_check.get("decision") or {}
    vehicle = decision.get("vehicle_label") or decision.get("vehicle") or ""
    etf_adv = sector_summary.get("etf_advantage")
    primary_name = sector_summary.get("primary_sector_name") or sector_summary.get("primary_sector_etf") or ""

    if vehicle == "Sector ETF":
        if etf_adv is not None and primary_name:
            parts.append(
                f"Broad risk tone favors sector ETF exposure; {primary_name} leads the alert basket "
                f"by {abs(etf_adv):.1f} percent."
            )
        else:
            parts.append("Broad risk tone favors sector ETF exposure over individual names today.")
    elif vehicle == "Stock Alerts":
        parts.append("Stock alerts are outperforming sector ETFs in today's scan.")
    elif vehicle == "Cash / Wait":
        parts.append("Neither stocks nor sector ETFs show a durable edge — patience is warranted.")

    if w.sizing_note:
        parts.append(w.sizing_note.strip())

    return " ".join(parts) if parts else None


def _sector_interest_summary(
    w: wl.WarrenResult,
    sector_summary: dict,
) -> str | None:
    counts = sector_summary.get("etf_alert_counts") or {}
    gaining = [etf for etf in (w.top_sectors or []) if etf]
    losing = list(w.avoid_sectors or [])

    parts: list[str] = []
    if gaining:
        names = [
            SECTOR_NAMES.get(e) or asem.ETF_DISPLAY_NAMES.get(e, e) for e in gaining[:3]
        ]
        parts.append(f"Sectors gaining interest: {', '.join(names)}.")
    if losing:
        lose_names = [
            SECTOR_NAMES.get(e) or asem.ETF_DISPLAY_NAMES.get(e, e) for e in losing[:2]
        ]
        parts.append(f"Sectors losing interest: {', '.join(lose_names)}.")
    if counts:
        top_etf = max(counts, key=counts.get)
        top_name = SECTOR_NAMES.get(top_etf) or asem.ETF_DISPLAY_NAMES.get(top_etf, top_etf)
        parts.append(
            f"Most alert activity clusters in {top_name} ({top_etf}) with {counts[top_etf]} names."
        )
    return " ".join(parts) if parts else None


def _wall_street_theme_summary(w: wl.WarrenResult, sector_summary: dict) -> str | None:
    themes: list[str] = []
    primary = sector_summary.get("primary_sector_etf") or ""
    if primary and w.sector_reasons.get(primary):
        themes.append(w.sector_reasons[primary].strip())
    for etf in (w.top_sectors or [])[:3]:
        if etf == primary:
            continue
        reason = w.sector_reasons.get(etf)
        if reason:
            label = SECTOR_NAMES.get(etf) or etf
            themes.append(f"{label}: {reason.strip()}")
    return " ".join(themes[:2]) if themes else None


def _sector_rankings(w: wl.WarrenResult, sector_summary: dict) -> list[dict[str, Any]]:
    counts = sector_summary.get("etf_alert_counts") or {}
    rankings: list[dict[str, Any]] = []
    for etf, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        trend = "gaining"
        if etf in (w.avoid_sectors or []):
            trend = "losing"
        elif etf not in (w.top_sectors or []):
            trend = "neutral"
        rankings.append({
            "sector_etf": etf,
            "sector_name": SECTOR_NAMES.get(etf) or asem.ETF_DISPLAY_NAMES.get(etf, etf),
            "alert_count": cnt,
            "trend": trend,
            "warren_rank": (w.top_sectors or []).index(etf) + 1 if etf in (w.top_sectors or []) else None,
        })
    return rankings


def build_atlas_morning_audio_source(
    pa_data: dict | None = None,
    *,
    active_tickers: set[str] | None = None,
    watchlist_tickers: set[str] | None = None,
    active_positions: list[dict] | None = None,
    warren: wl.WarrenResult | None = None,
    auto_export_portfolio: bool = True,
    industry_cache: dict[str, str] | None = None,
    fetch_benchmarks: bool = True,
    owned_price_fetcher=None,
    owned_bm_fetcher=None,
) -> dict[str, Any]:
    """
    Build the full deterministic audio-source payload.

    Stable v1 top-level keys (for GET /api/atlas/morning-audio-source):
        readiness, warnings, freshness, sector_etf_coverage,
        sector_etf_allocation_check, sector_strength_summary,
        portfolio_discipline_summary, technology_subsector_summary,
        suggested_audio_bullets

    v2 additions: contract_version, generated_at, source_status_summary,
        portfolio_*, post_alert lag fields, enriched technology subsector benchmarks.

    v3 additions: owned_position_performance, notes (informational, non-blocking).

    v4 additions: market_sentiment_summary, sector_interest_summary,
        wall_street_theme_summary, sector_rankings, selected_stock_ideas,
        selected_stock_ideas_top, selected_stock_ideas_count.
    """
    active_tickers = active_tickers or set()
    watchlist_tickers = watchlist_tickers or set()
    w = warren or wl.get_latest_warren()
    pos_lookup = _positions_lookup(active_positions)

    pa_rows = _rows_from_pa_data(pa_data, positions_lookup=pos_lookup)
    warren_ctx = _load_warren_context_by_date()
    enriched = asem.enrich_alert_rows(
        pa_rows,
        warren_ctx,
        positions_lookup=pos_lookup,
        industry_cache=industry_cache or {},
    )
    coverage = asem.compute_sector_etf_coverage(enriched)

    enriched_pa = dict(pa_data or {})
    if enriched and pa_data:
        orig_series = pa_data.get("series") or []
        enriched_series = []
        enrich_by_key = {
            (r.get("ticker"), r.get("scan_date")): r for r in enriched
        }
        for s in orig_series:
            key = (s.get("ticker"), s.get("scan_date"))
            er = enrich_by_key.get(key, {})
            merged = dict(s)
            if er.get("sector_etf") and not (s.get("sector_etf") or "").strip():
                merged["sector_etf"] = er["sector_etf"]
            enriched_series.append(merged)
        enriched_pa["series"] = enriched_series

    allocation_check = ms.compute_etf_allocation_check(enriched_pa if enriched_pa else {"series": []})

    latest_pa_date = _latest_post_alert_date(enriched)
    lag = _classify_post_alert_lag(
        latest_scan_date=w.date,
        post_alert_latest_date=latest_pa_date,
    )

    portfolio = ape.resolve_portfolio_readiness(
        active_positions=active_positions,
        auto_export=auto_export_portfolio,
    )

    benchmark_start = _parse_date(latest_pa_date) or _parse_date(w.date) or (date.today() - timedelta(days=10))
    benchmark_returns = (
        ab.fetch_subsector_benchmark_returns(benchmark_start)
        if fetch_benchmarks else {}
    )

    freshness = {
        "latest_warren_run_date": w.date or None,
        "latest_warren_freshness": w.freshness,
        "latest_scan_date": w.date or None,
        "latest_post_alert_performance_date": latest_pa_date,
        "post_alert_latest_date": lag["post_alert_latest_date"],
        "lag_days": lag["lag_days"],
        "lag_status": lag["lag_status"],
        "source_is_stale": (
            w.freshness in ("STALE", "NONE", "FAILED")
            or _is_stale_date(w.date, WARREN_STALE_DAYS)
            or lag["lag_status"] == "stale_lag"
        ),
    }

    sector_summary = _sector_strength_summary(w, allocation_check, enriched)
    suggested = ms.classify_suggested_tickers(w, active_tickers, watchlist_tickers)
    discipline = _portfolio_discipline_summary(
        allocation_check=allocation_check,
        active_positions=active_positions,
        w=w,
        active_tickers=active_tickers,
        watchlist_tickers=watchlist_tickers,
    )
    tech_summary = asem.technology_subsector_summary(
        enriched,
        benchmark_returns=benchmark_returns if fetch_benchmarks else None,
    )

    alert_tickers = {
        (s.get("ticker") or "").strip().upper()
        for s in (pa_data or {}).get("series") or []
        if s.get("ticker")
    }
    owned_tickers = set(portfolio.get("owned_tickers_detected") or [])

    owned_perf = aop.compute_owned_position_performance(
        baseline_date=w.date or latest_pa_date,
        primary_sector_etf=sector_summary.get("primary_sector_etf") or "",
        alert_tickers=alert_tickers,
        portfolio_path=portfolio.get("portfolio_export_path"),
        price_fetcher=owned_price_fetcher,
        bm_fetcher=owned_bm_fetcher,
    )

    warnings, notes = _build_warnings_and_notes(
        w=w,
        pa_rows=enriched,
        coverage=coverage,
        allocation_check=allocation_check,
        portfolio_ready=portfolio.get("portfolio_ready", False),
        lag_status=lag["lag_status"],
        owned_perf=owned_perf,
        owned_tickers=owned_tickers,
        alert_tickers=alert_tickers,
    )
    readiness = _overall_readiness(warnings, coverage)

    selected_ideas = _selected_stock_ideas(suggested, enriched, w, max_ideas=5)
    market_sentiment = _market_sentiment_summary(w, allocation_check, sector_summary)
    sector_interest = _sector_interest_summary(w, sector_summary)
    wall_street_theme = _wall_street_theme_summary(w, sector_summary)
    sector_rankings = _sector_rankings(w, sector_summary)

    bullets = _suggested_audio_bullets(
        readiness=readiness,
        sector_summary=sector_summary,
        discipline=discipline,
        w=w,
        portfolio=portfolio,
        owned_perf=owned_perf,
        tech_summary=tech_summary,
        lag=lag,
        warnings=warnings,
        notes=notes,
        coverage=coverage,
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status_summary = _source_status_summary(
        readiness=readiness,
        warnings=warnings,
        notes=notes,
        portfolio=portfolio,
        owned_perf=owned_perf,
        lag=lag,
        coverage=coverage,
        sector_summary=sector_summary,
    )

    return {
        "readiness": readiness,
        "warnings": warnings,
        "notes": notes,
        "freshness": freshness,
        "sector_etf_coverage": coverage,
        "sector_etf_allocation_check": {
            "primary_etf": allocation_check.get("primary_etf"),
            "primary_ret": allocation_check.get("primary_ret"),
            "etf_ret": allocation_check.get("etf_ret"),
            "spy_ret": allocation_check.get("spy_ret"),
            "qqq_ret": allocation_check.get("qqq_ret"),
            "mag7_ret": allocation_check.get("mag7_ret"),
            "diff_stock_vs_etf": allocation_check.get("diff_stock_vs_etf"),
            "decision": allocation_check.get("decision"),
            "allocation": allocation_check.get("allocation"),
            "day_window": allocation_check.get("day_window"),
            "n_stocks": allocation_check.get("n_stocks"),
            "n_new": allocation_check.get("n_new"),
            "n_owned": allocation_check.get("n_owned"),
            "error": allocation_check.get("error"),
        },
        "sector_strength_summary": sector_summary,
        "portfolio_discipline_summary": discipline,
        "technology_subsector_summary": tech_summary,
        "suggested_audio_bullets": bullets,
        "generated_at": generated_at,
        "contract_version": CONTRACT_VERSION,
        "source_status_summary": status_summary,
        "portfolio_source": portfolio.get("portfolio_source"),
        "portfolio_position_count": portfolio.get("portfolio_position_count"),
        "portfolio_export_exists": portfolio.get("portfolio_export_exists"),
        "portfolio_export_path": portfolio.get("portfolio_export_path"),
        "portfolio_last_modified": portfolio.get("portfolio_last_modified"),
        "owned_tickers_detected": portfolio.get("owned_tickers_detected"),
        "portfolio_ready": portfolio.get("portfolio_ready"),
        "post_alert_latest_date": lag["post_alert_latest_date"],
        "latest_scan_date": lag["latest_scan_date"],
        "lag_days": lag["lag_days"],
        "lag_status": lag["lag_status"],
        "owned_position_performance": owned_perf,
        "market_sentiment_summary": market_sentiment,
        "sector_interest_summary": sector_interest,
        "wall_street_theme_summary": wall_street_theme,
        "sector_rankings": sector_rankings,
        "selected_stock_ideas": selected_ideas,
        "selected_stock_ideas_top": selected_ideas[:5],
        "selected_stock_ideas_count": len(selected_ideas),
    }
