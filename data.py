"""SQLite-backed position store for the Decision Cockpit.

All reads and writes go through this module. The DB file lives alongside
this script as ``positions.db``. On first run, call ``import_from_csv``
to migrate the existing sample_positions.csv into the DB.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "positions.db")

ACTIVE_STATUSES    = ["in_position", "trimming_zone"]
WATCHLIST_STATUSES = ["researching", "watchlist"]
ALL_STATUSES       = ["researching", "watchlist", "in_position",
                      "trimming_zone", "exited", "archived"]

# ---------------------------------------------------------------------------
# Sector normalization  (raw yfinance string → standardized sector ETF code)
# ---------------------------------------------------------------------------

SECTOR_ETF_MAP: dict[str, str] = {
    "Financial Services":      "XLF",
    "Banking":                 "XLF",
    "Banks":                   "XLF",
    "Energy":                  "XLE",
    "Oil & Gas":               "XLE",
    "Oil, Gas & Consumable Fuels": "XLE",
    "Technology":              "XLK",
    "Information Technology":  "XLK",
    "Semiconductors":          "XLK",
    "Healthcare":              "XLV",
    "Health Care":             "XLV",
    "Pharmaceuticals":         "XLV",
    "Biotechnology":           "XLV",
    "Consumer Cyclical":       "XLY",
    "Consumer Discretionary":  "XLY",
    "Retail":                  "XLY",
    "Consumer Defensive":      "XLP",
    "Consumer Staples":        "XLP",
    "Industrials":             "XLI",
    "Industrial Conglomerates": "XLI",
    "Aerospace & Defense":     "XLI",
    "Basic Materials":         "XLB",
    "Materials":               "XLB",
    "Chemicals":               "XLB",
    "Metals & Mining":         "XLB",
    "Utilities":               "XLU",
    "Real Estate":             "XLRE",
    "REITs":                   "XLRE",
    "Communication Services":  "XLC",
    "Telecommunications":      "XLC",
    "Media":                   "XLC",
    "Entertainment":           "XLC",
}

# Set of valid normalized sector ETF codes used by the rest of the app
SECTOR_ETFS: frozenset[str] = frozenset(SECTOR_ETF_MAP.values())


def normalize_sector(raw: str) -> str:
    """
    Convert a raw sector name (e.g. "Financial Services") into a sector ETF
    code (e.g. "XLF").

    - If ``raw`` is already a valid ETF code → return it unchanged.
    - If a mapping exists → return the mapped code.
    - Otherwise → return "UNKNOWN".

    Case-insensitive on input.
    """
    if not raw:
        return "UNKNOWN"
    cleaned = raw.strip()
    upper   = cleaned.upper()
    # Already a valid ETF code
    if upper in SECTOR_ETFS:
        return upper
    # Direct map (case-insensitive search)
    for key, etf in SECTOR_ETF_MAP.items():
        if key.lower() == cleaned.lower():
            return etf
    # Partial / fuzzy fallback: check if any map key is a substring
    for key, etf in SECTOR_ETF_MAP.items():
        if key.lower() in cleaned.lower() or cleaned.lower() in key.lower():
            return etf
    return "UNKNOWN"


_FIELDS = [
    "ticker", "shares", "cost_basis", "target_weight", "current_weight",
    "sector", "sector_raw", "conviction", "investingpro_url", "notes",
    "fair_value_upside_pct", "financial_health_score",
    "analyst_target_upside_pct", "manual_fundamental_note", "status",
]

_REAL_FIELDS = {
    "shares", "cost_basis", "target_weight", "current_weight",
    "fair_value_upside_pct", "analyst_target_upside_pct",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    ticker                    TEXT PRIMARY KEY,
    shares                    REAL,
    cost_basis                REAL,
    target_weight             REAL,
    current_weight            REAL,
    sector                    TEXT DEFAULT '',
    sector_raw                TEXT DEFAULT '',
    conviction                TEXT DEFAULT '',
    investingpro_url          TEXT DEFAULT '',
    notes                     TEXT DEFAULT '',
    fair_value_upside_pct     REAL,
    financial_health_score    TEXT DEFAULT '',
    analyst_target_upside_pct REAL,
    manual_fundamental_note   TEXT DEFAULT '',
    status                    TEXT DEFAULT 'in_position',
    created_at                TEXT DEFAULT (datetime('now')),
    updated_at                TEXT DEFAULT (datetime('now'))
);
"""

# Migration: add sector_raw to existing databases that pre-date this column
_MIGRATION_ADD_SECTOR_RAW = (
    "ALTER TABLE positions ADD COLUMN sector_raw TEXT DEFAULT '';"
)


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(_SCHEMA)
        # Additive migration: add sector_raw if not present (safe on existing DBs)
        try:
            con.execute(_MIGRATION_ADD_SECTOR_RAW)
        except sqlite3.OperationalError:
            pass  # column already exists — nothing to do


def get_positions(statuses: list[str] | None = None) -> list[dict]:
    with _conn() as con:
        if statuses:
            ph = ",".join("?" * len(statuses))
            rows = con.execute(
                f"SELECT * FROM positions WHERE status IN ({ph}) ORDER BY ticker",
                statuses,
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM positions ORDER BY ticker"
            ).fetchall()
    return [dict(r) for r in rows]


def get_position(ticker: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM positions WHERE ticker=?",
            (ticker.strip().upper(),),
        ).fetchone()
    return dict(row) if row else None


def upsert_position(pos: dict) -> None:
    norm: dict = {}
    for f in _FIELDS:
        v = pos.get(f)
        if f == "ticker":
            norm[f] = str(v or "").strip().upper()
        elif f in _REAL_FIELDS:
            norm[f] = _to_float(v)
        else:
            norm[f] = str(v).strip() if v not in (None, "") else None
    ph = ",".join("?" * len(_FIELDS))
    with _conn() as con:
        con.execute(
            f"INSERT OR REPLACE INTO positions ({','.join(_FIELDS)}, updated_at) "
            f"VALUES ({ph}, datetime('now'))",
            [norm[f] for f in _FIELDS],
        )


def archive_position(ticker: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE positions "
            "SET status='archived', updated_at=datetime('now') "
            "WHERE ticker=?",
            (ticker.strip().upper(),),
        )


def import_from_csv(path: str) -> int:
    """One-time migration from CSV into SQLite. Skips rows without a ticker."""
    if not os.path.exists(path):
        return 0
    count = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("ticker"):
                continue
            row.setdefault("status", "in_position")
            upsert_position(row)
            count += 1
    return count


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if f != f else f  # reject NaN
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# JSON bulk import helpers
# ---------------------------------------------------------------------------

VALID_STATUSES = set(ALL_STATUSES)
KNOWN_SECTORS  = {
    "XLK", "XLF", "XLE", "XLI", "XLY",
    "XLP", "XLU", "XLV", "XLRE", "XLB", "XLC",
}

def validate_import_row(row: dict) -> list[str]:
    """
    Return a list of error strings for a single candidate import row.
    Empty list means the row is valid.
    """
    errors: list[str] = []
    ticker = str(row.get("ticker") or "").strip().upper()
    if not ticker:
        errors.append("ticker is required")
    qty = row.get("shares") or row.get("quantity")
    if qty is None or qty == "":
        errors.append("quantity/shares is required")
    else:
        try:
            float(qty)
        except (TypeError, ValueError):
            errors.append(f"quantity must be numeric, got {qty!r}")
    status = row.get("status", "in_position")
    if status and status not in VALID_STATUSES:
        errors.append(f"status {status!r} not in {sorted(VALID_STATUSES)}")
    return errors


def import_positions_json(
    rows: list[dict],
    *,
    dry_run: bool = False,
) -> dict:
    """
    Bulk-import a list of position dicts.  Returns a summary dict:
        {
            "applied":  [ticker, ...],   # rows written
            "skipped":  [ticker, ...],   # rows with validation errors
            "errors":   {ticker: [msg]}, # per-row errors
            "dry_run":  bool,
        }
    Each row is MERGED with any existing DB row so only supplied fields
    are overwritten — existing fields (notes, urls, …) are preserved.
    Accepted field aliases (Moomoo / generic):
        "symbol" | "ticker"
        "quantity" | "qty" | "shares"
        "average_cost" | "avg_cost" | "cost_basis"
        "market_value" | "current_price" | "price"
        "name" | "company"
        "sector_etf" | "sector"
        "industry"
    """
    applied:  list[str]       = []
    skipped:  list[str]       = []
    err_map:  dict[str, list] = {}

    seen: set[str] = set()

    for raw in rows:
        # ── Normalise field aliases ──────────────────────────────────────
        r: dict = {}
        r["ticker"]    = str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
        r["shares"]    = raw.get("shares") or raw.get("quantity") or raw.get("qty")
        r["cost_basis"]  = raw.get("cost_basis") or raw.get("average_cost") or raw.get("avg_cost")
        r["notes"]     = str(raw.get("notes") or raw.get("industry") or "").strip()
        r["status"]    = str(raw.get("status") or "in_position").strip()

        # ── Sector normalization ─────────────────────────────────────────
        # raw_sector_input: may be a raw yfinance string or already an ETF code
        raw_sector_input = (
            raw.get("sector_raw") or raw.get("sector_etf") or raw.get("sector") or ""
        ).strip()
        r["sector_raw"] = raw_sector_input  # preserve as-received
        r["sector"]     = normalize_sector(raw_sector_input)  # always ETF code

        # Optional numeric / text fields passed through if present
        for f in ("target_weight", "current_weight", "conviction",
                  "investingpro_url", "fair_value_upside_pct",
                  "financial_health_score", "analyst_target_upside_pct",
                  "manual_fundamental_note"):
            if f in raw:
                r[f] = raw[f]

        ticker = r["ticker"]

        # ── Dedup within batch ───────────────────────────────────────────
        if ticker in seen:
            err_map.setdefault(ticker, []).append("duplicate in batch — last row wins")
        seen.add(ticker)

        # ── Validate ─────────────────────────────────────────────────────
        errs = validate_import_row(r)
        if errs:
            skipped.append(ticker or "(no ticker)")
            err_map[ticker or "(no ticker)"] = errs
            continue

        # ── Merge with existing row to avoid wiping unrelated fields ─────
        existing = get_position(ticker) or {}
        merged = dict(existing)
        for k, v in r.items():
            if k == "sector":
                # Do not overwrite a non-empty, valid existing sector with UNKNOWN
                existing_sector = str(merged.get("sector") or "").strip().upper()
                if existing_sector and existing_sector in SECTOR_ETFS and v == "UNKNOWN":
                    continue
                # Only overwrite if new value is non-empty and valid
                if v and v not in (None, ""):
                    merged[k] = v
            elif k == "sector_raw":
                # Always store sector_raw if provided (it's informational)
                if v:
                    merged[k] = v
            else:
                # Only overwrite if the new value is non-empty
                if v not in (None, "", []):
                    merged[k] = v
        # Ensure ticker and status are always set
        merged["ticker"] = ticker
        merged.setdefault("status", "in_position")

        if not dry_run:
            upsert_position(merged)

        applied.append(ticker)

    return {
        "applied":  applied,
        "skipped":  skipped,
        "errors":   err_map,
        "dry_run":  dry_run,
    }
