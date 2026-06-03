"""
Load and merge Warren AI theme catalyst output into Atlas Theme Catalyst Radar.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
WARREN_LATEST_PATH = os.path.join(_HERE, "warren_ai_daily", "output", "theme_catalyst_radar_latest.json")
WARREN_OUTPUT_DIR = os.path.join(_HERE, "warren_ai_daily", "output")

WARREN_STALE_DAYS = 7
CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def _parse_date(ds: str | None) -> date | None:
    if not ds:
        return None
    try:
        return datetime.strptime(str(ds)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_ts(ds: str | None) -> datetime | None:
    if not ds:
        return None
    try:
        return datetime.fromisoformat(str(ds).replace("Z", "+00:00"))
    except Exception:
        return None


def load_warren_theme_catalyst(path: str | None = None) -> dict[str, Any]:
    p = path or WARREN_LATEST_PATH
    if not os.path.isfile(p):
        return {
            "loaded": False,
            "path": p,
            "generated_at": None,
            "stale": True,
            "parse_ok": False,
            "catalysts": [],
            "parse_error": "file_missing",
        }
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:
        return {
            "loaded": True,
            "path": p,
            "generated_at": None,
            "stale": True,
            "parse_ok": False,
            "catalysts": [],
            "parse_error": f"json_error:{exc}",
        }

    generated_at = raw.get("generated_at")
    ts = _parse_ts(generated_at)
    stale = True
    if ts:
        stale = (datetime.now(timezone.utc) - ts).days > WARREN_STALE_DAYS

    return {
        "loaded": True,
        "path": p,
        "generated_at": generated_at,
        "stale": stale,
        "parse_ok": bool(raw.get("parse_ok", True)) and isinstance(raw.get("catalysts"), list),
        "catalysts": list(raw.get("catalysts") or []),
        "parse_error": raw.get("parse_error"),
        "source": raw.get("source") or "warren_ai",
    }


def _warren_to_manual_shape(w: dict[str, Any]) -> dict[str, Any]:
    """Convert Warren catalyst JSON row to manual-config-compatible dict."""
    return {
        "id": f"warren_{(w.get('theme') or 'unknown').replace(' ', '_')}",
        "private_company": w.get("private_company") or "",
        "event_type": w.get("event_type") or "unknown",
        "theme": w.get("theme") or "unknown",
        "themes_affected": [w.get("theme")] if w.get("theme") else [],
        "catalyst_label": w.get("why_it_matters") or w.get("event_type") or "Warren catalyst",
        "catalyst_date": w.get("event_date"),
        "status": "upcoming",
        "confidence": w.get("confidence") or "medium",
        "source_name": "Warren AI theme catalyst research",
        "source_freshness": w.get("source_freshness") or "warren_ai",
        "date_added": None,
        "last_verified": (w.get("generated_at") or "")[:10] or None,
        "verification_status": "warren_ai",
        "manual_review_required": False,
        "action_context": w.get("suggested_watch_status") or "Monitor — Warren research only",
        "proxy_tickers": list(w.get("public_proxy_tickers") or []),
        "proxy_etfs": list(w.get("proxy_etfs") or []),
        "sector_etf_basket": list(w.get("proxy_etfs") or [])[:4],
        "why_it_matters": w.get("why_it_matters"),
        "wall_street_interest_summary": w.get("wall_street_interest_summary"),
        "confirmation_triggers": list(w.get("confirmation_triggers") or []),
        "invalidation_triggers": list(w.get("invalidation_triggers") or []),
        "risk_notes": list(w.get("risk_notes") or []),
        "suggested_watch_status": w.get("suggested_watch_status"),
        "_warren_source": True,
    }


def _merge_manual_warren(manual: dict[str, Any], warren: dict[str, Any]) -> dict[str, Any]:
    """Merge one manual catalyst with Warren enrichment. Manual date wins unless Warren higher confidence."""
    out = dict(manual)
    w_shape = _warren_to_manual_shape(warren)

    w_conf = CONF_RANK.get(str(warren.get("confidence") or "").lower(), 0)
    m_conf = CONF_RANK.get(str(manual.get("confidence") or "").lower(), 0)
    w_fresh = (warren.get("source_freshness") or "") == "warren_ai"

    if warren.get("event_date") and w_fresh and w_conf > m_conf:
        out["catalyst_date"] = warren.get("event_date")

    for field in (
        "why_it_matters", "wall_street_interest_summary",
        "confirmation_triggers", "invalidation_triggers", "risk_notes",
        "suggested_watch_status",
    ):
        if warren.get(field):
            out[field] = warren.get(field)

    # Union proxy tickers — manual first, Warren adds
    proxies = list(dict.fromkeys(
        list(manual.get("proxy_tickers") or []) + list(warren.get("public_proxy_tickers") or [])
    ))
    etfs = list(dict.fromkeys(
        list(manual.get("proxy_etfs") or []) + list(warren.get("proxy_etfs") or [])
    ))
    if proxies:
        out["proxy_tickers"] = proxies
    if etfs:
        out["proxy_etfs"] = etfs
        out["sector_etf_basket"] = etfs[:4]

    out["warren_enriched"] = True
    out["warren_suggested_watch_status"] = warren.get("suggested_watch_status")
    out["source_freshness"] = "merged"
    out["last_verified"] = (warren.get("generated_at") or out.get("last_verified") or "")[:10] or out.get("last_verified")
    return out


def merge_catalyst_sources(
    manual_catalysts: list[dict[str, Any]],
    warren_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Merge manual YAML catalysts with Warren JSON catalysts.

    Returns (merged_list, merge_stats).
    """
    warren_rows = list(warren_payload.get("catalysts") or [])
    manual_by_theme = {(c.get("theme") or "").lower(): c for c in manual_catalysts if c.get("theme")}
    manual_by_company = {
        (c.get("private_company") or "").lower(): c
        for c in manual_catalysts if c.get("private_company")
    }

    merged: list[dict[str, Any]] = []
    used_warren: set[int] = set()
    enriched = 0
    manual_only = 0

    for manual in manual_catalysts:
        theme_key = (manual.get("theme") or "").lower()
        company_key = (manual.get("private_company") or "").lower()
        w_match = None
        w_idx = None
        for i, w in enumerate(warren_rows):
            if i in used_warren:
                continue
            wt = (w.get("theme") or "").lower()
            wc = (w.get("private_company") or "").lower()
            if (theme_key and wt == theme_key) or (company_key and wc == company_key):
                w_match = w
                w_idx = i
                break
        if w_match is not None:
            merged.append(_merge_manual_warren(manual, w_match))
            used_warren.add(w_idx)  # type: ignore[arg-type]
            enriched += 1
        else:
            m = dict(manual)
            m["data_source"] = "manual"
            merged.append(m)
            manual_only += 1

    warren_discovered = 0
    for i, w in enumerate(warren_rows):
        if i in used_warren:
            continue
        row = _warren_to_manual_shape(w)
        row["data_source"] = "warren"
        merged.append(row)
        warren_discovered += 1

    stats = {
        "manual_catalyst_count": len(manual_catalysts),
        "warren_catalyst_count": len(warren_rows),
        "merged_catalyst_count": len(merged),
        "manual_enriched_by_warren": enriched,
        "manual_only_count": manual_only,
        "warren_discovered_count": warren_discovered,
        "warren_new_themes": [
            (w.get("theme") or "unknown")
            for i, w in enumerate(warren_rows) if i not in used_warren
        ],
    }
    return merged, stats
