"""
Theme Catalyst Radar — event-driven theme detection for AtlasInvestor.

Surfaces themes driven by upcoming catalysts (e.g. SpaceX IPO) via public proxy baskets.
Does NOT generate audio or place trades.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import atlas_theme_catalyst_config as atcfg
import atlas_theme_catalyst_warren as atw
import morning_status as ms
import theme_registry as tr
import warren_loader as wl

CONTRACT_VERSION = "atlas_theme_catalyst_radar_v3"

PriceFetcher = Callable[[str], Any]
BmFetcher = Callable[[str], Any]

THEME_PHASES = ("early", "confirmed", "crowded", "fading", "expired")

MANUAL_REVIEW_MAX_DAYS = 7
NEAR_TERM_TRADING_DAYS = 10
NEAR_TERM_VERIFY_MAX_DAYS = 3


def _parse_date(ds: str | None) -> date | None:
    if not ds:
        return None
    try:
        return datetime.strptime(ds[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _business_days_until(start: date, end: date) -> int:
    """Trading days strictly after start through end (inclusive of end if weekday)."""
    if end <= start:
        return 0
    days = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def _return_over_days(hist, days: int) -> float | None:
    if hist is None or getattr(hist, "empty", True) or len(hist) < days + 1:
        return None
    p_end = float(hist["Close"].iloc[-1])
    p_start = float(hist["Close"].iloc[-(days + 1)])
    if p_start <= 0:
        return None
    return round((p_end - p_start) / p_start * 100, 2)


def _sma(hist, window: int) -> float | None:
    if hist is None or getattr(hist, "empty", True):
        return None
    w = min(len(hist), window)
    if w < 5:
        return None
    return float(hist["Close"].tail(w).mean())


def _volume_ratio(hist) -> float | None:
    if hist is None or getattr(hist, "empty", True):
        return None
    if "Volume" not in hist.columns:
        return None
    w = min(len(hist), 20)
    if w < 5:
        return None
    recent = float(hist["Volume"].iloc[-1])
    avg = float(hist["Volume"].tail(w).mean())
    if avg <= 0:
        return None
    return round(recent / avg, 2)


def compute_proxy_metrics(
    ticker: str,
    *,
    relevant_etf: str = "",
    price_fetcher: PriceFetcher | None = None,
    bm_fetcher: BmFetcher | None = None,
    warren_tickers: set[str] | None = None,
    selected_tickers: set[str] | None = None,
) -> dict[str, Any]:
    """Compute proxy basket metrics for one ticker."""
    fetch_p = price_fetcher or ms._fetch_history_cached
    fetch_b = bm_fetcher or ms._fetch_bm_hist
    tk = ticker.strip().upper()
    hist = fetch_p(tk)
    spy = fetch_b("SPY")
    qqq = fetch_b("QQQ")
    etf_sym = (relevant_etf or "SPY").upper()
    etf_hist = fetch_b(etf_sym)

    ret_1d = _return_over_days(hist, 1)
    ret_5d = _return_over_days(hist, 5)
    ret_10d = _return_over_days(hist, 10)
    spy_5d = _return_over_days(spy, 5)
    qqq_5d = _return_over_days(qqq, 5)
    etf_5d = _return_over_days(etf_hist, 5)

    close = float(hist["Close"].iloc[-1]) if hist is not None and not hist.empty else None
    sma20 = _sma(hist, 20)
    sma50 = _sma(hist, 50)

    return {
        "ticker": tk,
        "return_1d": ret_1d,
        "return_5d": ret_5d,
        "return_10d": ret_10d,
        "rs_vs_spy": round(ret_5d - spy_5d, 2) if ret_5d is not None and spy_5d is not None else None,
        "rs_vs_qqq": round(ret_5d - qqq_5d, 2) if ret_5d is not None and qqq_5d is not None else None,
        "rs_vs_etf": round(ret_5d - etf_5d, 2) if ret_5d is not None and etf_5d is not None else None,
        "relevant_etf": etf_sym,
        "volume_ratio_20d": _volume_ratio(hist),
        "above_20ma": bool(close and sma20 and close > sma20),
        "above_50ma": bool(close and sma50 and close > sma50),
        "in_warren_alerts": tk in (warren_tickers or set()),
        "in_selected_stocks": tk in (selected_tickers or set()),
        "data_status": "ok" if ret_5d is not None else "missing",
    }


def _outperform_count(proxies: list[dict]) -> int:
    return sum(
        1 for p in proxies
        if (p.get("rs_vs_spy") or 0) > 0 or (p.get("rs_vs_qqq") or 0) > 0
    )


def _underperform_count(proxies: list[dict]) -> int:
    return sum(
        1 for p in proxies
        if (p.get("rs_vs_spy") or 0) < 0 and (p.get("rs_vs_qqq") or 0) < 0
    )


def _volume_support_count(proxies: list[dict]) -> int:
    return sum(1 for p in proxies if (p.get("volume_ratio_20d") or 0) > 1.0)


def _classify_theme_phase(proxies: list[dict]) -> str:
    """
    early — catalyst exists, proxy confirmation mixed
    confirmed — >=3 proxies beat SPY/QQQ with volume support
    crowded — strong confirmation plus extension or overheated volume
    fading — basket underperforming SPY/QQQ
    """
    if not proxies:
        return "early"

    total = len(proxies)
    outperform = _outperform_count(proxies)
    underperform = _underperform_count(proxies)
    vol_support = _volume_support_count(proxies)

    if underperform >= max(3, (total + 1) // 2):
        return "fading"

    if outperform >= 3 and vol_support >= 2:
        extended = sum(
            1 for p in proxies
            if p.get("above_20ma") and p.get("above_50ma")
            and (p.get("return_10d") or 0) > 8
        )
        overheated = sum(1 for p in proxies if (p.get("volume_ratio_20d") or 0) > 1.5)
        if extended >= 3 or overheated >= 4:
            return "crowded"
        return "confirmed"

    return "early"


def _resolve_catalyst_status(
    cat: dict[str, Any],
    phase: str,
    as_of: date,
) -> str:
    cat_date = _parse_date(cat.get("catalyst_date"))
    if cat_date and cat_date < as_of:
        if phase in ("confirmed", "crowded"):
            return "active_post_event"
        return "expired"
    return cat.get("status") or "upcoming"


def _source_metadata(cat: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_name": cat.get("source_name") or cat.get("source") or "unknown",
        "source_url": cat.get("source_url") or "",
        "date_added": cat.get("date_added"),
        "last_verified": cat.get("last_verified"),
        "verification_status": cat.get("verification_status") or "pending",
        "confidence": cat.get("confidence") or "medium",
        "manual_review_required": bool(cat.get("manual_review_required", True)),
        "source_freshness": cat.get("source_freshness") or "manual",
    }


def _freshness_warnings(cat: dict[str, Any], as_of: date) -> list[str]:
    warnings: list[str] = []
    meta = _source_metadata(cat)
    last_verified = _parse_date(meta.get("last_verified"))
    cat_date = _parse_date(cat.get("catalyst_date"))

    if meta["source_freshness"] == "manual" and last_verified:
        if (as_of - last_verified).days > MANUAL_REVIEW_MAX_DAYS:
            warnings.append("catalyst_needs_review")

    if cat_date and cat_date >= as_of and last_verified:
        trading_days = _business_days_until(as_of, cat_date)
        if 0 < trading_days <= NEAR_TERM_TRADING_DAYS:
            if (as_of - last_verified).days > NEAR_TERM_VERIFY_MAX_DAYS:
                warnings.append("near_term_catalyst_needs_review")

    return warnings


def _rs_volume_confirmation(proxies: list[dict]) -> dict[str, Any]:
    with_rs = sum(1 for p in proxies if (p.get("rs_vs_spy") or 0) > 0)
    with_vol = _volume_support_count(proxies)
    total = len(proxies) or 1
    return {
        "relative_strength_confirmed": with_rs >= 3,
        "volume_confirmed": with_vol >= 2,
        "proxies_beating_spy": with_rs,
        "proxies_elevated_volume": with_vol,
        "confirmation_score": round((with_rs + with_vol) / (2 * total) * 100, 1),
    }


def _compute_theme_scores(
    proxies: list[dict],
    cat: dict[str, Any],
    as_of: date,
) -> dict[str, Any]:
    total = len(proxies) or 1
    outperform = _outperform_count(proxies)
    rs_vals = [p.get("rs_vs_spy") for p in proxies if p.get("rs_vs_spy") is not None]
    avg_rs = sum(rs_vals) / len(rs_vals) if rs_vals else 0.0
    vol_ratios = [p.get("volume_ratio_20d") for p in proxies if p.get("volume_ratio_20d") is not None]
    avg_vol = sum(vol_ratios) / len(vol_ratios) if vol_ratios else 1.0

    proxy_rs_score = min(100.0, round(outperform / total * 70 + max(avg_rs, 0) * 3, 1))
    vol_score = min(100.0, round(_volume_support_count(proxies) / total * 100, 1))
    selected_score = round(
        sum(1 for p in proxies if p.get("in_selected_stocks")) / total * 100, 1,
    )
    warren_score = round(
        sum(1 for p in proxies if p.get("in_warren_alerts")) / total * 100, 1,
    )

    cat_date = _parse_date(cat.get("catalyst_date"))
    if cat_date and cat_date >= as_of:
        td = _business_days_until(as_of, cat_date)
        proximity = min(100.0, max(20.0, 100.0 - td * 8))
    elif cat_date and cat_date < as_of:
        proximity = 10.0
    else:
        proximity = 25.0

    extended = sum(
        1 for p in proxies
        if p.get("above_20ma") and (p.get("return_10d") or 0) > 10
    )
    crowding_score = min(100.0, round(extended / total * 60 + max(avg_vol - 1, 0) * 40, 1))

    overall = round(
        proxy_rs_score * 0.30
        + vol_score * 0.20
        + selected_score * 0.10
        + warren_score * 0.10
        + proximity * 0.15
        + (100.0 - crowding_score) * 0.15,
        1,
    )

    return {
        "proxy_relative_strength_score": proxy_rs_score,
        "volume_confirmation_score": vol_score,
        "selected_stock_overlap_score": selected_score,
        "warren_alert_overlap_score": warren_score,
        "event_proximity_score": round(proximity, 1),
        "crowding_score": crowding_score,
        "overall_theme_score": overall,
    }


def _build_theme_audio(
    *,
    theme: str,
    phase: str,
    cat: dict[str, Any],
    scores: dict[str, Any],
    confirmation: dict[str, Any],
    company: str,
) -> dict[str, Any]:
    theme_label = theme.replace("_", " ")
    event = cat.get("event_type") or "catalyst"
    cat_date = cat.get("catalyst_date") or "TBD"
    outperform = confirmation.get("proxies_beating_spy", 0)

    if phase == "confirmed":
        summary = (
            f"{theme_label.title()} is a confirmed theme tied to the {company} {event} "
            f"on {cat_date}. {outperform} proxies beat SPY with volume support."
        )
        promotion = (
            "Theme is promoted for audio when overall score exceeds 60 and "
            "at least 3 proxies beat SPY or QQQ with elevated volume."
        )
        invalidation = (
            "Invalidate if fewer than 2 proxies beat SPY over 5 days or volume support fades."
        )
        watch_rules = [
            "Favor leaders with RS and volume — avoid laggards in the basket.",
            "Do not treat confirmation as a buy signal without entry discipline.",
        ]
    elif phase == "crowded":
        summary = (
            f"{theme_label.title()} is crowded — sympathy proxies are extended with overheated volume. "
            f"Treat {company} {event} as known news."
        )
        promotion = "Do not promote further unless a pullback resets RS without breaking trend."
        invalidation = "Invalidate chase setups when crowding score exceeds 70."
        watch_rules = [
            "Avoid new chase entries in extended proxy names.",
            "Watch for fade risk after catalyst date.",
        ]
    elif phase == "fading":
        summary = (
            f"{theme_label.title()} is fading — the public proxy basket is underperforming SPY and QQQ "
            f"despite the {company} {event} overhang."
        )
        promotion = "Promote only if 3+ proxies reclaim RS versus SPY with volume."
        invalidation = "Theme remains invalidated while majority of proxies lag benchmarks."
        watch_rules = [
            "Do not allocate capital to fading sympathy names.",
            "Wait for RS repair before reconsidering the theme.",
        ]
    elif phase == "expired":
        summary = (
            f"{theme_label.title()} catalyst date has passed without active confirmation. "
            f"Treat as expired unless proxies re-accelerate."
        )
        promotion = "No promotion — catalyst window closed."
        invalidation = "Theme inactive post-event without RS confirmation."
        watch_rules = ["Archive theme unless post-event RS confirms a new leg."]
    else:
        summary = (
            f"{theme_label.title()} is an early watch theme tied to the {company} {event} window "
            f"on {cat_date}. The public proxy basket is mixed, so do not chase yet."
        )
        promotion = (
            "Promote the theme only if at least 3 proxies start beating SPY or QQQ with volume support."
        )
        invalidation = (
            "Invalidate if proxy basket fades below SPY for 5 days or catalyst passes without confirmation."
        )
        watch_rules = [
            "Watch proxy basket — no capital until RS and volume confirm.",
            "Do not chase on headline alone.",
        ]

    bullets = [
        summary,
        f"Overall theme score: {scores.get('overall_theme_score', 0):.0f} — phase {phase}.",
        promotion,
    ]
    if cat.get("manual_review_required"):
        bullets.append("Source requires manual verification before promoting confidence.")

    return {
        "theme_audio_summary": summary,
        "top_theme_bullets": bullets[:4],
        "theme_watch_rules": watch_rules,
        "promotion_trigger": promotion,
        "invalidation_trigger": invalidation,
    }


def _etf_basket_summary(
    etf_symbols: list[str],
    *,
    bm_fetcher: BmFetcher | None = None,
) -> list[dict[str, Any]]:
    fetch_b = bm_fetcher or ms._fetch_bm_hist
    rows: list[dict[str, Any]] = []
    for sym in etf_symbols:
        hist = fetch_b(sym)
        rows.append({
            "etf": sym,
            "return_1d": _return_over_days(hist, 1),
            "return_5d": _return_over_days(hist, 5),
            "return_10d": _return_over_days(hist, 10),
        })
    return rows


def _collect_warren_tickers(w: wl.WarrenResult) -> set[str]:
    tickers: set[str] = set()
    tickers.update(w.all_tickers or [])
    tickers.update(w.actionable or [])
    tickers.update(w.watch_tickers or [])
    for t in w.top3_trades or []:
        if isinstance(t, dict) and t.get("ticker"):
            tickers.add(t["ticker"].upper())
        elif isinstance(t, str):
            tickers.add(t.upper())
    return {t.upper() for t in tickers if t}


def _collect_selected_stock_tickers(
    w: wl.WarrenResult,
    *,
    theme_scores: dict | None = None,
) -> set[str]:
    selected = _collect_warren_tickers(w)
    scores = theme_scores or tr.load_theme_scores()
    for _theme, data in (scores.get("themes") or {}).items():
        if not isinstance(data, dict):
            continue
        for key in ("selected", "passing", "pass", "candidates"):
            for item in data.get(key) or []:
                if isinstance(item, str):
                    selected.add(item.upper())
                elif isinstance(item, dict) and item.get("ticker"):
                    selected.add(str(item["ticker"]).upper())
    return selected


def _theme_in_selected(theme: str, selected: set[str], proxies: list[dict]) -> bool:
    if any(p.get("ticker") in selected for p in proxies):
        return True
    theme_lower = theme.lower()
    return any(theme_lower in t.lower() for t in selected)


def _missed_theme_audit(
    *,
    theme: str,
    proxies: list[dict],
    selected_tickers: set[str],
    catalyst_active: bool,
) -> bool:
    if not catalyst_active:
        return False
    if _outperform_count(proxies) < 3:
        return False
    return not _theme_in_selected(theme, selected_tickers, proxies)


def _build_warnings(
    catalysts: list[dict],
    config_loaded: bool,
    freshness_warnings: list[str],
    *,
    warren_payload: dict[str, Any] | None = None,
) -> list[str]:
    warnings: list[str] = []
    if not config_loaded and not (warren_payload and warren_payload.get("catalysts")):
        warnings.append("catalyst_config_missing")
    active = [c for c in catalysts if c.get("status") not in ("expired",)]
    if not active:
        if catalysts:
            warnings.append("all_catalysts_expired")
        elif not config_loaded and not (warren_payload and warren_payload.get("catalysts")):
            warnings.append("no_catalysts_configured")
    for w in freshness_warnings:
        if w not in warnings:
            warnings.append(w)
    if warren_payload:
        if not warren_payload.get("loaded"):
            warnings.append("warren_theme_output_missing")
        elif not warren_payload.get("parse_ok"):
            warnings.append("warren_theme_output_invalid")
        elif warren_payload.get("stale"):
            warnings.append("warren_theme_output_stale")
    return warnings


def _overall_readiness(warnings: list[str], catalysts: list[dict]) -> str:
    if "catalyst_config_missing" in warnings or "no_catalysts_configured" in warnings:
        return "not_ready"
    active = [c for c in catalysts if c.get("status") not in ("expired",)]
    if not active and catalysts:
        return "ready_with_warnings"
    blocking = {
        w for w in warnings
        if w not in ("catalyst_needs_review", "near_term_catalyst_needs_review", "all_catalysts_expired")
    }
    if blocking:
        return "ready_with_warnings"
    if warnings:
        return "ready_with_warnings"
    return "ready"


def _suggested_audio_bullets(
    *,
    readiness: str,
    theme_summaries: dict[str, dict],
    missed: list[dict],
    warnings: list[str],
) -> list[str]:
    if readiness == "not_ready":
        return ["Theme Catalyst Radar is not ready — configure catalyst sources first."]

    bullets: list[str] = []
    ranked = sorted(
        theme_summaries.values(),
        key=lambda t: t.get("theme_scores", {}).get("overall_theme_score", 0),
        reverse=True,
    )
    for ts in ranked[:2]:
        audio = ts.get("theme_audio_summary")
        if audio:
            bullets.append(audio)

    for m in missed[:1]:
        bullets.append(
            f"Missed theme: {m.get('theme')} — proxies outperforming but absent from selected stocks."
        )
    for w in warnings[:2]:
        bullets.append(f"Review required: {w.replace('_', ' ')}.")

    if not bullets:
        bullets.append("No major theme catalysts require action today.")
    return bullets[:6]


def build_theme_catalyst_radar(
    *,
    warren: wl.WarrenResult | None = None,
    config_path: str | None = None,
    warren_theme_path: str | None = None,
    price_fetcher: PriceFetcher | None = None,
    bm_fetcher: BmFetcher | None = None,
    theme_scores: dict | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Build full Theme Catalyst Radar payload."""
    as_of = as_of_date or date.today()
    w = warren or wl.get_latest_warren()
    cfg = atcfg.load_config(config_path)
    config_loaded = bool(cfg.get("catalysts"))
    manual_catalysts = list(cfg.get("catalysts") or [])

    warren_payload = atw.load_warren_theme_catalyst(warren_theme_path)
    raw_catalysts, merge_stats = atw.merge_catalyst_sources(manual_catalysts, warren_payload)

    warren_tickers = _collect_warren_tickers(w)
    selected_tickers = _collect_selected_stock_tickers(w, theme_scores=theme_scores)

    mapping = cfg.get("private_company_mappings") or {}
    catalysts_out: list[dict[str, Any]] = []
    theme_summaries: dict[str, dict[str, Any]] = {}
    proxy_baskets: dict[str, list[dict]] = {}
    missed_candidates: list[dict[str, Any]] = []
    all_freshness_warnings: list[str] = []
    expired_themes: list[str] = []

    primary_etf_default = mapping.get("SpaceX", {}).get("primary_etf") or "ARKX"

    for cat in raw_catalysts:
        company = cat.get("private_company", "")
        company_map = mapping.get(company, {})
        proxy_syms = cat.get("proxy_tickers") or company_map.get("proxy_tickers") or []
        etf_syms = cat.get("proxy_etfs") or company_map.get("proxy_etfs") or []
        sector_etfs = cat.get("sector_etf_basket") or etf_syms[:4]
        theme = cat.get("theme") or "unknown"
        rel_etf = company_map.get("primary_etf") or (etf_syms[0] if etf_syms else primary_etf_default)

        proxies = [
            compute_proxy_metrics(
                sym,
                relevant_etf=rel_etf,
                price_fetcher=price_fetcher,
                bm_fetcher=bm_fetcher,
                warren_tickers=warren_tickers,
                selected_tickers=selected_tickers,
            )
            for sym in proxy_syms
        ]
        proxy_baskets[theme] = proxies

        phase = _classify_theme_phase(proxies)
        lifecycle_status = _resolve_catalyst_status(cat, phase, as_of)
        if lifecycle_status == "expired":
            phase = "expired"
            expired_themes.append(theme)

        confirmation = _rs_volume_confirmation(proxies)
        scores = _compute_theme_scores(proxies, cat, as_of)
        etf_basket = _etf_basket_summary(sector_etfs, bm_fetcher=bm_fetcher)
        has_price = any(p.get("data_status") == "ok" for p in proxies)
        catalyst_active = lifecycle_status not in ("expired",)

        cat_freshness = _freshness_warnings(cat, as_of)
        all_freshness_warnings.extend(cat_freshness)

        missed = _missed_theme_audit(
            theme=theme,
            proxies=proxies,
            selected_tickers=selected_tickers,
            catalyst_active=catalyst_active,
        )

        conf = cat.get("confidence") or "medium"
        if not has_price:
            conf = "low"

        source_meta = _source_metadata(cat)
        audio = _build_theme_audio(
            theme=theme,
            phase=phase,
            cat={**cat, **source_meta},
            scores=scores,
            confirmation=confirmation,
            company=company,
        )

        data_source = cat.get("data_source")
        if cat.get("warren_enriched"):
            data_source = "merged"
        elif cat.get("_warren_source"):
            data_source = data_source or "warren"
        else:
            data_source = data_source or "manual"

        catalyst_entry = {
            "id": cat.get("id"),
            "private_company": company,
            "event_type": cat.get("event_type"),
            "theme": theme,
            "themes_affected": cat.get("themes_affected") or company_map.get("themes") or [theme],
            "catalyst_label": cat.get("catalyst_label") or cat.get("event_type"),
            "catalyst_date": cat.get("catalyst_date"),
            "status": lifecycle_status,
            "confidence": conf,
            "source": cat.get("source"),
            "data_source": data_source,
            "warren_enriched": bool(cat.get("warren_enriched")),
            "warren_suggested_watch_status": cat.get("warren_suggested_watch_status"),
            "why_it_matters": cat.get("why_it_matters"),
            "wall_street_interest_summary": cat.get("wall_street_interest_summary"),
            "confirmation_triggers": cat.get("confirmation_triggers") or [],
            "invalidation_triggers": cat.get("invalidation_triggers") or [],
            "risk_notes": cat.get("risk_notes") or [],
            **source_meta,
            "freshness_warnings": cat_freshness,
            "proxy_tickers": proxy_syms,
            "proxy_etfs": etf_syms,
            "sector_etf_basket": etf_basket,
            "theme_phase": phase,
            "theme_scores": scores,
            "relative_strength_confirmation": confirmation["relative_strength_confirmed"],
            "volume_confirmation": confirmation["volume_confirmed"],
            "confirmation_detail": confirmation,
            "action_context": cat.get("action_context") or "Monitor — no chase without confirmation",
            "missed_theme_candidate": missed,
            **audio,
            "_has_price_data": has_price,
        }
        catalysts_out.append(catalyst_entry)

        if missed:
            missed_candidates.append({
                "theme": theme,
                "private_company": company,
                "catalyst_date": cat.get("catalyst_date"),
                "outperforming_proxies": _outperform_count(proxies),
                "reason": "Proxies outperform SPY/QQQ but theme absent from selected stocks",
            })

        theme_summaries[theme] = {
            "theme": theme,
            "theme_phase": phase,
            "status": lifecycle_status,
            "catalyst_count": 1,
            "proxy_count": len(proxies),
            "proxies_with_data": sum(1 for p in proxies if p.get("data_status") == "ok"),
            "avg_return_5d": _avg([p.get("return_5d") for p in proxies]),
            "avg_rs_vs_spy": _avg([p.get("rs_vs_spy") for p in proxies]),
            "confirmation": confirmation,
            "theme_scores": scores,
            "missed_theme_candidate": missed,
            "freshness_warnings": cat_freshness,
            **audio,
        }

    active_catalysts = [c for c in catalysts_out if c.get("status") != "expired"]
    top_confirmed = [c for c in active_catalysts if c.get("theme_phase") == "confirmed"]
    early_watch = [c for c in active_catalysts if c.get("theme_phase") == "early"]
    crowded = [c for c in active_catalysts if c.get("theme_phase") in ("crowded", "fading")]

    warnings = _build_warnings(
        catalysts_out, config_loaded, all_freshness_warnings, warren_payload=warren_payload,
    )
    readiness = _overall_readiness(warnings, catalysts_out)
    bullets = _suggested_audio_bullets(
        readiness=readiness,
        theme_summaries=theme_summaries,
        missed=missed_candidates,
        warnings=warnings,
    )

    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of_date": as_of.strftime("%Y-%m-%d"),
        "readiness": readiness,
        "warnings": warnings,
        "catalysts": catalysts_out,
        "theme_summaries": theme_summaries,
        "proxy_baskets": proxy_baskets,
        "top_confirmed_themes": [c["theme"] for c in top_confirmed],
        "early_watch_themes": [c["theme"] for c in early_watch],
        "crowded_or_extended_themes": [c["theme"] for c in crowded],
        "expired_themes": expired_themes,
        "missed_theme_candidates": missed_candidates,
        "suggested_audio_bullets": bullets,
        "private_company_mappings": mapping,
        "warren_scan_date": w.date,
        "warren_theme_source_status": (
            "ok" if warren_payload.get("loaded") and warren_payload.get("parse_ok")
            else "missing" if not warren_payload.get("loaded")
            else "invalid"
        ),
        "warren_theme_output_path": warren_payload.get("path"),
        "warren_theme_generated_at": warren_payload.get("generated_at"),
        "warren_theme_catalyst_count": merge_stats.get("warren_catalyst_count", 0),
        "warren_theme_stale": bool(warren_payload.get("stale")),
        "manual_catalyst_count": merge_stats.get("manual_catalyst_count", 0),
        "merged_catalyst_count": merge_stats.get("merged_catalyst_count", 0),
        "warren_discovered_themes": merge_stats.get("warren_new_themes") or [],
        "warren_merge_stats": merge_stats,
    }


def _avg(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)
