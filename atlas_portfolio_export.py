"""
Portfolio export readiness for Atlas Morning Audio Source.

Safe export from positions.db — never invents holdings.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import data as db

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_EXPORT_PATH = os.path.join(_HERE, "config", "current_portfolio.json")
WARREN_SUMMARY_PATH = os.path.join(_HERE, "warren_ai_daily", "output", "portfolio_summary.json")
POSITIONS_DB_PATH = db.DB_PATH


def _file_mtime_iso(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _load_json_positions(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("positions", "holdings", "portfolio"):
                if isinstance(data.get(key), list):
                    return data[key]
    except Exception:
        pass
    return []


def export_portfolio_from_db(*, write: bool = True) -> dict[str, Any]:
    """
    Build config/current_portfolio.json from positions.db active holdings.

    Only writes when write=True and holdings exist. Never fabricates tickers.
    """
    holdings = db.get_positions(db.ACTIVE_STATUSES)
    if not holdings:
        return {"written": False, "reason": "no_active_holdings", "path": CONFIG_EXPORT_PATH}

    payload = {
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "positions.db",
        "position_count": len(holdings),
        "positions": [
            {
                "ticker": p.get("ticker"),
                "shares": p.get("shares"),
                "cost_basis": p.get("cost_basis"),
                "sector": p.get("sector"),
                "sector_raw": p.get("sector_raw"),
                "status": p.get("status"),
                "notes": p.get("notes"),
            }
            for p in holdings
        ],
    }

    if write:
        os.makedirs(os.path.dirname(CONFIG_EXPORT_PATH), exist_ok=True)
        with open(CONFIG_EXPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    return {"written": True, "reason": "exported_from_positions_db", "path": CONFIG_EXPORT_PATH}


def resolve_portfolio_readiness(
    *,
    active_positions: list[dict] | None = None,
    auto_export: bool = True,
) -> dict[str, Any]:
    """
    Resolve portfolio source for audio readiness.

    Priority:
      1. config/current_portfolio.json
      2. warren_ai_daily/output/portfolio_summary.json
      3. auto-export from positions.db (if auto_export and holdings exist)
      4. positions.db direct (portfolio_ready true, export missing)
    """
    active_positions = active_positions or db.get_positions(db.ACTIVE_STATUSES)
    owned_tickers = sorted({
        (p.get("ticker") or "").strip().upper()
        for p in active_positions
        if p.get("ticker")
    })

    export_path: str | None = None
    source = "none"
    positions: list[dict] = []

    if os.path.isfile(CONFIG_EXPORT_PATH):
        export_path = CONFIG_EXPORT_PATH
        source = "config/current_portfolio.json"
        positions = _load_json_positions(CONFIG_EXPORT_PATH)
    elif os.path.isfile(WARREN_SUMMARY_PATH):
        export_path = WARREN_SUMMARY_PATH
        source = "warren_ai_daily/output/portfolio_summary.json"
        positions = _load_json_positions(WARREN_SUMMARY_PATH)
    elif auto_export and owned_tickers:
        result = export_portfolio_from_db(write=True)
        if result.get("written"):
            export_path = CONFIG_EXPORT_PATH
            source = "positions.db_export"
            positions = _load_json_positions(CONFIG_EXPORT_PATH)
        elif os.path.isfile(POSITIONS_DB_PATH):
            source = "positions.db"
            positions = active_positions

    count = len(positions) if positions else len(owned_tickers)
    export_exists = export_path is not None and os.path.isfile(export_path)
    portfolio_ready = count > 0 and (export_exists or source == "positions.db")

    return {
        "portfolio_source": source,
        "portfolio_position_count": count,
        "portfolio_export_exists": export_exists,
        "portfolio_export_path": export_path,
        "portfolio_last_modified": _file_mtime_iso(export_path) if export_path else None,
        "owned_tickers_detected": owned_tickers,
        "portfolio_ready": portfolio_ready,
    }
