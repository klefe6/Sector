"""
Deterministic sector and technology subsector → ETF mapping for AtlasInvestor.

Existing sector_etf values from Warren AI always win. Inference runs only when
sector_etf is missing, using setup text, sector names, and industry hints.
"""
from __future__ import annotations

import re
from typing import Any

# Broad GICS-style sector names → SPDR sector ETF
BROAD_SECTOR_ETF: dict[str, str] = {
    "technology": "XLK",
    "tech": "XLK",
    "information technology": "XLK",
    "financials": "XLF",
    "financial": "XLF",
    "health care": "XLV",
    "healthcare": "XLV",
    "energy": "XLE",
    "industrials": "XLI",
    "industrial": "XLI",
    "consumer discretionary": "XLY",
    "consumer discr": "XLY",
    "consumer staples": "XLP",
    "utilities": "XLU",
    "real estate": "XLRE",
    "materials": "XLB",
    "communication services": "XLC",
    "communications": "XLC",
}

# Technology subsector / theme keywords → ETF (first symbol is preferred)
TECH_SUBSECTOR_RULES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("semiconductor", "semiconductors", "chip", "chips", "semi "), ("SMH", "SOXX")),
    (("cybersecurity", "cyber security", "security software", "cyber "), ("CIBR",)),
    (("software", "saas", "application software"), ("IGV",)),
    (("cloud", "cloud computing", "cloud infrastructure"), ("WCLD", "SKYY")),
    (("data center", "data centers", "ai infrastructure", "ai infra", "datacenter"), ("SMH", "XLK")),
    (("hardware", "computer hardware"), ("XLK",)),
]

# SPDR ticker → display name (subset used by Atlas)
ETF_DISPLAY_NAMES: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLB": "Materials",
    "XLC": "Communication Services",
    "SMH": "Semiconductors",
    "SOXX": "Semiconductors",
    "IGV": "Software",
    "WCLD": "Cloud Computing",
    "SKYY": "Cloud Computing",
    "CIBR": "Cybersecurity",
    "QQQ": "Nasdaq 100",
    "SPY": "S&P 500",
}


def _normalize_etf(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _haystack(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).lower()


def infer_sector_etf(
    *,
    existing_etf: str = "",
    setup: str = "",
    sector_text: str = "",
    industry_text: str = "",
    ticker: str = "",
) -> dict[str, Any]:
    """
    Resolve sector ETF for one alert row.

    Returns:
        sector_etf, source (existing|inferred|unknown), inference_reason,
        candidate_etfs (when multiple options exist)
    """
    existing = _normalize_etf(existing_etf)
    if existing:
        return {
            "sector_etf": existing,
            "source": "existing",
            "inference_reason": None,
            "candidate_etfs": [existing],
        }

    text = _haystack(setup, sector_text, industry_text, ticker)

    # Technology subsectors first (more specific)
    for keywords, etfs in TECH_SUBSECTOR_RULES:
        if any(kw in text for kw in keywords):
            return {
                "sector_etf": etfs[0],
                "source": "inferred",
                "inference_reason": f"keyword:{keywords[0]}",
                "candidate_etfs": list(etfs),
            }

    # Broad sector names
    for name, etf in sorted(BROAD_SECTOR_ETF.items(), key=lambda x: -len(x[0])):
        if name in text:
            return {
                "sector_etf": etf,
                "source": "inferred",
                "inference_reason": f"sector:{name}",
                "candidate_etfs": [etf],
            }

    # SPDR ticker mentioned in text (e.g. "XLK sector")
    m = re.search(r"\b(XL[A-Z]{1,2}|SMH|SOXX|IGV|WCLD|SKYY|CIBR)\b", text.upper())
    if m:
        sym = m.group(1)
        return {
            "sector_etf": sym,
            "source": "inferred",
            "inference_reason": f"symbol_in_text:{sym}",
            "candidate_etfs": [sym],
        }

    return {
        "sector_etf": "",
        "source": "unknown",
        "inference_reason": None,
        "candidate_etfs": [],
    }


def enrich_alert_row(
    row: dict[str, Any],
    *,
    sector_text: str = "",
    industry_text: str = "",
) -> dict[str, Any]:
    """Return a copy of row with sector_etf enrichment fields added."""
    out = dict(row)
    combined_setup = _haystack(
        out.get("setup"),
        out.get("notes"),
        out.get("thesis"),
        out.get("alert_text"),
        out.get("alert"),
    )
    combined_sector = _haystack(sector_text, out.get("sector"), out.get("ticker_sector"))
    combined_industry = _haystack(industry_text, out.get("industry"))
    resolved = infer_sector_etf(
        existing_etf=out.get("sector_etf") or "",
        setup=combined_setup,
        sector_text=combined_sector,
        industry_text=combined_industry,
        ticker=out.get("ticker") or "",
    )
    out["sector_etf"] = resolved["sector_etf"]
    out["sector_etf_source"] = resolved["source"]
    out["sector_etf_inference_reason"] = resolved["inference_reason"]
    out["sector_etf_candidates"] = resolved["candidate_etfs"]
    return out


def enrich_alert_rows(
    rows: list[dict[str, Any]],
    warren_context: dict[str, dict[str, str]] | None = None,
    *,
    positions_lookup: dict[str, dict[str, Any]] | None = None,
    industry_cache: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    Enrich alert rows with sector ETF mapping.

    warren_context: {scan_date: {ticker: sector_etf}} plus optional
                    {scan_date: {"sector_names": {etf: name}}} for sector text.
    positions_lookup: {TICKER: position dict} for sector_raw / notes hints.
    industry_cache: optional pre-fetched {TICKER: industry} — no live yfinance here.
    """
    warren_context = warren_context or {}
    positions_lookup = positions_lookup or {}
    industry_cache = industry_cache or {}
    enriched: list[dict[str, Any]] = []
    for row in rows:
        sd = row.get("scan_date") or ""
        tk = (row.get("ticker") or "").upper()
        pos = positions_lookup.get(tk) or {}
        ctx = warren_context.get(sd, {})
        sector_text = _haystack(row.get("sector"), row.get("ticker_sector"), pos.get("sector_raw"))
        if isinstance(ctx.get("ticker_sector"), dict):
            etf_hint = ctx["ticker_sector"].get(tk, "")
            if etf_hint:
                sector_text = _haystack(sector_text, ETF_DISPLAY_NAMES.get(etf_hint, etf_hint))
        if isinstance(ctx.get("sector_reasons"), dict) and row.get("sector_etf"):
            sector_text = _haystack(sector_text, ctx["sector_reasons"].get(row["sector_etf"], ""))
        industry_text = _haystack(row.get("industry"), pos.get("sector_raw"))
        if not industry_text and tk in industry_cache:
            industry_text = industry_cache[tk]
        merged = dict(row)
        if not merged.get("notes") and pos.get("notes"):
            merged["notes"] = pos["notes"]
        enriched.append(enrich_alert_row(merged, sector_text=sector_text, industry_text=industry_text))
    return enriched


def compute_sector_etf_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize sector ETF mapping coverage across alert rows."""
    total = len(rows)
    if total == 0:
        return {
            "total_alert_rows": 0,
            "rows_with_existing_sector_etf": 0,
            "rows_inferred_by_mapping": 0,
            "rows_still_unknown": 0,
            "coverage_percent": 0.0,
        }

    existing = sum(1 for r in rows if r.get("sector_etf_source") == "existing")
    inferred = sum(1 for r in rows if r.get("sector_etf_source") == "inferred")
    unknown  = sum(1 for r in rows if r.get("sector_etf_source") == "unknown")
    mapped   = existing + inferred

    return {
        "total_alert_rows": total,
        "rows_with_existing_sector_etf": existing,
        "rows_inferred_by_mapping": inferred,
        "rows_still_unknown": unknown,
        "coverage_percent": round(mapped / total * 100, 1),
    }


def technology_subsector_summary(
    rows: list[dict[str, Any]],
    *,
    benchmark_returns: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """Summarize technology subsector ETF inference and benchmark paths for audio/debug."""
    tech_etfs = {"XLK", "SMH", "SOXX", "IGV", "WCLD", "SKYY", "CIBR", "QQQ", "SPY"}
    counts: dict[str, int] = {}
    inferred_subsectors: list[str] = []

    for row in rows:
        etf = _normalize_etf(row.get("sector_etf") or "")
        if etf not in tech_etfs:
            continue
        counts[etf] = counts.get(etf, 0) + 1
        if row.get("sector_etf_source") == "inferred" and row.get("sector_etf_inference_reason"):
            inferred_subsectors.append(row["sector_etf_inference_reason"])

    dominant = max(counts, key=counts.get) if counts else None
    subsector_benchmark_returns: dict[str, float | None] = {}
    subsector_leaders: list[dict[str, Any]] = []
    subsector_laggers: list[dict[str, Any]] = []
    subsector_data_status = "missing"

    if benchmark_returns:
        subsector_benchmark_returns = {
            sym: benchmark_returns.get(sym) for sym in sorted(tech_etfs)
        }
        ranked = [
            {"etf": sym, "return_pct": ret, "name": ETF_DISPLAY_NAMES.get(sym, sym)}
            for sym, ret in subsector_benchmark_returns.items()
            if ret is not None
        ]
        ranked.sort(key=lambda x: x["return_pct"], reverse=True)
        subsector_leaders = ranked[:3]
        subsector_laggers = sorted(ranked, key=lambda x: x["return_pct"])[:3]
        available = sum(1 for v in subsector_benchmark_returns.values() if v is not None)
        if available >= 5:
            subsector_data_status = "ok"
        elif available > 0:
            subsector_data_status = "partial"
        else:
            subsector_data_status = "missing"

    return {
        "technology_row_count": sum(counts.values()),
        "subsector_etf_counts": counts,
        "dominant_subsector_etf": dominant,
        "dominant_subsector_name": ETF_DISPLAY_NAMES.get(dominant or "", ""),
        "inferred_subsector_hints": sorted(set(inferred_subsectors))[:8],
        "subsector_benchmark_returns": subsector_benchmark_returns,
        "subsector_leaders": subsector_leaders,
        "subsector_laggers": subsector_laggers,
        "subsector_data_status": subsector_data_status,
    }
