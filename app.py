"""
Investing Decision Cockpit — Phase 4: Selector + Macro Regime.

Run:   python app.py
Then:  http://127.0.0.1:8061

Positions stored in SQLite (positions.db). Sector RRG direction pulled live
from sector_engine.py (1-hour cache). Macro regime from FRED (6-hour cache).
Sector RRG chart (port 8003) untouched.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, date
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import dash
from dash import Dash, html, dcc, Input, Output, State, ctx, no_update, dash_table, ALL
from flask import jsonify

import plotly.graph_objects as go
from plotly.subplots import make_subplots

import time
import yfinance as yf

import numpy as np
import pandas as _pd
from dateutil.relativedelta import relativedelta as _relativedelta

import data as db
import selector as sel
import macro_regime as mr
import warren_loader as wl
import chart_data as cd
import portfolio_exposure as pe
import morning_status as ms
import warren_history as wh
import theme_registry as tr
import theme_diagnostic_parser as tdp
import theme_scan_parser as tsp
import atlas_morning_audio_source as amas
import atlas_theme_catalyst_radar as atcr
from trim_monitor import analyze_portfolio
from alerts import build_alerts as build_alerts_list, todays_verdict
from sector_engine import get_sector_statuses, SectorStatus

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORT       = int(os.environ.get("DECISION_PORT", "8061"))
SAMPLE_CSV = os.path.join(os.path.dirname(__file__), "sample_positions.csv")

# ---------------------------------------------------------------------------
# Capital breakdown config
# Total portfolio value and non-tracked buckets.
# Edit these constants to match your brokerage account.
# ---------------------------------------------------------------------------
TOTAL_PORTFOLIO_VALUE: float = float(os.environ.get("TOTAL_PORTFOLIO_VALUE", "0"))

# Non-tracked position buckets: (display_label, dollar_value)
# Add or remove lines to match your holdings outside the active tracking list.
_EXTRA_BUCKETS: list[tuple[str, float]] = [
    ("BOXX",  float(os.environ.get("BOXX_VALUE",  "0"))),
    ("NVDA",  float(os.environ.get("NVDA_VALUE",  "0"))),
]
# Filter out zero-value buckets so they don't clutter the display
EXTRA_BUCKETS: list[tuple[str, float]] = [
    (label, val) for label, val in _EXTRA_BUCKETS if val > 0
]


def _is_donut_sector_etf_filter(label: str) -> bool:
    """True when donut slice label matches Active Holdings \"Sector\" (ETF code)."""
    if not label:
        return False
    u = str(label).strip().upper()
    if u in db.SECTOR_ETFS:
        return True
    return u == "UNKNOWN"


# ---------------------------------------------------------------------------
# Ticker info cache (company name + sector ETF) via yfinance
# ---------------------------------------------------------------------------

_TICKER_INFO_CACHE: dict[str, dict] = {}
_TICKER_INFO_TTL = 3600  # seconds


def _fetch_ticker_info(ticker: str) -> dict:
    """
    Return ticker metadata via yfinance with in-process caching.
    Keys: company, sector_raw, sector_normalized, industry.
    ``sector_normalized`` is the ETF code ready for the `sector` DB field.
    """
    ticker = ticker.strip().upper()
    cached = _TICKER_INFO_CACHE.get(ticker)
    if cached and (time.time() - cached.get("_ts", 0)) < _TICKER_INFO_TTL:
        return cached
    try:
        info       = yf.Ticker(ticker).info or {}
        sector_raw = info.get("sector") or ""
        result = {
            "company":           info.get("longName") or info.get("shortName") or ticker,
            "sector_raw":        sector_raw,
            "sector_normalized": db.normalize_sector(sector_raw),
            "industry":          info.get("industry") or "",
            "_ts":               time.time(),
        }
    except Exception:
        result = {
            "company": ticker, "sector_raw": "", "sector_normalized": "UNKNOWN",
            "industry": "", "_ts": time.time(),
        }
    _TICKER_INFO_CACHE[ticker] = result
    return result

SECTORS = [
    "XLV", "XLRE", "XLB", "XLU", "XLP",
    "XLF", "XLY", "XLE", "XLI", "XLC", "XLK",
]
STATUS_OPTIONS = [
    "researching", "watchlist", "in_position",
    "trimming_zone", "exited", "archived",
]

# ---------------------------------------------------------------------------
# Bootstrap DB
# ---------------------------------------------------------------------------

db.init_db()
if not db.get_positions():
    n = db.import_from_csv(SAMPLE_CSV)
    if n:
        print(f"Migrated {n} positions from {SAMPLE_CSV}")

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

C = {
    "bg":         "#fafaf7",
    "surface":    "#ffffff",
    "surface2":   "#f4f3ed",
    "border":     "rgba(0,0,0,0.08)",
    "text":       "#1a1a1a",
    "muted":      "#6e6d68",
    "dim":        "#9a988f",
    "red_bg":     "#fceaea", "red_fg":     "#a32d2d",
    "amber_bg":   "#fbeed4", "amber_fg":   "#854f0b",
    "blue_bg":    "#e6f1fb", "blue_fg":    "#0c447c",
    "green_bg":   "#e9f3de", "green_fg":   "#3b6d11",
    "neutral_bg": "#eeede7", "neutral_fg": "#5f5e5a",
}

VERDICT_COLORS = {
    "Strong trim":    (C["red_bg"],     C["red_fg"]),
    "Trim candidate": (C["amber_bg"],   C["amber_fg"]),
    "Watch":          (C["blue_bg"],    C["blue_fg"]),
    "Hold":           (C["neutral_bg"], C["neutral_fg"]),
}

QUAD_COLORS = {
    "Leading":   (C["green_bg"],  C["green_fg"]),
    "Improving": (C["blue_bg"],   C["blue_fg"]),
    "Weakening": (C["amber_bg"],  C["amber_fg"]),
    "Lagging":   (C["red_bg"],    C["red_fg"]),
}

ENTRY_VERDICT_COLORS = {
    "Strong entry":  (C["green_bg"],   C["green_fg"]),
    "Setup forming": (C["blue_bg"],    C["blue_fg"]),
    "Watch":         (C["amber_bg"],   C["amber_fg"]),
    "Skip":          (C["neutral_bg"], C["neutral_fg"]),
}

REGIME_COLORS = {
    "Risk On":  (C["green_bg"],   C["green_fg"]),
    "Neutral":  (C["blue_bg"],    C["blue_fg"]),
    "Caution":  (C["amber_bg"],   C["amber_fg"]),
    "Risk Off": (C["red_bg"],     C["red_fg"]),
    "Unknown":  (C["neutral_bg"], C["neutral_fg"]),
}

# ---------------------------------------------------------------------------
# Table style constants
# ---------------------------------------------------------------------------

_CELL = {
    "fontFamily": "system-ui, -apple-system, sans-serif",
    "fontSize": "13px",
    "padding": "9px 12px",
    "border": "none",
    "borderTop": f"1px solid {C['border']}",
    "color": C["text"],
    "backgroundColor": C["surface"],
}
_HEADER = {
    "fontWeight": 600,
    "fontSize": "11px",
    "color": C["muted"],
    "backgroundColor": C["surface2"],
    "border": "none",
    "padding": "9px 12px",
    "textAlign": "left",
    "textTransform": "uppercase",
    "letterSpacing": "0.04em",
}
_TABLE = {
    "borderRadius": "12px",
    "overflow": "hidden",
    "border": f"1px solid {C['border']}",
}

# ---------------------------------------------------------------------------
# Table column definitions
# ---------------------------------------------------------------------------

_ETF_DISPLAY_NAMES: dict[str, str] = {
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLK":  "Technology",
    "XLV":  "Health Care",
    "XLY":  "Cons. Discretionary",
    "XLP":  "Cons. Staples",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLC":  "Comm. Services",
}

PORTFOLIO_COLS = [{"name": c, "id": c} for c in [
    "Ticker", "Status", "Sector", "Price", "Gain%",
    ">20EMA", ">50EMA", "RSI", "ATR×", "Wt%", "Flags", "Verdict", "Updated",
]]

WATCHLIST_COLS = [{"name": c, "id": c} for c in [
    "Ticker", "Status", "Sector", "Sector Status",
    "Sector Signal", "Price", ">20EMA", ">50EMA", "RSI", "Updated",
]]

SECTOR_ROTATION_COLS = [{"name": n, "id": i} for n, i in [
    ("Sector",      "Ticker"),
    ("Sector name", "Sector Name"),
    ("Quadrant",    "Quadrant"),
    ("Direction", "Direction"),
    ("Score",     "Score"),
    ("Label",     "Decision Label"),
    ("ΔRSR",      "ΔRSR"),
    ("ΔRSM",      "ΔRSM"),
]]

CANDIDATE_COLS = (
    [{"name": n, "id": i} for n, i in [
        ("Ticker",     "ticker"),
        ("Own",        "ownership"),
        ("Price",      "price"),
        ("RSI",        "rsi"),
        ("vs 20 EMA",  "pct_20ema"),
        ("vs 50 EMA",  "pct_50ema"),
        ("30d Return", "ret_30d"),
        ("RS vs ETF",  "rs_etf"),
        ("Flags",      "flags"),
        ("Verdict",    "verdict"),
        ("Score",      "score"),
    ]] +
    [{"name": "InvestingPro", "id": "investingpro", "presentation": "markdown"}] +
    [{"name": "Reason",      "id": "reason"}]
)

# ---------------------------------------------------------------------------
# Conditional styling
# ---------------------------------------------------------------------------

PORTFOLIO_COND = [
    *[
        {
            "if": {"filter_query": f'{{Verdict}} = "{v}"', "column_id": "Verdict"},
            "backgroundColor": bg, "color": fg, "fontWeight": 600,
        }
        for v, (bg, fg) in VERDICT_COLORS.items()
    ],
    {"if": {"filter_query": '{Price} = "ERR"'},
     "backgroundColor": C["red_bg"], "color": C["red_fg"]},
]

# Active Holdings: align headers with numeric / pill content (tbl-portfolio only)
_PORTFOLIO_NUM_COLS = (
    "Price", "Gain%", ">20EMA", ">50EMA", "RSI", "ATR×", "Wt%", "Flags",
)
PORTFOLIO_HEADER_ALIGN = [
    *[{"if": {"column_id": c}, "textAlign": "right", "paddingRight": "14px"}
      for c in _PORTFOLIO_NUM_COLS],
    {"if": {"column_id": "Verdict"}, "textAlign": "center"},
    {"if": {"column_id": "Updated"}, "textAlign": "center"},
    {"if": {"column_id": "Ticker"}, "textAlign": "left", "paddingLeft": "4px"},
    {"if": {"column_id": "Status"}, "textAlign": "left"},
    {"if": {"column_id": "Sector"}, "textAlign": "left"},
]
PORTFOLIO_DATA_ALIGN = [
    *[{"if": {"column_id": c}, "textAlign": "right", "paddingRight": "14px"}
      for c in _PORTFOLIO_NUM_COLS],
    {"if": {"column_id": "Verdict"}, "textAlign": "center"},
    {"if": {"column_id": "Updated"}, "textAlign": "center"},
    {"if": {"column_id": "Ticker"}, "textAlign": "left", "paddingLeft": "4px"},
    {"if": {"column_id": "Status"}, "textAlign": "left"},
    {"if": {"column_id": "Sector"}, "textAlign": "left"},
]

WATCHLIST_COND = [
    *[
        {
            "if": {"filter_query": f'{{Sector Status}} = "{q}"',
                   "column_id": "Sector Status"},
            "backgroundColor": bg, "color": fg,
        }
        for q, (bg, fg) in QUAD_COLORS.items()
    ],
    {"if": {"filter_query": '{Sector Signal} contains "★"', "column_id": "Sector Signal"},
     "backgroundColor": C["green_bg"], "color": C["green_fg"], "fontWeight": 600},
    {"if": {"filter_query": '{Sector Signal} contains "⚠"', "column_id": "Sector Signal"},
     "backgroundColor": C["amber_bg"], "color": C["amber_fg"]},
]

SECTOR_ROTATION_COND = [
    *[
        {
            "if": {"filter_query": f'{{Quadrant}} = "{q}"', "column_id": "Quadrant"},
            "backgroundColor": bg, "color": fg, "fontWeight": 600,
        }
        for q, (bg, fg) in QUAD_COLORS.items()
    ],
    {"if": {"filter_query": '{Direction} contains "strengthening"', "column_id": "Direction"},
     "backgroundColor": C["green_bg"], "color": C["green_fg"], "fontWeight": 600},
    {"if": {"filter_query": '{Direction} contains "deteriorating"', "column_id": "Direction"},
     "backgroundColor": C["red_bg"], "color": C["red_fg"], "fontWeight": 600},
    {"if": {"filter_query": '{Direction} contains "stalling"', "column_id": "Direction"},
     "backgroundColor": C["neutral_bg"], "color": C["neutral_fg"]},
    {"if": {"filter_query": "{Score} > 25", "column_id": "Score"},
     "backgroundColor": C["green_bg"], "color": C["green_fg"], "fontWeight": 600},
    {"if": {"filter_query": "{Score} < -25", "column_id": "Score"},
     "backgroundColor": C["red_bg"], "color": C["red_fg"], "fontWeight": 600},
]

CANDIDATE_COND = [
    *[
        {
            "if": {"filter_query": f'{{verdict}} = "{v}"', "column_id": "verdict"},
            "backgroundColor": bg, "color": fg, "fontWeight": 600,
        }
        for v, (bg, fg) in ENTRY_VERDICT_COLORS.items()
    ],
    {"if": {"filter_query": '{price} = "ERR"'},
     "backgroundColor": C["red_bg"], "color": C["red_fg"]},
    {"if": {"filter_query": '{ownership} contains "add candidate"', "column_id": "ownership"},
     "backgroundColor": C["green_bg"], "color": C["green_fg"], "fontWeight": 600},
    {"if": {"filter_query": '{ownership} contains "trim"', "column_id": "ownership"},
     "backgroundColor": C["amber_bg"], "color": C["amber_fg"], "fontWeight": 600},
    {"if": {"filter_query": '{ownership} contains "exit"', "column_id": "ownership"},
     "backgroundColor": C["red_bg"], "color": C["red_fg"], "fontWeight": 600},
    {"if": {"filter_query": '{ownership} contains "Already owned"', "column_id": "ownership"},
     "backgroundColor": C["surface2"], "color": C["muted"], "fontWeight": 600},
]

# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def run_analysis():
    sector_statuses = get_sector_statuses()
    sector_map = {t: s.quadrant  for t, s in sector_statuses.items()}
    dir_map    = {t: s.direction for t, s in sector_statuses.items()}

    active_pos    = db.get_positions(db.ACTIVE_STATUSES)
    watchlist_pos = db.get_positions(db.WATCHLIST_STATUSES)

    active_res  = analyze_portfolio(active_pos,    sector_status_map=sector_map,
                                                   sector_direction_map=dir_map)
    watch_res   = analyze_portfolio(watchlist_pos, sector_status_map=sector_map,
                                                   sector_direction_map=dir_map)
    return active_res, watch_res, sector_statuses, datetime.now()

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _f(v, d=2, sfx=""):
    return f"{v:.{d}f}{sfx}" if v is not None else "—"

def _pct(v, d=1):
    return _f(v, d, "%")

# ---------------------------------------------------------------------------
# UI component helpers
# ---------------------------------------------------------------------------

def card(children, pad="1rem 1.25rem", **extra):
    s = {
        "background": C["surface"],
        "border": f"1px solid {C['border']}",
        "borderRadius": "12px",
        "padding": pad,
    }
    s.update(extra)
    return html.Div(children, style=s)


def badge(text, bg, fg, size="11px"):
    return html.Span(text, style={
        "background": bg, "color": fg, "fontSize": size,
        "padding": "3px 9px", "borderRadius": "8px",
        "fontWeight": 600, "whiteSpace": "nowrap",
        "display": "inline-block",
    })


def section_hdr(title):
    return html.Div(title, style={
        "fontSize": "13px", "fontWeight": 700,
        "color": C["text"], "textTransform": "uppercase",
        "letterSpacing": "0.05em",
    })


def _btn(label, btn_id, primary=False, danger=False, disabled=False):
    if primary:
        bg, fg, border = C["blue_fg"], "#fff", "none"
    elif danger:
        bg, fg, border = C["red_bg"], C["red_fg"], f"1px solid {C['red_fg']}33"
    else:
        bg, fg, border = C["surface"], C["text"], f"1px solid {C['border']}"
    return html.Button(label, id=btn_id, n_clicks=0, disabled=disabled, style={
        "background": bg, "color": fg, "border": border,
        "borderRadius": "8px", "padding": "6px 14px",
        "fontSize": "13px", "cursor": "pointer",
        "fontWeight": 500, "marginRight": "8px",
        "opacity": "0.45" if disabled else "1",
    })

# ---------------------------------------------------------------------------
# Shared: Sector Rotation section
# ---------------------------------------------------------------------------

def _sector_rotation_rows(statuses: dict[str, SectorStatus]) -> list[dict]:
    rows = []
    for t in sorted(statuses.keys()):
        s = statuses[t]
        rows.append({
            "Ticker":         t,
            "Sector Name":    _ETF_DISPLAY_NAMES.get(t, "—"),
            "Quadrant":       s.quadrant,
            "Direction":      s.direction,
            "Score":          s.accel_score if s.sufficient_data else 0,
            "Decision Label": s.decision_label,
            "ΔRSR":           round(s.delta_rsr, 2) if s.sufficient_data else "—",
            "ΔRSM":           round(s.delta_rsm, 2) if s.sufficient_data else "—",
        })
    return rows


def build_sector_rotation_section(
    statuses: dict[str, SectorStatus],
    table_id: str,
    selectable: bool = False,
    selected_rows: list | None = None,
) -> html.Div:
    """
    Reusable sector rotation DataTable section.
    Used by both Dashboard tab and Selector tab.

    Parameters
    ----------
    statuses      : dict of SectorStatus objects from sector_engine
    table_id      : DOM id for the DataTable (must differ between tabs)
    selectable    : if True, rows can be selected (for Selector tab)
    selected_rows : pre-selected row indices (Selector tab default selection)
    """
    if not statuses:
        return html.Div("Sector data unavailable.",
                        style={"color": C["muted"], "fontSize": "13px"})

    rows = _sector_rotation_rows(statuses)

    return html.Div([
        html.Div([
            section_hdr("Sector Rotation"),
            html.Span(
                "Direction: current bar vs. avg of prior 2 bars  ·  "
                "Score −100 to +100 (positive = improving, negative = deteriorating)",
                style={"fontSize": "11px", "color": C["dim"],
                       "fontStyle": "italic", "marginLeft": "12px"},
            ),
        ], style={"display": "flex", "alignItems": "baseline", "marginBottom": "10px"}),

        dash_table.DataTable(
            id=table_id,
            data=rows,
            columns=SECTOR_ROTATION_COLS,
            row_selectable="single" if selectable else False,
            selected_rows=selected_rows or [],
            style_cell={**_CELL, "whiteSpace": "nowrap"},
            style_header=_HEADER,
            style_data_conditional=SECTOR_ROTATION_COND,
            style_table={**_TABLE, "overflowX": "auto"},
            cell_selectable=False,
        ),

        html.Div(
            "Quadrant: Leading / Improving / Weakening / Lagging  "
            "based on RS Ratio and RS Momentum vs SPY (weekly, 52-week window).",
            style={"fontSize": "11px", "color": C["dim"],
                   "marginTop": "6px", "fontStyle": "italic"},
        ),
    ])


def _best_selector_sector(statuses: dict[str, SectorStatus],
                           all_positions: list[dict]) -> str | None:
    """
    Default sector selection for Selector tab.
    Priority order:
      1. Strengthening + Improving
      2. Strengthening + Leading
      3. Any strengthening sector
      4. Highest accel_score with candidates
      5. Highest accel_score overall
    Prefers sectors that have watchlist candidates.
    """
    rows = _sector_rotation_rows(statuses)
    rows_sorted = sorted(rows, key=lambda r: r["Score"], reverse=True)

    candidate_sectors = {
        p.get("sector", "").strip().upper()
        for p in all_positions if p.get("status") == "watchlist"
    }

    def _has_cands(etf):
        return etf in candidate_sectors

    def _try_pick(row_list):
        with_cands = [r for r in row_list if _has_cands(r["Ticker"])]
        return (with_cands or row_list)[0]["Ticker"] if (with_cands or row_list) else None

    # Tier 1: improving + strengthening
    t1 = [r for r in rows_sorted
          if r["Quadrant"] == "Improving" and "strengthening" in r["Direction"]]
    if t1:
        return _try_pick(t1)

    # Tier 2: leading + strengthening
    t2 = [r for r in rows_sorted
          if r["Quadrant"] == "Leading" and "strengthening" in r["Direction"]]
    if t2:
        return _try_pick(t2)

    # Tier 3: any strengthening
    t3 = [r for r in rows_sorted if "strengthening" in r["Direction"]]
    if t3:
        return _try_pick(t3)

    # Tier 4: lagging but not deteriorating (turning)
    t4 = [r for r in rows_sorted
          if r["Quadrant"] == "Lagging" and "deteriorating" not in r["Direction"]]
    if t4:
        return _try_pick(t4)

    # Fallback: highest score overall
    return rows_sorted[0]["Ticker"] if rows_sorted else None


# ---------------------------------------------------------------------------
# Dashboard section builders
# ---------------------------------------------------------------------------

def build_verdict(active, ts):
    return [
        html.Div(
            f"Updated {ts.strftime('%a %b %d, %Y · %H:%M')}",
            style={"fontSize": "12px", "color": C["muted"], "marginBottom": "4px"},
        ),
        html.Div(todays_verdict(active), style={"fontSize": "15px", "fontWeight": 500}),
    ]


def build_metrics(active, watchlist):
    n_pos    = len(active)
    n_strong = sum(1 for r in active if r.verdict == "Strong trim" and not r.error)
    n_cand   = sum(1 for r in active if r.verdict == "Trim candidate" and not r.error)
    n_wl     = len(watchlist)
    n_err    = sum(1 for r in active + watchlist if r.error)

    def tile(label, val, color=None):
        return html.Div([
            html.Div(label, style={"fontSize": "11px", "color": C["muted"],
                                   "marginBottom": "6px", "textTransform": "uppercase",
                                   "letterSpacing": "0.04em"}),
            html.Div(str(val), style={"fontSize": "24px", "fontWeight": 600,
                                      "color": color or C["text"]}),
        ], style={"background": C["surface2"], "padding": "1rem 1.25rem",
                  "borderRadius": "10px"})

    return html.Div([
        tile("Active holdings", n_pos),
        tile("Trim candidates", n_strong + n_cand,
             C["red_fg"] if n_strong else C["amber_fg"] if n_cand else C["text"]),
        tile("Strong trim", n_strong, C["red_fg"] if n_strong else C["text"]),
        tile("Watchlist", n_wl),
        tile("Data errors", n_err, C["red_fg"] if n_err else C["text"]),
    ], style={
        "display": "grid",
        "gridTemplateColumns": "repeat(5, minmax(0, 1fr))",
        "gap": "12px",
    })


def build_capital_breakdown_section(active: list) -> html.Div:
    """
    'Capital Breakdown' block placed above the Portfolio Exposure section.
    Shows total portfolio value split into: deployed, non-tracked buckets, cash.
    TOTAL_PORTFOLIO_VALUE and EXTRA_BUCKETS are configured at the top of this file.
    """
    # Skip entirely when the user has not configured a total
    if TOTAL_PORTFOLIO_VALUE <= 0:
        return html.Div(
            html.Div([
                html.Span("⚙  ", style={"color": C["amber_fg"]}),
                html.Span(
                    "Total portfolio value not configured. "
                    "True portfolio allocation is incomplete. "
                    "Set ",
                    style={"fontSize": "11px", "color": C["muted"]},
                ),
                html.Code("TOTAL_PORTFOLIO_VALUE",
                          style={"fontSize": "11px", "color": C["amber_fg"]}),
                html.Span(" as an environment variable or in app.py.",
                          style={"fontSize": "11px", "color": C["muted"]}),
            ], style={
                "background": C["surface2"], "borderRadius": "8px",
                "padding": "8px 14px", "border": f"1px solid {C['border']}",
                "fontSize": "11px",
            }),
            style=_MB,
        )

    bd = pe.compute_capital_breakdown(active, TOTAL_PORTFOLIO_VALUE, EXTRA_BUCKETS)

    def _row(label, value, pct, color=None):
        return html.Div([
            html.Span(label, style={
                "fontSize": "12px", "color": C["muted"],
                "display": "inline-block", "minWidth": "160px",
            }),
            html.Span(
                f"${value:,.0f}",
                style={"fontSize": "13px", "fontWeight": 600,
                       "color": color or C["text"],
                       "display": "inline-block", "minWidth": "90px"},
            ),
            html.Span(
                f"({pct:.1f}%)",
                style={"fontSize": "12px", "color": C["muted"]},
            ),
        ], style={"marginBottom": "4px"})

    rows = [
        _row("Deployed (active positions)", bd.deployed_value, bd.deployed_pct,
             color=C["blue_fg"]),
    ]
    for label, val, pct in bd.extra_items:
        rows.append(_row(label, val, pct))
    rows.append(_row("Cash / undeployed", bd.cash_value, bd.cash_pct,
                     color=C["green_fg"] if bd.cash_value > 0 else C["muted"]))

    warn_style = {"fontSize": "12px", "color": C["amber_fg"],
                  "marginTop": "6px", "fontWeight": 500, "lineHeight": "1.45"}
    warn = None
    if not bd.reconciled:
        extras_sum = sum(v for _, v, _ in bd.extra_items)
        components_sum = bd.deployed_value + extras_sum
        difference = components_sum - bd.total_value
        breakdown_total = bd.deployed_value + extras_sum + bd.cash_value
        lines = []
        if difference > 0:
            lines.append(
                f"⚠ Components exceed total portfolio by ${difference:,.0f}"
            )
        elif difference < 0:
            lines.append(
                f"⚠ ${-difference:,.0f} unaccounted (likely cash or missing positions)"
            )
        lines.append(
            f"Breakdown total: ${breakdown_total:,.0f} vs Declared total: ${bd.total_value:,.0f}"
        )
        warn = html.Div(
            [html.Div(line, style=warn_style) for line in lines],
            style={"marginTop": "4px"},
        )

    return html.Div([
        section_hdr("Capital Breakdown"),
        html.Div(style={"marginBottom": "8px"}),
        html.Div([
            html.Span("Total Portfolio", style={
                "fontSize": "12px", "color": C["muted"],
                "display": "inline-block", "minWidth": "160px",
            }),
            html.Span(f"${bd.total_value:,.0f}", style={
                "fontSize": "14px", "fontWeight": 700, "color": C["text"],
            }),
        ], style={"marginBottom": "8px"}),
        html.Div(rows),
        *([warn] if warn else []),
    ], style=_MB)


def build_exposure_section(active: list, sector_status_map: dict) -> html.Div:
    """
    Portfolio Exposure section: donut + concentration bar + alignment table.
    Placed between metric tiles and alerts.
    """
    # ── Empty state ──────────────────────────────────────────────────────────
    # Filter to positions that actually have price data
    priced = [r for r in active if not getattr(r, "error", None)
              and pe._market_value(r) is not None]

    if not priced:
        return html.Div([
            section_hdr("Portfolio Exposure"),
            html.Div("Add positions to see exposure breakdown.",
                     style={"fontSize": "13px", "color": C["muted"],
                            "marginTop": "8px"}),
        ], style=_MB)

    slices, total_val = pe.compute_sector_exposure(active)
    conc_rows         = pe.compute_concentration(active)
    align_rows        = pe.compute_sector_alignment(active, sector_status_map)
    extra_count       = max(0, len(priced) - 10)

    # ── Shared colors + helpers ───────────────────────────────────────────────
    _DONUT_COLORS = [
        "#3b82f6", "#10b981", "#f59e0b", "#ef4444",
        "#8b5cf6", "#ec4899", "#14b8a6", "#f97316",
        "#84cc16", "#06b6d4", "#a855f7",
    ]

    def _pie_layout(center_text: str, clickable: bool = True) -> dict:
        return dict(
            margin        = dict(t=10, b=10, l=10, r=10),
            paper_bgcolor = "rgba(0,0,0,0)",
            plot_bgcolor  = "rgba(0,0,0,0)",
            showlegend    = True,
            legend        = dict(
                font        = dict(size=11, color=C["text"]),
                bgcolor     = "rgba(0,0,0,0)",
                x           = 1.02, y = 0.5,
                orientation = "v",
            ),
            annotations   = [dict(
                text      = f"<b>{center_text}</b>",
                x=0.5, y=0.5,
                font      = dict(size=14, color=C["text"]),
                showarrow = False,
            )],
            height    = 300,
            clickmode = "event+select" if clickable else False,
        )

    def _make_donut(labels, values, hover_lines, colors, center: str,
                    clickable: bool = True) -> go.Figure:
        n = len(labels)
        c = (colors * ((n // len(colors)) + 1))[:n]
        fig = go.Figure(go.Pie(
            labels        = labels,
            values        = values,
            hole          = 0.62,
            textinfo      = "none",
            hovertemplate = (
                "<b>%{label}</b><br>"
                "$%{value:,.0f} · %{percent:.1%}<br>"
                "%{customdata[0]}<extra></extra>"
            ),
            customdata = [[h] for h in hover_lines],
            marker     = dict(colors=c, line=dict(color=C["bg"], width=2)),
        ))
        fig.update_layout(**_pie_layout(center, clickable=clickable))
        return fig

    def _chart_title(text: str) -> html.Div:
        return html.Div(text, style={
            "fontSize": "11px", "fontWeight": 600, "color": C["muted"],
            "textTransform": "uppercase", "letterSpacing": "0.04em",
            "marginBottom": "4px",
        })

    def _empty_donut(msg: str) -> html.Div:
        return html.Div(msg, style={
            "height": "300px", "display": "flex",
            "alignItems": "center", "justifyContent": "center",
            "fontSize": "12px", "color": C["muted"],
            "fontStyle": "italic", "textAlign": "center",
            "padding": "0 12px",
        })

    # ── Capital breakdown for right donut ─────────────────────────────────────
    bd_cb = None
    if TOTAL_PORTFOLIO_VALUE > 0:
        bd_cb = pe.compute_capital_breakdown(
            active, TOTAL_PORTFOLIO_VALUE, EXTRA_BUCKETS,
        )

    # ── Left donut: Sector Allocation (deployed active sectors only) ──────────
    left_labels = [s.sector for s in slices]
    left_values = [float(s.value) for s in slices]
    left_hover  = [f"{s.n_positions} position(s)" for s in slices]
    left_center = f"${total_val:,.0f}"
    left_graph  = dcc.Graph(
        id     = "donut-sector",
        figure = _make_donut(left_labels, left_values, left_hover,
                             _DONUT_COLORS, left_center, clickable=True),
        config = {"displayModeBar": False},
        style  = {"height": "300px"},
    ) if left_labels else _empty_donut("No deployed positions with prices available.")

    # ── Right donut: Total Portfolio (Cash+BOXX + each sector + NVDA) ─────────
    _TOTAL_COLORS = [
        "#64748b",                                   # Cash+BOXX → muted first
        "#3b82f6", "#10b981", "#f59e0b", "#ef4444",
        "#8b5cf6", "#ec4899", "#14b8a6", "#f97316",
        "#84cc16", "#06b6d4", "#a855f7",
    ]
    if bd_cb is not None:
        boxx_amt  = sum(v for lbl, v in EXTRA_BUCKETS
                        if str(lbl).strip().upper() == "BOXX")
        nvda_amt  = sum(v for lbl, v in EXTRA_BUCKETS
                        if str(lbl).strip().upper() == "NVDA")
        cash_boxx = float(bd_cb.cash_value) + float(boxx_amt)

        right_parts: list[tuple[str, float, str]] = []
        if cash_boxx > 0:
            right_parts.append((
                "Cash + BOXX", cash_boxx,
                f"Cash ${float(bd_cb.cash_value):,.0f} + BOXX ${float(boxx_amt):,.0f}",
            ))
        for s in slices:
            right_parts.append((
                s.sector, float(s.value), f"{s.n_positions} position(s)",
            ))
        if nvda_amt > 0:
            right_parts.append((
                "NVDA", float(nvda_amt),
                "Non-tracked (configured NVDA_VALUE)",
            ))
        right_parts.sort(key=lambda x: -x[1])
        r_labels = [p[0] for p in right_parts]
        r_values = [p[1] for p in right_parts]
        r_hover  = [p[2] for p in right_parts]
        right_graph = dcc.Graph(
            id     = "donut-total-portfolio",
            figure = _make_donut(r_labels, r_values, r_hover,
                                 _TOTAL_COLORS, f"${sum(r_values):,.0f}",
                                 clickable=False),
            config = {"displayModeBar": False},
            style  = {"height": "300px"},
        )
    else:
        right_graph = _empty_donut(
            "Set TOTAL_PORTFOLIO_VALUE and BOXX_VALUE\nto see full portfolio allocation."
        )

    # ── Row 1: two donuts side by side ────────────────────────────────────────
    _half = {"flex": "1 1 45%", "minWidth": "280px"}
    donuts_row = html.Div([
        html.Div([_chart_title("Sector Allocation"),         left_graph],  style=_half),
        html.Div([_chart_title("Total Portfolio Allocation"), right_graph], style=_half),
    ], style={"display": "flex", "gap": "1rem", "flexWrap": "wrap",
              "marginBottom": "0.5rem"})

    # ── Row 2: Top 10 Positions bar chart (full width, below donuts) ─────────
    _BAR_COLOR_MAP = {
        "green":   C["green_fg"],
        "amber":   C["amber_fg"],
        "red":     C["red_fg"],
        "neutral": C["muted"],
    }
    bar_colors = [_BAR_COLOR_MAP.get(r.color, C["muted"]) for r in conc_rows]
    bar_hover  = [
        (f"<b>{r.ticker}</b><br>"
         f"Weight: {r.weight_pct:.1f}%"
         + (f"<br>Target: {r.target_pct:.1f}%" if r.target_pct is not None else "")
         + (f"<br>Diff: {r.diff_pct:+.1f}%" if r.diff_pct is not None else "")
         + f"<br>Value: ${r.value:,.0f}"
         + "<extra></extra>")
        for r in conc_rows
    ]
    bar_fig = go.Figure(go.Bar(
        x             = [r.weight_pct for r in conc_rows],
        y             = [r.ticker     for r in conc_rows],
        orientation   = "h",
        marker_color  = bar_colors,
        hovertemplate = bar_hover,
        text          = [f"{r.weight_pct:.1f}%" for r in conc_rows],
        textposition  = "outside",
        textfont      = dict(size=11, color=C["text"]),
    ))
    bar_fig.update_layout(
        margin        = dict(t=10, b=10, l=10, r=50),
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(0,0,0,0)",
        xaxis         = dict(showgrid=True, gridcolor=C["border"],
                             ticksuffix="%", title=None,
                             color=C["muted"], tickfont=dict(size=10)),
        yaxis         = dict(autorange="reversed",
                             color=C["text"], tickfont=dict(size=11), title=None),
        height        = max(180, len(conc_rows) * 36),
        showlegend    = False,
    )
    bar_row = html.Div([
        _chart_title(
            "Top 10 Positions by Weight"
            + (f" (+{extra_count} more)" if extra_count else "")
        ),
        dcc.Graph(
            id     = "bar-concentration",
            figure = bar_fig,
            config = {"displayModeBar": False},
        ),
    ], style={"marginBottom": "0.5rem"})

    charts_row = html.Div([donuts_row, bar_row])

    # ── Sector filter indicator (populated by callback) ───────────────────────
    filter_badge = html.Div(id="exposure-sector-filter-badge",
                            style={"fontSize": "12px", "color": C["blue_fg"],
                                   "marginTop": "4px", "minHeight": "18px"})

    # ── Alignment table ───────────────────────────────────────────────────────
    _VERDICT_COLORS = {
        "red":     (C["red_bg"],     C["red_fg"]),
        "amber":   (C["amber_bg"],   C["amber_fg"]),
        "blue":    (C["blue_bg"],    C["blue_fg"]),
        "neutral": (C["neutral_bg"], C["neutral_fg"]),
    }

    def _align_cell(content, color_key="neutral", bold=False):
        bg, fg = _VERDICT_COLORS.get(color_key, _VERDICT_COLORS["neutral"])
        return html.Td(content, style={
            "padding": "6px 10px",
            "fontSize": "12px",
            "color": fg,
            "backgroundColor": bg if color_key != "neutral" else "transparent",
            "fontWeight": 600 if bold else 400,
            "borderBottom": f"1px solid {C['border']}",
        })

    def _quad_color(q):
        return {"Leading": "green", "Improving": "blue",
                "Weakening": "amber", "Lagging": "red"}.get(q, "neutral")

    # ── Portfolio signal line ─────────────────────────────────────────────────
    signal_msg, signal_sev = pe.compute_portfolio_signal(align_rows)
    signal_icon  = "⚠" if signal_sev == "warn" else "✓"
    signal_color = C["amber_fg"] if signal_sev == "warn" else C["green_fg"]
    signal_line  = html.Div(
        f"{signal_icon}  {signal_msg}",
        style={
            "fontSize": "12px", "color": signal_color,
            "fontWeight": 500, "marginTop": "12px", "marginBottom": "4px",
        },
    )

    # ── Alignment table ───────────────────────────────────────────────────────
    # Determine which baseline mode was used and surface the description.
    # Default mode is "equal_weight"; pass baseline_mode arg here when adding
    # future modes (spy_weight, custom_model).
    _baseline_mode = "equal_weight"
    baseline_desc  = pe.BASELINE_DESCRIPTIONS.get(_baseline_mode, "")
    any_auto = any(ar.target_source == "Auto equal-weight" for ar in align_rows)

    align_header = html.Tr([
        html.Th(h, style={
            "padding": "6px 10px", "fontSize": "11px", "fontWeight": 600,
            "color": C["muted"], "textTransform": "uppercase",
            "letterSpacing": "0.04em", "borderBottom": f"1px solid {C['border']}",
            "textAlign": "left",
        })
        for h in ["Sector", "Quadrant", "Your Weight", "Target", "Type", "Diff", "Verdict"]
    ])

    align_body_rows = []
    for ar in align_rows:
        vc         = ar.verdict_color
        diff_str   = f"{ar.diff_pct:+.1f}%" if ar.diff_pct else "—"
        diff_color = ("red" if (ar.diff_pct or 0) > 0 else
                      "blue" if (ar.diff_pct or 0) < 0 else "neutral")
        src_color  = "neutral" if ar.target_source == "Manual target" else "amber"
        tgt_str    = f"{ar.target_pct:.1f}%" if ar.target_pct else "—"
        align_body_rows.append(html.Tr([
            _align_cell(ar.sector),
            _align_cell(ar.quadrant, _quad_color(ar.quadrant)),
            _align_cell(f"{ar.weight_pct:.1f}%"),
            _align_cell(tgt_str),
            _align_cell(ar.target_source, src_color),
            _align_cell(diff_str, diff_color),
            _align_cell(ar.verdict, vc, bold=True),
        ]))

    auto_note = html.Div(
        "* Auto equal-weight = 100% ÷ N held sectors. "
        "Set target_weight on any position to switch that sector to manual.",
        style={"fontSize": "11px", "color": C["muted"],
               "fontStyle": "italic", "marginTop": "4px"},
    ) if any_auto else None

    align_header_label = html.Div([
        html.Span("Sector Exposure vs Market Regime", style={
            "fontSize": "11px", "fontWeight": 600, "color": C["muted"],
            "textTransform": "uppercase", "letterSpacing": "0.04em",
        }),
        html.Br(),
        html.Span(
            f"Baseline: {baseline_desc}.",
            style={"fontSize": "11px", "color": C["muted"],
                   "fontStyle": "italic", "fontWeight": 400},
        ),
    ], style={"marginBottom": "6px"})

    alignment_table = html.Div([
        align_header_label,
        html.Table([
            html.Thead(align_header),
            html.Tbody(align_body_rows),
        ], style={
            "width": "100%", "borderCollapse": "collapse",
            "background": C["surface"],
        }),
        *([auto_note] if auto_note else []),
    ]) if align_rows else None

    # ── Rebalancing plan ──────────────────────────────────────────────────────
    rebal_rows = pe.compute_rebalancing_plan(align_rows, total_val)

    rebal_section = None
    if rebal_rows:
        # ── Summary lines ────────────────────────────────────────────────
        trims       = [r for r in rebal_rows if r.direction == "Trim"][:2]
        true_adds   = [r for r in rebal_rows if r.direction == "Add"][:2]
        avoid_adds  = [r for r in rebal_rows if r.direction == "Underweight, but avoid add"]

        summary_children = []
        for r in trims:
            summary_children.append(html.Div(
                f"Trim ~${r.trim_amount:,.0f} from {r.sector} to reach target",
                style={"color": C["red_fg"], "fontWeight": 500},
            ))
        for r in true_adds:
            summary_children.append(html.Div(
                f"Add ~${r.add_amount:,.0f} to {r.sector} to reach target "
                f"({r.quadrant})",
                style={"color": C["blue_fg"], "fontWeight": 500},
            ))
        if avoid_adds:
            labels = ", ".join(r.sector for r in avoid_adds)
            summary_children.append(html.Div(
                f"Underweight but weak: {labels} — do not add until quadrant improves.",
                style={"color": C["amber_fg"]},
            ))
        if not summary_children:
            summary_children.append(html.Div(
                "All sectors within tolerance band.",
                style={"color": C["muted"]},
            ))

        rebal_summary = html.Div(summary_children, style={
            "fontSize": "12px", "marginBottom": "8px",
        })

        # ── Rebalancing table ─────────────────────────────────────────────
        def _rebal_cell(content, color=None):
            return html.Td(content, style={
                "padding": "5px 10px", "fontSize": "12px",
                "color": color or C["text"],
                "borderBottom": f"1px solid {C['border']}",
            })

        rebal_header = html.Tr([
            html.Th(h, style={
                "padding": "5px 10px", "fontSize": "11px", "fontWeight": 600,
                "color": C["muted"], "textTransform": "uppercase",
                "letterSpacing": "0.04em",
                "borderBottom": f"1px solid {C['border']}",
                "textAlign": "left",
            })
            for h in ["Sector", "Quadrant", "Actual %", "Target %",
                       "Gap %", "Amount", "Action"]
        ])

        # Consistent direction → color + display text
        _dir_cfg = {
            "Trim":                        (C["red_fg"],    600),
            "Add":                         (C["blue_fg"],   600),
            "Underweight, but avoid add":  (C["amber_fg"],  400),
            "Hold":                        (C["muted"],     400),
        }

        rebal_body = []
        for r in rebal_rows:
            dir_color, dir_weight = _dir_cfg.get(r.direction, (C["text"], 400))

            # Amount column: "Trim $X" / "+$X" / "(gap $X)" / "—"
            if r.direction == "Trim":
                amt_str  = f"−${r.trim_amount:,.0f}"
                amt_color = C["red_fg"]
            elif r.direction == "Add":
                amt_str  = f"+${r.add_amount:,.0f}"
                amt_color = C["blue_fg"]
            elif r.direction == "Underweight, but avoid add":
                amt_str  = f"(gap ${r.add_amount:,.0f})"
                amt_color = C["amber_fg"]
            else:
                amt_str  = "—"
                amt_color = C["muted"]

            rebal_body.append(html.Tr([
                _rebal_cell(r.sector),
                _rebal_cell(r.quadrant, color={
                    "Leading":  C["green_fg"],
                    "Improving":C["blue_fg"],
                    "Weakening":C["amber_fg"],
                    "Lagging":  C["red_fg"],
                }.get(r.quadrant, C["muted"])),
                _rebal_cell(f"{r.actual_pct:.1f}%"),
                _rebal_cell(f"{r.target_pct:.1f}%"),
                _rebal_cell(f"{r.diff_pct:+.1f}%"),
                _rebal_cell(amt_str, color=amt_color),
                html.Td(r.direction, style={
                    "padding": "5px 10px", "fontSize": "12px",
                    "borderBottom": f"1px solid {C['border']}",
                    "color": dir_color, "fontWeight": dir_weight,
                }),
            ]))

        rebal_section = html.Div([
            html.Div("Rebalancing Plan", style={
                "fontSize": "11px", "fontWeight": 600, "color": C["muted"],
                "textTransform": "uppercase", "letterSpacing": "0.04em",
                "marginTop": "14px", "marginBottom": "6px",
            }),
            rebal_summary,
            html.Table([
                html.Thead(rebal_header),
                html.Tbody(rebal_body),
            ], style={
                "width": "100%", "borderCollapse": "collapse",
                "background": C["surface"],
            }),
            html.Div([
                f"Hold band: ±{pe._HOLD_BAND_PCT:.0f}% — sectors within this band show 'Hold'. ",
                html.Span(
                    "'Underweight, but avoid add' = below target, but RRG quadrant is Weakening/Lagging.",
                    style={"color": C["amber_fg"]},
                ),
            ], style={"fontSize": "11px", "color": C["muted"],
                      "fontStyle": "italic", "marginTop": "6px"}),
        ])

    exposure_note = html.Div(
        "Sector Allocation uses deployed active holdings only. "
        "Total Portfolio Allocation includes Cash + BOXX and configured non-tracked sleeves. "
        "Alignment and concentration tables use deployed active holdings only.",
        style={"fontSize": "11px", "color": C["muted"],
               "fontStyle": "italic", "marginBottom": "8px"},
    ) if TOTAL_PORTFOLIO_VALUE > 0 else None

    children = [
        section_hdr("Portfolio Exposure"),
        html.Div(style={"marginBottom": "6px"}),
        *([exposure_note] if exposure_note else []),
        charts_row,
        filter_badge,
        signal_line,
    ]
    if alignment_table:
        children.append(alignment_table)
    if rebal_section:
        children.append(rebal_section)

    return html.Div(children, style=_MB)


def build_alerts_panel(active):
    alerts = build_alerts_list(active)
    if not alerts:
        return card(
            html.Div("No active alerts — all positions in normal range.",
                     style={"fontSize": "13px", "color": C["muted"]}),
            pad="14px 16px",
        )
    rows = []
    for a in alerts:
        result = next((r for r in active if r.ticker == a.ticker), None)
        bg, fg = {
            "red":   (C["red_bg"],   C["red_fg"]),
            "amber": (C["amber_bg"], C["amber_fg"]),
            "blue":  (C["blue_bg"],  C["blue_fg"]),
        }.get(a.severity, (C["neutral_bg"], C["neutral_fg"]))

        n_flags  = result.n_flags if result and not result.error else 0
        last_upd = (result.last_updated or "—") if result else "—"
        flags    = (result.flags or [])[:4] if result else []
        flag_txt = " · ".join(flags) if flags else a.detail

        dir_note = ""
        if result and result.sector_direction and result.sector_status:
            if (result.sector_status == "Leading"
                    and result.sector_direction == "↓ deteriorating"):
                dir_note = f"  ⚠ Sector leading but fading ({result.sector_direction})"
            elif (result.sector_status == "Weakening"
                    and result.sector_direction == "↓ deteriorating"):
                dir_note = f"  ⚠⚠ Sector deteriorating ({result.sector_direction})"
            elif result.sector_direction not in ("→ stalling", "insufficient history", ""):
                dir_note = f"  Sector {result.sector_status}: {result.sector_direction}"

        rows.append(html.Div([
            html.Div([
                html.Span(a.ticker, style={"fontWeight": 700, "fontSize": "14px",
                                           "marginRight": "10px", "color": fg}),
                badge(a.severity.upper(), bg, fg),
                html.Span(f"  {n_flags} flag{'s' if n_flags != 1 else ''}",
                          style={"fontSize": "12px", "color": fg, "marginLeft": "10px"}),
                html.Span(f"  ·  data {last_upd}",
                          style={"fontSize": "11px", "color": fg,
                                 "marginLeft": "10px", "opacity": "0.75"}),
            ], style={"display": "flex", "alignItems": "center",
                      "marginBottom": "5px", "flexWrap": "wrap", "gap": "4px"}),
            html.Div(flag_txt or "—", style={"fontSize": "12px", "color": fg}),
            html.Div(dir_note, style={"fontSize": "11px", "color": fg,
                                      "marginTop": "3px", "opacity": "0.85"})
            if dir_note else html.Span(),
        ], style={"background": bg, "padding": "12px 16px",
                  "borderRadius": "8px", "marginBottom": "8px"}))
    return html.Div(rows)


def _portfolio_rows(results):
    rows = []
    for r in results:
        err = bool(r.error)
        rows.append({
            "Ticker":  r.ticker,
            "Status":  r.status,
            "Sector":  r.sector or "—",
            "Price":   "ERR" if err else _f(r.current_price, 2),
            "Gain%":   "—"   if err else _pct(r.pct_gain),
            ">20EMA":  "—"   if err else _pct(r.pct_above_20ema),
            ">50EMA":  "—"   if err else _pct(r.pct_above_50ema),
            "RSI":     "—"   if err else _f(r.rsi, 0),
            "ATR×":    "—"   if err else _f(r.atr_multiple, 1),
            "Wt%":     _f(r.current_weight, 1) if r.current_weight else "—",
            "Flags":   str(r.n_flags),
            "Verdict": (r.error or "")[:50] if err else r.verdict,
            "Updated": r.last_updated or "—",
        })
    return rows


def _watchlist_rows(results, sector_statuses: dict[str, SectorStatus] | None = None):
    sector_statuses = sector_statuses or {}
    rows = []
    for r in results:
        err = bool(r.error)
        ss = sector_statuses.get(r.sector)
        signal = ""
        if ss and ss.sufficient_data:
            if ss.quadrant == "Improving" and "strengthening" in ss.direction:
                signal = "★ Sector accelerating"
            elif ss.quadrant == "Leading" and "strengthening" in ss.direction:
                signal = "★ Sector strong"
            elif ss.quadrant in ("Weakening", "Lagging") and "deteriorating" in ss.direction:
                signal = "⚠ Sector deteriorating"
            elif ss.quadrant in ("Weakening", "Lagging"):
                signal = "⚠ Sector weak"
        rows.append({
            "Ticker":        r.ticker,
            "Status":        r.status,
            "Sector":        r.sector or "—",
            "Sector Status": r.sector_status or "—",
            "Sector Signal": signal,
            "Price":         "ERR" if err else _f(r.current_price, 2),
            ">20EMA":        "—"   if err else _pct(r.pct_above_20ema),
            ">50EMA":        "—"   if err else _pct(r.pct_above_50ema),
            "RSI":           "—"   if err else _f(r.rsi, 0),
            "Updated":       r.last_updated or "—",
        })
    return rows

# ---------------------------------------------------------------------------
# Macro Regime section builder
# ---------------------------------------------------------------------------

def build_macro_regime_section(regime: mr.MacroRegime) -> html.Div:
    """Render the Macro Regime card. Handles missing key and partial data."""

    _mbottom = {"marginBottom": "1.5rem"}

    if regime.key_missing:
        return html.Div(card([
            html.Div([
                section_hdr("Macro Regime"),
                html.Span("FRED API not configured",
                          style={"fontSize": "12px", "color": C["amber_fg"],
                                 "marginLeft": "12px", "fontWeight": 600}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "8px"}),
            html.Div(
                "Set the FRED_API_KEY environment variable to enable macro regime data. "
                "Get a free key at research.stlouisfed.org/useraccount/apikeys",
                style={"fontSize": "13px", "color": C["muted"]},
            ),
        ], pad="14px 18px"), style=_mbottom)

    bg, fg = REGIME_COLORS.get(regime.label, REGIME_COLORS["Unknown"])

    def kv(label, val, color=None):
        return html.Div([
            html.Span(label, style={"fontSize": "11px", "color": C["muted"],
                                    "textTransform": "uppercase",
                                    "letterSpacing": "0.04em", "marginRight": "6px"}),
            html.Span(str(val) if val is not None else "—",
                      style={"fontSize": "13px", "fontWeight": 500,
                             "color": color or C["text"]}),
        ], style={"marginRight": "20px", "marginBottom": "4px"})

    indicators = html.Div([
        kv("10Y Yield",      f"{regime.dgs10:.2f}%" if regime.dgs10 else "—"),
        kv("2Y Yield",       f"{regime.dgs2:.2f}%" if regime.dgs2 else "—"),
        kv("10Y−2Y Spread",  f"{regime.spread_10y2y:+.2f}%" if regime.spread_10y2y is not None else "—"),
        kv("Fed Funds",      f"{regime.fedfunds:.2f}%" if regime.fedfunds else "—"),
        kv("CPI YoY",        f"{regime.cpi_yoy:.1f}%" if regime.cpi_yoy else "—"),
        kv("Unemployment",   f"{regime.unrate:.1f}%" if regime.unrate else "—"),
        kv("VIX",            f"{regime.vix:.1f}" if regime.vix else "—"),
    ], style={"display": "flex", "flexWrap": "wrap"})

    components = html.Div([
        html.Span(f"Yield curve: {regime.yield_curve}", style={"marginRight": "14px"}),
        html.Span(f"Rates: {regime.rates_trend}", style={"marginRight": "14px"}),
        html.Span(f"Inflation: {regime.inflation_trend}", style={"marginRight": "14px"}),
        html.Span(f"Labor: {regime.labor_trend}", style={"marginRight": "14px"}),
        html.Span(f"Risk: {regime.risk_regime}"),
    ], style={"fontSize": "12px", "color": C["muted"], "flexWrap": "wrap",
              "display": "flex", "marginTop": "8px"})

    warnings_div = html.Div()
    if regime.warnings:
        warnings_div = html.Div(
            " · ".join(f"⚠ {w}" for w in regime.warnings),
            style={"fontSize": "11px", "color": C["amber_fg"],
                   "marginTop": "8px", "fontStyle": "italic"},
        )

    return html.Div(card([
        html.Div([
            section_hdr("Macro Regime"),
            html.Span(regime.label, style={
                "background": bg, "color": fg,
                "fontSize": "12px", "padding": "3px 10px",
                "borderRadius": "8px", "fontWeight": 700,
                "marginLeft": "12px",
            }),
            html.Span(f"  FRED data as of {regime.last_updated}",
                      style={"fontSize": "11px", "color": C["dim"],
                             "marginLeft": "10px"}),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),
        indicators,
        components,
        html.Div(regime.reason,
                 style={"fontSize": "11px", "color": C["dim"],
                        "marginTop": "8px", "fontStyle": "italic"}),
        warnings_div,
    ], pad="14px 18px"), style=_mbottom)

# ---------------------------------------------------------------------------
# Morning Scan Status section builder
# ---------------------------------------------------------------------------

def _ms_chip(text: str, color: str = "#3b82f6") -> html.Span:
    return html.Span(text, style={
        "background": color, "color": "#fff",
        "borderRadius": "5px", "padding": "2px 8px",
        "fontSize": "11px", "fontWeight": 600,
        "marginRight": "4px", "marginBottom": "3px",
        "display": "inline-block",
    })


def _verdict_color(verdict: str) -> str:
    v = verdict.lower()
    if "already owned" in v:
        return C["blue_fg"] if "blue_fg" in C else "#3b82f6"
    if "watchlist" in v:
        return "#8b5cf6"
    if "avoid" in v:
        return C["red_fg"]
    return C["green_fg"]


def _status_badge(label: str, ok: bool) -> html.Span:
    bg = "#22c55e" if ok else C["surface"]
    fg = "#fff" if ok else C["dim"]
    bd = "1px solid #22c55e" if ok else f"1px solid {C['border']}"
    return html.Span("Yes" if ok else "No", style={
        "background": bg, "color": fg, "border": bd,
        "borderRadius": "4px", "padding": "1px 7px",
        "fontSize": "11px", "fontWeight": 600,
    })


def _health_status_badge(status: str) -> html.Span:
    colours = {
        "Complete": ("#22c55e", "#fff"),
        "Partial":  (C["amber_fg"], "#000"),
        "Missing":  (C["surface"], C["dim"]),
        "Stale":    (C["amber_fg"], "#000"),
        "Parse error": (C["red_fg"], "#fff"),
    }
    bg, fg = colours.get(status, (C["surface"], C["dim"]))
    return html.Span(status, style={
        "background": bg, "color": fg,
        "borderRadius": "4px", "padding": "1px 8px",
        "fontSize": "11px", "fontWeight": 600,
    })


def build_morning_scan_section(
    active: list[dict],
    watchlist: list[dict],
) -> html.Div:
    """
    Dashboard section: Morning Scan Status.
    Placed after Macro Regime, before Sector Rotation.
    Contains 4 sub-sections:
      1. Suggested Tickers Today
      2. Suggested vs Current Holdings
      3. Forward Walk Test
      4. Automation Health
    """
    def _sg(obj, key, default=None):
        """Safe attribute/dict getter — works for both PositionResult and dict."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    active_tickers    = {_sg(p, "ticker") for p in active    if _sg(p, "ticker")}
    watchlist_tickers = {_sg(p, "ticker") for p in watchlist if _sg(p, "ticker")}

    # ── Load latest Warren AI result ─────────────────────────────────────────
    w = wl.get_latest_warren()

    # ── Build sub-sections ───────────────────────────────────────────────────

    # 1. Suggested Tickers Today
    def _sub1() -> html.Div:
        if w.freshness == "NONE":
            return html.Div([
                section_hdr("Suggested Tickers Today"),
                html.Div("No Warren AI scan found.",
                         style={"color": C["dim"], "fontSize": "12px"}),
            ])

        suggested = ms.classify_suggested_tickers(w, active_tickers, watchlist_tickers)
        freshness_badge = html.Span(
            w.freshness,
            style={
                "background": C["green_fg"] if w.freshness == "FRESH" else C["amber_fg"],
                "color": "#fff", "borderRadius": "5px", "padding": "2px 9px",
                "fontSize": "11px", "fontWeight": 700, "marginLeft": "10px",
            },
        )

        rows: list[dict] = []
        for s in suggested:
            theme_names = tr.get_ticker_themes(s.ticker)
            rows.append({
                "Ticker": ("★ " if s.is_top3 else "") + s.ticker,
                "Sector": s.sector_etf or "—",
                "Themes": ", ".join(theme_names) if theme_names else "—",
                "Rank":   str(s.rank) if s.rank else "—",
                "Setup":  s.setup or "—",
                "Entry":  getattr(s, "entry_disp", None) or "—",
                "SL":     getattr(s, "sl_disp", None) or "—",
                "TP":     getattr(s, "tp_disp", None) or "—",
                "R/R":    getattr(s, "rr_disp", None) or "—",
                "Status": s.portfolio_status,
                "Verdict": s.verdict,
                "Scan Date": s.scan_date,
            })

        tbl = dash_table.DataTable(
            data=rows,
            columns=[{"name": c, "id": c} for c in
                     ["Ticker", "Sector", "Themes", "Rank", "Setup", "Entry", "SL", "TP", "R/R",
                      "Status", "Verdict", "Scan Date"]],
            style_table={"overflowX": "auto"},
            style_cell={**_CELL, "minWidth": "60px"},
            style_header={**_HEADER},
            style_data_conditional=[
                {"if": {"filter_query": '{Status} = "Already owned"'},
                 "color": C["blue_fg"], "fontWeight": 600},
                {"if": {"filter_query": '{Verdict} = "New candidate"'},
                 "color": C["green_fg"], "fontWeight": 600},
                {"if": {"filter_query": '{Verdict} = "Watchlist candidate"'},
                 "color": "#8b5cf6", "fontWeight": 600},
                {"if": {"filter_query": '{Verdict} = "Avoid or not actionable"'},
                 "color": C["red_fg"]},
            ],
            page_size=20,
        )
        return html.Div([
            html.Div([
                section_hdr("Suggested Tickers Today"),
                freshness_badge,
                html.Span(f" · Scan: {w.date}",
                          style={"fontSize": "11px", "color": C["dim"]}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "8px"}),
            tbl,
            html.Div(
                "Full plan tickers have entry, SL, target, and R/R. "
                "Others are mentioned only — levels missing.",
                style={"fontSize": "10px", "color": C["dim"],
                       "fontStyle": "italic", "marginTop": "6px"},
            ),
        ])

    # 2. Suggested vs Current Holdings
    def _sub2() -> html.Div:
        if w.freshness == "NONE":
            return html.Div()

        suggested = ms.classify_suggested_tickers(w, active_tickers, watchlist_tickers)
        owned     = [s for s in suggested if s.portfolio_status == "Already owned"]
        wl_items  = [s for s in suggested if s.portfolio_status == "Watchlist"]
        new_cands = [s for s in suggested if s.portfolio_status == "New"]

        summary = (
            f"Today: {len(suggested)} suggested tickers · "
            f"{len(owned)} already owned · "
            f"{len(new_cands)} new candidates"
            + (f" · {len(wl_items)} watchlist" if wl_items else "")
        )

        def _chip_group(label: str, items: list, color: str) -> html.Div:
            if not items:
                return html.Div()
            chips = [_ms_chip(s.ticker, color) for s in items]
            return html.Div([
                html.Span(label + ": ", style={"fontSize": "11px", "color": C["dim"],
                                               "fontWeight": 600, "marginRight": "4px"}),
                *chips,
            ], style={"marginBottom": "4px", "display": "flex", "flexWrap": "wrap",
                      "alignItems": "center"})

        return html.Div([
            html.Div(summary, style={"fontSize": "12px", "color": C["muted"],
                                     "marginBottom": "8px"}),
            _chip_group("Already owned", owned, "#3b82f6"),
            _chip_group("New candidates", new_cands, "#22c55e"),
            _chip_group("Watchlist", wl_items, "#8b5cf6"),
        ])

    # 3. Forward Walk Test (with benchmark enrichment)
    def _sub3() -> html.Div:
        try:
            walk_rows = ms.get_forward_walk_results(14, active_tickers, watchlist_tickers)
        except Exception as exc:
            return html.Div([
                section_hdr("Forward Walk Test"),
                html.Div(f"Error computing forward walk: {exc}",
                         style={"color": C["red_fg"], "fontSize": "12px"}),
            ])

        if not walk_rows:
            return html.Div([
                section_hdr("Forward Walk Test"),
                html.Div("No prior scan data available for forward performance tracking.",
                         style={"color": C["dim"], "fontSize": "12px"}),
            ])

        # Enrich with benchmark returns
        try:
            enriched = ms.enrich_forward_walk_with_benchmarks(walk_rows)
            summ = ms.forward_walk_summary_enriched(enriched)
        except Exception:
            enriched = walk_rows          # type: ignore[assignment]
            summ = ms.forward_walk_summary(walk_rows)

        # Summary lines
        avg_str  = f"{summ.avg_return:+.2f}%" if summ.avg_return is not None else "—"
        win_str  = f"{summ.win_rate:.1f}%"    if summ.win_rate   is not None else "—"
        mfe_str  = f"{summ.avg_mfe:+.1f}%"   if summ.avg_mfe    is not None else "—"
        mae_str  = f"{summ.avg_mae:+.1f}%"   if summ.avg_mae    is not None else "—"

        exc_mag7_str = (
            f"{summ.avg_excess_mag7:+.2f}%"
            if hasattr(summ, "avg_excess_mag7") and summ.avg_excess_mag7 is not None
            else "—"
        )
        exc_qqq_str  = (
            f"{summ.avg_excess_qqq:+.2f}%"
            if hasattr(summ, "avg_excess_qqq") and summ.avg_excess_qqq is not None
            else "—"
        )
        beat_mag7_str = (
            f"{summ.pct_beat_mag7:.0f}%"
            if hasattr(summ, "pct_beat_mag7") and summ.pct_beat_mag7 is not None
            else "—"
        )
        beat_qqq_str = (
            f"{summ.pct_beat_qqq:.0f}%"
            if hasattr(summ, "pct_beat_qqq") and summ.pct_beat_qqq is not None
            else "—"
        )

        winners_txt = "  ".join(
            f"{t} {r:+.1f}%" for t, r in summ.top_winners if r > 0
        ) or "none"
        losers_txt = "  ".join(
            f"{t} {r:+.1f}%" for t, r in summ.worst_losers if r <= 0
        ) or "none"

        scan_days_count = len({r.scan_date for r in walk_rows})
        lvl_counts = ms.summarize_forward_walk_level_outcomes(walk_rows)
        lvl_line = ms.format_level_outcome_summary(lvl_counts)
        summary_lines = html.Div([
            html.Div(
                lvl_line,
                style={"fontSize": "11px", "color": C["muted"], "marginBottom": "6px",
                       "lineHeight": "1.45"},
            ),
            html.Div(
                f"Last {scan_days_count} scan day(s): {summ.with_prices} tickers · "
                f"{summ.positive} positive · {summ.negative} negative · "
                f"avg return {avg_str} · win rate {win_str}",
                style={"fontSize": "12px", "color": C["muted"], "marginBottom": "2px"},
            ),
            html.Div(
                f"Avg MFE {mfe_str} · Avg MAE {mae_str} · "
                f"Top: {winners_txt}  ·  Laggards: {losers_txt}",
                style={"fontSize": "11px", "color": C["dim"], "marginBottom": "2px"},
            ),
            html.Div(
                f"vs MAG7: avg excess {exc_mag7_str} · {beat_mag7_str} beat MAG7  "
                f"vs QQQ: avg excess {exc_qqq_str} · {beat_qqq_str} beat QQQ",
                style={"fontSize": "11px", "color": C["dim"], "marginBottom": "8px"},
            ),
        ])

        # Table rows
        def _pf(v, prefix="", suffix="%") -> str:
            if v is None:
                return "—"
            sign = "+" if v > 0 else ""
            return f"{prefix}{sign}{v:.1f}{suffix}"

        tbl_data: list[dict] = []
        for r in enriched:
            ret_str = f"{r.return_pct:+.2f}%" if r.return_pct is not None else "Missing"
            scan_px = f"${r.scan_price:.2f}"   if r.scan_price    is not None else "—"
            cur_px  = f"${r.current_price:.2f}" if r.current_price is not None else "—"
            mag7_r  = getattr(r, "mag7_ret",   None)
            qqq_r   = getattr(r, "qqq_ret",    None)
            xlk_r   = getattr(r, "xlk_ret",    None)
            spy_r   = getattr(r, "spy_ret",    None)
            ex_mag7 = getattr(r, "excess_mag7", None)
            ex_qqq  = getattr(r, "excess_qqq",  None)
            tbl_data.append({
                "Date":     r.scan_date,
                "Ticker":   r.ticker,
                "Setup":    r.setup or "—",
                "Entry":    getattr(r, "entry_disp", None) or "—",
                "SL":       getattr(r, "sl_disp", None) or "—",
                "TP":       getattr(r, "tp_disp", None) or "—",
                "R/R":      getattr(r, "rr_disp", None) or "—",
                "Outcome":  getattr(r, "outcome", None) or "—",
                "Scan $":   scan_px,
                "Now $":    cur_px,
                "Return":   ret_str,
                "Days":     str(r.days_since),
                "MAG7":     _pf(mag7_r),
                "QQQ":      _pf(qqq_r),
                "XLK":      _pf(xlk_r),
                "SPY":      _pf(spy_r),
                "vs MAG7":  _pf(ex_mag7),
                "vs QQQ":   _pf(ex_qqq),
                "Status":   r.portfolio_status,
            })

        walk_tbl = dash_table.DataTable(
            data=tbl_data,
            columns=[{"name": c, "id": c} for c in
                     ["Date", "Ticker", "Setup", "Entry", "SL", "TP", "R/R", "Outcome",
                      "Scan $", "Now $", "Return",
                      "Days", "MAG7", "QQQ", "XLK", "SPY", "vs MAG7", "vs QQQ", "Status"]],
            style_table={"overflowX": "auto"},
            style_cell={**_CELL, "minWidth": "50px"},
            style_header={**_HEADER},
            style_data_conditional=[
                {"if": {"filter_query": '{Return} contains "+"'},
                 "color": C["green_fg"]},
                {"if": {"filter_query": '{Return} contains "-"', "column_id": "Return"},
                 "color": C["red_fg"]},
                {"if": {"filter_query": '{Return} = "Missing"'},
                 "color": C["dim"]},
                {"if": {"filter_query": '{vs MAG7} contains "+"', "column_id": "vs MAG7"},
                 "color": C["green_fg"]},
                {"if": {"filter_query": '{vs MAG7} contains "-"', "column_id": "vs MAG7"},
                 "color": C["red_fg"]},
                {"if": {"filter_query": '{vs QQQ} contains "+"', "column_id": "vs QQQ"},
                 "color": C["green_fg"]},
                {"if": {"filter_query": '{vs QQQ} contains "-"', "column_id": "vs QQQ"},
                 "color": C["red_fg"]},
            ],
            sort_action="native",
            page_size=20,
        )

        return html.Div([
            section_hdr("Forward Walk Test"),
            html.Div(
                "Tracks forward performance of previously suggested tickers vs benchmarks. "
                "Past suggestions only — not buy signals.",
                style={"fontSize": "11px", "color": C["dim"], "fontStyle": "italic",
                       "marginBottom": "4px"},
            ),
            html.Div(
                "Level outcomes use daily OHLC bars, so intraday order is approximate.",
                style={"fontSize": "10px", "color": C["dim"], "fontStyle": "italic",
                       "marginBottom": "8px"},
            ),
            summary_lines,
            walk_tbl,
        ])

    # 4. Automation Health
    def _sub4() -> html.Div:
        # Scheduled run days: Tuesday=1 … Saturday=5  (Mon=0, Sun=6 are intentional skips)
        SCHEDULED_WEEKDAYS = {1, 2, 3, 4, 5}  # Mon=0 Sun=6 skipped
        SKIP_WEEKDAYS = {0, 6}  # Mon, Sun intentionally not scheduled

        health = ms.get_automation_health(7)
        today      = date.today()
        today_ds   = today.strftime("%Y-%m-%d")
        today_dow  = today.weekday()  # Mon=0 … Sun=6

        today_health = health[0] if health else None
        last_fail    = ms.get_automation_last_failure_banner()
        is_cdp       = bool(last_fail and "CDP" in last_fail.upper())

        # Compute expected vs unexpected missing days
        expected_skip_dates: list[str] = []
        unexpected_missing: list[str] = []
        for h in health:
            if h.status != "Missing":
                continue
            try:
                d_obj = date.fromisoformat(h.date)
            except Exception:
                continue
            if d_obj.weekday() in SKIP_WEEKDAYS:
                expected_skip_dates.append(h.date)
            else:
                unexpected_missing.append(h.date)

        # Schedule banner
        schedule_line = html.Div([
            html.Span("Automation schedule: ",
                      style={"color": C["dim"], "fontSize": "10px", "fontWeight": 600}),
            html.Span("Tue – Sat",
                      style={"color": C["text"], "fontSize": "10px", "fontWeight": 700}),
            html.Span("  ·  Sunday and Monday: intentionally skipped",
                      style={"color": C["muted"], "fontSize": "10px"}),
        ], style={"marginBottom": "8px"})

        # Action hints
        action_els: list = []
        if today_dow in SCHEDULED_WEEKDAYS:
            no_scan_today = (today_health is None) or (
                today_health.date == today_ds and today_health.status in ("Missing", "Partial")
                and not today_health.warren_scan
            )
            if no_scan_today:
                action_els.append(html.Div([
                    html.Span("Action needed: ",
                              style={"color": C["red_fg"], "fontWeight": 700, "fontSize": "11px"}),
                    html.Span("No scan found for today. Run ",
                              style={"color": C["amber_fg"], "fontSize": "11px"}),
                    html.Code("npm run morning-full",
                              style={"fontSize": "11px", "color": C["muted"]}),
                ], style={"marginBottom": "6px"}))

        if is_cdp:
            action_els.append(html.Div([
                html.Span("Chrome CDP not reachable. Launch Chrome first:",
                          style={"color": C["amber_fg"], "fontSize": "11px",
                                 "display": "block", "marginBottom": "4px"}),
                html.Code(
                    'google-chrome --remote-debugging-port=9222 '
                    "--remote-allow-origins='*' "
                    '--user-data-dir="$HOME/.warren-ai-chrome-profile"',
                    style={"display": "block", "background": "#f0ede4",
                           "padding": "4px 8px", "borderRadius": "4px",
                           "fontSize": "10px", "color": "#1a1a1a",
                           "fontFamily": "monospace", "marginBottom": "4px"},
                ),
                html.Code(
                    'cd "/home/klefe6/Coding Projects/rrg/warren_ai_daily" '
                    '&& npm run morning-full',
                    style={"display": "block", "background": "#f0ede4",
                           "padding": "4px 8px", "borderRadius": "4px",
                           "fontSize": "10px", "color": "#1a1a1a",
                           "fontFamily": "monospace"},
                ),
            ], style={"marginBottom": "8px"}))
        elif last_fail:
            action_els.append(html.Div(
                last_fail,
                style={"fontSize": "11px", "color": C["amber_fg"],
                       "marginBottom": "8px", "lineHeight": "1.45", "fontWeight": 600},
            ))

        # Missing / unexpected notes
        date_notes: list = []
        if unexpected_missing:
            date_notes.append(html.Div([
                html.Span("Unexpected missing runs: ",
                          style={"fontSize": "11px", "color": C["red_fg"], "fontWeight": 600}),
                html.Span(", ".join(unexpected_missing),
                          style={"fontSize": "11px", "color": C["muted"]}),
            ], style={"marginBottom": "4px"}))
        if expected_skip_dates:
            date_notes.append(html.Div([
                html.Span("Expected skips (Sun/Mon): ",
                          style={"fontSize": "11px", "color": C["dim"], "fontWeight": 600}),
                html.Span(", ".join(expected_skip_dates),
                          style={"fontSize": "11px", "color": C["muted"]}),
            ], style={"marginBottom": "4px"}))
        partial_dates = [h.date for h in health if h.status == "Partial"]
        if partial_dates:
            date_notes.append(html.Div([
                html.Span("Incomplete runs: ",
                          style={"fontSize": "11px", "color": C["amber_fg"], "fontWeight": 600}),
                html.Span(", ".join(partial_dates),
                          style={"fontSize": "11px", "color": C["muted"]}),
            ], style={"marginBottom": "8px"}))

        tbl_data = []
        for h in health:
            try:
                d_obj   = date.fromisoformat(h.date)
                dow_str = d_obj.strftime("%a")
            except Exception:
                dow_str = ""
            skip_note = " (skip)" if h.status == "Missing" and (
                any(d_obj.weekday() == sw for sw in SKIP_WEEKDAYS)
                if "d_obj" in dir() else False
            ) else ""
            tbl_data.append({
                "Date":      f"{h.date} {dow_str}",
                "Full run":  "Yes" if h.full_run else "No",
                "Scan":      "Yes" if h.warren_scan else "No",
                "Port. Mon": "Yes" if h.portfolio_mon else "No",
                "Export":    "Yes" if h.export_done else "No",
                "Modified":  h.last_modified,
                "Status":    (h.status + skip_note),
            })

        health_tbl = dash_table.DataTable(
            data=tbl_data,
            columns=[{"name": c, "id": c} for c in
                     ["Date", "Full run", "Scan", "Port. Mon", "Export", "Modified", "Status"]],
            style_table={"overflowX": "auto"},
            style_cell={**_CELL, "minWidth": "55px"},
            style_header={**_HEADER},
            style_data_conditional=[
                {"if": {"filter_query": '{Status} = "Complete"'},
                 "color": C["green_fg"], "fontWeight": 600},
                {"if": {"filter_query": '{Status} contains "Partial"'},
                 "color": C["amber_fg"]},
                {"if": {"filter_query": '{Status} contains "Missing"'},
                 "color": C["dim"]},
                {"if": {"filter_query": '{Status} contains "skip"'},
                 "color": C["muted"]},
                {"if": {"filter_query": '{Status} contains "error"'},
                 "color": C["red_fg"]},
                {"if": {"filter_query": '{Full run} = "No"', "column_id": "Full run"},
                 "color": C["dim"]},
                {"if": {"filter_query": '{Scan} = "No"', "column_id": "Scan"},
                 "color": C["dim"]},
            ],
        )

        return html.Div([
            section_hdr("Automation Health — Last 7 Days"),
            schedule_line,
            *action_els,
            *date_notes,
            health_tbl,
        ])

    # Assemble all four sub-sections
    # 5. Sector Diagnostics (compact — XLK focus)
    def _sub_diag() -> html.Div:
        if w.freshness == "NONE":
            return html.Div()
        try:
            diag = ms.get_sector_diagnostics_cached(w)
        except Exception:
            return html.Div(
                "Sector diagnostics unavailable.",
                style={"fontSize": "11px", "color": C["dim"]},
            )

        rows: list[dict] = []
        # Show all 11 sectors but highlight XLK first
        focus_sectors = ["XLK"] + [s for s in ms._SECTOR_UNIVERSE if s != "XLK"]
        for etf in focus_sectors:
            d = diag.get(etf, {})
            if not d:
                continue
            warren_yn  = "Yes" if d.get("sector_selected_by_warren") else "No"
            rrg_yn     = "Yes" if d.get("sector_selected_by_rrg")    else "No"
            final_yn   = "Yes" if d.get("sector_in_final_scan")      else "No"
            considered = d.get("candidates_in_scan", 0)
            passed     = d.get("candidates_passed", 0)
            top_rej    = d.get("top_rejection_reason", "—")
            rrg_quad   = d.get("rrg_quadrant", "—")
            rows.append({
                "Sector":      etf,
                "Warren AI":   warren_yn,
                "RRG":         rrg_yn,
                "RRG Quad":    rrg_quad,
                "Final Scan":  final_yn,
                "Candidates":  str(considered),
                "Passed":      str(passed),
                "Top Rejection": top_rej,
            })

        if not rows:
            return html.Div(
                "No diagnostics data.",
                style={"fontSize": "11px", "color": C["dim"]},
            )

        # XLK-specific callout
        xlk_d          = diag.get("XLK", {})
        xlk_warren     = xlk_d.get("sector_selected_by_warren", False)
        xlk_rrg        = xlk_d.get("sector_selected_by_rrg", False)
        xlk_in_scan    = xlk_d.get("sector_in_final_scan", False)
        xlk_considered = xlk_d.get("candidates_in_scan", 0)
        xlk_passed     = xlk_d.get("candidates_passed", 0)
        xlk_top_rej    = xlk_d.get("top_rejection_reason", "—")
        top4_today     = ", ".join(w.top_sectors) if w.top_sectors else "—"

        if xlk_warren and xlk_rrg:
            xlk_source = "Warren AI + RRG"
        elif xlk_warren:
            xlk_source = "Warren AI"
        elif xlk_rrg:
            xlk_source = "RRG (mandatory — Leading/Improving)"
        else:
            xlk_source = "Not selected"

        if xlk_in_scan:
            xlk_color = C["green_fg"]
            xlk_msg = (
                f"XLK: In final scan ({xlk_source}) · "
                f"{xlk_considered} candidates considered · "
                f"{xlk_passed} passed step 04 filters"
                + (f" · top rejection: {xlk_top_rej}" if xlk_considered > xlk_passed else "")
            )
        else:
            xlk_color = C["amber_fg"]
            xlk_msg = (
                f"XLK: NOT in final scan today. Warren AI top 4: {top4_today}. "
                f"RRG did not flag XLK as Leading/Improving with score >0. "
                f"Run export-rrg-sectors before morning-full to auto-inject."
            )

        return html.Div([
            section_hdr("Sector Candidate Diagnostics"),
            html.Div(
                xlk_msg,
                style={"fontSize": "11px", "color": xlk_color,
                       "marginBottom": "8px", "fontWeight": 600},
            ),
            html.Div(
                "Hybrid selection: sectors reach the scan via Warren AI OR RRG (Leading/Improving, score>0). "
                "Market cap is informational only — removed as a hard gate. "
                "Liquidity (vol≥500K), price≥$15, RSI, extension, and FV upside filters remain active.",
                style={"fontSize": "10px", "color": C["dim"], "fontStyle": "italic",
                       "marginBottom": "8px"},
            ),
            dash_table.DataTable(
                data=rows,
                columns=[{"name": c, "id": c} for c in
                         ["Sector", "Warren AI", "RRG", "RRG Quad", "Final Scan",
                          "Candidates", "Passed", "Top Rejection"]],
                style_table={"overflowX": "auto"},
                style_cell={**_CELL, "minWidth": "55px"},
                style_header={**_HEADER},
                style_data_conditional=[
                    {"if": {"filter_query": '{Warren AI} = "Yes"', "column_id": "Warren AI"},
                     "color": C["blue_fg"], "fontWeight": 600},
                    {"if": {"filter_query": '{RRG} = "Yes"', "column_id": "RRG"},
                     "color": C["green_fg"], "fontWeight": 600},
                    {"if": {"filter_query": '{Final Scan} = "Yes"', "column_id": "Final Scan"},
                     "color": C["green_fg"], "fontWeight": 600},
                    {"if": {"filter_query": '{Final Scan} = "No"', "column_id": "Final Scan"},
                     "color": C["dim"]},
                    {"if": {"filter_query": '{Sector} = "XLK"'},
                     "fontWeight": 700, "background": C["surface2"]},
                ],
            ),
        ])

    return html.Div(card([
        html.Div([
            section_hdr("Morning Scan Status"),
        ], style={"marginBottom": "10px"}),

        html.Div(
            (
                "Warren AI scan is STALE: latest successful run is "
                + (w.date or "—")
                + ", not today ("
                + date.today().strftime("%Y-%m-%d")
                + "). Tables below use that scan date until a new run completes."
            )
            if w.freshness == "STALE" else (
                "Warren AI last run FAILED or was incomplete. Check output folder logs "
                "and re-run when ready."
            )
            if w.freshness == "FAILED" else (
                "No Warren AI automation output found under warren_ai_daily/output/. "
                "Run morning-full when Chrome CDP is ready."
            )
            if w.freshness == "NONE" else (
                "Scan date matches today ("
                + date.today().strftime("%Y-%m-%d")
                + ") — morning workflow data is current."
            )
            if w.freshness == "FRESH" else (
                "Warren AI scan status is unknown — verify output files."
            ),
            style={
                "fontSize": "12px",
                "lineHeight": "1.45",
                "padding": "10px 12px",
                "borderRadius": "8px",
                "marginBottom": "14px",
                "border": f"1px solid {C['border']}",
                "background": (
                    C["amber_bg"] if w.freshness in ("STALE", "FAILED") else (
                        C["red_bg"] if w.freshness == "NONE" else C["surface2"]
                    )
                ),
                "color": (
                    C["amber_fg"] if w.freshness in ("STALE", "FAILED") else (
                        C["red_fg"] if w.freshness == "NONE" else C["muted"]
                    )
                ),
                "fontWeight": 600 if w.freshness in ("STALE", "FAILED", "NONE") else 500,
            },
        ),

        # Sub-section 1: Suggested Tickers Today
        html.Div(_sub1(), style={"marginBottom": "18px"}),

        html.Hr(style={"borderColor": C["border"], "margin": "0 0 14px 0"}),

        # Sub-section 2: Suggested vs Current Holdings
        html.Div([
            section_hdr("Suggested vs Current Holdings"),
            html.Div(style={"height": "6px"}),
            _sub2(),
        ], style={"marginBottom": "18px"}),

        html.Hr(style={"borderColor": C["border"], "margin": "0 0 14px 0"}),

        # Sub-section 3: Sector Candidate Diagnostics
        html.Div(_sub_diag(), style={"marginBottom": "18px"}),

        html.Hr(style={"borderColor": C["border"], "margin": "0 0 14px 0"}),

        # Sub-section 4: Forward Walk Test
        html.Div(_sub3(), style={"marginBottom": "18px"}),

        html.Hr(style={"borderColor": C["border"], "margin": "0 0 14px 0"}),

        # Sub-section 5: Automation Health
        html.Div(_sub4()),
    ], pad="18px"))


# ---------------------------------------------------------------------------
# Selector section builders
# ---------------------------------------------------------------------------

def build_top4_sector_cards(
    statuses: dict[str, SectorStatus],
    all_positions: list[dict],
    board_rows: list,          # sel.SectorBoardRow list, already sorted
    w: wl.WarrenResult | None = None,
) -> html.Div:
    """
    Top 4 Sectors To Research — compact clickable cards.
    Shows best 4 sectors by direction/accel score, with candidate info.
    """
    if not board_rows:
        return html.Div()

    # Sort board rows by the accel_score from sector_engine (priority: direction)
    def _sort_key(r: sel.SectorBoardRow):
        ss = statuses.get(r.etf)
        return ss.accel_score if (ss and ss.sufficient_data) else 0

    top4 = sorted(board_rows, key=_sort_key, reverse=True)[:4]

    # Map ticker → top candidate for that sector
    wl_pos = [p for p in all_positions if p.get("status") == "watchlist"]
    sector_top: dict[str, tuple] = {}
    for etf in {r.etf for r in top4}:
        cands_in = [p for p in wl_pos
                    if p.get("sector", "").strip().upper() == etf]
        if cands_in:
            sector_top[etf] = (cands_in[0].get("ticker", ""), None)

    # Pre-compute Warren AI candidates per sector
    warren_sector_tickers: dict[str, list[str]] = {}
    if w and w.ticker_sector:
        for tk, etf in w.ticker_sector.items():
            warren_sector_tickers.setdefault(etf, []).append(tk)

    def _card(r: sel.SectorBoardRow):
        ss  = statuses.get(r.etf)
        quad = ss.quadrant if ss else "Unknown"
        direction = ss.direction if ss else "—"
        score     = ss.accel_score if (ss and ss.sufficient_data) else 0
        bg, fg    = QUAD_COLORS.get(quad, (C["neutral_bg"], C["neutral_fg"]))

        # Candidate counts
        wl_in_sector = [p for p in all_positions
                        if p.get("status") == "watchlist"
                        and p.get("sector", "").strip().upper() == r.etf]
        ai_tickers   = warren_sector_tickers.get(r.etf, [])
        wl_count     = len(wl_in_sector)
        ai_count     = len(ai_tickers)

        # Check whether this sector appeared in today's Warren scan at all
        warren_scanned_sectors: set[str] = set()
        if w and w.top_sectors:
            warren_scanned_sectors = {s.upper() for s in w.top_sectors}

        cand_divs = []
        if ai_count:
            cand_divs.append(html.Span(
                f"Warren AI: {ai_count}", style={
                    "background": C["blue_bg"], "color": C["blue_fg"],
                    "fontSize": "10px", "fontWeight": 700,
                    "padding": "1px 6px", "borderRadius": "8px", "marginRight": "4px",
                }
            ))
        if wl_count:
            cand_divs.append(html.Span(
                f"Watchlist: {wl_count}", style={
                    "background": C["amber_bg"], "color": C["amber_fg"],
                    "fontSize": "10px", "fontWeight": 600,
                    "padding": "1px 6px", "borderRadius": "8px",
                }
            ))

        zero_note = html.Div()
        if not ai_count and not wl_count:
            # Distinguish between "not scanned" and "scanned but nothing passed"
            if w and w.freshness not in ("NONE", "") and r.etf not in warren_scanned_sectors:
                not_scanned_msg = (
                    f"Not in today's Warren AI scan "
                    f"(scanned: {', '.join(w.top_sectors) or '—'}). "
                    f"No watchlist items in {r.etf}."
                )
            elif w and w.freshness not in ("NONE", "") and r.etf in warren_scanned_sectors:
                not_scanned_msg = (
                    "Warren AI scanned this sector — no candidates survived "
                    "execution filters (RSI, extension, setup, FV upside)."
                )
            else:
                not_scanned_msg = f"No Warren AI scan data. No watchlist items in {r.etf}."
            zero_note = html.Div(
                not_scanned_msg,
                style={"fontSize": "10px", "color": C["dim"], "marginTop": "4px",
                       "fontStyle": "italic", "lineHeight": "1.4"},
            )

        return html.Button(
            [
                html.Div(r.etf, style={"fontWeight": 700, "fontSize": "14px"}),
                html.Div(r.name, style={"fontSize": "11px", "color": C["muted"],
                                        "marginBottom": "6px"}),
                html.Div([
                    html.Span(quad, style={
                        "background": bg, "color": fg,
                        "fontSize": "10px", "padding": "2px 7px",
                        "borderRadius": "6px", "fontWeight": 600,
                        "marginRight": "6px",
                    }),
                    html.Span(f"Score {int(score):+d}",
                              style={"fontSize": "11px", "color": C["muted"]}),
                ]),
                html.Div(direction, style={"fontSize": "11px", "color": C["dim"],
                                           "marginTop": "2px", "marginBottom": "6px"}),
                html.Div(cand_divs, style={"display": "flex", "flexWrap": "wrap",
                                            "gap": "3px", "marginBottom": "2px"}),
                zero_note,
            ],
            id={"type": "top4-card", "etf": r.etf},
            n_clicks=0,
            style={
                "background": C["surface"], "border": f"1px solid {C['border']}",
                "borderRadius": "12px", "padding": "14px 16px",
                "cursor": "pointer", "textAlign": "left",
                "width": "100%",
            },
        )

    return html.Div([
        html.Div([
            section_hdr("Top 4 Sectors To Research"),
            html.Span("Click to select sector",
                      style={"fontSize": "11px", "color": C["dim"],
                             "fontStyle": "italic", "marginLeft": "12px"}),
        ], style={"display": "flex", "alignItems": "baseline", "marginBottom": "10px"}),
        html.Div([_card(r) for r in top4], style={
            "display": "grid",
            "gridTemplateColumns": "repeat(4, minmax(0, 1fr))",
            "gap": "12px",
        }),
    ])


def build_suggested_tickers(
    sector_etf: str | None,
    all_positions: list[dict],
) -> html.Div:
    """
    Suggested Tickers To Review for the selected sector.
    Top 5 watchlist candidates, sorted by candidate score.
    """
    _mbottom = {"marginBottom": "1rem"}

    if not sector_etf:
        return html.Div(
            card(html.Div("Select a sector above to see suggested tickers.",
                          style={"fontSize": "13px", "color": C["muted"]}),
                 pad="20px 24px"),
            style=_mbottom,
        )

    etf_close = sel.get_etf_close_map().get(sector_etf)

    try:
        candidates = sel.analyze_candidates(all_positions, sector_etf, etf_close)
    except Exception as exc:
        return html.Div(
            card(html.Div(f"Error analyzing {sector_etf}: {exc}",
                          style={"color": C["red_fg"], "fontSize": "13px"}),
                 pad="20px 24px"),
            style=_mbottom,
        )

    name = sel.SECTOR_NAMES.get(sector_etf, sector_etf)

    if not candidates:
        return html.Div(card([
            html.Div(f"No watchlist candidates in {sector_etf} ({name}).",
                     style={"fontSize": "14px", "fontWeight": 500, "marginBottom": "6px"}),
            html.Div(
                "Use '+ Add to watchlist' and set the sector to "
                f"{sector_etf} to see candidates here.",
                style={"fontSize": "13px", "color": C["muted"]},
            ),
        ], pad="20px 24px"), style=_mbottom)

    top5 = candidates[:5]
    owned = {p.get("ticker", "").upper() for p in db.get_positions(db.ACTIVE_STATUSES)}
    pm = wl.get_latest_portfolio_monitor()
    rows = []
    for r in top5:
        own_lbl = ""
        if r.ticker.upper() in owned:
            own_lbl = wl.classify_owned_vs_monitor(r.ticker, pm)
        if r.error:
            row = {k: "—" for k in ["price","rsi","pct_20ema","pct_50ema",
                                      "ret_30d","rs_etf","flags","score"]}
            row.update({
                "ticker":       r.ticker,
                "ownership":    own_lbl or "—",
                "price":        "ERR",
                "verdict":      "Skip",
                "investingpro": f"[🔗]({r.investingpro_url})" if r.investingpro_url else "—",
                "reason":       r.error,
            })
        else:
            row = {
                "ticker":       r.ticker,
                "ownership":    own_lbl or "—",
                "price":        _f(r.current_price, 2),
                "rsi":          _f(r.rsi, 0),
                "pct_20ema":    _pct(r.pct_vs_20ema),
                "pct_50ema":    _pct(r.pct_vs_50ema),
                "ret_30d":      _pct(r.return_30d),
                "rs_etf":       _pct(r.rs_vs_sector),
                "flags":        str(len(r.flags)),
                "verdict":      r.verdict,
                "score":        str(round(r.candidate_score)),
                "investingpro": f"[🔗]({r.investingpro_url})" if r.investingpro_url else "—",
                "reason":       r.reason,
            }
        rows.append(row)

    return html.Div([
        html.Div([
            section_hdr(f"Suggested Tickers To Review — {name} ({sector_etf})"),
            html.Span("Top 5 watchlist candidates by entry score",
                      style={"fontSize": "11px", "color": C["dim"],
                             "fontStyle": "italic", "marginLeft": "12px"}),
        ], style={"display": "flex", "alignItems": "baseline", "marginBottom": "10px"}),
        html.Div(
            "Entry flags: near 20 EMA · near 50 EMA · RSI 35–60 · "
            "above 200 EMA · positive 30d return · outperforming sector ETF.",
            style={"fontSize": "11px", "color": C["dim"],
                   "marginBottom": "10px", "fontStyle": "italic"},
        ),
        dash_table.DataTable(
            data=rows,
            columns=CANDIDATE_COLS,
            style_cell={**_CELL, "whiteSpace": "normal", "maxWidth": "220px"},
            style_cell_conditional=[
                {"if": {"column_id": "reason"},    "maxWidth": "300px",
                 "whiteSpace": "normal", "lineHeight": "1.4"},
                {"if": {"column_id": "investingpro"}, "textAlign": "center"},
                {"if": {"column_id": "ticker"},    "fontWeight": 600},
            ],
            style_header=_HEADER,
            style_data_conditional=CANDIDATE_COND,
            style_table={**_TABLE, "overflowX": "auto"},
            cell_selectable=False,
        ),
    ], style=_mbottom)

# ---------------------------------------------------------------------------
# Modal form
# ---------------------------------------------------------------------------

_INPUT    = {"width": "100%", "padding": "7px 10px", "borderRadius": "6px",
             "border": f"1px solid {C['border']}", "fontSize": "13px",
             "boxSizing": "border-box", "background": C["surface"]}
_TEXTAREA = {**_INPUT, "height": "60px", "resize": "vertical"}


def _field(label, component, hint=None):
    kids = [
        html.Label(label, style={"fontSize": "12px", "color": C["muted"],
                                  "display": "block", "marginBottom": "3px"}),
        component,
    ]
    if hint:
        kids.append(html.Div(hint, style={"fontSize": "11px", "color": C["dim"],
                                           "marginTop": "2px"}))
    return html.Div(kids, style={"marginBottom": "11px"})


def build_modal():
    return html.Div(
        id="modal-wrapper",
        style={"display": "none"},
        children=[
            html.Div(style={"position": "fixed", "inset": "0",
                            "background": "rgba(0,0,0,0.45)", "zIndex": 999}),
            html.Div(style={
                "position": "fixed", "top": "50%", "left": "50%",
                "transform": "translate(-50%,-50%)",
                "background": C["surface"], "borderRadius": "16px",
                "padding": "28px 32px", "width": "680px",
                "maxWidth": "95vw", "maxHeight": "88vh", "overflowY": "auto",
                "boxShadow": "0 24px 64px rgba(0,0,0,0.22)", "zIndex": 1000,
            }, children=[
                html.Div("Add / Edit Position", style={
                    "fontSize": "17px", "fontWeight": 700, "marginBottom": "18px",
                }),
                html.Div(id="form-error", style={
                    "color": C["red_fg"], "fontSize": "12px", "marginBottom": "8px",
                }),
                html.Div([
                    html.Div([
                        _field("Ticker *", dcc.Input(id="form-ticker", type="text",
                                                     placeholder="e.g. NVDA", style=_INPUT)),
                        _field("Status", dcc.Dropdown(
                            id="form-status",
                            options=[{"label": s, "value": s} for s in STATUS_OPTIONS],
                            value="in_position", clearable=False,
                        )),
                        _field("Sector ETF", dcc.Dropdown(
                            id="form-sector",
                            options=[{"label": s, "value": s} for s in SECTORS],
                            clearable=True,
                        )),
                        _field("Shares", dcc.Input(id="form-shares", type="number",
                                                   min=0, placeholder="e.g. 50", style=_INPUT)),
                        _field("Cost Basis ($)", dcc.Input(id="form-cost-basis", type="number",
                                                           min=0, placeholder="e.g. 420.00",
                                                           style=_INPUT)),
                        _field("Target Weight (%)", dcc.Input(id="form-target-weight",
                                                              type="number", min=0, max=100,
                                                              placeholder="e.g. 6.0", style=_INPUT)),
                        _field("Current Weight (%)", dcc.Input(id="form-current-weight",
                                                               type="number", min=0, max=100,
                                                               placeholder="optional", style=_INPUT)),
                        _field("Conviction", dcc.Dropdown(
                            id="form-conviction",
                            options=[{"label": v or "(none)", "value": v}
                                     for v in ["A", "B", "C", ""]],
                            clearable=True,
                        )),
                    ], style={"flex": "1", "paddingRight": "20px"}),
                    html.Div([
                        _field("InvestingPro URL", dcc.Input(id="form-url", type="text",
                                                             placeholder="https://...",
                                                             style=_INPUT)),
                        _field("Notes", dcc.Textarea(id="form-notes", placeholder="Free text",
                                                     style=_TEXTAREA)),
                        html.Div("InvestingPro — manual fields", style={
                            "fontSize": "10px", "color": C["dim"], "fontWeight": 700,
                            "textTransform": "uppercase", "letterSpacing": "0.06em",
                            "marginBottom": "10px", "marginTop": "6px",
                        }),
                        _field("Fair Value Upside %", dcc.Input(id="form-fv-upside",
                                                                type="number",
                                                                placeholder="e.g. 18.5",
                                                                style=_INPUT),
                               hint="Enter manually from InvestingPro"),
                        _field("Financial Health Score", dcc.Input(id="form-health-score",
                                                                   type="text",
                                                                   placeholder="e.g. Good / 3.4",
                                                                   style=_INPUT)),
                        _field("Analyst Target Upside %", dcc.Input(id="form-analyst-upside",
                                                                     type="number",
                                                                     placeholder="e.g. 12.0",
                                                                     style=_INPUT)),
                        _field("Fundamental Note", dcc.Textarea(id="form-fund-note",
                                                                placeholder="Free text",
                                                                style=_TEXTAREA)),
                    ], style={"flex": "1"}),
                ], style={"display": "flex"}),
                html.Div([
                    html.Button("Save", id="btn-save", n_clicks=0, style={
                        "background": C["blue_fg"], "color": "#fff", "border": "none",
                        "borderRadius": "8px", "padding": "8px 22px", "fontSize": "13px",
                        "cursor": "pointer", "fontWeight": 600, "marginRight": "10px",
                    }),
                    html.Button("Cancel", id="btn-cancel", n_clicks=0, style={
                        "background": C["surface2"], "color": C["text"],
                        "border": f"1px solid {C['border']}", "borderRadius": "8px",
                        "padding": "8px 16px", "fontSize": "13px", "cursor": "pointer",
                    }),
                ], style={"marginTop": "20px", "display": "flex", "alignItems": "center"}),
            ]),
        ],
    )

# ---------------------------------------------------------------------------
# Morning status banner
# ---------------------------------------------------------------------------

def build_morning_status_banner(w: wl.WarrenResult, pm: wl.PortfolioMonitorResult) -> html.Div:
    """Compact banner at top of Selector tab showing both workflow freshness."""

    def _badge(label: str, freshness: str) -> html.Div:
        bg, fg = _WARREN_FRESHNESS_COLORS_STATIC.get(
            freshness, ("#eeede7", "#5f5e5a")
        )
        return html.Div([
            html.Span(label + ": ", style={"fontSize": "11px", "color": "#6e6d68",
                                            "fontWeight": 600}),
            html.Span(freshness or "UNKNOWN", style={
                "background": bg, "color": fg, "fontWeight": 700,
                "padding": "2px 8px", "borderRadius": "12px", "fontSize": "11px",
            }),
        ], style={"marginRight": "16px", "display": "inline-block"})

    badges = html.Div([
        _badge("New ideas", w.freshness),
        _badge("Portfolio monitor", pm.freshness),
        html.Span(
            f"Last run: {w.date or '—'}",
            style={"fontSize": "11px", "color": "#9a988f", "marginRight": "16px"},
        ),
    ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap", "gap": "4px"})

    # Warning if portfolio monitor is NONE and positions exist
    warning = html.Div()
    active_count = len(db.get_positions(db.ACTIVE_STATUSES))
    if pm.freshness == "NONE" and active_count > 0:
        warning = html.Div(
            f"Portfolio monitor not yet run — {active_count} active position(s) in DB. "
            "Run: npm run morning-full",
            style={
                "marginTop": "6px", "fontSize": "11px",
                "color": "#854f0b", "fontStyle": "italic",
            },
        )

    # Output paths (collapsed, small)
    run_date = w.date or pm.date or ""
    paths_text = ""
    if run_date:
        daydir = os.path.join(wl.WARREN_OUTPUT_DIR, run_date)
        paths_text = f"Output: {daydir}"

    return html.Div([
        html.Div("Morning Status", style={
            "fontWeight": 700, "fontSize": "12px",
            "color": C["muted"], "textTransform": "uppercase",
            "letterSpacing": "0.04em", "marginBottom": "6px",
        }),
        badges,
        warning,
        html.Div(paths_text, style={
            "fontSize": "10px", "color": C["dim"],
            "marginTop": "4px", "wordBreak": "break-all",
        }) if paths_text else html.Div(),
    ], style={
        "background": C["surface2"],
        "borderRadius": "10px",
        "padding": "10px 14px",
        "marginBottom": "1rem",
        "border": f"1px solid {C['border']}",
    })

# Forward-declare colors dict so builder above can reference it
_WARREN_FRESHNESS_COLORS_STATIC = {
    "FRESH":   (C["green_bg"],   C["green_fg"]),
    "STALE":   (C["amber_bg"],   C["amber_fg"]),
    "FAILED":  (C["red_bg"],     C["red_fg"]),
    "NONE":    (C["neutral_bg"], C["neutral_fg"]),
    "UNKNOWN": (C["neutral_bg"], C["neutral_fg"]),
}

# ---------------------------------------------------------------------------
# Opportunity Cost Check section
# ---------------------------------------------------------------------------

def build_opportunity_cost_section(active: list) -> html.Div:
    """
    Compact table comparing active portfolio against MAG7, QQQ, XLK, SPY
    over 1-day, 5-day, and 1-month windows.
    """
    try:
        occ_rows = ms.get_opportunity_cost_rows(active)
    except Exception as exc:
        return html.Div(
            f"Opportunity cost data unavailable: {exc}",
            style={"fontSize": "11px", "color": C["dim"]},
        )

    def _fmt(v: "Optional[float]") -> str:
        if v is None:
            return "—"
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"

    def _cell_style(v: "Optional[float]") -> dict:
        base = {
            "fontSize": "13px",
            "padding": "10px 12px",
            "borderTop": f"1px solid {C['border']}",
            "textAlign": "right",
            "fontFamily": "system-ui, -apple-system, sans-serif",
        }
        if v is None:
            base["color"] = C["dim"]
            return base
        base["color"] = C["green_fg"] if v > 0 else C["red_fg"]
        base["fontWeight"] = 600
        return base

    th_l = {
        **{k: v for k, v in _HEADER.items() if k != "textAlign"},
        "textAlign": "left",
        "borderBottom": f"1px solid {C['border']}",
    }
    th_r = {
        **{k: v for k, v in _HEADER.items() if k != "textAlign"},
        "textAlign": "right",
        "borderBottom": f"1px solid {C['border']}",
    }
    td_l = {
        "fontSize": "13px",
        "padding": "10px 12px",
        "borderTop": f"1px solid {C['border']}",
        "color": C["text"],
        "fontFamily": "system-ui, -apple-system, sans-serif",
    }

    body_rows: list = []
    for r in occ_rows:
        is_active = "Active Portfolio" in r.label
        body_rows.append(html.Tr([
            html.Td(r.label, style={**td_l, "fontWeight": 700 if is_active else 400}),
            html.Td(_fmt(r.ret_1d),  style=_cell_style(r.ret_1d)),
            html.Td(_fmt(r.ret_5d),  style=_cell_style(r.ret_5d)),
            html.Td(_fmt(r.ret_1m),  style=_cell_style(r.ret_1m)),
        ]))

    occ_table = html.Table(
        [
            html.Thead(html.Tr([
                html.Th("Benchmark", style=th_l),
                html.Th("1 Day", style=th_r),
                html.Th("5 Days", style=th_r),
                html.Th("1 Month", style=th_r),
            ])),
            html.Tbody(body_rows),
        ],
        style={
            "width": "100%",
            "borderCollapse": "collapse",
            "backgroundColor": C["surface"],
            "border": f"1px solid {C['border']}",
            "borderRadius": "8px",
        },
    )

    return html.Div(card([
        html.Div([
            section_hdr("Opportunity Cost Check"),
            html.Span(
                "vs Mag 7 · QQQ · XLK · SPY",
                style={"fontSize": "11px", "color": C["dim"], "marginLeft": "10px"},
            ),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "8px"}),
        html.Div(
            "Factual only. Past benchmark returns are not predictions.",
            style={"fontSize": "10px", "color": C["dim"], "fontStyle": "italic",
                   "marginBottom": "10px"},
        ),
        html.Div(occ_table, style={"overflowX": "auto", "marginTop": "4px"}),
    ], pad="14px 18px"))


# ---------------------------------------------------------------------------
# Daily Decision Cockpit — above-the-fold helper builders
# ---------------------------------------------------------------------------

def _details_section(summary_text: str, children: list, open_: bool = True) -> html.Details:
    """Styled collapsible Details/Summary element for secondary sections (open by default)."""
    return html.Details(
        children=[
            html.Summary(
                summary_text,
                style={
                    "cursor": "pointer",
                    "fontSize": "12px",
                    "fontWeight": 700,
                    "color": C["muted"],
                    "textTransform": "uppercase",
                    "letterSpacing": "0.05em",
                    "padding": "10px 16px",
                    "background": C["surface2"],
                    "borderRadius": "8px",
                    "border": f"1px solid {C['border']}",
                    "listStyle": "none",
                    "userSelect": "none",
                },
            ),
            html.Div(
                children,
                style={"paddingTop": "12px"},
            ),
        ],
        open=open_,
        style={"marginBottom": "1.5rem"},
    )


def build_system_health_ribbon(
    w: "wl.WarrenResult",
    pm: "wl.PortfolioMonitorResult",
    sector_statuses: dict,
) -> html.Div:
    """Compact one-line system health ribbon at top of Dashboard."""

    def _pill(label: str, status: str) -> html.Span:
        mapping = {
            "FRESH":   (C["green_bg"],   C["green_fg"],   "Fresh"),
            "STALE":   (C["amber_bg"],   C["amber_fg"],   "Stale"),
            "FAILED":  (C["red_bg"],     C["red_fg"],     "Failed"),
            "MISSING": (C["red_bg"],     C["red_fg"],     "Missing"),
            "NONE":    (C["neutral_bg"], C["neutral_fg"], "None"),
        }
        bg, fg, txt = mapping.get(status, (C["neutral_bg"], C["neutral_fg"], status or "Unknown"))
        return html.Span([
            html.Span(label + ": ", style={"color": C["muted"], "fontWeight": 600}),
            html.Span(txt, style={
                "background": bg, "color": fg, "borderRadius": "4px",
                "padding": "1px 7px", "fontWeight": 700,
            }),
        ], style={"marginRight": "16px", "fontSize": "11px", "whiteSpace": "nowrap"})

    rrg_ok = sector_statuses and any(
        getattr(v, "sufficient_data", False) for v in sector_statuses.values()
    )
    rrg_status = "FRESH" if rrg_ok else "MISSING"

    fail_banner = ms.get_automation_last_failure_banner()
    is_cdp = fail_banner and "CDP" in fail_banner.upper()
    all_ok = (
        w.freshness == "FRESH"
        and pm.freshness in ("FRESH", "NONE")
        and rrg_ok
        and not fail_banner
    )

    if all_ok:
        status_txt   = "✓  All systems current"
        status_color = C["green_fg"]
    elif is_cdp:
        status_txt   = "⚠  Chrome CDP not reachable — automation blocked"
        status_color = C["amber_fg"]
    elif w.freshness == "STALE":
        # Warren freshness is only "FRESH" on the scan's calendar day; any older
        # successful run is labeled STALE. w.date is the latest successful run date found.
        status_txt = (
            f"⚠  Latest successful morning scan: {w.date or '—'} "
            f"(Morning Scan shows Stale until a successful run finishes on today’s calendar date)"
        )
        status_color = C["amber_fg"]
    elif w.freshness in ("NONE", "FAILED"):
        status_txt   = "⚠  No fresh scan — run morning-full when Chrome CDP is ready"
        status_color = C["amber_fg"]
    else:
        status_txt   = ""
        status_color = C["muted"]

    cdp_block = html.Div()
    if is_cdp:
        cdp_block = html.Div([
            html.Div("To resolve: open the dedicated Warren AI Chrome window, then run:",
                     style={"marginBottom": "4px", "color": C["amber_fg"]}),
            html.Code(
                'google-chrome --remote-debugging-port=9222 '
                "--remote-allow-origins='*' "
                '--user-data-dir="$HOME/.warren-ai-chrome-profile"',
                style={
                    "display": "block", "background": "#f0ede4",
                    "padding": "5px 10px", "borderRadius": "4px",
                    "fontSize": "11px", "color": "#1a1a1a",
                    "fontFamily": "monospace", "marginBottom": "4px",
                    "wordBreak": "break-all",
                },
            ),
            html.Code(
                'cd "/home/klefe6/Coding Projects/rrg/warren_ai_daily" '
                '&& npm run morning-full',
                style={
                    "display": "block", "background": "#f0ede4",
                    "padding": "5px 10px", "borderRadius": "4px",
                    "fontSize": "11px", "color": "#1a1a1a",
                    "fontFamily": "monospace", "wordBreak": "break-all",
                },
            ),
        ], style={"fontSize": "11px", "marginTop": "6px", "lineHeight": "1.5"})

    return html.Div([
        html.Div([
            html.Div([
                html.Span("System Health", style={
                    "fontSize": "11px", "fontWeight": 700,
                    "textTransform": "uppercase", "letterSpacing": "0.05em",
                    "color": C["muted"], "marginRight": "18px",
                }),
                _pill("Morning Scan",      w.freshness),
                _pill("Portfolio Monitor", pm.freshness),
                _pill("RRG Data",          rrg_status),
            ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap"}),
            html.Div(status_txt, style={
                "fontSize": "11px", "color": status_color,
                "fontWeight": 600, "marginTop": "5px",
            }) if status_txt else html.Div(),
        ]),
        cdp_block,
    ], style={
        "background": C["surface2"],
        "borderRadius": "10px",
        "padding": "10px 16px",
        "border": f"1px solid {C['border']}",
    })


def build_leadership_gap_card(
    active: list,
    sector_statuses: dict,
) -> html.Div:
    """Leadership Gap & Opportunity Cost — combined above-fold card."""
    try:
        occ_rows = ms.get_opportunity_cost_rows(active)
    except Exception:
        occ_rows = []

    exp = ms.compute_mag7_exposure(active, EXTRA_BUCKETS, TOTAL_PORTFOLIO_VALUE)

    def _fmt(v) -> str:
        if v is None:
            return "—"
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"

    def _col(v) -> str:
        if v is None:
            return C["dim"]
        return C["green_fg"] if v > 0 else C["red_fg"]

    # ── Narrative sentence ─────────────────────────────────────────────────
    port_row = next((r for r in occ_rows if "Active Portfolio" in r.label), None)
    xlk_row  = next((r for r in occ_rows if r.label == "XLK"), None)

    narrative_parts: list[str] = []
    if port_row and xlk_row and port_row.ret_5d is not None and xlk_row.ret_5d is not None:
        p5  = port_row.ret_5d
        x5  = xlk_row.ret_5d
        ps  = "up" if p5 > 0 else "down"
        xs  = "up" if x5 > 0 else "down"
        narrative_parts.append(
            f"Active portfolio {ps} {abs(p5):.1f}% over 5 days"
            f" while XLK is {xs} {abs(x5):.1f}%."
        )

    xlk_d = exp.xlk_pct_of_deployed
    if xlk_d is not None:
        narrative_parts.append(
            f"XLK exposure: {xlk_d:.1f}% of deployed capital."
            if xlk_d >= 2
            else f"Current XLK exposure is {xlk_d:.1f}% of deployed capital."
        )

    leading = sorted(
        [etf for etf, s in sector_statuses.items()
         if getattr(s, "quadrant", "") == "Leading"],
        key=lambda e: getattr(sector_statuses.get(e), "accel_score", 0), reverse=True,
    )
    if leading:
        names = [sel.SECTOR_NAMES.get(e, e) for e in leading[:3]]
        narrative_parts.append(f"Leading sector(s): {', '.join(names)}.")

    narrative = " ".join(narrative_parts) if narrative_parts else "Benchmark data loading…"

    # ── Comparison table ──────────────────────────────────────────────────
    th_s = {
        **{k: v for k, v in _HEADER.items() if k != "textAlign"},
        "textAlign": "right", "padding": "6px 10px",
        "borderBottom": f"1px solid {C['border']}",
    }
    th_l = {**th_s, "textAlign": "left"}
    td_l = {
        "fontSize": "12px", "padding": "7px 10px",
        "borderTop": f"1px solid {C['border']}", "color": C["text"],
    }
    tbl_rows = []
    for r in occ_rows:
        is_active = "Active Portfolio" in r.label
        tbl_rows.append(html.Tr([
            html.Td(r.label, style={**td_l, "fontWeight": 700 if is_active else 400}),
            html.Td(_fmt(r.ret_1d), style={**td_l, "textAlign": "right",
                                           "color": _col(r.ret_1d),
                                           "fontWeight": 600 if r.ret_1d is not None else 400}),
            html.Td(_fmt(r.ret_5d), style={**td_l, "textAlign": "right",
                                           "color": _col(r.ret_5d),
                                           "fontWeight": 600 if r.ret_5d is not None else 400}),
            html.Td(_fmt(r.ret_1m), style={**td_l, "textAlign": "right",
                                           "color": _col(r.ret_1m),
                                           "fontWeight": 600 if r.ret_1m is not None else 400}),
        ]))

    occ_tbl = html.Table(
        [
            html.Thead(html.Tr([
                html.Th("", style=th_l),
                html.Th("1 Day",   style=th_s),
                html.Th("5 Days",  style=th_s),
                html.Th("1 Month", style=th_s),
            ])),
            html.Tbody(tbl_rows),
        ],
        style={"width": "100%", "borderCollapse": "collapse",
               "background": C["surface"], "border": f"1px solid {C['border']}",
               "borderRadius": "8px"},
    )

    # ── Exposure pills ─────────────────────────────────────────────────────
    exp_pills: list = []
    if exp.pct_of_deployed is not None:
        col = C["green_fg"] if (exp.pct_of_deployed or 0) > 5 else C["amber_fg"]
        exp_pills.append(html.Span([
            html.Span("Mag 7 (deployed): ", style={"color": C["dim"]}),
            html.Span(f"{exp.pct_of_deployed:.1f}%",
                      style={"fontWeight": 700, "color": col}),
        ], style={"marginRight": "20px", "fontSize": "12px"}))
    if exp.xlk_pct_of_deployed is not None:
        col = C["green_fg"] if (exp.xlk_pct_of_deployed or 0) > 5 else C["amber_fg"]
        exp_pills.append(html.Span([
            html.Span("XLK (deployed): ", style={"color": C["dim"]}),
            html.Span(f"{exp.xlk_pct_of_deployed:.1f}%",
                      style={"fontWeight": 700, "color": col}),
        ], style={"marginRight": "20px", "fontSize": "12px"}))

    return html.Div(card([
        html.Div([
            section_hdr("Leadership Gap & Opportunity Cost"),
            html.Span("vs MAG7 · QQQ · XLK · SPY",
                      style={"fontSize": "11px", "color": C["dim"], "marginLeft": "10px"}),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),
        html.Div(
            narrative,
            style={
                "fontSize": "13px", "fontWeight": 500, "color": C["text"],
                "lineHeight": "1.55", "marginBottom": "12px",
                "padding": "10px 14px",
                "background": C["surface2"], "borderRadius": "8px",
                "border": f"1px solid {C['border']}",
            },
        ),
        html.Div(exp_pills, style={"display": "flex", "flexWrap": "wrap",
                                   "marginBottom": "10px"}) if exp_pills else html.Div(),
        html.Div(occ_tbl, style={"overflowX": "auto"}),
        html.Div(
            "Factual only. Past benchmark returns are not predictions.",
            style={"fontSize": "10px", "color": C["dim"],
                   "fontStyle": "italic", "marginTop": "8px"},
        ),
    ], pad="14px 18px"))


def build_portfolio_action_card(active: list, qqq_5d: "Optional[float]" = None) -> html.Div:
    """Current Portfolio Action — chip groups with gain% and market context."""
    if not active:
        return html.Div()

    trim_now   = [r for r in active if r.verdict == "Strong trim"    and not r.error]
    candidates = [r for r in active if r.verdict == "Trim candidate" and not r.error]
    watch      = [r for r in active if r.verdict == "Watch"          and not r.error]
    hold       = [r for r in active
                  if r.verdict not in ("Strong trim", "Trim candidate", "Watch")
                  and not r.error]

    def _chip(r, bg: str, fg: str) -> html.Div:
        # Main reason
        reason = ""
        if r.sector_status and r.sector_status not in ("", "Unknown"):
            reason = f"{r.sector} {r.sector_status}"
        elif getattr(r, "flags", None):
            reason = r.flags[0]

        # Gain badge
        gain_parts: list = []
        if r.pct_gain is not None:
            sign  = "+" if r.pct_gain > 0 else ""
            gcol  = C["green_fg"] if r.pct_gain > 0 else C["red_fg"]
            gain_parts.append(
                html.Span(f"{sign}{r.pct_gain:.0f}%",
                          style={"fontWeight": 700, "color": gcol,
                                 "fontSize": "11px", "marginLeft": "6px"})
            )

        return html.Div([
            html.Div([
                html.Span(r.ticker, style={"fontWeight": 700, "fontSize": "13px"}),
                *gain_parts,
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "2px"}),
            html.Div(reason or "—",
                     style={"fontSize": "10px", "opacity": "0.8", "lineHeight": "1.3"}),
        ], style={
            "background": bg, "color": fg, "borderRadius": "8px",
            "padding": "6px 12px", "marginRight": "6px", "marginBottom": "6px",
            "display": "inline-block", "minWidth": "80px",
            "verticalAlign": "top",
        })

    def _group(label: str, items: list, bg: str, fg: str, border_color: str = "") -> html.Div:
        if not items:
            return html.Div()
        return html.Div([
            html.Div(label, style={
                "fontSize": "10px", "fontWeight": 700, "color": fg,
                "marginBottom": "6px", "textTransform": "uppercase",
                "letterSpacing": "0.06em", "padding": "3px 10px",
                "background": bg, "borderRadius": "5px",
                "display": "inline-block",
            }),
            html.Div([_chip(r, bg, fg) for r in items],
                     style={"display": "flex", "flexWrap": "wrap",
                            "alignItems": "flex-start"}),
        ], style={
            "borderLeft": f"3px solid {border_color or fg}",
            "paddingLeft": "10px", "marginBottom": "12px",
        })

    n_trim = len(trim_now) + len(candidates)
    summary_parts = (
        ([f"{n_trim} trim signal{'s' if n_trim != 1 else ''}"] if n_trim else [])
        + ([f"{len(watch)} watch"] if watch else [])
        + ([f"{len(hold)} hold"] if hold else [])
    )

    # Market context line (5-day QQQ as reference, same period for all chips)
    mkt_context: list = []
    if qqq_5d is not None:
        sign = "+" if qqq_5d > 0 else ""
        col  = C["green_fg"] if qqq_5d > 0 else C["red_fg"]
        mkt_context = [
            html.Div([
                html.Span("Market context — QQQ 5-day: ",
                          style={"fontSize": "10px", "color": C["dim"]}),
                html.Span(f"{sign}{qqq_5d:.1f}%",
                          style={"fontSize": "10px", "fontWeight": 700, "color": col}),
                html.Span("  ·  Gain% shown is since purchase, not same window.",
                          style={"fontSize": "10px", "color": C["dim"]}),
            ], style={"marginBottom": "10px"}),
        ]

    return html.Div(card([
        html.Div([
            section_hdr("Current Portfolio Action"),
            html.Span(
                "  " + "  ·  ".join(summary_parts) if summary_parts else "",
                style={"fontSize": "12px", "color": C["muted"], "marginLeft": "8px"},
            ),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),
        *mkt_context,
        _group("Trim",  trim_now,           C["red_bg"],     C["red_fg"]),
        _group("Watch", candidates + watch, C["amber_bg"],   C["amber_fg"]),
        _group("Hold",  hold,               C["neutral_bg"], C["neutral_fg"]),
        html.Div(
            "Based on trim monitor signals. Full detail in Active Holdings below.",
            style={"fontSize": "10px", "color": C["dim"],
                   "fontStyle": "italic", "marginTop": "2px"},
        ),
    ], pad="14px 18px"))


def build_new_ideas_card(
    w: "wl.WarrenResult",
    active_tickers: set,
    watchlist_tickers: set,
) -> html.Div:
    """New Ideas To Review — top candidates from latest scan, trade-plan quality first."""
    if w.freshness == "NONE":
        return html.Div(card([
            section_hdr("New Ideas To Review"),
            html.Div("No Warren AI scan found. Run morning-full to populate.",
                     style={"fontSize": "12px", "color": C["dim"], "marginTop": "8px"}),
        ], pad="14px 18px"))

    suggested = ms.classify_suggested_tickers(w, active_tickers, watchlist_tickers)
    candidates = [s for s in suggested if s.portfolio_status in ("New", "Watchlist")]

    def _has_plan(s) -> bool:
        return bool(
            s.entry_disp and s.entry_disp not in ("—", "")
            and s.sl_disp and s.sl_disp not in ("—", "")
            and s.rr_disp and s.rr_disp not in ("—", "")
        )

    def _sort_key(s):
        return (0 if s.is_top3 else 1, 0 if _has_plan(s) else 1,
                s.rank if s.rank else 99)

    candidates.sort(key=_sort_key)
    with_plan    = [s for s in candidates if _has_plan(s)][:5]
    without_plan = [s for s in candidates if not _has_plan(s)][:8]

    stale_note = ""
    if w.freshness == "STALE":
        stale_note = f" (stale from {w.date})"
    elif w.freshness == "FAILED":
        stale_note = " (scan failed)"

    def _plan_chip(s) -> html.Div:
        sector_nm   = sel.SECTOR_NAMES.get(s.sector_etf, s.sector_etf or "—")
        star        = "★ " if s.is_top3 else ""
        bg          = C["green_bg"] if s.portfolio_status == "New" else C["surface2"]
        theme_names = tr.get_ticker_themes(s.ticker)
        theme_chips = [
            html.Span(tn, style={
                "background": "#1e2a3a", "color": "#7eb3d6",
                "borderRadius": "4px", "padding": "1px 6px",
                "fontSize": "10px", "fontWeight": 600,
                "whiteSpace": "nowrap",
            })
            for tn in theme_names
        ]

        def _lv(label: str, val: str, color=None) -> html.Div:
            """Single label + value row."""
            return html.Div([
                html.Span(label, style={
                    "fontSize": "10px", "color": C["dim"],
                    "minWidth": "44px", "display": "inline-block",
                }),
                html.Span(val, style={
                    "fontSize": "10px", "fontWeight": 600,
                    "color": color or C["text"],
                }),
            ], style={"lineHeight": "1.6"})

        entry_val = s.entry_disp or "—"
        tp_val    = getattr(s, "tp_disp", None) or "—"
        rr_color  = C["green_fg"] if (s.rr_disp or "—") not in ("—", "") else C["dim"]

        return html.Div([
            # Clickable ticker badge — updates chart store
            html.Div([
                html.Button(
                    f"{star}{s.ticker}",
                    id={"type": "chart-chip", "ticker": s.ticker},
                    n_clicks=0,
                    style={
                        "fontWeight": 700, "fontSize": "14px", "marginRight": "10px",
                        "color": C["text"], "background": "none", "border": "none",
                        "cursor": "pointer", "padding": "0", "textDecoration": "underline",
                    },
                    title="Click to review chart",
                ),
                html.Span(sector_nm,
                          style={"fontSize": "11px", "color": C["dim"], "marginRight": "8px"}),
                html.Span(s.setup or "—",
                          style={"fontSize": "11px", "color": C["muted"],
                                 "fontStyle": "italic"}),
            ], style={"marginBottom": "6px", "display": "flex",
                      "alignItems": "center", "flexWrap": "wrap"}),
            # Level grid (two columns)
            html.Div([
                html.Div([
                    _lv("Entry:",  entry_val),
                    _lv("Stop:",   s.sl_disp or "—"),
                ], style={"flex": "1", "minWidth": "120px"}),
                html.Div([
                    _lv("Target:", tp_val),
                    _lv("R/R:",    s.rr_disp or "—", rr_color),
                ], style={"flex": "1", "minWidth": "100px"}),
            ], style={"display": "flex", "gap": "16px",
                      "marginBottom": "5px" if theme_chips else "0"}),
            # Theme chips
            *(
                [html.Div(theme_chips,
                          style={"display": "flex", "flexWrap": "wrap", "gap": "4px"})]
                if theme_chips else []
            ),
        ], style={
            "background": bg, "border": f"1px solid {C['border']}",
            "borderRadius": "8px", "padding": "10px 14px", "marginBottom": "6px",
        })

    def _mention_chip(s) -> html.Span:
        return html.Button(
            ("★ " if s.is_top3 else "") + s.ticker + " ⚠",
            id={"type": "chart-chip", "ticker": s.ticker},
            n_clicks=0,
            title="Mentioned only — no entry/stop/target. Click to see chart.",
            style={
                "background": C["surface2"], "borderRadius": "5px",
                "padding": "3px 9px", "fontSize": "12px", "fontWeight": 600,
                "marginRight": "6px", "marginBottom": "4px",
                "display": "inline-block", "color": C["muted"],
                "border": f"1px solid {C['border']}", "cursor": "pointer",
            },
        )

    body_children: list = []
    if with_plan:
        body_children += [
            html.Div("Complete trade plan", style={
                "fontSize": "11px", "fontWeight": 700, "color": C["green_fg"],
                "marginBottom": "6px", "textTransform": "uppercase",
                "letterSpacing": "0.04em",
            }),
            *[_plan_chip(s) for s in with_plan],
        ]
    if without_plan:
        body_children += [
            html.Div("Mentioned only — levels missing", style={
                "fontSize": "11px", "fontWeight": 700, "color": C["muted"],
                "margin": "8px 0 6px", "textTransform": "uppercase",
                "letterSpacing": "0.04em",
            }),
            html.Div([_mention_chip(s) for s in without_plan],
                     style={"display": "flex", "flexWrap": "wrap"}),
        ]
    if not body_children:
        body_children = [html.Div("No new candidates in latest scan.",
                                  style={"fontSize": "12px", "color": C["dim"]})]

    return html.Div(card([
        html.Div([
            section_hdr("New Ideas To Review"),
            html.Span(
                f"  Scan: {w.date}{stale_note}",
                style={"fontSize": "11px", "marginLeft": "10px",
                       "color": C["amber_fg"] if stale_note else C["dim"]},
            ),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),
        html.Div(body_children),
        html.Div(
            "Full scan table in Morning Scan Details below. "
            "★ = Warren AI Top 3.",
            style={"fontSize": "10px", "color": C["dim"],
                   "fontStyle": "italic", "marginTop": "10px"},
        ),
    ], pad="14px 18px"))


def build_theme_radar_card(compact: bool = True) -> html.Div:
    """
    Theme Radar — theme diagnostics + weekly scores.
    When ``compact`` is True (Dashboard two-column layout), use dense rows and
    omit the leadership stack; drivers stay inside expandable details only.
    """
    themes_cfg = tr.load_themes()

    def _score_color(score: "int | None") -> str:
        if score is None:  return C["dim"]
        if score >= 35:    return C["green_fg"]
        if score >= -34:   return C["amber_fg"]
        return C["red_fg"]

    def _dir_icon(direction: str) -> str:
        return {"Improving": "↑", "Deteriorating": "↓", "Stable": "→", "New": "✦"}.get(direction, "")

    def _mode_badge(mode: str) -> html.Span:
        explicit = "explicit" in mode
        return html.Span(
            "Explicit theme scan" if explicit else "Inferred from scan",
            style={
                "background": C["green_bg"] if explicit else "#1e2a3a",
                "color":      C["green_fg"] if explicit else "#7eb3d6",
                "borderRadius": "5px", "padding": "2px 8px",
                "fontSize": "10px", "fontWeight": 700,
            },
        )

    if not themes_cfg:
        return html.Div(card([
            html.Div("Theme Radar", style={
                "fontWeight": 700, "fontSize": "13px",
                "textTransform": "uppercase", "letterSpacing": "0.05em",
                "color": C["text"], "marginBottom": "8px",
            }),
            html.Div("No themes configured. Edit warren_ai_daily/config/themes.yaml.",
                     style={"fontSize": "12px", "color": C["dim"]}),
        ], pad="14px 18px"))

    # Auto-run inferred diagnostics if needed (fallback only)
    try:
        tdp.build_theme_diagnostics(force=False)
    except Exception:
        pass

    diag = tr.load_theme_diagnostics()
    meta = tr.load_theme_diagnostics_meta()
    scores_data = tr.load_theme_scores()
    scores = scores_data.get("themes", {})
    mode = meta.get("mode", "inferred")
    scan_date = meta.get("scan_date") or scores_data.get("as_of") or "—"

    if not diag:
        return html.Div(card([
            html.Div("Theme Radar", style={
                "fontWeight": 700, "fontSize": "13px",
                "textTransform": "uppercase", "letterSpacing": "0.05em",
                "color": C["text"], "marginBottom": "8px",
            }),
            html.Div("Themes configured, but no diagnostics for the latest scan.",
                     style={"fontSize": "12px", "color": C["amber_fg"], "marginBottom": "8px"}),
            html.Div(f"Configured themes: {', '.join(themes_cfg.keys())}",
                     style={"fontSize": "11px", "color": C["dim"]}),
        ], pad="14px 18px"))

    # ── Leadership stack (full-width layout only) ───────────────────────────
    leadership_stack = html.Div()
    if not compact:
        top_themes = sorted(
            [(name, scores.get(name, {}).get("score") or -999) for name in diag],
            key=lambda x: -x[1],
        )[:3]
        top_theme_names = [t[0] for t in top_themes if t[1] > -999]

        total_deployed = 0.0
        try:
            import trim_monitor as _tm
            active_pm = _tm.get_position_verdicts()
            for r in active_pm:
                mv = getattr(r, "market_value", None) or getattr(r, "current_value", None)
                if mv:
                    total_deployed += float(mv)
        except Exception:
            active_pm = []
        exposures = tr.get_portfolio_theme_exposure(
            active_pm, extra_buckets=EXTRA_BUCKETS, total_deployed=total_deployed
        ) if active_pm else []
        held_exposures = [e for e in exposures if e.tickers_held]

        if held_exposures:
            exp_txt = "  ·  ".join(
                f"{e.theme}: {', '.join(e.tickers_held)} ({e.pct_deployed:.1f}%)"
                for e in sorted(held_exposures, key=lambda x: -x.dollar_value)[:3]
            )
        else:
            exp_txt = "No theme exposure in portfolio"

        gaps = [
            name for name in diag
            if (scores.get(name, {}).get("score") or 0) >= 35
            and all(e.theme != name or not e.tickers_held for e in exposures)
        ]

        leadership_stack = html.Div([
            html.Div([
                html.Span("Top themes: ", style={"color": C["dim"], "fontSize": "10px", "fontWeight": 600}),
                html.Span(", ".join(top_theme_names) or "—",
                          style={"color": C["text"], "fontSize": "10px", "fontWeight": 700}),
            ], style={"marginBottom": "3px"}),
            html.Div([
                html.Span("Current exposure: ", style={"color": C["dim"], "fontSize": "10px", "fontWeight": 600}),
                html.Span(exp_txt, style={"color": C["muted"], "fontSize": "10px"}),
            ], style={"marginBottom": "3px"}),
            *([html.Div([
                html.Span("Gaps (high score, no holdings): ",
                          style={"color": C["amber_fg"], "fontSize": "10px", "fontWeight": 600}),
                html.Span(", ".join(gaps), style={"color": C["amber_fg"], "fontSize": "10px"}),
            ])] if gaps else []),
        ], style={
            "background": C["surface2"], "borderRadius": "8px",
            "padding": "8px 14px", "marginBottom": "12px",
            "border": f"1px solid {C['border']}",
        })

    # ── Theme rows ─────────────────────────────────────────────────────────
    rows: list = []
    for theme_name, d in diag.items():
        considered  = d.get("candidates_considered", 0)
        passed      = d.get("candidates_passed", 0)
        passed_t    = d.get("passed_tickers", []) or []
        best_t      = d.get("best_candidates", []) or passed_t[:3]
        rej_reason  = d.get("top_rejection_reason", "—") or "—"
        src         = d.get("source", mode)

        sc          = scores.get(theme_name, {})
        score       = sc.get("score")
        label       = sc.get("label", "")
        direction   = sc.get("direction", "")
        sc_change   = sc.get("score_change")
        pos_drivers = sc.get("positive_drivers", [])[:3]
        neg_drivers = sc.get("negative_drivers", [])[:3]

        score_str  = f"{score:+d}" if score is not None else "—"
        change_str = f" {sc_change:+d}" if sc_change is not None else ""
        dir_icon   = _dir_icon(direction)
        s_color    = _score_color(score)

        ticker_chips = html.Div(
            [html.Span(t, style={
                "background": C["green_bg"], "borderRadius": "4px",
                "padding": "1px 7px", "fontSize": "11px", "fontWeight": 600,
                "color": C["green_fg"],
            }) for t in (best_t or passed_t)]
            or [html.Span("—", style={"fontSize": "11px", "color": C["dim"]})],
            style={"display": "flex", "flexWrap": "wrap", "gap": "3px", "marginTop": "4px"},
        )

        src_badge = html.Span(
            "Explicit" if "explicit" in src else "Inferred",
            style={
                "background": C["green_bg"] if "explicit" in src else "#1e2a3a",
                "color":      C["green_fg"] if "explicit" in src else "#7eb3d6",
                "borderRadius": "4px", "padding": "1px 6px",
                "fontSize": "9px", "fontWeight": 700,
            },
        )

        # Collapsible drivers section (never expanded by default)
        driver_inner: list = []
        if pos_drivers:
            driver_inner.append(html.Div(
                [html.Span("↑ ", style={"color": C["green_fg"], "fontWeight": 700}),
                 html.Span("; ".join(pos_drivers),
                           style={"fontSize": "10px", "color": C["dim"]})],
                style={"display": "flex", "marginBottom": "2px"},
            ))
        if neg_drivers:
            driver_inner.append(html.Div(
                [html.Span("↓ ", style={"color": C["red_fg"], "fontWeight": 700}),
                 html.Span("; ".join(neg_drivers),
                           style={"fontSize": "10px", "color": C["dim"]})],
                style={"display": "flex"},
            ))

        drivers_block = html.Span("")
        if driver_inner:
            drivers_block = html.Details([
                html.Summary(
                    "Drivers",
                    style={
                        "cursor": "pointer", "fontSize": "10px",
                        "color": C["muted"], "fontWeight": 600,
                        "marginTop": "5px", "userSelect": "none",
                    },
                ),
                html.Div(driver_inner, style={"paddingTop": "4px", "paddingLeft": "4px"}),
            ], open=False)

        if compact:
            rej_short = rej_reason if len(rej_reason) <= 70 else rej_reason[:67] + "…"
            lbl_short = label if len(label) <= 36 else label[:33] + "…"
            chips_small = [
                html.Span(t, style={
                    "background": C["green_bg"], "borderRadius": "3px",
                    "padding": "0 5px", "fontSize": "10px", "fontWeight": 600,
                    "color": C["green_fg"],
                }) for t in (best_t or passed_t)[:6]
            ] or [html.Span("—", style={"fontSize": "10px", "color": C["dim"]})]
            ticker_chips_small = html.Div(
                chips_small,
                style={"display": "inline-flex", "flexWrap": "wrap", "gap": "2px"},
            )
            rows.append(html.Div([
                html.Div([
                    html.Span(theme_name, style={
                        "fontWeight": 700, "fontSize": "11px", "color": C["text"],
                        "flex": "1 1 100px", "minWidth": "88px",
                    }),
                    html.Span(score_str, style={
                        "fontSize": "12px", "fontWeight": 700, "color": s_color,
                        "marginRight": "6px",
                    }),
                    html.Span(lbl_short, style={
                        "fontSize": "10px", "color": s_color,
                        "marginRight": "6px", "maxWidth": "160px",
                    }),
                    html.Span(f"{passed} passed", style={
                        "fontSize": "10px", "color": C["dim"], "whiteSpace": "nowrap",
                    }),
                ], style={
                    "display": "flex", "flexWrap": "wrap", "gap": "6px",
                    "alignItems": "center", "marginBottom": "3px",
                }),
                html.Div([
                    html.Span("Best: ", style={"fontSize": "9px", "color": C["dim"]}),
                    ticker_chips_small,
                    html.Span(" · Rejection: ", style={
                        "fontSize": "9px", "color": C["dim"], "marginLeft": "6px",
                    }),
                    html.Span(rej_short, style={
                        "fontSize": "9px", "color": C["muted"], "fontStyle": "italic",
                    }),
                ], style={
                    "display": "flex", "flexWrap": "wrap", "alignItems": "center",
                    "gap": "4px",
                }),
                drivers_block,
            ], style={
                "background": C["surface2"], "borderRadius": "6px",
                "padding": "6px 10px", "marginBottom": "5px",
                "border": f"1px solid {C['border']}",
            }))
        else:
            rows.append(html.Div([
                html.Div([
                    html.Span(theme_name, style={
                        "fontWeight": 700, "fontSize": "12px", "color": C["text"],
                        "marginRight": "12px",
                    }),
                    html.Span(f"{dir_icon} {score_str}{change_str}", style={
                        "fontSize": "13px", "fontWeight": 700,
                        "color": s_color, "marginRight": "8px",
                    }),
                    html.Span(label, style={
                        "fontSize": "10px", "color": s_color, "marginRight": "10px",
                    }),
                    src_badge,
                ], style={"display": "flex", "alignItems": "center",
                          "flexWrap": "wrap", "marginBottom": "3px"}),
                html.Div([
                    html.Span(f"{considered} checked · {passed} passed", style={
                        "fontSize": "10px", "color": C["dim"], "marginRight": "10px",
                    }),
                    html.Span(f"top rejection: {rej_reason}", style={
                        "fontSize": "10px", "color": C["muted"], "fontStyle": "italic",
                    }),
                ], style={"display": "flex", "flexWrap": "wrap"}),
                ticker_chips,
                drivers_block,
            ], style={
                "background": C["surface2"], "borderRadius": "8px",
                "padding": "10px 14px", "marginBottom": "6px",
                "border": f"1px solid {C['border']}",
            }))

    _hdr_fs = "12px" if compact else "13px"
    _card_pad = "10px 12px" if compact else "14px 18px"
    _body = (
        html.Div(rows, style={"maxHeight": "520px", "overflowY": "auto"})
        if compact else html.Div(rows)
    ) if rows else html.Div(
        "No theme diagnostics in file.",
        style={"fontSize": "12px", "color": C["dim"]},
    )
    _card_children: list = [
        html.Div([
            html.Span("Theme Radar", style={
                "fontWeight": 700, "fontSize": _hdr_fs,
                "textTransform": "uppercase", "letterSpacing": "0.05em",
                "color": C["text"],
            }),
            html.Span("  Themes overlay — classic sectors unchanged",
                      style={"fontSize": "10px" if compact else "11px",
                             "color": C["dim"], "fontStyle": "italic"}),
            _mode_badge(mode),
            html.Span(f"Scan: {scan_date}",
                      style={"fontSize": "10px", "color": C["dim"]}),
        ], style={"display": "flex", "alignItems": "center",
                  "flexWrap": "wrap", "gap": "8px",
                  "marginBottom": "8px" if compact else "10px"}),
    ]
    if not compact:
        _card_children.append(leadership_stack)
    _card_children.append(_body)
    return html.Div(card(_card_children, pad=_card_pad))


def build_portfolio_theme_exposure_card(active: list, compact: bool = True) -> html.Div:
    """
    Portfolio Theme Exposure — overlapping exposure + weekly theme score context.
    Does NOT sum to 100%; tickers may appear in multiple themes.
    ``compact`` uses a dense table layout for the two-column Dashboard grid.
    """
    themes_cfg  = tr.load_themes()
    scores_data = tr.load_theme_scores()
    theme_scores = scores_data.get("themes", {})

    header = html.Div([
        html.Span("Portfolio Theme Exposure",
                  style={"fontWeight": 700, "fontSize": "13px",
                         "textTransform": "uppercase", "letterSpacing": "0.05em",
                         "color": C["text"]}),
    ], style={"marginBottom": "6px"})

    if not themes_cfg:
        return html.Div(card([
            header,
            html.Div("No themes configured. Edit themes.yaml.",
                     style={"fontSize": "12px", "color": C["dim"]}),
        ], pad="14px 18px"))

    # Compute total deployed
    total_deployed = 0.0
    for r in active:
        mv = getattr(r, "market_value", None) or getattr(r, "current_value", None)
        if mv:
            total_deployed += float(mv)
        else:
            pr = getattr(r, "current_price", None)
            sh = getattr(r, "shares", None) or getattr(r, "quantity", None)
            if pr and sh:
                total_deployed += float(pr) * float(sh)

    exposures = tr.get_portfolio_theme_exposure(
        active, extra_buckets=EXTRA_BUCKETS, total_deployed=total_deployed,
    )

    def _score_color(score) -> str:
        if score is None: return C["dim"]
        if score >= 35:   return C["green_fg"]
        if score >= -34:  return C["amber_fg"]
        return C["red_fg"]

    def _dir_icon(d: str) -> str:
        return {"Improving": "↑", "Deteriorating": "↓", "Stable": "→", "New": "✦"}.get(d, "")

    th_st = {
        "textAlign": "left", "fontSize": "10px", "fontWeight": 700,
        "color": C["muted"], "textTransform": "uppercase",
        "letterSpacing": "0.04em", "padding": "6px 8px",
        "borderBottom": f"1px solid {C['border']}",
    }
    td_st = {
        "fontSize": "11px", "padding": "6px 8px",
        "borderTop": f"1px solid {C['border']}", "verticalAlign": "top",
        "color": C["text"],
    }

    if compact:
        tbl_rows: list = []
        for e in sorted(exposures, key=lambda x: (-x.dollar_value, x.theme)):
            sc    = theme_scores.get(e.theme, {})
            score = sc.get("score")
            label = (sc.get("label") or "")[:22]
            direction = sc.get("direction", "")
            pct_str = f"{e.pct_deployed:.1f}%" if e.pct_deployed is not None else "0.0%"
            dv_str  = f"${e.dollar_value:,.0f}" if e.dollar_value > 0 else "$0"
            s_color = _score_color(score)
            score_str = f"{score:+d}" if score is not None else "—"
            dir_icon  = _dir_icon(direction)
            tick_txt  = ", ".join(e.tickers_held) or "—"
            if len(tick_txt) > 48:
                tick_txt = tick_txt[:45] + "…"
            gap_title = ""
            if score is not None and score >= 35 and e.dollar_value == 0:
                gap_title = " · High score, no exposure"
            tbl_rows.append(html.Tr([
                html.Td(e.theme, style={**td_st, "fontWeight": 700, "maxWidth": "140px"}),
                html.Td(f"{dv_str} ({pct_str})", style={
                    **td_st, "color": C["green_fg"] if e.dollar_value > 0 else C["dim"],
                    "whiteSpace": "nowrap",
                }),
                html.Td(
                    html.Span(f"{dir_icon} {score_str}", style={"color": s_color, "fontWeight": 600}),
                    style=td_st,
                ),
                html.Td(
                    tick_txt + gap_title,
                    style={**td_st, "color": C["muted"], "fontSize": "10px"},
                ),
            ]))
        if not tbl_rows:
            tbl_rows = [html.Tr([html.Td(
                "No active holdings.", colSpan=4,
                style={**td_st, "color": C["dim"], "fontStyle": "italic"},
            )])]
        tbl = html.Table(
            [
                html.Thead(html.Tr([
                    html.Th("Theme", style=th_st),
                    html.Th("Exposure", style=th_st),
                    html.Th("Score", style=th_st),
                    html.Th("Tickers", style=th_st),
                ])),
                html.Tbody(tbl_rows),
            ],
            style={"width": "100%", "borderCollapse": "collapse"},
        )
        return html.Div(card([
            header,
            html.Div(tbl, style={"overflowX": "auto"}),
            html.Div(
                "Overlapping themes; does not sum to 100%. Scores from weekly theme scan.",
                style={"fontSize": "9px", "color": C["dim"],
                       "fontStyle": "italic", "marginTop": "6px"},
            ),
        ], pad="10px 12px"))

    rows: list = []
    for e in sorted(exposures, key=lambda x: (-x.dollar_value, x.theme)):
        sc    = theme_scores.get(e.theme, {})
        score = sc.get("score")
        label = sc.get("label", "")
        direction = sc.get("direction", "")
        pct_str = f"{e.pct_deployed:.1f}%" if e.pct_deployed is not None else "0.0%"
        dv_str  = f"${e.dollar_value:,.0f}" if e.dollar_value > 0 else "$0"
        s_color = _score_color(score)
        score_str = f"{score:+d}" if score is not None else "—"
        dir_icon  = _dir_icon(direction)

        gap_note = None
        if score is not None and score >= 35 and e.dollar_value == 0:
            gap_note = html.Span(
                "Score high, no exposure",
                style={"fontSize": "10px", "color": C["amber_fg"],
                       "fontStyle": "italic", "marginLeft": "8px"},
            )

        ticker_chips = html.Div(
            [html.Span(t, style={
                "background": C["green_bg"], "borderRadius": "4px",
                "padding": "1px 7px", "fontSize": "11px", "fontWeight": 600,
                "color": C["green_fg"],
            }) for t in e.tickers_held] or [],
            style={"display": "inline-flex", "flexWrap": "wrap", "gap": "2px"},
        )

        rows.append(html.Div([
            html.Div([
                html.Span(e.theme, style={
                    "fontWeight": 700, "fontSize": "12px", "color": C["text"],
                    "marginRight": "14px",
                }),
                html.Span(f"{dv_str} · {pct_str} deployed", style={
                    "fontSize": "11px", "color": C["green_fg"] if e.dollar_value > 0 else C["dim"],
                    "fontWeight": 600 if e.dollar_value > 0 else 400,
                    "marginRight": "12px",
                }),
                html.Span(f"{dir_icon} Score {score_str} {label}", style={
                    "fontSize": "11px", "color": s_color, "fontWeight": 600,
                }),
                *([] if gap_note is None else [gap_note]),
            ], style={"display": "flex", "alignItems": "center",
                      "flexWrap": "wrap", "marginBottom": "3px"}),
            *([] if not e.tickers_held else [ticker_chips]),
        ], style={
            "background": C["surface2"], "borderRadius": "8px",
            "padding": "8px 12px", "marginBottom": "5px",
            "border": f"1px solid {C['border']}",
        }))

    if not rows:
        rows = [html.Div("No active holdings.", style={"fontSize": "12px", "color": C["dim"]})]

    return html.Div(card([
        header,
        html.Div(rows),
        html.Div(
            "Theme exposures are overlapping and do not sum to 100%.  "
            "Score from weekly theme scan (—  if no scan yet).",
            style={"fontSize": "10px", "color": C["dim"],
                   "fontStyle": "italic", "marginTop": "8px"},
        ),
    ], pad="14px 18px"))


def build_weekly_theme_score_summary(compact: bool = True) -> html.Div:
    """
    Weekly Theme Score Summary — category chips for theme momentum.
    ``compact`` tightens padding for the Dashboard two-column stack above Theme Radar.
    """
    scores_data = tr.load_theme_scores()
    themes_cfg  = tr.load_themes()

    _hfs = "12px" if compact else "13px"
    _hmb = "5px" if compact else "8px"
    header = html.Div(
        [html.Span("Weekly Theme Score Summary",
                   style={"fontWeight": 700, "fontSize": _hfs,
                          "textTransform": "uppercase", "letterSpacing": "0.05em",
                          "color": C["text"]})],
        style={"marginBottom": _hmb},
    )

    if not themes_cfg:
        return html.Span("")  # hidden if no themes configured

    scores = scores_data.get("themes", {})
    scan_summary = scores_data.get("summary", {})

    if not scores:
        return html.Div(card([
            header,
            html.Div(
                "No weekly theme scores available yet. Run morning-full after theme scan setup.",
                style={"fontSize": "12px", "color": C["dim"]},
            ),
        ], pad="12px 18px"))

    def _score_color(score) -> str:
        if score is None: return C["dim"]
        if score >= 35:   return C["green_fg"]
        if score >= -34:  return C["amber_fg"]
        return C["red_fg"]

    # Sort themes by score descending
    sorted_themes = sorted(
        [(name, sc) for name, sc in scores.items()],
        key=lambda x: x[1].get("score", 0) or 0,
        reverse=True,
    )

    # Categorise
    top_improving:    list[tuple] = []
    strong_extended:  list[tuple] = []
    high_no_setup:    list[tuple] = []
    deteriorating:    list[tuple] = []

    # Load diagnostics to know if there are passed candidates
    diag = tr.load_theme_diagnostics()
    for name, sc in sorted_themes:
        score     = sc.get("score")
        direction = sc.get("direction", "")
        passed    = len((diag.get(name) or {}).get("passed_tickers", []))
        if score is None:
            continue
        if score >= 35 and direction in ("Improving", "New"):
            if passed > 0:
                top_improving.append((name, sc, passed))
            else:
                high_no_setup.append((name, sc))
        elif score >= 35:  # stable but strong
            if passed == 0:
                strong_extended.append((name, sc))
            else:
                top_improving.append((name, sc, passed))
        elif score <= -35:
            deteriorating.append((name, sc))

    def _theme_chip(name: str, sc: dict, extra: str = "") -> html.Span:
        score = sc.get("score")
        direction = sc.get("direction", "")
        score_str = f"{score:+d}" if score is not None else "—"
        dir_icon = {"Improving": "↑", "Deteriorating": "↓", "Stable": "→", "New": "✦"}.get(direction, "")
        return html.Span(
            f"{name} {dir_icon}{score_str}{(' · ' + extra) if extra else ''}",
            style={
                "background": C["surface2"], "borderRadius": "5px",
                "padding": "3px 9px", "fontSize": "11px",
                "color": _score_color(score), "fontWeight": 600,
                "marginRight": "5px", "marginBottom": "4px",
                "display": "inline-block",
                "border": f"1px solid {C['border']}",
            },
        )

    def _section(label: str, items: list, extra_fn=None) -> list:
        if not items:
            return []
        chips = []
        for item in items:
            name, sc = item[0], item[1]
            extra = str(item[2]) + " passed" if len(item) > 2 else ""
            chips.append(_theme_chip(name, sc, extra))
        _mt = "4px" if compact else "8px"
        return [
            html.Div(label, style={
                "fontSize": "10px", "fontWeight": 700, "color": C["muted"],
                "textTransform": "uppercase", "letterSpacing": "0.04em",
                "marginBottom": "3px", "marginTop": _mt,
            }),
            html.Div(chips, style={"display": "flex", "flexWrap": "wrap"}),
        ]

    body: list = []
    body += _section("Top Improving — clean setups available", top_improving)
    body += _section("Strong but extended — no clean setups yet", strong_extended)
    body += _section("High score — waiting for setup", high_no_setup)
    body += _section("Deteriorating or avoid", deteriorating)

    if not body:
        body = [html.Div("All themes neutral or no data.",
                         style={"fontSize": "12px", "color": C["dim"]})]

    as_of = scores_data.get("as_of", "")
    footer = html.Div(
        f"Scores as of {as_of}  ·  Research signals only — not buy/sell recommendations.",
        style={"fontSize": "9px" if compact else "10px", "color": C["dim"],
               "fontStyle": "italic", "marginTop": "6px" if compact else "10px"},
    )

    _pad = "8px 12px" if compact else "12px 18px"
    return html.Div(card([header, html.Div(body), footer], pad=_pad))


def build_macro_badge(regime: "mr.MacroRegime") -> html.Div:
    """Compact one-line macro regime badge (demoted from full card)."""
    if regime.key_missing:
        return html.Div(
            "Macro: FRED API key not configured.",
            style={
                "fontSize": "11px", "color": C["dim"],
                "padding": "6px 14px", "display": "inline-block",
                "background": C["surface2"], "borderRadius": "8px",
                "border": f"1px solid {C['border']}",
            },
        )
    bg, fg = REGIME_COLORS.get(regime.label, REGIME_COLORS["Unknown"])
    parts = [regime.label]
    if regime.risk_regime and regime.risk_regime != regime.label:
        parts.append(regime.risk_regime)
    if regime.inflation_trend:
        parts.append(f"Inflation {regime.inflation_trend}")
    if regime.vix:
        parts.append(f"VIX {regime.vix:.1f}")
    text = "  |  ".join(parts)
    return html.Div([
        html.Span("Macro: ", style={"fontSize": "11px", "fontWeight": 700,
                                    "color": C["muted"], "marginRight": "5px"}),
        html.Span(text, style={"fontSize": "11px", "fontWeight": 600, "color": fg}),
    ], style={
        "background": bg, "borderRadius": "8px", "padding": "6px 14px",
        "display": "inline-flex", "alignItems": "center",
        "border": f"1px solid {C['border']}",
    })


# ---------------------------------------------------------------------------
# Reusable freshness badge
# ---------------------------------------------------------------------------

def _freshness_badge(freshness: str, prefix: str = "") -> html.Span:
    """Small colored inline badge for section headers and summary lines."""
    mapping = {
        "FRESH":   (C["green_bg"],   C["green_fg"],   "Fresh"),
        "STALE":   (C["amber_bg"],   C["amber_fg"],   "Stale"),
        "FAILED":  (C["red_bg"],     C["red_fg"],     "Failed"),
        "MISSING": (C["red_bg"],     C["red_fg"],     "Missing"),
        "NONE":    (C["neutral_bg"], C["neutral_fg"], "None"),
    }
    bg, fg, txt = mapping.get(
        freshness, (C["neutral_bg"], C["neutral_fg"], freshness or "Unknown")
    )
    label = (prefix + ": " if prefix else "") + txt
    return html.Span(label, style={
        "background": bg, "color": fg, "borderRadius": "4px",
        "padding": "1px 7px", "fontWeight": 700, "fontSize": "10px",
        "marginLeft": "6px", "verticalAlign": "middle",
    })


# ---------------------------------------------------------------------------
# Daily Decision Summary — unified top-of-page box
# ---------------------------------------------------------------------------

def build_daily_decision_summary(
    active: list,
    w: "wl.WarrenResult",
    pm: "wl.PortfolioMonitorResult",
    sector_statuses: dict,
    active_tickers: set,
    watchlist_tickers: set,
) -> html.Div:
    """One compact box at the top of the Dashboard answering all 4 daily questions."""

    # ── System health ────────────────────────────────────────────────────────
    fail_banner = ms.get_automation_last_failure_banner()
    rrg_ok = bool(sector_statuses and any(
        getattr(v, "sufficient_data", False) for v in sector_statuses.values()
    ))
    system_ok = (
        w.freshness == "FRESH"
        and pm.freshness in ("FRESH", "NONE")
        and rrg_ok
        and not fail_banner
    )
    sys_label = "All systems current" if system_ok else (
        fail_banner or
        (f"Scan {w.freshness.lower()}" if w.freshness not in ("FRESH", "NONE") else "") or
        "Issues detected"
    )
    sys_color = C["green_fg"] if system_ok else C["amber_fg"]

    # ── Positions ────────────────────────────────────────────────────────────
    trim_all  = [r for r in active if r.verdict in ("Strong trim", "Trim candidate") and not r.error]
    watch_all = [r for r in active if r.verdict == "Watch" and not r.error]
    hold_all  = [r for r in active
                 if r.verdict not in ("Strong trim", "Trim candidate", "Watch") and not r.error]

    # ── New ideas ────────────────────────────────────────────────────────────
    suggested = []
    if w.freshness != "NONE":
        try:
            suggested = ms.classify_suggested_tickers(w, active_tickers, watchlist_tickers)
        except Exception:
            pass

    new_cands = [s for s in suggested if s.portfolio_status in ("New", "Watchlist")]

    def _has_plan(s) -> bool:
        return bool(
            s.entry_disp and s.entry_disp not in ("—", "")
            and s.sl_disp   and s.sl_disp   not in ("—", "")
            and s.rr_disp   and s.rr_disp   not in ("—", "")
        )

    with_plan    = [s.ticker for s in new_cands if     _has_plan(s)][:5]
    without_plan = [s.ticker for s in new_cands if not _has_plan(s)][:8]

    # ── Leadership / OCC ─────────────────────────────────────────────────────
    try:
        occ_rows = ms.get_opportunity_cost_rows(active)
    except Exception:
        occ_rows = []

    def _occ(label_frag: str):
        return next((r for r in occ_rows if label_frag in r.label), None)

    port_row = _occ("Active Portfolio")
    xlk_row  = _occ("XLK")
    qqq_row  = _occ("QQQ")
    mag7_row = _occ("MAG7")

    exp = ms.compute_mag7_exposure(active, EXTRA_BUCKETS, TOTAL_PORTFOLIO_VALUE)

    xlk_obj  = sector_statuses.get("XLK")
    xlk_quad = getattr(xlk_obj, "quadrant",  "—") if xlk_obj else "—"
    xlk_dir  = getattr(xlk_obj, "direction", "")  if xlk_obj else ""

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _chip(ticker: str, bg: str, fg: str) -> html.Span:
        return html.Span(ticker, style={
            "background": bg, "color": fg, "borderRadius": "4px",
            "padding": "2px 8px", "fontSize": "12px", "fontWeight": 700,
            "marginRight": "4px", "display": "inline-block",
        })

    def _pct_span(v):
        if v is None:
            return html.Span("—", style={"color": C["dim"], "fontSize": "12px"})
        sign  = "+" if v > 0 else ""
        color = C["green_fg"] if v > 0 else C["red_fg"]
        return html.Span(f"{sign}{v:.1f}%", style={"fontWeight": 700, "color": color,
                                                    "fontSize": "12px"})

    def _label(txt: str, color=None) -> html.Span:
        return html.Span(txt, style={
            "fontSize": "11px", "fontWeight": 700,
            "color": color or C["muted"],
            "textTransform": "uppercase", "letterSpacing": "0.04em",
            "marginRight": "6px",
        })

    def _sep() -> html.Span:
        return html.Span("  ", style={})

    row_style = {
        "display": "flex", "alignItems": "center", "flexWrap": "wrap",
        "marginBottom": "9px", "minHeight": "26px",
    }

    # ── Row 1: Positions ──────────────────────────────────────────────────────
    pos_children: list = []
    if trim_all:
        pos_children += [_label("Trim", C["red_fg"])]
        pos_children += [_chip(r.ticker, C["red_bg"], C["red_fg"]) for r in trim_all]
        pos_children += [_sep()]
    if watch_all:
        pos_children += [_label("Watch", C["amber_fg"])]
        pos_children += [_chip(r.ticker, C["amber_bg"], C["amber_fg"]) for r in watch_all]
        pos_children += [_sep()]
    if hold_all:
        pos_children += [_label("Hold")]
        pos_children += [_chip(r.ticker, C["neutral_bg"], C["neutral_fg"]) for r in hold_all]
    if not active:
        pos_children = [html.Span("No active positions.", style={"fontSize": "12px", "color": C["dim"]})]

    # ── Row 2: New ideas ─────────────────────────────────────────────────────
    ideas_children: list = []
    if with_plan:
        ideas_children += [_label("Full plan", C["green_fg"])]
        ideas_children += [_chip(t, C["green_bg"], C["green_fg"]) for t in with_plan]
        ideas_children += [_sep()]
    if without_plan:
        ideas_children += [_label("Mentioned")]
        ideas_children.append(html.Span(
            ", ".join(without_plan),
            style={"fontSize": "12px", "color": C["dim"]},
        ))
    if not ideas_children:
        if w.freshness == "NONE":
            ideas_children = [html.Span("No scan yet. Run morning-full.",
                                        style={"fontSize": "12px", "color": C["dim"]})]
        else:
            ideas_children = [html.Span("No new candidates in latest scan.",
                                        style={"fontSize": "12px", "color": C["dim"]})]

    # ── Row 3: Leadership ─────────────────────────────────────────────────────
    xlk_col = (C["green_fg"] if xlk_quad in ("Leading", "Improving")
               else C["amber_fg"] if xlk_quad == "Weakening"
               else C["muted"])
    dir_short = xlk_dir.replace("↑ ", "").replace("↓ ", "").replace("→ ", "")

    xlk_exp_pct  = exp.xlk_pct_of_deployed
    mag7_exp_pct = exp.pct_of_deployed

    leadership_children: list = [
        html.Span(f"XLK: {xlk_quad}" + (f" {dir_short}" if dir_short else ""),
                  style={"fontSize": "12px", "fontWeight": 700, "color": xlk_col,
                         "marginRight": "14px"}),
        html.Span(
            f"XLK exp: {xlk_exp_pct:.1f}%" if xlk_exp_pct is not None else "XLK exp: —",
            style={"fontSize": "12px", "color": C["muted"], "marginRight": "14px"},
        ),
        html.Span(
            f"MAG7 exp: {mag7_exp_pct:.1f}%" if mag7_exp_pct is not None else "MAG7 exp: —",
            style={"fontSize": "12px", "color": C["muted"], "marginRight": "20px"},
        ),
        html.Span("5-day: ", style={"fontSize": "11px", "color": C["dim"], "marginRight": "4px"}),
        html.Span("Port ", style={"fontSize": "11px", "color": C["dim"]}),
        _pct_span(port_row.ret_5d if port_row else None),
        html.Span("  QQQ ", style={"fontSize": "11px", "color": C["dim"], "marginLeft": "10px"}),
        _pct_span(qqq_row.ret_5d if qqq_row else None),
        html.Span("  XLK ", style={"fontSize": "11px", "color": C["dim"], "marginLeft": "10px"}),
        _pct_span(xlk_row.ret_5d if xlk_row else None),
        html.Span("  MAG7 ", style={"fontSize": "11px", "color": C["dim"], "marginLeft": "10px"}),
        _pct_span(mag7_row.ret_5d if mag7_row else None),
    ]

    # ── Warnings ──────────────────────────────────────────────────────────────
    warning_divs: list = []
    if xlk_quad in ("Leading", "Improving") and (xlk_exp_pct is None or xlk_exp_pct < 2):
        xd  = f"{xlk_exp_pct:.1f}%" if xlk_exp_pct  is not None else "0%"
        md  = f"{mag7_exp_pct:.1f}%" if mag7_exp_pct is not None else "0%"
        warning_divs.append(html.Div(
            f"⚠  XLK is {xlk_quad} but XLK exposure is {xd} and MAG7 exposure is {md}.",
            style={"fontSize": "11px", "color": C["amber_fg"],
                   "fontWeight": 600, "marginTop": "4px"},
        ))
    if fail_banner:
        warning_divs.append(html.Div(
            f"⚠  {fail_banner}",
            style={"fontSize": "11px", "color": C["amber_fg"],
                   "fontWeight": 600, "marginTop": "4px"},
        ))

    return html.Div(card([
        # Title row
        html.Div([
            html.Span("Daily Decision Summary", style={
                "fontSize": "14px", "fontWeight": 700, "color": C["text"],
                "textTransform": "uppercase", "letterSpacing": "0.06em",
            }),
            html.Span(f"  ·  Scan: {w.date or '—'}",
                      style={"fontSize": "11px", "color": C["dim"], "marginLeft": "8px"}),
            html.Span("  ●", style={"color": sys_color, "marginLeft": "12px"}),
            html.Span(f" {sys_label}",
                      style={"fontSize": "11px", "color": sys_color,
                             "fontWeight": 600, "marginLeft": "4px"}),
        ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap",
                  "marginBottom": "16px", "paddingBottom": "12px",
                  "borderBottom": f"1px solid {C['border']}"}),

        # Row: Positions
        html.Div([
            html.Div("Positions", style={
                "fontSize": "10px", "fontWeight": 700, "color": C["dim"],
                "textTransform": "uppercase", "letterSpacing": "0.05em",
                "minWidth": "80px", "marginRight": "10px",
            }),
            html.Div(pos_children,
                     style={"display": "flex", "flexWrap": "wrap", "alignItems": "center"}),
        ], style={**row_style}),

        # Row: New Ideas
        html.Div([
            html.Div("New Ideas", style={
                "fontSize": "10px", "fontWeight": 700, "color": C["dim"],
                "textTransform": "uppercase", "letterSpacing": "0.05em",
                "minWidth": "80px", "marginRight": "10px",
            }),
            html.Div(ideas_children,
                     style={"display": "flex", "flexWrap": "wrap", "alignItems": "center"}),
        ], style={**row_style}),

        # Row: Leadership + 5-day returns
        html.Div([
            html.Div("Leadership", style={
                "fontSize": "10px", "fontWeight": 700, "color": C["dim"],
                "textTransform": "uppercase", "letterSpacing": "0.05em",
                "minWidth": "80px", "marginRight": "10px",
            }),
            html.Div(leadership_children,
                     style={"display": "flex", "flexWrap": "wrap", "alignItems": "center"}),
        ], style={**row_style, "marginBottom": "0"}),

        # Warnings
        *warning_divs,
    ], pad="18px 20px"))


# ---------------------------------------------------------------------------
# Scanner Edge Check — compact Forward Walk summary card
# ---------------------------------------------------------------------------

def build_fw_summary_card(
    active_tickers: set,
    watchlist_tickers: set,
) -> html.Div:
    """Compact 'Scanner Edge Check' summary above the full Forward Walk table."""
    try:
        walk_rows = ms.get_forward_walk_results(60, active_tickers, watchlist_tickers)
        if not walk_rows:
            return html.Div(
                "No prior scan data for Scanner Edge Check.",
                style={"fontSize": "11px", "color": C["dim"],
                       "padding": "10px", "fontStyle": "italic"},
            )
        enriched = ms.enrich_forward_walk_with_benchmarks(walk_rows)
        summ     = ms.forward_walk_summary_enriched(enriched)
    except Exception as exc:
        return html.Div(
            f"Scanner edge data unavailable: {exc}",
            style={"fontSize": "11px", "color": C["dim"]},
        )

    lvl_counts  = ms.summarize_forward_walk_level_outcomes(walk_rows)
    n_total     = len(walk_rows)
    n_scan_days = len({r.scan_date for r in walk_rows})
    target_hit  = lvl_counts.get("target_hit",    0)
    stop_hit    = lvl_counts.get("stop_hit",       0)
    open_pos    = lvl_counts.get("open",           0)
    lvls_miss   = lvl_counts.get("levels_missing", 0)
    rejected    = lvl_counts.get("rejected",       0)

    avg_ret   = summ.avg_return
    win_rate  = summ.win_rate
    beat_qqq  = getattr(summ, "pct_beat_qqq",  None)
    beat_mag7 = getattr(summ, "pct_beat_mag7", None)

    def _v(v, suffix="%", prec=1, signed=True):
        if v is None:
            return "—"
        sign = "+" if signed and v > 0 else ""
        return f"{sign}{v:.{prec}f}{suffix}"

    def _col(v):
        if v is None:
            return C["muted"]
        return C["green_fg"] if v > 0 else C["red_fg"]

    def _stat(label: str, val: str, color=None) -> html.Div:
        return html.Div([
            html.Div(label, style={
                "fontSize": "10px", "color": C["dim"],
                "textTransform": "uppercase", "letterSpacing": "0.03em",
                "marginBottom": "4px",
            }),
            html.Div(val, style={
                "fontSize": "20px", "fontWeight": 700,
                "color": color or C["text"],
            }),
        ], style={
            "textAlign": "center", "flex": "1", "minWidth": "80px",
            "padding": "8px 4px",
            "background": C["surface2"], "borderRadius": "8px",
        })

    # Caveat about missing levels (only rows that had a plan but couldn't parse — NOT rejected)
    n_with_plan = n_total - rejected
    caveat_els: list = []
    if lvls_miss > 0:
        caveat_els.append(html.Div(
            f"⚠  {lvls_miss} of {n_with_plan} planned alerts have missing trade plan levels. "
            "Outcome stats exclude those rows. "
            f"({rejected} additional rows were rejected by Warren AI and never received a plan.)",
            style={"fontSize": "10px", "color": C["amber_fg"],
                   "fontWeight": 600, "marginBottom": "8px"},
        ))
    elif rejected > 0:
        caveat_els.append(html.Div(
            f"ℹ  {rejected} of {n_total} rows were rejected by Warren AI (no trade plan). "
            "Outcome stats apply to planned rows only.",
            style={"fontSize": "10px", "color": C["dim"], "marginBottom": "8px"},
        ))

    stats_row = html.Div([
        _stat("Avg Return", _v(avg_ret),           _col(avg_ret)),
        _stat("Win Rate",   _v(win_rate, "%", 0, False) if win_rate is not None else "—"),
        _stat("Beat QQQ",   _v(beat_qqq,  "%", 0, False) if beat_qqq  is not None else "—"),
        _stat("Beat MAG7",  _v(beat_mag7, "%", 0, False) if beat_mag7 is not None else "—"),
        _stat("Target Hit", str(target_hit),       C["green_fg"] if target_hit > 0 else C["dim"]),
        _stat("Stop Hit",   str(stop_hit),         C["amber_fg"] if stop_hit   > 0 else C["dim"]),
        _stat("Open",       str(open_pos)),
        _stat("Rejected",   str(rejected),         C["dim"]),
    ], style={"display": "flex", "flexWrap": "wrap", "gap": "8px"})

    return html.Div(card([
        html.Div([
            section_hdr("Scanner Edge Check"),
            html.Span(
                f"  Last {n_scan_days} scan day(s) · {n_with_plan} alerts with trade plans",
                style={"fontSize": "11px", "color": C["dim"], "marginLeft": "8px"},
            ),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "8px"}),
        html.Div(
            "Past Warren AI suggestions vs benchmarks. Trade plan outcomes computed only for "
            "rows with entry, stop, and target. Informational only — not buy signals.",
            style={"fontSize": "10px", "color": C["dim"],
                   "fontStyle": "italic", "marginBottom": "10px"},
        ),
        *caveat_els,
        stats_row,
        html.Div(
            "Level outcomes use daily OHLC bars, so intraday order is approximate.",
            style={"fontSize": "10px", "color": C["dim"],
                   "fontStyle": "italic", "marginTop": "10px"},
        ),
    ], pad="14px 18px"))


# ---------------------------------------------------------------------------
# Mag 7 Exposure card
# ---------------------------------------------------------------------------

# Leadership sleeve target (0 = disabled)
LEADERSHIP_SLEEVE_TARGET_PCT: float = float(
    os.environ.get("LEADERSHIP_SLEEVE_TARGET_PCT", "0")
)


def build_mag7_exposure_card(active: list) -> html.Div:
    """
    Compact Mag 7 and XLK exposure card shown above Portfolio Exposure.
    Informational only — no trade recommendations.
    """
    exp = ms.compute_mag7_exposure(active, EXTRA_BUCKETS, TOTAL_PORTFOLIO_VALUE)

    def _dol(v: float) -> str:
        return f"${v:,.0f}"

    def _pct(v) -> str:
        return f"{v:.1f}%" if v is not None else "—"

    label_style = {
        "fontSize": "12px",
        "color": C["dim"],
        "padding": "8px 12px",
        "borderTop": f"1px solid {C['border']}",
        "verticalAlign": "top",
        "width": "52%",
    }
    value_style = {
        "fontSize": "13px",
        "fontWeight": 600,
        "color": C["muted"],
        "padding": "8px 12px",
        "borderTop": f"1px solid {C['border']}",
        "textAlign": "right",
        "whiteSpace": "nowrap",
    }

    intro: list = []
    if exp.holdings_in_mag7:
        intro.append(html.Div([
            html.Span("Held Mag 7 tickers: ", style={"fontSize": "11px", "color": C["dim"]}),
            html.Span(", ".join(exp.holdings_in_mag7),
                      style={"fontSize": "11px", "fontWeight": 600, "color": C["muted"]}),
            html.Br(),
            html.Span(
                f"Deployed in those names: {_dol(exp.deployed_mag7_value)}",
                style={"fontSize": "11px", "color": C["dim"]},
            ),
        ], style={"marginBottom": "12px", "lineHeight": "1.5"}))
    else:
        intro.append(html.Div(
            "No direct Mag 7 positions in active portfolio.",
            style={"fontSize": "11px", "color": C["dim"], "marginBottom": "12px", "lineHeight": "1.5"},
        ))

    if exp.extra_mag7_value > 0:
        extra_names = [lbl for lbl, _ in EXTRA_BUCKETS if lbl.upper() in ms.MAG7]
        intro.append(html.Div([
            html.Span("Non-tracked buckets (Mag 7): ", style={"fontSize": "11px", "color": C["dim"]}),
            html.Span(", ".join(extra_names) if extra_names else "—",
                      style={"fontSize": "11px", "fontWeight": 600, "color": C["muted"]}),
            html.Br(),
            html.Span(f"Amount: {_dol(exp.extra_mag7_value)}",
                      style={"fontSize": "11px", "color": C["dim"]}),
        ], style={"marginBottom": "14px", "lineHeight": "1.5"}))

    metrics = [
        ("Total Mag 7 exposure",       _dol(exp.total_mag7_value)),
        ("% of total portfolio",       _pct(exp.pct_of_total)),
        ("% of deployed capital",      _pct(exp.pct_of_deployed)),
        ("XLK deployed exposure",      _dol(exp.xlk_deployed_value)),
        ("XLK % of deployed",          _pct(exp.xlk_pct_of_deployed)),
    ]
    metric_rows = [
        html.Tr([
            html.Td(label, style=label_style),
            html.Td(val, style=value_style),
        ])
        for label, val in metrics
    ]

    mini_table = html.Table(
        [html.Tbody(metric_rows)],
        style={
            "width": "100%",
            "borderCollapse": "collapse",
            "backgroundColor": C["surface"],
            "border": f"1px solid {C['border']}",
            "borderRadius": "8px",
        },
    )

    foot: list = []
    if TOTAL_PORTFOLIO_VALUE <= 0:
        foot.append(html.Div(
            "Set TOTAL_PORTFOLIO_VALUE in .env to show % of total portfolio.",
            style={"fontSize": "11px", "color": C["amber_fg"], "marginTop": "12px", "lineHeight": "1.45"},
        ))

    if exp.leadership_alert:
        foot.append(html.Div(
            "XLK is Leading/Strengthening, but Mag 7 and Technology exposure is low. "
            "Informational only.",
            style={"fontSize": "11px", "color": C["amber_fg"],
                   "fontStyle": "italic", "marginTop": "10px", "lineHeight": "1.45"},
        ))

    if LEADERSHIP_SLEEVE_TARGET_PCT > 0 and TOTAL_PORTFOLIO_VALUE > 0:
        current_pct = exp.pct_of_total or 0
        target_pct  = LEADERSHIP_SLEEVE_TARGET_PCT
        target_val  = TOTAL_PORTFOLIO_VALUE * target_pct / 100
        gap_pct     = current_pct - target_pct
        gap_val     = exp.total_mag7_value - target_val
        gap_sign    = "+" if gap_val > 0 else ""
        foot.append(html.Div([
            html.Div("Leadership sleeve vs target", style={
                "fontSize": "11px", "fontWeight": 600, "color": C["muted"],
                "marginTop": "12px", "marginBottom": "6px",
            }),
            html.Div([
                html.Span("Target: ", style={"fontSize": "11px", "color": C["dim"]}),
                html.Span(f"{target_pct:.1f}%  ({_dol(target_val)})",
                          style={"fontSize": "11px", "fontWeight": 600, "color": C["muted"]}),
            ], style={"marginBottom": "4px"}),
            html.Div([
                html.Span("Gap: ", style={"fontSize": "11px", "color": C["dim"]}),
                html.Span(
                    f"{gap_sign}{gap_pct:.1f}%  ({gap_sign}{_dol(abs(gap_val))})",
                    style={"fontSize": "11px", "fontWeight": 600,
                           "color": C["green_fg"] if gap_val < 0 else C["red_fg"]},
                ),
            ]),
        ]))

    return html.Div(card([
        section_hdr("Mag 7 and Leadership Exposure"),
        *intro,
        html.Div(mini_table, style={"overflowX": "auto", "marginTop": "4px"}),
        *foot,
    ], pad="16px 18px"))


# ---------------------------------------------------------------------------
# Warren AI panel builders
# ---------------------------------------------------------------------------

_WARREN_FRESHNESS_COLORS = {
    "FRESH":   (C["green_bg"],   C["green_fg"]),
    "STALE":   (C["amber_bg"],   C["amber_fg"]),
    "FAILED":  (C["red_bg"],     C["red_fg"]),
    "NONE":    (C["neutral_bg"], C["neutral_fg"]),
    "UNKNOWN": (C["neutral_bg"], C["neutral_fg"]),
}

_WARREN_VERDICT_COLORS = {
    "Strong Buy":    (C["green_bg"],   C["green_fg"]),
    "Buy Breakout":  (C["green_bg"],   C["green_fg"]),
    "Buy Pullback":  (C["blue_bg"],    C["blue_fg"]),
    "Watch":         (C["amber_bg"],   C["amber_fg"]),
    "Watchlist":     (C["amber_bg"],   C["amber_fg"]),
    "Avoid":         (C["red_bg"],     C["red_fg"]),
    "Not in scan":   (C["neutral_bg"], C["neutral_fg"]),
}


def _warren_verdict_badge(text: str) -> html.Span:
    bg, fg = _WARREN_VERDICT_COLORS.get(text, (C["neutral_bg"], C["neutral_fg"]))
    return html.Span(text, style={
        "background": bg, "color": fg, "fontWeight": 700,
        "padding": "3px 10px", "borderRadius": "20px", "fontSize": "12px",
    })


def _freshness_badge(freshness: str, run_date: str) -> html.Div:
    bg, fg = _WARREN_FRESHNESS_COLORS.get(freshness, (C["neutral_bg"], C["neutral_fg"]))
    label = f"{freshness}  ·  {run_date}" if run_date else freshness
    return html.Div(label, style={
        "display": "inline-block", "background": bg, "color": fg,
        "fontWeight": 700, "padding": "3px 12px", "borderRadius": "20px",
        "fontSize": "12px", "marginBottom": "10px",
    })


def build_warren_panel(w: wl.WarrenResult, sector_statuses: dict | None = None) -> html.Div:
    """Build the Warren AI morning scan section for the Selector tab."""

    if w.freshness == "NONE":
        return html.Div([
            html.Div("Warren AI Morning Scan", style={
                "fontWeight": 700, "fontSize": "14px", "marginBottom": "8px",
            }),
            html.Div("No successful Warren AI run found in the last 7 days.", style={
                "color": C["muted"], "fontSize": "13px",
            }),
        ], style={
            "background": C["surface"], "borderRadius": "14px",
            "padding": "18px 20px", "border": f"1px solid {C['border']}",
            "marginBottom": "1.5rem",
        })

    # ── Header row ─────────────────────────────────────────────────────────
    header = html.Div([
        html.Div([
            html.Span("Warren AI Morning Scan", style={
                "fontWeight": 700, "fontSize": "14px", "marginRight": "12px",
            }),
            _freshness_badge(w.freshness, w.date),
        ]),
        html.Div(f"Generated {w.generated_at[:10]} {w.generated_at[11:16]} UTC" if w.generated_at else "",
                 style={"fontSize": "11px", "color": C["dim"]}),
    ], style={"display": "flex", "justifyContent": "space-between",
               "alignItems": "flex-start", "marginBottom": "14px"})

    # ── Market regime card ──────────────────────────────────────────────────
    regime_label = w.regime or "Unknown"
    # Normalise for color lookup (strip "Mild " prefix Warren AI uses)
    regime_key = regime_label.replace("Mild ", "").replace("Strong ", "")
    reg_bg, reg_fg = REGIME_COLORS.get(regime_key, REGIME_COLORS["Unknown"])
    regime_card = html.Div([
        html.Div([
            html.Span("Market Regime  ", style={"fontSize": "11px", "color": C["muted"],
                                                 "textTransform": "uppercase",
                                                 "letterSpacing": "0.04em"}),
            html.Span(regime_label, style={
                "background": reg_bg, "color": reg_fg,
                "fontWeight": 700, "padding": "2px 10px",
                "borderRadius": "20px", "fontSize": "12px",
            }),
        ], style={"marginBottom": "4px"}),
        html.Div(w.regime_one_liner, style={"fontSize": "13px", "color": C["text"],
                                             "fontStyle": "italic", "marginBottom": "4px"}),
        html.Div(w.sizing_note, style={"fontSize": "12px", "color": C["muted"]}),
    ], style={
        "background": C["surface2"], "borderRadius": "10px",
        "padding": "12px 14px", "marginBottom": "14px",
    })

    # ── Top sectors strip (labeled by source) ───────────────────────────────
    rrg_top = []
    if sector_statuses:
        rrg_top = [
            etf for etf, ss in sector_statuses.items()
            if ss.sufficient_data and ss.quadrant in ("Leading", "Improving") and ss.accel_score > 0
        ]
        # Sort by accel_score descending
        rrg_top = sorted(rrg_top, key=lambda e: sector_statuses[e].accel_score, reverse=True)[:4]

    overlap = [e for e in w.top_sectors if e in rrg_top] if (w.top_sectors and rrg_top) else []
    disagree_note = html.Div()
    if w.top_sectors and rrg_top and not overlap:
        disagree_note = html.Div([
            html.Div("AI readiness and RRG model disagree on sector leadership. "
                     "Prioritize tickers confirmed by both, or use as a rotation watch signal.",
                     style={"fontWeight": 600, "marginBottom": "4px"}),
            html.Div("Best confirmation today: prioritize tickers that are both in "
                     "WarrenAI Actionable Today and from sectors with an improving RRG direction.",
                     style={"fontStyle": "italic"}),
        ], style={
            "fontSize": "12px", "color": C["amber_fg"],
            "background": C["amber_bg"], "borderRadius": "8px",
            "padding": "8px 12px", "marginBottom": "10px",
            "lineHeight": "1.5",
        })

    def _sector_chip(etf: str, source: str) -> html.Div:
        reason = w.sector_reasons.get(etf, "")
        is_ai     = source == "ai"
        is_rrg    = source == "rrg"
        is_both   = source == "both"
        bg = C["green_bg"] if is_both else (C["blue_bg"] if is_ai else C["surface2"])
        fg = C["green_fg"] if is_both else (C["blue_fg"] if is_ai else C["muted"])
        tag = ("✓ Both" if is_both else ("AI" if is_ai else "RRG"))
        rrg_label = ""
        if sector_statuses and etf in sector_statuses:
            ss = sector_statuses[etf]
            if ss.sufficient_data:
                rrg_label = f"{ss.quadrant}, {ss.direction}"
        return html.Div([
            html.Div([
                html.Span(etf, style={"fontWeight": 700, "fontSize": "13px", "color": fg}),
                html.Span(f"  {tag}", style={
                    "fontSize": "10px", "fontWeight": 700, "color": fg,
                    "background": fg + "20", "padding": "1px 6px",
                    "borderRadius": "8px", "marginLeft": "6px",
                }),
            ], style={"marginBottom": "3px"}),
            html.Div(rrg_label or reason[:80], style={
                "fontSize": "11px", "color": C["muted"], "lineHeight": "1.3",
            }),
        ], style={
            "flex": "1", "minWidth": "160px",
            "background": bg, "borderRadius": "10px",
            "padding": "10px 12px",
        })

    merged_sectors: dict[str, str] = {}
    for e in w.top_sectors:
        merged_sectors[e] = "both" if e in rrg_top else "ai"
    for e in rrg_top:
        if e not in merged_sectors:
            merged_sectors[e] = "rrg"

    sector_chips_divs = [_sector_chip(e, src) for e, src in merged_sectors.items()]
    sectors_row = html.Div([
        html.Div("Top Sectors Today", style={
            "fontSize": "11px", "color": C["muted"],
            "textTransform": "uppercase", "letterSpacing": "0.04em",
            "marginBottom": "4px", "fontWeight": 600,
        }),
        html.Div([
            html.Span("AI", style={"background": C["blue_bg"], "color": C["blue_fg"],
                                    "fontSize": "10px", "fontWeight": 700,
                                    "padding": "1px 7px", "borderRadius": "8px",
                                    "marginRight": "6px"}),
            html.Span("= WarrenAI  ", style={"fontSize": "11px", "color": C["muted"]}),
            html.Span("RRG", style={"background": C["surface2"], "color": C["muted"],
                                     "fontSize": "10px", "fontWeight": 700,
                                     "padding": "1px 7px", "borderRadius": "8px",
                                     "marginRight": "6px"}),
            html.Span("= RRG model  ", style={"fontSize": "11px", "color": C["muted"]}),
            html.Span("✓ Both", style={"background": C["green_bg"], "color": C["green_fg"],
                                        "fontSize": "10px", "fontWeight": 700,
                                        "padding": "1px 7px", "borderRadius": "8px",
                                        "marginRight": "6px"}),
            html.Span("= confirmed overlap", style={"fontSize": "11px", "color": C["muted"]}),
        ], style={"marginBottom": "8px", "display": "flex", "flexWrap": "wrap", "gap": "2px",
                   "alignItems": "center"}),
        disagree_note,
        html.Div(sector_chips_divs,
                 style={"display": "flex", "flexWrap": "wrap", "gap": "6px"}),
    ], style={"marginBottom": "14px"}) if merged_sectors else html.Div()

    # ── Top 3 Trades ────────────────────────────────────────────────────────
    trade_cards = []
    for i, trade in enumerate(w.top3_trades):
        tk     = trade.get("ticker", "")
        name   = trade.get("name", "")
        verdict = trade.get("verdict", "")
        why    = trade.get("why", "")
        tr_row = w.ticker_rows.get(tk, wl.TickerRow(ticker=tk, sector_etf=""))
        sector_tag = tr_row.sector_etf or w.ticker_sector.get(tk, "")
        setup_tag  = tr_row.setup or ""
        trade_cards.append(html.Button([
            # Header
            html.Div([
                html.Span(f"#{i+1}", style={
                    "fontSize": "12px", "fontWeight": 700,
                    "color": C["green_fg"], "marginRight": "8px",
                }),
                html.Span(tk, style={
                    "fontWeight": 900, "fontSize": "20px",
                    "marginRight": "8px", "color": C["text"],
                }),
                html.Span(name, style={"fontSize": "12px", "color": C["muted"]}),
            ], style={"marginBottom": "6px"}),
            # Verdict + sector + setup badges
            html.Div([
                html.Span(verdict, style={
                    "background": C["green_bg"], "color": C["green_fg"],
                    "fontWeight": 700, "fontSize": "11px",
                    "padding": "2px 8px", "borderRadius": "10px", "marginRight": "6px",
                }) if verdict else None,
                html.Span(sector_tag, style={
                    "background": C["blue_bg"], "color": C["blue_fg"],
                    "fontWeight": 600, "fontSize": "11px",
                    "padding": "2px 7px", "borderRadius": "10px", "marginRight": "6px",
                }) if sector_tag else None,
                html.Span(setup_tag, style={
                    "background": C["surface2"], "color": C["muted"],
                    "fontWeight": 500, "fontSize": "11px",
                    "padding": "2px 7px", "borderRadius": "10px",
                }) if setup_tag else None,
            ], style={"marginBottom": "10px", "display": "flex",
                       "flexWrap": "wrap", "gap": "4px", "alignItems": "center"}),
            # Entry / Stop / Target / R/R grid
            html.Div([
                *([html.Div([
                    html.Div("Entry", style={"fontSize": "10px", "color": C["muted"],
                                              "textTransform": "uppercase", "letterSpacing": "0.04em"}),
                    html.Div(trade.get("entry", ""), style={"fontSize": "12px", "fontWeight": 700,
                                                             "color": C["green_fg"]}),
                ], style={"marginRight": "18px"})] if trade.get("entry") else []),
                *([html.Div([
                    html.Div("Stop", style={"fontSize": "10px", "color": C["muted"],
                                             "textTransform": "uppercase", "letterSpacing": "0.04em"}),
                    html.Div(trade.get("stop", ""), style={"fontSize": "12px", "fontWeight": 600,
                                                            "color": C["red_fg"]}),
                ], style={"marginRight": "18px"})] if trade.get("stop") else []),
                *([html.Div([
                    html.Div("Target", style={"fontSize": "10px", "color": C["muted"],
                                               "textTransform": "uppercase", "letterSpacing": "0.04em"}),
                    html.Div(trade.get("target", ""), style={"fontSize": "12px", "fontWeight": 600}),
                ], style={"marginRight": "18px"})] if trade.get("target") else []),
                *([html.Div([
                    html.Div("R/R", style={"fontSize": "10px", "color": C["muted"],
                                            "textTransform": "uppercase", "letterSpacing": "0.04em"}),
                    html.Div(trade.get("rr", ""), style={"fontSize": "12px", "fontWeight": 800,
                                                          "color": C["green_fg"]}),
                ])] if trade.get("rr") else []),
            ], style={"display": "flex", "alignItems": "flex-start",
                       "marginBottom": "10px" if why else "0"}),
            # Why (reason)
            *([html.Div(why, style={
                "fontSize": "12px", "color": C["muted"],
                "lineHeight": "1.5", "fontStyle": "italic",
                "borderTop": f"1px solid {C['border']}", "paddingTop": "8px",
            })] if why else []),
        ],
        id={"type": "ticker-chip", "ticker": tk},
        n_clicks=0,
        style={
            "background": C["surface"], "borderRadius": "12px",
            "border": f"2px solid {C['green_fg']}30",
            "padding": "16px 18px", "flex": "1", "minWidth": "260px",
            "cursor": "pointer", "textAlign": "left",
            "transition": "box-shadow 0.15s",
        }))

    trades_row = html.Div([
        html.Div([
            html.Span("Top 3 Trades Today", style={
                "fontSize": "11px", "color": C["muted"],
                "textTransform": "uppercase", "letterSpacing": "0.04em",
                "fontWeight": 600,
            }),
            html.Span("  · click a card to look up that ticker",
                      style={"fontSize": "11px", "color": C["dim"], "fontStyle": "italic"}),
        ], style={"marginBottom": "8px"}),
        html.Div(trade_cards, style={"display": "flex", "flexWrap": "wrap", "gap": "12px"}),
    ], style={"marginBottom": "14px"}) if trade_cards else html.Div()

    # ── Action lists (clickable chips) ───────────────────────────────────────
    def _pill_list(label: str, tickers: list[str], bg: str, fg: str) -> html.Div:
        if not tickers:
            return html.Div()
        pills = [
            html.Button(t, id={"type": "ticker-chip", "ticker": t}, n_clicks=0, style={
                "background": bg, "color": fg, "border": "none",
                "padding": "4px 12px", "borderRadius": "20px",
                "fontSize": "12px", "fontWeight": 600, "marginRight": "5px",
                "cursor": "pointer",
            })
            for t in tickers
        ]
        return html.Div([
            html.Span(label + "  ", style={"fontSize": "11px", "color": C["muted"],
                                           "fontWeight": 600, "textTransform": "uppercase",
                                           "letterSpacing": "0.03em"}),
            *pills,
        ], style={"marginBottom": "8px", "display": "flex", "flexWrap": "wrap",
                   "alignItems": "center", "gap": "4px"})

    # Plain text non-clickable list (for sectors/text items)
    def _text_pill_list(label: str, items: list[str], bg: str, fg: str) -> html.Div:
        if not items:
            return html.Div()
        pills = [html.Span(t, style={
            "background": bg, "color": fg,
            "padding": "3px 10px", "borderRadius": "20px",
            "fontSize": "12px", "fontWeight": 600, "marginRight": "5px",
        }) for t in items]
        return html.Div([
            html.Span(label + "  ", style={"fontSize": "11px", "color": C["muted"],
                                           "fontWeight": 600, "textTransform": "uppercase",
                                           "letterSpacing": "0.03em"}),
            *pills,
        ], style={"marginBottom": "6px", "flexWrap": "wrap"})

    actionable_top8 = w.actionable[:8]
    action_row = html.Div([
        html.Div("Click a ticker chip to look it up →",
                 style={"fontSize": "11px", "color": C["dim"],
                        "fontStyle": "italic", "marginBottom": "8px"}),
        _pill_list("Actionable today", actionable_top8, C["green_bg"], C["green_fg"]),
        _pill_list("Watch tomorrow",  w.watch_tomorrow, C["amber_bg"], C["amber_fg"]),
        _pill_list("Watch",           w.watch_tickers,  C["blue_bg"],  C["blue_fg"]),
        _pill_list("Avoid",           w.avoid,          C["red_bg"],   C["red_fg"]),
        _text_pill_list("Avoid sectors", w.avoid_sectors, C["red_bg"],  C["red_fg"]),
    ], style={"marginBottom": "14px"}) if (actionable_top8 or w.watch_tomorrow or w.avoid) else html.Div()

    # ── Stale warning ────────────────────────────────────────────────────────
    stale_banner = html.Div()
    if w.freshness == "STALE":
        stale_banner = html.Div(
            f"Data from {w.date} — run Warren AI again for today's scan",
            style={
                "background": C["amber_bg"], "color": C["amber_fg"],
                "borderRadius": "8px", "padding": "8px 14px",
                "fontSize": "12px", "fontWeight": 600, "marginBottom": "10px",
            }
        )

    # ── Parser warnings ──────────────────────────────────────────────────────
    parse_warn = html.Div()
    if w.warnings:
        parse_warn = html.Div(
            "Parse notes: " + "; ".join(w.warnings),
            style={"fontSize": "11px", "color": C["dim"], "marginTop": "8px"},
        )

    return html.Div([
        header,
        stale_banner,
        regime_card,
        sectors_row,
        trades_row,
        action_row,
        parse_warn,
    ], style={
        "background": C["surface"], "borderRadius": "14px",
        "padding": "18px 20px", "border": f"1px solid {C['border']}",
        "marginBottom": "1.5rem",
    })


def build_portfolio_thesis_panel(pm: wl.PortfolioMonitorResult) -> html.Div:
    """Warren AI portfolio monitor (Prompts 6 & 7) — separate from new-opportunity scan."""
    active_count = len(db.get_positions(db.ACTIVE_STATUSES))
    if pm.freshness == "NONE":
        if active_count == 0:
            none_msg = html.Div([
                html.Div("No active holdings found in the Cockpit database.",
                         style={"fontSize": "13px", "color": C["muted"], "marginBottom": "6px"}),
                html.Div("Add positions with status 'in_position' or 'trimming_zone' on the Dashboard tab.",
                         style={"fontSize": "12px", "color": C["dim"]}),
            ])
        else:
            none_msg = html.Div([
                html.Div(
                    f"{active_count} active holding(s) found. Portfolio monitor has not run yet today.",
                    style={"fontSize": "13px", "color": C["muted"], "marginBottom": "8px"},
                ),
                html.Div("Run these commands in a terminal (Chrome CDP must be open):", style={
                    "fontSize": "12px", "color": C["muted"], "marginBottom": "4px",
                }),
                html.Pre(
                    'cd "/home/klefe6/Coding Projects/rrg/warren_ai_daily"\n'
                    'npm run export-portfolio\n'
                    'npm run portfolio-monitor\n'
                    '# or run everything at once:\n'
                    'npm run morning-full',
                    style={
                        "background": C["surface2"], "padding": "10px 14px",
                        "borderRadius": "8px", "fontSize": "12px",
                        "fontFamily": "monospace", "margin": "0",
                        "border": f"1px solid {C['border']}",
                        "whiteSpace": "pre-wrap",
                    },
                ),
            ])
        return html.Div([
            html.Div("Current Portfolio Thesis Monitor", style={
                "fontWeight": 700, "fontSize": "14px", "marginBottom": "10px",
            }),
            none_msg,
        ], style={
            "background": C["surface"], "borderRadius": "14px",
            "padding": "18px 20px", "border": f"1px solid {C['border']}",
            "marginBottom": "1.5rem",
        })

    bg, fg = _WARREN_FRESHNESS_COLORS.get(pm.freshness, (C["neutral_bg"], C["neutral_fg"]))
    badge = html.Span(pm.freshness, style={
        "background": bg, "color": fg, "fontWeight": 700,
        "padding": "3px 12px", "borderRadius": "20px", "fontSize": "12px",
    })

    n_hold = len(pm.positions_used or [])
    path_line = html.Div(f"Output: {pm.output_path}", style={
        "fontSize": "11px", "color": C["dim"], "marginTop": "6px",
        "wordBreak": "break-all",
    })

    def _chips(label: str, tickers: list[str], chip_bg: str, chip_fg: str) -> html.Div:
        if not tickers:
            return html.Div()
        pills = [html.Span(x, style={
            "background": chip_bg, "color": chip_fg, "fontWeight": 600,
            "padding": "2px 8px", "borderRadius": "12px", "fontSize": "12px",
            "marginRight": "5px",
        }) for x in tickers[:12]]
        return html.Div([
            html.Span(label, style={"fontSize": "11px", "color": C["muted"], "fontWeight": 600,
                                     "marginRight": "8px"}),
            *pills,
        ], style={"marginBottom": "8px", "flexWrap": "wrap"})

    # Suggested actions rollup from parsed step 7
    action_counts: dict[str, int] = {}
    for row in pm.ticker_actions or []:
        a = (row.get("action_today") or "").strip() or "Unknown"
        action_counts[a] = action_counts.get(a, 0) + 1
    action_line = ""
    if action_counts:
        parts = [f"{k}: {v}" for k, v in sorted(action_counts.items(), key=lambda kv: -kv[1])]
        action_line = " · ".join(parts[:8])

    warn = html.Div()
    if pm.warnings:
        warn = html.Div("⚠ " + " ".join(pm.warnings), style={
            "fontSize": "12px", "color": C["amber_fg"], "marginTop": "8px",
        })

    status_note = html.Div()
    if pm.freshness == "FAILED" or not pm.success:
        status_note = html.Div(
            "Last run did not complete successfully — review portfolio_step files or rerun.",
            style={"fontSize": "12px", "color": C["amber_fg"], "marginTop": "6px"},
        )

    return html.Div([
        html.Div([
            html.Span("Current Portfolio Thesis Monitor", style={
                "fontWeight": 700, "fontSize": "14px", "marginRight": "10px",
            }),
            badge,
            html.Span(f"  ·  {pm.date}", style={"fontSize": "12px", "color": C["muted"]}),
        ], style={"marginBottom": "10px"}),
        html.Div(f"Holdings checked: {n_hold}", style={"fontSize": "13px", "marginBottom": "8px"}),
        status_note,
        _chips("Improved thesis:", pm.improved, C["green_bg"], C["green_fg"]),
        _chips("Increased risk:", pm.increased_risk, C["amber_bg"], C["amber_fg"]),
        html.Div("Suggested actions (from monitor):", style={
            "fontSize": "11px", "color": C["muted"], "fontWeight": 600, "marginTop": "6px",
        }),
        html.Div(action_line or "(Parse the action table in portfolio_step_07.md if empty.)", style={
            "fontSize": "12px", "color": C["text"], "marginBottom": "6px",
        }),
        path_line,
        warn,
    ], style={
        "background": C["surface"], "borderRadius": "14px",
        "padding": "18px 20px", "border": f"1px solid {C['border']}",
        "marginBottom": "1.5rem",
    })


def build_portfolio_ticker_line(ticker: str, pm: wl.PortfolioMonitorResult) -> html.Div:
    """Ownership + monitor classification for ticker lookup (legacy helper)."""
    t = ticker.strip().upper()
    if not t:
        return html.Div()
    active = {p.get("ticker", "").upper() for p in db.get_positions(db.ACTIVE_STATUSES)}
    if t not in active:
        return html.Div("Not owned", style={"fontSize": "13px", "color": C["muted"]})
    lbl = wl.classify_owned_vs_monitor(t, pm)
    return html.Div(lbl, style={"fontSize": "13px", "fontWeight": 600})


def build_ticker_lookup_section() -> html.Div:
    """Build the 'Is this a good buy?' ticker lookup widget — primary decision tool."""
    return html.Div([
        # Header
        html.Div([
            html.Div("Is this a good new buy today?", style={
                "fontWeight": 800, "fontSize": "18px", "color": C["text"],
            }),
            html.Div("Primary decision tool — type any ticker for an instant verdict",
                     style={"fontSize": "12px", "color": C["muted"],
                            "marginTop": "2px"}),
        ], style={"marginBottom": "14px"}),

        # Input row
        html.Div([
            dcc.Input(
                id="warren-ticker-input",
                type="text",
                placeholder="e.g. CNX",
                debounce=True,
                style={
                    **{k: v for k, v in _INPUT.items()},
                    "width": "140px", "textTransform": "uppercase",
                    "fontSize": "16px", "fontWeight": 700,
                    "padding": "9px 12px",
                    "marginRight": "10px",
                },
            ),
            html.Span("↩ Enter",
                      style={"fontSize": "11px", "color": C["dim"],
                             "fontStyle": "italic"}),
        ], style={"marginBottom": "12px", "display": "flex", "alignItems": "center"}),

        # Verdict result (+ example chips in empty state)
        html.Div(id="warren-ticker-result"),
    ], style={
        "background": C["surface"], "borderRadius": "14px",
        "padding": "20px 22px",
        "border": f"2px solid {C['blue_fg']}30",
        "marginBottom": "1.5rem",
        "boxShadow": "0 2px 12px rgba(12,68,124,0.06)",
    })


def build_ticker_verdict(
    ticker: str,
    w: wl.WarrenResult,
    pm: wl.PortfolioMonitorResult | None = None,
    sector_statuses: dict | None = None,
) -> html.Div:
    """Big verdict card combining Warren AI scan + portfolio ownership + RRG context."""
    t = ticker.strip().upper()
    if not t:
        return html.Div()

    tr = w.ticker_rows.get(t)
    in_scan = t in w.all_tickers

    # ── Determine main verdict ───────────────────────────────────────────────
    active_tickers = {p.get("ticker", "").upper() for p in db.get_positions(db.ACTIVE_STATUSES)}
    is_owned = t in active_tickers
    own_lbl = wl.classify_owned_vs_monitor(t, pm) if (pm and is_owned) else (
        "Already owned" if is_owned else "Not owned"
    )
    own_is_exit_risk  = "exit risk"  in own_lbl.lower()
    own_is_trim_risk  = "trim risk"  in own_lbl.lower()
    own_is_hold_only  = "hold only"  in own_lbl.lower()
    own_is_add_cand   = "add candidate" in own_lbl.lower()

    if w.freshness == "NONE":
        main_label = "NO DATA"
        sub_label  = "No Warren AI run available."
        v_bg, v_fg = C["neutral_bg"], C["neutral_fg"]
    elif not in_scan and is_owned:
        main_label = "ALREADY OWNED"
        sub_label  = own_lbl
        v_bg, v_fg = C["blue_bg"], C["blue_fg"]
    elif not in_scan:
        main_label = "NOT IN TODAY'S SCAN"
        sub_label  = "Not among the 16 candidates from today's Warren AI run. Treat as manual research only."
        v_bg, v_fg = C["neutral_bg"], C["neutral_fg"]
    elif t in w.avoid:
        main_label = "AVOID"
        sub_label  = "Warren AI flagged this ticker. Setup broken or overextended."
        v_bg, v_fg = C["red_bg"], C["red_fg"]
    elif t in (w.actionable or []):
        rank = w.actionable.index(t) + 1
        verdict_raw = tr.verdict if (tr and tr.verdict) else "Strong Entry"
        if is_owned:
            main_label = "ALREADY OWNED"
            sub_label  = f"Also #{ rank } on today's actionable list. {own_lbl}"
            v_bg, v_fg = C["blue_bg"], C["blue_fg"]
        else:
            main_label = "GOOD NEW BUY"
            sub_label  = f"{verdict_raw} · Ranked #{rank} of {len(w.actionable)} actionable trades."
            v_bg, v_fg = C["green_bg"], C["green_fg"]
    elif t in (w.watch_tomorrow or []):
        main_label = "WAIT"
        sub_label  = "On the watch-tomorrow list. Setup not yet actionable — monitor for trigger."
        v_bg, v_fg = C["amber_bg"], C["amber_fg"]
    elif t in (w.watch_tickers or []):
        main_label = "WAIT"
        sub_label  = "In the general watchlist. Not a primary execution today."
        v_bg, v_fg = C["amber_bg"], C["amber_fg"]
    else:
        main_label = "WAIT"
        sub_label  = "Was among the 16 scanned but filtered from the top-8 execution list."
        v_bg, v_fg = C["amber_bg"], C["amber_fg"]

    # ── Verdict banner ───────────────────────────────────────────────────────
    sector_etf = (tr.sector_etf if tr else "") or w.ticker_sector.get(t, "")
    header = html.Div([
        html.Div([
            html.Span(t, style={"fontWeight": 900, "fontSize": "28px",
                                "marginRight": "14px", "letterSpacing": "-0.5px"}),
            html.Span(main_label, style={
                "background": v_bg, "color": v_fg, "fontWeight": 800,
                "fontSize": "14px", "padding": "5px 16px", "borderRadius": "20px",
                "letterSpacing": "0.04em",
            }),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "4px"}),
        html.Div(sub_label, style={
            "fontSize": "13px", "color": v_fg if in_scan else C["muted"],
            "fontStyle": "italic",
        }),
    ], style={
        "background": v_bg, "border": f"2px solid {v_fg}30",
        "borderRadius": "14px 14px 0 0",
        "padding": "16px 20px",
    })

    # ── Detail grid ──────────────────────────────────────────────────────────
    def _row(label: str, val: str, highlight: bool = False) -> html.Div:
        return html.Div([
            html.Span(label, style={
                "fontSize": "10px", "color": C["muted"], "fontWeight": 600,
                "minWidth": "88px", "display": "inline-block",
                "textTransform": "uppercase", "letterSpacing": "0.03em",
            }),
            html.Span(val, style={
                "fontSize": "12px", "fontWeight": 700 if highlight else 500,
                "color": C["green_fg"] if highlight else C["text"],
            }),
        ], style={"padding": "3px 0", "borderBottom": f"1px solid {C['border']}"})

    detail_rows = []

    # Source / rank
    if in_scan:
        src = "WarrenAI"
        if t in (w.actionable or []):
            src += f"  ·  Rank #{w.actionable.index(t)+1} actionable"
        detail_rows.append(_row("Source", src))

    # Sector with RRG label
    if sector_etf:
        rrg_label = ""
        if sector_statuses and sector_etf in sector_statuses:
            ss = sector_statuses[sector_etf]
            rrg_label = f"  [{ss.quadrant}, {ss.direction}]" if ss.sufficient_data else ""
        detail_rows.append(_row("Sector", f"{sector_etf}{rrg_label}"))

    if tr:
        if tr.setup:
            detail_rows.append(_row("Setup type", tr.setup))
        if tr.entry_zone:
            detail_rows.append(_row("Entry zone", tr.entry_zone, highlight=True))
        if tr.stop:
            detail_rows.append(_row("Stop", tr.stop))
        if tr.target:
            detail_rows.append(_row("Target", tr.target))
        if tr.rr:
            detail_rows.append(_row("R/R", tr.rr, highlight=True))
        if tr.price:
            detail_rows.append(_row("Price", tr.price))
        if tr.vs_20ema:
            detail_rows.append(_row("vs 20 EMA", tr.vs_20ema))
        if tr.vs_50ema:
            detail_rows.append(_row("vs 50 EMA", tr.vs_50ema))
        if tr.rsi:
            detail_rows.append(_row("RSI", tr.rsi))
        if tr.fv_upside:
            detail_rows.append(_row("FV upside", tr.fv_upside))
        if tr.eps:
            detail_rows.append(_row("EPS trend", tr.eps))
        if tr.key_risk:
            detail_rows.append(_row("Key risk", tr.key_risk))
        if tr.size_note:
            detail_rows.append(_row("Position size", tr.size_note))

    # Not-in-scan: show RRG sector context
    if not in_scan and sector_statuses:
        # Find which sector contains this ticker from positions DB
        all_pos = db.get_positions()
        pos = next((p for p in all_pos if p.get("ticker", "").upper() == t), None)
        etf_for_lookup = (pos.get("sector", "") if pos else "") or sector_etf
        if etf_for_lookup and etf_for_lookup in sector_statuses:
            ss = sector_statuses[etf_for_lookup]
            if ss.sufficient_data:
                detail_rows.append(_row("RRG Sector", f"{etf_for_lookup} — {ss.quadrant}, {ss.direction}"))
        # Watchlist membership
        wl_tickers = {p.get("ticker","").upper() for p in db.get_positions()
                      if p.get("status") == "watchlist"}
        if t in wl_tickers:
            detail_rows.append(_row("Your watchlist", "Yes — in Cockpit watchlist"))
        else:
            detail_rows.append(_row("Your watchlist", "Not in your watchlist"))

    # Portfolio ownership row
    own_bg, own_fg = C["neutral_bg"], C["neutral_fg"]
    if own_is_exit_risk or own_is_trim_risk:
        own_bg, own_fg = C["red_bg"], C["red_fg"]
    elif own_is_add_cand:
        own_bg, own_fg = C["green_bg"], C["green_fg"]
    elif is_owned:
        own_bg, own_fg = C["blue_bg"], C["blue_fg"]

    ownership_row = html.Div([
        html.Span("PORTFOLIO", style={
            "fontSize": "10px", "color": C["muted"], "fontWeight": 600,
            "minWidth": "88px", "display": "inline-block",
            "textTransform": "uppercase", "letterSpacing": "0.03em",
        }),
        html.Span(own_lbl, style={
            "fontSize": "12px", "fontWeight": 700,
            "background": own_bg, "color": own_fg,
            "padding": "2px 8px", "borderRadius": "10px",
        }),
    ], style={"padding": "6px 0 3px"})

    freshness_note = html.Div(
        f"WarrenAI data: {w.freshness}  ·  {w.date or '—'}",
        style={"fontSize": "10px", "color": C["dim"], "marginTop": "8px"},
    )

    body = html.Div([
        *detail_rows,
        ownership_row,
        freshness_note,
    ], style={
        "padding": "12px 14px",
        "background": C["surface"],
        "borderRadius": "0 0 12px 12px",
        "border": f"2px solid {v_fg}30",
        "borderTop": "none",
    })

    # Compact header for left pane
    left_header = html.Div([
        html.Div([
            html.Span(t, style={"fontWeight": 900, "fontSize": "22px",
                                "marginRight": "10px", "letterSpacing": "-0.5px"}),
            html.Span(main_label, style={
                "background": v_bg, "color": v_fg, "fontWeight": 800,
                "fontSize": "12px", "padding": "3px 12px", "borderRadius": "20px",
                "letterSpacing": "0.04em", "whiteSpace": "nowrap",
            }),
        ], style={"display": "flex", "alignItems": "center",
                  "flexWrap": "wrap", "gap": "6px", "marginBottom": "4px"}),
        html.Div(sub_label, style={
            "fontSize": "12px", "color": v_fg if in_scan else C["muted"],
            "fontStyle": "italic", "lineHeight": "1.4",
        }),
    ], style={
        "background": v_bg, "border": f"2px solid {v_fg}30",
        "borderRadius": "12px 12px 0 0",
        "padding": "12px 14px",
    })

    return html.Div([left_header, body], className="verdict-left")


# ---------------------------------------------------------------------------
# History tab — local Warren AI archive (warren_ai_daily/output)
# ---------------------------------------------------------------------------

def _hist_cell(txt, w="auto", mono=False):
    return html.Td(
        str(txt) if txt is not None else "—",
        style={
            "padding": "6px 8px",
            "fontSize": "12px",
            "borderBottom": f"1px solid {C['border']}",
            "fontFamily": "ui-monospace, monospace" if mono else "inherit",
            "maxWidth": w,
            "whiteSpace": "nowrap",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
        },
    )


def build_history_tab_panel(
    selected_date: str | None,
    active_tickers: set,
    watchlist_tickers: set,
) -> html.Div:
    """Build the History tab body (AI signal archive). Read-only local files."""
    metas = wh.list_history_dates()
    if not metas:
        return html.Div([
            section_hdr("AI Signal History"),
            html.Div(
                "No saved Warren AI output found under "
                f"{wh.WARREN_OUTPUT_DIR}. Run the daily workflow to populate "
                "`warren_ai_daily/output/YYYY-MM-DD/`.",
                style={"fontSize": "13px", "color": C["muted"], "marginTop": "8px"},
            ),
        ])

    dates_ok = {m.date for m in metas}
    day = selected_date if selected_date in dates_ok else metas[0].date

    parsed = wh.parse_history_day(day)
    w = parsed.get("warren")
    meta = parsed.get("meta") or {}
    raw = parsed.get("raw") or {}
    rows = parsed.get("suggestion_rows") or []
    counts = parsed.get("counts") or {}
    warns = parsed.get("warnings") or []

    # Forward walk (reuse morning_status engine)
    fw_rows: list = []
    try:
        fw_rows = ms.get_forward_walk_rows_for_scan_date(
            day,
            active_tickers=active_tickers,
            watchlist_tickers=watchlist_tickers,
            max_lookback_days=400,
        )
    except Exception:
        fw_rows = []

    try:
        fw_all = ms.get_forward_walk_results(
            days=120,
            active_tickers=active_tickers,
            watchlist_tickers=watchlist_tickers,
        )
    except Exception:
        fw_all = []

    by_scan: dict[str, list] = {}
    for r in fw_all:
        by_scan.setdefault(getattr(r, "scan_date", ""), []).append(r)

    recent_dates = [m.date for m in metas[:10]]
    recent_tbl = wh.recent_signal_day_stats(recent_dates, by_scan)

    # Overlap column for suggestions
    for r in rows:
        t = r.get("ticker") or ""
        if t in active_tickers:
            r["overlap"] = "Already owned (current)"
        elif t in watchlist_tickers:
            r["overlap"] = "Watchlist (current)"
        else:
            r["overlap"] = "—"

    note = html.Div(
        "This tab reads locally saved output from `warren_ai_daily/output/YYYY-MM-DD/`. "
        "It does not depend on Investing.com chat history.",
        style={"fontSize": "11px", "color": C["dim"], "marginTop": "10px", "lineHeight": "1.5"},
    )

    # Recent signal days table
    rh = html.Tr([
        html.Th("Date", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Suggestions", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Complete plans", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Top 3", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Avg return", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Win rate", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Best", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Worst", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Run status", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
    ])
    rb = []
    for rr in recent_tbl:
        rb.append(html.Tr([
            _hist_cell(rr.get("date")),
            _hist_cell(rr.get("suggestions")),
            _hist_cell(rr.get("complete_plans")),
            _hist_cell(rr.get("top3")),
            _hist_cell(rr.get("avg_return")),
            _hist_cell(rr.get("win_rate")),
            _hist_cell(rr.get("best"), w="160px"),
            _hist_cell(rr.get("worst"), w="160px"),
            _hist_cell(rr.get("status")),
        ]))

    recent_block = html.Div([
        section_hdr("Recent signal days"),
        html.Table([html.Thead(rh), html.Tbody(rb)], style={
            "width": "100%",
            "borderCollapse": "collapse",
            "marginTop": "8px",
            "background": C["surface"],
            "border": f"1px solid {C['border']}",
            "borderRadius": "10px",
        }),
    ], style={"marginBottom": "1rem"})

    files_found = [k for k, v in (raw.get("files") or {}).items() if v.get("present")]
    missing = raw.get("missing") or []

    status_card = card([
        section_hdr("Run status"),
        html.Div(f"Date: {day}", style={"fontSize": "13px", "fontWeight": 600}),
        html.Div(f"Folder: {meta.get('path', raw.get('path', ''))}", style={
            "fontSize": "11px", "color": C["muted"], "wordBreak": "break-all",
        }),
        html.Div([
            html.Span("Files found: ", style={"color": C["muted"], "fontSize": "12px"}),
            html.Span(str(len(files_found)), style={"fontWeight": 700}),
            html.Span("  ·  Missing: ", style={"color": C["muted"], "fontSize": "12px", "marginLeft": "10px"}),
            html.Span(", ".join(missing) if missing else "—", style={"fontSize": "12px"}),
        ], style={"marginTop": "8px"}),
        html.Div([
            html.Span("Run status: ", style={"color": C["muted"], "fontSize": "12px"}),
            badge(meta.get("status", "Unknown"), C["surface2"], C["text"]),
        ], style={"marginTop": "8px"}),
        html.Div(f"Last modified (newest file): {meta.get('last_modified', '—')}", style={
            "fontSize": "12px", "color": C["muted"], "marginTop": "6px",
        }),
        *([html.Div("Warnings: " + "; ".join(warns), style={
            "fontSize": "12px", "color": C["amber_fg"], "marginTop": "8px",
        })] if warns else []),
    ], pad="14px 16px")

    # Summary card
    pm_yes = "Yes" if meta.get("has_portfolio_monitor") else "No"
    summ = card([
        section_hdr("Daily summary"),
        html.Div([
            html.Div([
                html.Span("Regime: ", style={"color": C["muted"], "fontSize": "12px"}),
                html.Span((w.regime if w else "—") or "—", style={"fontWeight": 700}),
            ]),
            html.Div([
                html.Span("Top sectors: ", style={"color": C["muted"], "fontSize": "12px"}),
                html.Span(", ".join(w.top_sectors) if w and w.top_sectors else "—", style={"fontSize": "12px"}),
            ], style={"marginTop": "6px"}),
            html.Div([
                html.Span("Suggested tickers: ", style={"color": C["muted"], "fontSize": "12px"}),
                html.Span(str(counts.get("suggested_tickers", 0)), style={"fontWeight": 700}),
                html.Span("  ·  Complete plans: ", style={"color": C["muted"], "marginLeft": "10px"}),
                html.Span(str(counts.get("complete_plans", 0)), style={"fontWeight": 700}),
                html.Span("  ·  Top 3 trades: ", style={"color": C["muted"], "marginLeft": "10px"}),
                html.Span(str(counts.get("top3", 0)), style={"fontWeight": 700}),
                html.Span("  ·  Mentioned only: ", style={"color": C["muted"], "marginLeft": "10px"}),
                html.Span(str(counts.get("mentioned_only", 0)), style={"fontWeight": 700}),
            ], style={"marginTop": "6px"}),
            html.Div(f"Portfolio monitor saved: {pm_yes}", style={
                "fontSize": "12px", "color": C["muted"], "marginTop": "6px",
            }),
        ]),
    ], pad="14px 16px")

    # Top 3 cards
    top3_cards = []
    if w and w.top3_trades:
        for i, trd in enumerate(w.top3_trades, start=1):
            tk = trd.get("ticker") or "?"
            tr_row = w.ticker_rows.get(tk) if w else None
            sec = (tr_row.sector_etf if tr_row and tr_row.sector_etf else
                   (w.ticker_sector.get(tk, "") if w else ""))
            er, sr, tpr, rr_txt = ms.warren_level_strings(w, tk) if w else ("", "", "", "")
            top3_cards.append(card([
                html.Div([
                    html.Span(f"Trade {i}", style={"fontSize": "11px", "color": C["muted"]}),
                    html.Span(f"  {tk}", style={"fontSize": "15px", "fontWeight": 800, "marginLeft": "8px"}),
                ]),
                html.Div(trd.get("name") or "", style={"fontSize": "12px", "color": C["muted"]}),
                html.Div(f"Sector: {sec or '—'}", style={"fontSize": "12px", "marginTop": "6px"}),
                html.Div(f"Setup: {(tr_row.setup if tr_row else '') or '—'}", style={
                    "fontSize": "12px", "marginTop": "4px",
                }),
                html.Div([
                    html.Div(f"Entry: {er or '—'}", style={"fontSize": "12px"}),
                    html.Div(f"Stop: {sr or '—'}", style={"fontSize": "12px"}),
                    html.Div(f"Target: {tpr or '—'}", style={"fontSize": "12px"}),
                    html.Div(f"R:R: {rr_txt or '—'}", style={"fontSize": "12px"}),
                ], style={"marginTop": "8px"}),
                html.Div(trd.get("why") or trd.get("verdict") or "", style={
                    "fontSize": "12px", "color": C["dim"], "marginTop": "8px", "fontStyle": "italic",
                }),
                html.Div("Source: final_action_plan.md", style={
                    "fontSize": "10px", "color": C["muted"], "marginTop": "8px",
                }),
            ], pad="12px 14px"))
    else:
        top3_cards.append(html.Div("No Top 3 trades parsed for this date.", style={
            "fontSize": "12px", "color": C["muted"],
        }))

    # Suggestions table
    sh = html.Tr([
        html.Th("Ticker", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Rank", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Sector", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Setup", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Status", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Overlap", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Entry", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Stop", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Target", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("R:R", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Verdict", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Themes", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Source", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
    ])
    sb = []
    for r in rows:
        thm = ", ".join(r.get("themes") or []) or "—"
        sb.append(html.Tr([
            _hist_cell(r.get("ticker"), w="72px", mono=True),
            _hist_cell(r.get("rank")),
            _hist_cell(r.get("sector"), w="56px"),
            _hist_cell(r.get("setup"), w="220px"),
            _hist_cell(r.get("status"), w="120px"),
            _hist_cell(r.get("overlap"), w="160px"),
            _hist_cell(r.get("entry"), w="120px"),
            _hist_cell(r.get("stop"), w="90px"),
            _hist_cell(r.get("target"), w="90px"),
            _hist_cell(r.get("rr"), w="70px"),
            _hist_cell(r.get("verdict"), w="120px"),
            _hist_cell(thm, w="140px"),
            _hist_cell(r.get("source"), w="120px"),
        ]))

    sugg_table = html.Div([
        section_hdr("All suggestions"),
        html.Div(style={"overflowX": "auto"}, children=[
            html.Table([html.Thead(sh), html.Tbody(sb)], style={
                "width": "100%",
                "borderCollapse": "collapse",
                "marginTop": "8px",
                "background": C["surface"],
                "border": f"1px solid {C['border']}",
                "borderRadius": "10px",
            }),
        ]),
    ], style={"marginBottom": "1rem"})

    # Performance block
    perf_intro = ""
    if not fw_rows:
        # Explain absence
        if not (meta.get("has_run_summary") and meta.get("status") == "Complete"):
            perf_intro = "Forward walk performance is only computed for successful runs in the lookback window."
        else:
            perf_intro = "No forward walk rows for this scan date (outside lookback or not enough post-scan bars yet)."
    perf_header = html.Tr([
        html.Th("Ticker", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Scan date", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Scan px", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Now", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Return", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Days", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("MFE%", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("MAE%", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Outcome", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
        html.Th("Portfolio", style={"padding": "6px 8px", "fontSize": "11px", "color": C["muted"]}),
    ])
    pb = []
    for r in fw_rows:
        pb.append(html.Tr([
            _hist_cell(getattr(r, "ticker", ""), mono=True),
            _hist_cell(getattr(r, "scan_date", "")),
            _hist_cell(getattr(r, "scan_price", "")),
            _hist_cell(getattr(r, "current_price", "")),
            _hist_cell(
                f"{r.return_pct:+.2f}%" if getattr(r, "return_pct", None) is not None else "—"
            ),
            _hist_cell(getattr(r, "days_since", "")),
            _hist_cell(getattr(r, "mfe_pct", "")),
            _hist_cell(getattr(r, "mae_pct", "")),
            _hist_cell(getattr(r, "outcome", ""), w="140px"),
            _hist_cell(getattr(r, "portfolio_status", ""), w="140px"),
        ]))

    perf_block = html.Div([
        section_hdr("Performance since signal"),
        html.Div(perf_intro, style={"fontSize": "12px", "color": C["muted"], "marginTop": "6px"}),
        html.Div(style={"overflowX": "auto"}, children=[
            html.Table([html.Thead(perf_header), html.Tbody(pb)], style={
                "width": "100%",
                "borderCollapse": "collapse",
                "marginTop": "8px",
                "background": C["surface"],
                "border": f"1px solid {C['border']}",
                "borderRadius": "10px",
            }),
        ]) if fw_rows else html.Div(
            "Not enough post-scan data yet, or performance unavailable (price fetch / run not in forward walk).",
            style={"fontSize": "12px", "color": C["muted"], "marginTop": "8px"},
        ),
    ], style={"marginBottom": "1rem"})

    # Raw snippets (collapsed)
    raw_children = []
    for fname in ("final_action_plan.md", "step_04.md", "step_03.md", "run_summary.json"):
        snip = (raw.get("snippets") or {}).get(fname)
        if fname == "run_summary.json" and raw.get("run_summary_raw") is not None:
            body = json.dumps(raw.get("run_summary_raw"), indent=2)[:8000]
        else:
            body = snip or "(missing)"
        raw_children.append(html.Details([
            html.Summary(fname, style={"cursor": "pointer", "fontWeight": 600, "fontSize": "12px"}),
            html.Pre(body, style={
                "fontSize": "11px",
                "whiteSpace": "pre-wrap",
                "maxHeight": "320px",
                "overflow": "auto",
                "background": C["bg"],
                "padding": "10px",
                "borderRadius": "8px",
                "border": f"1px solid {C['border']}",
            }),
        ], style={"marginBottom": "8px"}))

    raw_block = html.Details([
        html.Summary("Raw files (collapsed)", style={
            "cursor": "pointer", "fontWeight": 700, "fontSize": "13px",
        }),
        html.Div(raw_children, style={"marginTop": "10px"}),
    ], style={"marginBottom": "1rem"})

    top3_row = html.Div(
        top3_cards,
        style={
            "display": "grid",
            "gridTemplateColumns": "repeat(auto-fit, minmax(240px, 1fr))",
            "gap": "12px",
            "marginBottom": "1rem",
        },
    )

    return html.Div([
        html.Div([
            html.Div("AI Signal History", style={"fontSize": "18px", "fontWeight": 800}),
            html.Div(
                "Browse locally saved Warren AI suggestions by date. This is an archive of what "
                "the automation captured at the time.",
                style={"fontSize": "12px", "color": C["muted"], "marginTop": "6px", "maxWidth": "900px"},
            ),
        ], style={"marginBottom": "12px"}),
        recent_block,
        html.Div([
            status_card,
            summ,
        ], style={
            "display": "grid",
            "gridTemplateColumns": "repeat(auto-fit, minmax(320px, 1fr))",
            "gap": "12px",
            "marginBottom": "12px",
        }),
        section_hdr("Top 3 trades"),
        top3_row,
        sugg_table,
        perf_block,
        raw_block,
        note,
    ])


# ---------------------------------------------------------------------------
# Tab styling
# ---------------------------------------------------------------------------

_TAB_STYLE = {
    "padding": "10px 20px 10px 0",
    "fontFamily": "system-ui, -apple-system, sans-serif",
    "fontSize": "14px", "fontWeight": 500,
    "color": C["muted"],
    "backgroundColor": "transparent",
    "border": "none",
    "borderBottom": "2px solid transparent",
}
_TAB_SELECTED_STYLE = {
    **_TAB_STYLE,
    "color": C["text"],
    "fontWeight": 700,
    "borderBottom": f"2px solid {C['blue_fg']}",
}

# ---------------------------------------------------------------------------
# App layout
# ---------------------------------------------------------------------------

app = Dash(__name__, title="Decision Cockpit",
           suppress_callback_exceptions=True)
server = app.server


@server.route("/healthz")
def healthz():
    """Lightweight liveness probe for Control Center / SentryBot checks."""
    return jsonify({"ok": True, "service": "decision-cockpit", "port": PORT})


# ---------------------------------------------------------------------------
# REST API — Warren AI
# ---------------------------------------------------------------------------

@server.route("/api/warren-ai/latest")
def api_warren_latest():
    """Return structured data from the latest successful Warren AI daily run."""
    result = wl.get_latest_warren()
    return jsonify(wl.warren_to_dict(result))


@server.route("/api/warren-ai/portfolio/latest")
def api_warren_portfolio_latest():
    """Return latest portfolio monitor outputs (Prompts 6 & 7)."""
    pm = wl.get_latest_portfolio_monitor()
    return jsonify(wl.portfolio_monitor_to_dict(pm))


@server.route("/api/warren-ai/morning/latest")
def api_warren_morning_latest():
    """Combined morning status: new ideas + portfolio monitor + summary."""
    w  = wl.get_latest_warren()
    pm = wl.get_latest_portfolio_monitor()

    # Load combined summary JSON if it exists
    combined: dict = {}
    dayDir = wl.WARREN_OUTPUT_DIR  # e.g. .../warren_ai_daily/output
    today  = datetime.now().strftime("%Y-%m-%d")
    summary_path = os.path.join(dayDir, today, "full_morning_summary.json")
    if os.path.isfile(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as fh:
                combined = json.load(fh)
        except Exception:
            pass

    return jsonify({
        "date":                   w.date or pm.date or today,
        "newIdeasFreshness":      w.freshness,
        "newIdeasDate":           w.date,
        "portfolioFreshness":     pm.freshness,
        "portfolioDate":          pm.date,
        "newIdeasSummary": {
            "regime":             w.regime,
            "topSectors":         w.top_sectors,
            "actionable":         w.actionable,
            "top3Trades":         w.top3_trades,
        },
        "portfolioSummary": {
            "holdingsChecked":    len(pm.positions_used),
            "improved":           pm.improved,
            "increasedRisk":      pm.increased_risk,
            "tickerActions":      pm.ticker_actions,
        },
        "combinedSummary":        combined,
        "combinedWarnings":       w.warnings + pm.warnings,
        "paths": {
            "dayDir":             os.path.join(dayDir, today),
            "finalActionPlan":    os.path.join(dayDir, today, "final_action_plan.md"),
            "portfolioStep6":     os.path.join(dayDir, today, "portfolio_step_06.md"),
            "portfolioStep7":     os.path.join(dayDir, today, "portfolio_step_07.md"),
            "combinedSummary":    summary_path,
        },
    })

@server.route("/api/atlas/morning-audio-source")
def api_atlas_morning_audio_source():
    """Deterministic audio-source readiness contract for AtlasInvestor morning analysis."""
    active, watchlist, _, _ = run_analysis()
    active_tickers = {getattr(p, "ticker", None) for p in active if getattr(p, "ticker", None)}
    watchlist_tickers = {getattr(p, "ticker", None) for p in watchlist if getattr(p, "ticker", None)}
    active_pos = db.get_positions(db.ACTIVE_STATUSES)
    try:
        fw_rows = ms.get_forward_walk_results(
            days=60, active_tickers=active_tickers, watchlist_tickers=watchlist_tickers,
        )
        pa_series, pa_skipped = ms.build_post_alert_series(fw_rows)
        sec_series, sec_skipped = ms.build_post_alert_sector_series(fw_rows)
        pa_data = {
            "series": pa_series, "skipped": pa_skipped,
            "sector_series": sec_series, "sector_skipped": sec_skipped,
        }
    except Exception as exc:
        pa_data = {"series": [], "sector_series": [], "error": str(exc)}
    payload = amas.build_atlas_morning_audio_source(
        pa_data,
        active_tickers=active_tickers,
        watchlist_tickers=watchlist_tickers,
        active_positions=active_pos,
    )
    return jsonify(payload)


@server.route("/api/atlas/theme-catalyst-radar")
def api_atlas_theme_catalyst_radar():
    """Event-driven theme catalyst radar — proxy basket confirmation for private-company catalysts."""
    payload = atcr.build_theme_catalyst_radar()
    return jsonify(payload)


@server.route("/api/chart/daily/<ticker>")
def api_chart_daily(ticker: str):
    """Return daily OHLCV + indicators as JSON. Source: yfinance."""
    from flask import request
    months = int(request.args.get("months", 6))
    months = max(1, min(months, 24))
    return jsonify(cd.get_daily_chart_data(ticker, months))


@server.route("/api/positions/import/preview", methods=["POST"])
def api_positions_import_preview():
    """
    Dry-run import: validate + enrich rows, return preview without writing to DB.
    Body: JSON array (or {positions: [...]}).
    Returns: {applied, skipped, errors, rows: [{Ticker,Name,Qty,Sector,...}]}
    """
    from flask import request as freq
    try:
        body = freq.get_json(force=True, silent=True) or []
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    if isinstance(body, dict):
        body = body.get("positions") or body.get("holdings") or []

    preview_rows = []
    for raw_row in body:
        ticker  = str(raw_row.get("ticker") or raw_row.get("symbol") or "").strip().upper()
        shares  = raw_row.get("shares") or raw_row.get("quantity") or raw_row.get("qty")
        cost    = raw_row.get("cost_basis") or raw_row.get("average_cost") or raw_row.get("avg_cost")
        mkt_val = raw_row.get("market_value") or raw_row.get("marketValue")
        price   = raw_row.get("current_price") or raw_row.get("price") or raw_row.get("lastPrice")
        if ticker:
            info           = _fetch_ticker_info(ticker)
            company        = info.get("company") or ticker
            explicit_raw   = (raw_row.get("sector_etf") or raw_row.get("sector") or "").strip()
            sector_raw     = explicit_raw or info.get("sector_raw") or ""
            sector_norm    = db.normalize_sector(sector_raw) if sector_raw else info.get("sector_normalized") or "UNKNOWN"
            industry       = raw_row.get("industry") or info.get("industry") or ""
        else:
            company = industry = ""
            sector_raw  = ""
            sector_norm = "UNKNOWN"
        errs = db.validate_import_row({
            "ticker": ticker, "shares": shares, "status": raw_row.get("status", "in_position"),
        })
        flags = errs[:]
        if sector_norm == "UNKNOWN":
            flags.append("sector unmapped — will store as UNKNOWN")
        preview_rows.append({
            "Ticker": ticker or "(missing)", "Name": company,
            "Qty": shares, "Mkt Value": mkt_val, "Price": price, "Avg Cost": cost,
            "Sector Raw": sector_raw or "", "Sector ETF": sector_norm,
            "Industry": industry,
            "Validation": ("OK" if not flags else "WARN/ERROR: " + "; ".join(flags)),
        })

    # Enrich body with sector_raw before dry-run import
    enriched = []
    for raw_row in body:
        t = str(raw_row.get("ticker") or raw_row.get("symbol") or "").strip().upper()
        if t and not (raw_row.get("sector_etf") or raw_row.get("sector") or raw_row.get("sector_raw")):
            info = _fetch_ticker_info(t)
            raw_row = dict(raw_row)
            raw_row["sector_raw"] = info.get("sector_raw") or ""
        enriched.append(raw_row)

    result = db.import_positions_json(enriched, dry_run=True)
    result["rows"] = preview_rows
    return jsonify(result)


# ---------------------------------------------------------------------------
# Chart builder
# ---------------------------------------------------------------------------

_CHART_COLORS = {
    "ema20":  "#3b9b47",   # green
    "ema50":  "#0c447c",   # blue
    "sma200": "#d97706",   # amber
    "up":     "#3b9b47",
    "down":   "#c0392b",
    "vol_up": "#a8d5ac",
    "vol_dn": "#f5b3b0",
}


def build_ticker_chart(ticker: str, data: dict) -> html.Div:
    """
    Build a compact 6-month daily candlestick + EMA overlay + volume chart.
    Returns html.Div with className='verdict-right' for 2-col layout.
    """
    rows_raw = data.get("rows", [])
    warnings = data.get("warnings", [])
    source   = data.get("source", "yfinance")
    updated  = (data.get("last_updated") or "")[:19].replace("T", " ")

    if not rows_raw:
        msg = warnings[0] if warnings else "Data source returned no daily bars."
        return html.Div([
            html.Div("6M daily chart", style={
                "fontSize": "11px", "fontWeight": 600, "color": C["muted"],
                "textTransform": "uppercase", "letterSpacing": "0.04em",
                "marginBottom": "6px",
            }),
            html.Div(f"Chart unavailable. {msg}", style={
                "fontSize": "12px", "color": C["dim"], "fontStyle": "italic",
            }),
        ], className="verdict-right")

    # ── Unpack series ─────────────────────────────────────────────────────
    dates  = [r["date"]   for r in rows_raw]
    opens  = [r["open"]   for r in rows_raw]
    highs  = [r["high"]   for r in rows_raw]
    lows   = [r["low"]    for r in rows_raw]
    closes = [r["close"]  for r in rows_raw]
    vols   = [r["volume"] for r in rows_raw]
    ema20  = [r.get("ema20")  for r in rows_raw]
    ema50  = [r.get("ema50")  for r in rows_raw]
    sma200 = [r.get("sma200") for r in rows_raw]
    has200 = any(v is not None for v in sma200)

    vol_colors = [
        _CHART_COLORS["vol_up"] if (c or 0) >= (o or 0)
        else _CHART_COLORS["vol_dn"]
        for c, o in zip(closes, opens)
    ]

    # ── Build figure ──────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.78, 0.22],
        vertical_spacing=0.04,
    )

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=dates, open=opens, high=highs, low=lows, close=closes,
        name=ticker,
        increasing_line_color=_CHART_COLORS["up"],
        decreasing_line_color=_CHART_COLORS["down"],
        increasing_fillcolor=_CHART_COLORS["up"],
        decreasing_fillcolor=_CHART_COLORS["down"],
        line_width=1,
        whiskerwidth=0.6,
        showlegend=False,
    ), row=1, col=1)

    # 20 EMA
    fig.add_trace(go.Scatter(
        x=dates, y=ema20, name="20 EMA",
        line={"color": _CHART_COLORS["ema20"], "width": 1.5},
        hovertemplate="%{y:.2f}<extra>20 EMA</extra>",
    ), row=1, col=1)

    # 50 EMA
    fig.add_trace(go.Scatter(
        x=dates, y=ema50, name="50 EMA",
        line={"color": _CHART_COLORS["ema50"], "width": 1.5},
        hovertemplate="%{y:.2f}<extra>50 EMA</extra>",
    ), row=1, col=1)

    # 200 SMA (dashed, only if available)
    if has200:
        fig.add_trace(go.Scatter(
            x=dates, y=sma200, name="200 SMA",
            line={"color": _CHART_COLORS["sma200"], "width": 1.5, "dash": "dot"},
            hovertemplate="%{y:.2f}<extra>200 SMA</extra>",
        ), row=1, col=1)

    # Volume bars
    fig.add_trace(go.Bar(
        x=dates, y=vols,
        name="Volume",
        marker_color=vol_colors,
        showlegend=False,
        hovertemplate="%{y:,.0f}<extra>Volume</extra>",
    ), row=2, col=1)

    # ── Layout ────────────────────────────────────────────────────────────
    fig.update_layout(
        height=1080,  # 3× prior default (360) for easier visual review
        margin={"l": 48, "r": 12, "t": 10, "b": 30},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#fafaf7",
        font={"family": "system-ui, -apple-system, sans-serif", "size": 11,
              "color": "#5f5e5a"},
        legend={
            "orientation": "h", "x": 0, "y": 1.04,
            "font": {"size": 11},
            "bgcolor": "rgba(0,0,0,0)",
        },
        xaxis_rangeslider_visible=False,
        xaxis2={"showticklabels": True, "tickformat": "%b %d"},
        yaxis={
            "side": "right", "showgrid": True,
            "gridcolor": "rgba(0,0,0,0.06)",
            "tickformat": ".2f",
        },
        yaxis2={
            "side": "right", "showgrid": False,
            "tickformat": ".2s",
        },
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, zeroline=False, showspikes=True,
                     spikecolor="#aaa", spikethickness=1)

    # ── Wrap in html.Div ─────────────────────────────────────────────────
    legend_chips = [
        html.Span("20 EMA", style={
            "background": _CHART_COLORS["ema20"] + "22",
            "color": _CHART_COLORS["ema20"],
            "fontSize": "10px", "fontWeight": 700,
            "padding": "1px 7px", "borderRadius": "8px", "marginRight": "5px",
        }),
        html.Span("50 EMA", style={
            "background": _CHART_COLORS["ema50"] + "22",
            "color": _CHART_COLORS["ema50"],
            "fontSize": "10px", "fontWeight": 700,
            "padding": "1px 7px", "borderRadius": "8px", "marginRight": "5px",
        }),
    ]
    if has200:
        legend_chips.append(html.Span("200 SMA", style={
            "background": _CHART_COLORS["sma200"] + "22",
            "color": _CHART_COLORS["sma200"],
            "fontSize": "10px", "fontWeight": 700,
            "padding": "1px 7px", "borderRadius": "8px", "marginRight": "5px",
        }))
    if warnings:
        legend_chips.append(html.Span(
            "⚠ " + warnings[0],
            style={"fontSize": "10px", "color": C["dim"], "fontStyle": "italic"},
        ))

    header = html.Div([
        html.Span("6M daily chart", style={
            "fontSize": "11px", "fontWeight": 700, "color": C["muted"],
            "textTransform": "uppercase", "letterSpacing": "0.04em",
            "marginRight": "10px",
        }),
        *legend_chips,
        html.Span(f" · {source} · {updated} UTC",
                  style={"fontSize": "10px", "color": C["dim"], "float": "right"}),
    ], style={"marginBottom": "4px", "display": "flex", "flexWrap": "wrap",
               "alignItems": "center"})

    return html.Div([
        header,
        dcc.Graph(
            figure=fig,
            config={"displayModeBar": False, "responsive": True},
            style={"width": "100%"},
        ),
    ], className="verdict-right")


_MB = {"marginBottom": "1.5rem"}

# Responsive two-column grid for compact summary cards (Dashboard cockpit).
_SUMMARY_GRID = {
    "display":               "grid",
    "gridTemplateColumns":   "repeat(auto-fit, minmax(420px, 1fr))",
    "gap":                   "12px",
    "marginBottom":          "12px",
    "alignItems":            "start",
}
_SUMMARY_CELL = {"minWidth": 0}

app.layout = html.Div(
    style={
        "background": C["bg"], "minHeight": "100vh",
        "padding": "28px 40px 48px",
        "fontFamily": "system-ui, -apple-system, 'Segoe UI', sans-serif",
        "color": C["text"],
    },
    children=[
        dcc.Store(id="version",                    data=0),
        dcc.Store(id="selected-sector",            data=None),
        dcc.Store(id="exposure-sector-filter",     data=None),
        dcc.Store(id="post-alert-data-store",      data=None),
        dcc.Store(id="selected-suggested-ticker",  data=None),

        # Header
        html.Div([
            html.Div("Investing Decision Cockpit",
                     style={"fontSize": "20px", "fontWeight": 700}),
            html.Div([
                html.Span(id="last-updated",
                          style={"fontSize": "12px", "color": C["muted"],
                                 "marginRight": "14px"}),
                html.Button("↻ Refresh prices", id="btn-refresh", n_clicks=0, style={
                    "background": C["surface"], "color": C["text"],
                    "border": f"1px solid {C['border']}", "borderRadius": "8px",
                    "padding": "6px 14px", "fontSize": "13px", "cursor": "pointer",
                }),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "marginBottom": "1.75rem"}),

        # Main tabs
        dcc.Tabs(
            id="main-tabs",
            value="dashboard",
            style={"marginBottom": "1.5rem",
                   "borderBottom": f"1px solid {C['border']}"},
            children=[

                # ── Dashboard tab ────────────────────────────────────────
                dcc.Tab(
                    label="Dashboard",
                    value="dashboard",
                    style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                    children=[

                        # ① Daily Decision Summary (unified top box)
                        html.Div(id="daily-decision-dash", style=_MB),

                        # ② System Health Ribbon (detailed)
                        html.Div(id="system-health-ribbon", style=_MB),

                        # ③ Leadership Gap & Opportunity Cost (full width)
                        html.Div(id="leadership-gap-dash", style=_MB),

                        # ④ Row 1 (two columns): Portfolio Action | Scanner Edge Check
                        html.Div([
                            html.Div(
                                id="portfolio-action-dash",
                                style={
                                    **_SUMMARY_CELL,
                                    "borderLeft": f"4px solid {C['blue_fg']}",
                                    "paddingLeft": "10px",
                                },
                            ),
                            html.Div(id="fw-summary-dash", style=_SUMMARY_CELL),
                        ], style={**_SUMMARY_GRID, "marginBottom": "12px"}),

                        # ⑤ New Ideas To Review — FULL WIDTH
                        html.Div(
                            id="new-ideas-dash",
                            style={**_MB, "width": "100%", "minWidth": 0},
                        ),

                        # ⑥ Suggested Ticker Chart Review — FULL WIDTH
                        html.Div(
                            id="chart-review-outer",
                            style={
                                **_MB,
                                "background": C["surface"],
                                "borderRadius": "12px",
                                "padding": "14px 18px",
                                "border": f"1px solid {C['border']}",
                            },
                            children=[
                                # Static controls (in layout so they don't get reset on re-render)
                                html.Div([
                                    html.Div([
                                        section_hdr("Suggested Ticker Chart Review"),
                                        html.Span(
                                            "  Daily candles · 20 EMA · 50 EMA · 200 SMA · "
                                            "trade levels from latest scan.",
                                            style={
                                                "fontSize": "11px", "color": C["dim"],
                                                "fontStyle": "italic", "marginLeft": "8px",
                                            },
                                        ),
                                    ], style={"display": "flex", "alignItems": "center",
                                              "flexWrap": "wrap", "marginBottom": "10px"}),
                                    html.Div([
                                        html.Div([
                                            html.Span("Ticker:", style={
                                                "fontSize": "11px", "color": C["dim"],
                                                "fontWeight": 600, "marginRight": "6px",
                                            }),
                                            dcc.Dropdown(
                                                id="chart-ticker-dropdown",
                                                options=[],
                                                value=None,
                                                clearable=False,
                                                style={
                                                    "minWidth": "280px", "fontSize": "12px",
                                                    "background": C["surface"],
                                                },
                                                optionHeight=36,
                                            ),
                                        ], style={"display": "flex", "alignItems": "center",
                                                  "gap": "6px"}),
                                        html.Div([
                                            html.Span("Lookback:", style={
                                                "fontSize": "11px", "color": C["dim"],
                                                "fontWeight": 600, "marginRight": "6px",
                                            }),
                                            dcc.RadioItems(
                                                id="chart-lookback",
                                                options=[
                                                    {"label": "3 M",  "value": 3},
                                                    {"label": "6 M",  "value": 6},
                                                    {"label": "1 Y",  "value": 12},
                                                ],
                                                value=6,
                                                inline=True,
                                                labelStyle={"fontSize": "12px",
                                                            "marginRight": "10px",
                                                            "cursor": "pointer"},
                                                inputStyle={"marginRight": "4px"},
                                            ),
                                        ], style={"display": "flex", "alignItems": "center",
                                                  "gap": "6px"}),
                                    ], style={
                                        "display": "flex", "flexWrap": "wrap",
                                        "gap": "16px", "alignItems": "center",
                                        "marginBottom": "12px",
                                    }),
                                ]),
                                # Dynamic: header + chart (updated by callback)
                                html.Div(id="chart-review-section"),
                            ],
                        ),

                        # ⑦ Row 4 (two columns): Weekly Theme Score Summary | Theme Radar
                        #    Row 5 (two columns): Mag 7 / XLK sleeve | Portfolio Theme Exposure
                        html.Div([
                            html.Div([
                                html.Div(id="theme-score-summary-dash", style=_SUMMARY_CELL),
                                html.Div(id="theme-radar-dash", style=_SUMMARY_CELL),
                            ], style=_SUMMARY_GRID),
                            html.Div([
                                html.Div(id="mag7-exposure-dash", style=_SUMMARY_CELL),
                                html.Div(id="theme-exposure-dash", style=_SUMMARY_CELL),
                            ], style=_SUMMARY_GRID),
                        ], style=_MB),

                        # ⑧ Compact Macro Badge (full width)
                        html.Div(id="macro-badge-dash", style=_MB),

                        # ⑨ Portfolio exposure charts & rebalancing (full width)
                        html.Div(id="exposure-section", style=_MB),
                        html.Div(id="capital-breakdown-section", style=_MB),

                        # ⑩ Post Alert Performance chart (interactive)
                        html.Div(
                            id="post-alert-outer",
                            style={**_MB, "background": C["surface"], "borderRadius": "12px",
                                   "padding": "18px", "border": f"1px solid {C['border']}"},
                            children=[
                                html.Div([
                                    html.Span(
                                        "Post Alert Performance",
                                        style={"fontSize": "13px", "fontWeight": 700,
                                               "textTransform": "uppercase",
                                               "letterSpacing": "0.05em", "color": C["text"]},
                                    ),
                                    html.Span(
                                        "  Normalized % return since alert date  ·  "
                                        "anchor = scan price (fallback: first available close). "
                                        "Sectors = sector ETF from the same scan date (selection vs. tape).",
                                        style={"fontSize": "11px", "color": C["dim"],
                                               "fontStyle": "italic"},
                                    ),
                                ], style={"marginBottom": "10px"}),
                                # Controls
                                html.Div([
                                    html.Div([
                                        html.Span("View:", style={
                                            "fontSize": "11px", "color": C["dim"],
                                            "marginRight": "6px", "fontWeight": 600}),
                                        dcc.RadioItems(
                                            id="pa-segment",
                                            options=[
                                                {"label": "Stocks", "value": "stocks"},
                                                {"label": "Sectors (ETF)", "value": "sectors"},
                                            ],
                                            value="stocks",
                                            inline=True,
                                            labelStyle={"fontSize": "12px", "marginRight": "10px",
                                                        "cursor": "pointer"},
                                            inputStyle={"marginRight": "4px"},
                                        ),
                                    ], style={"display": "flex", "alignItems": "center",
                                              "marginRight": "22px", "marginBottom": "4px"}),
                                    html.Div([
                                        html.Span("X Axis:", style={
                                            "fontSize": "11px", "color": C["dim"],
                                            "marginRight": "6px", "fontWeight": 600}),
                                        dcc.RadioItems(
                                            id="pa-mode",
                                            options=[
                                                {"label": "Days Since Alert", "value": "days"},
                                                {"label": "Calendar Date",   "value": "calendar"},
                                            ],
                                            value="days",
                                            inline=True,
                                            labelStyle={"fontSize": "12px", "marginRight": "10px",
                                                        "cursor": "pointer"},
                                            inputStyle={"marginRight": "4px"},
                                        ),
                                    ], style={"display": "flex", "alignItems": "center",
                                              "marginRight": "22px", "marginBottom": "4px"}),
                                    html.Div([
                                        html.Span("Lines:", style={
                                            "fontSize": "11px", "color": C["dim"],
                                            "marginRight": "6px", "fontWeight": 600}),
                                        dcc.RadioItems(
                                            id="pa-lines",
                                            options=[
                                                {"label": "All",     "value": "all"},
                                                {"label": "Average", "value": "avg"},
                                                {"label": "Both",    "value": "both"},
                                            ],
                                            value="avg",
                                            inline=True,
                                            labelStyle={"fontSize": "12px", "marginRight": "10px",
                                                        "cursor": "pointer"},
                                            inputStyle={"marginRight": "4px"},
                                        ),
                                    ], style={"display": "flex", "alignItems": "center",
                                              "marginRight": "22px", "marginBottom": "4px"}),
                                    html.Div([
                                        html.Span("Filter:", style={
                                            "fontSize": "11px", "color": C["dim"],
                                            "marginRight": "6px", "fontWeight": 600}),
                                        dcc.RadioItems(
                                            id="pa-filter",
                                            options=[
                                                {"label": "All",   "value": "all"},
                                                {"label": "Top 3", "value": "top3"},
                                                {"label": "New",   "value": "new"},
                                                {"label": "Owned", "value": "owned"},
                                            ],
                                            value="all",
                                            inline=True,
                                            labelStyle={"fontSize": "12px", "marginRight": "10px",
                                                        "cursor": "pointer"},
                                            inputStyle={"marginRight": "4px"},
                                        ),
                                    ], style={"display": "flex", "alignItems": "center",
                                              "marginRight": "22px", "marginBottom": "4px"}),
                                    html.Div([
                                        html.Span("Dedupe:", style={
                                            "fontSize": "11px", "color": C["dim"],
                                            "marginRight": "6px", "fontWeight": 600}),
                                        dcc.RadioItems(
                                            id="pa-dedupe",
                                            options=[
                                                {"label": "None", "value": "none"},
                                                {"label": "Latest per ticker", "value": "latest"},
                                                {"label": "Earliest per ticker", "value": "earliest"},
                                            ],
                                            value="latest",
                                            inline=True,
                                            labelStyle={"fontSize": "12px", "marginRight": "10px",
                                                        "cursor": "pointer"},
                                            inputStyle={"marginRight": "4px"},
                                        ),
                                    ], style={"display": "flex", "alignItems": "center",
                                              "marginRight": "22px", "marginBottom": "4px"}),
                                    html.Div([
                                        html.Span("Overlays:", style={
                                            "fontSize": "11px", "color": C["dim"],
                                            "marginRight": "6px", "fontWeight": 600}),
                                        dcc.Checklist(
                                            id="pa-benchmarks",
                                            options=[
                                                {"label": "MAG7", "value": "MAG7"},
                                                {"label": "QQQ",  "value": "QQQ"},
                                                {"label": "XLK",  "value": "XLK"},
                                                {"label": "SPY",  "value": "SPY"},
                                            ],
                                            value=["MAG7", "QQQ", "XLK"],
                                            inline=True,
                                            labelStyle={"fontSize": "12px", "marginRight": "8px",
                                                        "cursor": "pointer"},
                                            inputStyle={"marginRight": "4px"},
                                        ),
                                    ], style={"display": "flex", "alignItems": "center",
                                              "marginBottom": "4px"}),
                                ], style={"display": "flex", "flexWrap": "wrap",
                                          "marginBottom": "8px"}),
                                # Stats note
                                html.Div(id="pa-stats", style={
                                    "fontSize": "11px", "color": C["dim"], "marginBottom": "6px",
                                }),
                                # Chart
                                dcc.Loading(
                                    dcc.Graph(
                                        id="pa-chart",
                                        style={"height": "380px"},
                                        config={"displayModeBar": True,
                                                "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                                                "displaylogo": False},
                                    ),
                                    type="circle",
                                ),
                            ],
                        ),

                        # ⑪ Sector ETF Allocation Check (decision support)
                        html.Div(
                            id="etf-allocation-check-outer",
                            style={**_MB},
                        ),

                        # ⑫ Atlas Morning Audio Source Readiness
                        html.Div(
                            id="atlas-audio-readiness-outer",
                            style={**_MB},
                        ),

                        # ⑬ Theme Catalyst Radar
                        html.Div(
                            id="theme-catalyst-radar-outer",
                            style={**_MB},
                        ),

                        # ⑧ Active Alerts (always visible)
                        html.Div([
                            html.Div("Active Alerts", style={
                                "fontSize": "13px", "fontWeight": 700,
                                "textTransform": "uppercase", "letterSpacing": "0.05em",
                                "marginBottom": "10px",
                            }),
                            html.Div(id="alerts-panel"),
                        ], style=_MB),

                        # ⑨ Morning Scan Details (expanded by default; collapsible)
                        html.Details([
                            html.Summary(
                                "▸  Morning Scan Details — Suggested Tickers, Forward Walk, Automation Health",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div(id="morning-scan-dash", style={"paddingTop": "12px"}),
                        ], style=_MB, open=True),

                        # ⑩ Verdict + Metrics (expanded by default; collapsible)
                        html.Details([
                            html.Summary(
                                "▸  Trim Monitor Summary — Verdict & Metric Tiles",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div([
                                html.Div(id="verdict-banner", style=_MB),
                                html.Div(id="metric-row",     style=_MB),
                            ], style={"paddingTop": "12px"}),
                        ], style=_MB, open=True),

                        # ⑪ Sector Rotation Details (expanded by default; collapsible)
                        html.Details([
                            html.Summary(
                                "▸  Sector Rotation Details",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div(id="sector-rotation-dash", style={"paddingTop": "12px"}),
                        ], style=_MB, open=True),

                        # ⑫ Opportunity Cost Full Table (collapsed by default — primary is in Leadership Gap card)
                        html.Details([
                            html.Summary(
                                "▸  Full Opportunity Cost Details",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div(id="opportunity-cost-dash", style={"paddingTop": "12px"}),
                        ], style=_MB, open=False),

                        # ⑬ Full Macro Regime (expanded by default; collapsible)
                        html.Details([
                            html.Summary(
                                "▸  Full Macro Regime Detail",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div(id="macro-regime-dash", style={"paddingTop": "12px"}),
                        ], style=_MB, open=True),

                        # ⑭ Active Holdings Table (expanded by default; collapsible)
                        html.Details([
                            html.Summary(
                                "▸  Active Holdings — Full Table",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div([
                                html.Div([
                                    section_hdr("Portfolio — Active Holdings"),
                                    html.Div([
                                        _btn("+ Add position",   "btn-add-position",  primary=True),
                                        _btn("Edit",             "btn-edit-position",  disabled=True),
                                        _btn("Archive",          "btn-archive-position", danger=True,
                                             disabled=True),
                                    ], style={"display": "flex"}),
                                ], style={"display": "flex", "justifyContent": "space-between",
                                          "alignItems": "center", "marginBottom": "10px"}),
                                dcc.Loading(
                                    dash_table.DataTable(
                                        id="tbl-portfolio",
                                        columns=PORTFOLIO_COLS,
                                        data=[],
                                        row_selectable="single",
                                        selected_rows=[],
                                        style_cell=_CELL,
                                        style_header=_HEADER,
                                        style_header_conditional=PORTFOLIO_HEADER_ALIGN,
                                        style_data_conditional=PORTFOLIO_COND + PORTFOLIO_DATA_ALIGN,
                                        style_table=_TABLE,
                                        cell_selectable=False,
                                    ),
                                    type="circle",
                                ),
                            ], style={"paddingTop": "12px"}),
                        ], style=_MB, open=True),

                        # ⑮ Watchlist Table (expanded by default; collapsible)
                        html.Details([
                            html.Summary(
                                "▸  Watchlist — Full Table",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div([
                                html.Div([
                                    section_hdr("Watchlist"),
                                    html.Div([
                                        _btn("+ Add to watchlist", "btn-add-watchlist", primary=True),
                                        _btn("Edit",               "btn-edit-watchlist",  disabled=True),
                                        _btn("Archive",            "btn-archive-watchlist", danger=True,
                                             disabled=True),
                                    ], style={"display": "flex"}),
                                ], style={"display": "flex", "justifyContent": "space-between",
                                          "alignItems": "center", "marginBottom": "10px"}),
                                dcc.Loading(
                                    dash_table.DataTable(
                                        id="tbl-watchlist",
                                        columns=WATCHLIST_COLS,
                                        data=[],
                                        row_selectable="single",
                                        selected_rows=[],
                                        style_cell=_CELL,
                                        style_header=_HEADER,
                                        style_data_conditional=WATCHLIST_COND,
                                        style_table=_TABLE,
                                        cell_selectable=False,
                                    ),
                                    type="circle",
                                ),
                            ], style={"paddingTop": "12px"}),
                        ], style=_MB, open=True),

                        # ⑯ Import Positions (expanded by default; collapsible — Data Utilities)
                        html.Details([
                            html.Summary(
                                "▸  Data Utilities — Import Positions from JSON",
                                style={
                                    "cursor": "pointer", "fontSize": "12px", "fontWeight": 700,
                                    "color": C["muted"], "textTransform": "uppercase",
                                    "letterSpacing": "0.05em", "padding": "10px 16px",
                                    "background": C["surface2"], "borderRadius": "8px",
                                    "border": f"1px solid {C['border']}",
                                    "listStyle": "none", "userSelect": "none",
                                },
                            ),
                            html.Div([
                                html.Div([
                                    section_hdr("Import Positions from JSON"),
                                    html.Div("Paste Moomoo or generic JSON.", style={
                                        "fontSize": "12px", "color": C["muted"],
                                    }),
                                ], style={"marginBottom": "8px"}),
                                dcc.Textarea(
                                    id="json-import-textarea",
                                    placeholder=(
                                        'Paste JSON array, e.g.\n'
                                        '[{"symbol":"BKU","quantity":50,"average_cost":30.10}]'
                                    ),
                                    style={
                                        "width": "100%", "height": "120px",
                                        "fontFamily": "monospace", "fontSize": "12px",
                                        "background": C["bg"], "color": C["text"],
                                        "border": f"1px solid {C['border']}",
                                        "borderRadius": "6px", "padding": "8px",
                                        "resize": "vertical", "boxSizing": "border-box",
                                    },
                                ),
                                html.Div([
                                    _btn("Preview", "btn-json-preview", primary=True),
                                    _btn("Clear",   "btn-json-clear"),
                                ], style={"display": "flex", "marginTop": "8px"}),
                                html.Div(id="json-import-status",
                                         style={"fontSize": "12px", "marginTop": "6px",
                                                "minHeight": "18px"}),
                                dcc.Loading(html.Div(id="json-import-preview"), type="dot"),
                            ], style={
                                "paddingTop": "12px",
                                "background": C["surface"],
                                "border": f"1px solid {C['border']}",
                                "borderRadius": "10px",
                                "padding": "16px",
                            }),
                        ], style=_MB, open=True),
                    ],
                ),

                # ── Selector tab ─────────────────────────────────────────
                dcc.Tab(
                    label="Research",
                    value="selector",
                    style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                    children=[
                        # ── a. Morning Status banner ───────────────────────
                        html.Div(id="morning-status-banner",
                                 style={"marginBottom": "0.75rem"}),

                        # ── b. Buy Decision — ticker lookup (PRIMARY) ──────
                        build_ticker_lookup_section(),

                        # ── c. Warren AI Morning Scan (regime, trades, chips)
                        html.Div(id="warren-panel", style=_MB),

                        # ── f. Portfolio Thesis Monitor ────────────────────
                        html.Div(id="portfolio-thesis-monitor", style=_MB),

                        # ── g. Macro Regime ────────────────────────────────
                        html.Div(id="macro-regime-sel", style=_MB),

                        # ── h. Sector Rotation (row-selectable) ───────────
                        html.Div([
                            html.Div(id="sector-rotation-sel"),
                        ], style=_MB),

                        # ── i. Top 4 sectors to research ──────────────────
                        html.Div(id="top4-cards", style=_MB),

                        # ── Suggested tickers (watchlist candidates) ───────
                        dcc.Loading(
                            html.Div(id="suggested-tickers"),
                            type="circle",
                        ),
                    ],
                ),

                # ── History tab — Warren AI local archive ──────────────────
                dcc.Tab(
                    label="History",
                    value="history",
                    style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                    children=[
                        html.Div([
                            dcc.Dropdown(
                                id="history-date-select",
                                options=[],
                                value=None,
                                clearable=False,
                                placeholder="Select a saved scan date",
                                style={
                                    "maxWidth": "520px",
                                    "fontSize": "13px",
                                    "marginBottom": "12px",
                                },
                            ),
                            html.Div(id="history-tab-body"),
                        ]),
                    ],
                ),
            ],
        ),

        # Modal (position: fixed — always accessible)
        build_modal(),
    ],
)

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

# ── 1. Dashboard display ──────────────────────────────────────────────────

@app.callback(
    # ── New cockpit sections ───────────────────────────────────────────────
    Output("daily-decision-dash",      "children"),
    Output("system-health-ribbon",     "children"),
    Output("leadership-gap-dash",      "children"),
    Output("portfolio-action-dash",    "children"),
    Output("new-ideas-dash",           "children"),
    Output("theme-radar-dash",          "children"),
    Output("theme-score-summary-dash", "children"),
    Output("theme-exposure-dash",      "children"),
    Output("fw-summary-dash",          "children"),
    Output("macro-badge-dash",         "children"),
    # ── Existing sections (IDs unchanged) ────────────────────────────────
    Output("macro-regime-dash",        "children"),
    Output("morning-scan-dash",        "children"),
    Output("opportunity-cost-dash",    "children"),
    Output("verdict-banner",           "children"),
    Output("metric-row",               "children"),
    Output("sector-rotation-dash",     "children"),
    Output("capital-breakdown-section","children"),
    Output("mag7-exposure-dash",       "children"),
    Output("exposure-section",         "children"),
    Output("alerts-panel",             "children"),
    Output("tbl-portfolio",            "data"),
    Output("tbl-watchlist",            "data"),
    Output("last-updated",             "children"),
    Output("post-alert-data-store",    "data"),
    Input("version", "data"),
)
def update_dashboard(_version):
    active, watchlist, sector_statuses, ts = run_analysis()
    regime     = mr.get_macro_regime()
    sector_map = {t: s.quadrant for t, s in sector_statuses.items()}

    # Warren + Portfolio Monitor (shared across multiple sections)
    w  = wl.get_latest_warren()
    pm = wl.get_latest_portfolio_monitor()

    active_tickers    = {getattr(p, "ticker", None) for p in active    if getattr(p, "ticker", None)}
    watchlist_tickers = {getattr(p, "ticker", None) for p in watchlist if getattr(p, "ticker", None)}

    # QQQ 5-day for position action card market context
    qqq_5d: "Optional[float]" = None
    try:
        occ_rows_cb = ms.get_opportunity_cost_rows(active)
        qqq_row_cb  = next((r for r in occ_rows_cb if r.label == "QQQ"), None)
        if qqq_row_cb:
            qqq_5d = qqq_row_cb.ret_5d
    except Exception:
        pass

    # Pre-compute post-alert series for the interactive chart
    try:
        fw_rows      = ms.get_forward_walk_results(days=60,
                                                   active_tickers=active_tickers,
                                                   watchlist_tickers=watchlist_tickers)
        pa_series, pa_skipped = ms.build_post_alert_series(fw_rows)
        sec_series, sec_skipped = ms.build_post_alert_sector_series(fw_rows)
        post_alert_data = {
            "series": pa_series,
            "skipped": pa_skipped,
            "sector_series": sec_series,
            "sector_skipped": sec_skipped,
        }
    except Exception as _pa_err:
        post_alert_data = {
            "series": [], "skipped": 0,
            "sector_series": [], "sector_skipped": 0,
            "error": str(_pa_err),
        }

    return (
        # New cockpit sections
        build_daily_decision_summary(active, w, pm, sector_statuses,
                                     active_tickers, watchlist_tickers),
        build_system_health_ribbon(w, pm, sector_statuses),
        build_leadership_gap_card(active, sector_statuses),
        build_portfolio_action_card(active, qqq_5d),
        build_new_ideas_card(w, active_tickers, watchlist_tickers),
        build_theme_radar_card(compact=True),
        build_weekly_theme_score_summary(compact=True),
        build_portfolio_theme_exposure_card(active, compact=True),
        build_fw_summary_card(active_tickers, watchlist_tickers),
        build_macro_badge(regime),
        # Existing sections
        build_macro_regime_section(regime),
        build_morning_scan_section(active, watchlist),
        build_opportunity_cost_section(active),
        card(build_verdict(active, ts), pad="14px 18px"),
        build_metrics(active, watchlist),
        build_sector_rotation_section(
            sector_statuses, table_id="tbl-sector-rotation-dash",
            selectable=False,
        ),
        build_capital_breakdown_section(active),
        build_mag7_exposure_card(active),
        build_exposure_section(active, sector_map),
        build_alerts_panel(active),
        _portfolio_rows(active),
        _watchlist_rows(watchlist, sector_statuses),
        f"Last refreshed {ts.strftime('%H:%M:%S')}",
        post_alert_data,
    )


# ── 2. Selector display (board + top4 + default sector) ───────────────────

@app.callback(
    Output("macro-regime-sel",    "children"),
    Output("sector-rotation-sel", "children"),
    Output("top4-cards",          "children"),
    Output("selected-sector",     "data"),
    Input("version", "data"),
)
def update_selector(_version):
    sector_statuses = get_sector_statuses()
    all_positions   = db.get_positions()
    regime          = mr.get_macro_regime()

    # Compute board rows for top4 cards
    try:
        board_rows = sel.analyze_sector_board(all_positions, sector_statuses)
    except Exception:
        board_rows = []

    default_etf = _best_selector_sector(sector_statuses, all_positions)

    # Pre-select the default row in the DataTable
    rot_rows = _sector_rotation_rows(sector_statuses)
    tickers  = [r["Ticker"] for r in rot_rows]
    default_idx = (tickers.index(default_etf) if default_etf in tickers else 0)

    sector_section = build_sector_rotation_section(
        sector_statuses,
        table_id="tbl-sector-rotation-sel",
        selectable=True,
        selected_rows=[default_idx],
    )

    return (
        build_macro_regime_section(regime),
        sector_section,
        build_top4_sector_cards(sector_statuses, all_positions, board_rows, wl.get_latest_warren()),
        default_etf,
    )


# ── 2b. Post Alert Performance chart ─────────────────────────────────────

# Colour palette for individual ticker traces
_PA_STATUS_COLORS = {
    "Already owned": "#0c447c",   # blue
    "Watchlist":     "#7c6d0c",   # amber/gold
    "New":           "#3b6d11",   # green
    "Unknown":       "#6e6d68",   # grey
    "Sector":        "#5c4d8a",   # sector ETF traces
}
_PA_BENCHMARK_COLORS = {
    "MAG7": "#a32d2d",
    "QQQ":  "#6a3d9a",
    "XLK":  "#1f78b4",
    "SPY":  "#ff7f00",
}

# Distinct colors for Post Alert — Sectors (ETF) view (one hue per SPDR sector ETF)
_PA_SECTOR_LINE_COLORS: dict[str, str] = {
    "XLK":  "#1565c0",  # Technology
    "XLF":  "#ef6c00",  # Financials
    "XLE":  "#2e7d32",  # Energy
    "XLI":  "#c62828",  # Industrials
    "XLY":  "#7b1fa2",  # Consumer Discr.
    "XLP":  "#5d4037",  # Consumer Staples
    "XLU":  "#00838f",  # Utilities
    "XLV":  "#00695c",  # Health Care
    "XLRE": "#ad1457",  # Real Estate
    "XLB":  "#37474f",  # Materials
    "XLC":  "#6a1b9a",  # Communications
}
_PA_SECTOR_LINE_FALLBACK = ["#1976d2", "#d84315", "#388e3c", "#6d4c41", "#4527a0"]


def _pa_sector_etf_line_color(etf: str | None) -> str:
    u = (etf or "").strip().upper()
    if u in _PA_SECTOR_LINE_COLORS:
        return _PA_SECTOR_LINE_COLORS[u]
    h = sum(ord(c) for c in u) if u else 0
    return _PA_SECTOR_LINE_FALLBACK[h % len(_PA_SECTOR_LINE_FALLBACK)]


def _pa_sector_trace_label(
    etf: str | None,
    scan_date: str | None,
    dedupe_mode: str | None,
) -> str:
    """Legend / hover label: CODE (Full Name) [· scan_date if dedupe None]."""
    u = (etf or "").strip().upper()
    full = sel.SECTOR_NAMES.get(u, "")
    core = f"{u} ({full})" if full else u
    if (dedupe_mode or "") == "none" and scan_date:
        return f"{core} · {scan_date}"
    return core


# Distinct colors for Post Alert — Stocks view (stable hash → palette)
_PA_STOCK_LINE_PALETTE: list[str] = [
    "#1565c0", "#c62828", "#2e7d32", "#6a1b9a", "#ef6c00",
    "#00838f", "#ad1457", "#37474f", "#5d4037", "#4527a0",
    "#1976d2", "#d84315", "#388e3c", "#7b1fa2", "#00695c",
    "#fbc02d", "#455a64", "#558b2f", "#e65100", "#283593",
    "#827717", "#4e342e",
]


def _pa_stock_line_color(ticker: str | None) -> str:
    u = (ticker or "").strip().upper()
    if not u:
        return _PA_STATUS_COLORS["Unknown"]
    h = sum((i + 1) * ord(c) for i, c in enumerate(u))
    return _PA_STOCK_LINE_PALETTE[h % len(_PA_STOCK_LINE_PALETTE)]


def _pa_pipeline_series(seq: list[dict], dedupe_mode: str) -> list[dict]:
    """Filter order is caller's job; here: same-scan collapse → ticker dedupe."""
    s = list(seq)
    s = ms.collapse_post_alert_series_same_scan(s)
    s = ms.dedupe_post_alert_series_by_ticker(s, dedupe_mode)
    return s


@app.callback(
    Output("pa-chart", "figure"),
    Output("pa-stats", "children"),
    Input("pa-mode",       "value"),
    Input("pa-lines",      "value"),
    Input("pa-filter",     "value"),
    Input("pa-dedupe",     "value"),
    Input("pa-segment",    "value"),
    Input("pa-benchmarks", "value"),
    Input("post-alert-data-store", "data"),
)
def update_post_alert_chart(mode, lines, filter_val, dedupe_mode, segment, benchmarks, store_data):
    _dim = {"fontSize": "11px", "color": C["dim"], "lineHeight": "1.45"}
    _dim2 = {"fontSize": "10px", "color": C["dim"], "lineHeight": "1.45", "marginTop": "4px"}

    empty_fig = go.Figure().update_layout(
        paper_bgcolor="#fafaf7", plot_bgcolor="#f4f3ed",
        margin=dict(l=10, r=10, t=10, b=10),
        font=dict(color="#6e6d68", size=11),
        xaxis=dict(showgrid=False), yaxis=dict(showgrid=False),
    )

    if not store_data:
        return empty_fig, html.Div("Loading…", style=_dim)

    segment = segment or "stocks"
    if segment == "sectors":
        raw_all = list(store_data.get("sector_series") or [])
        skipped = int(store_data.get("sector_skipped", 0) or 0)
        if not raw_all:
            msg = store_data.get("error") or (
                "No sector ETF paths — suggestions may lack sector tags in Warren output, "
                "or prices did not load. Use Stocks view or refresh after a successful scan."
            )
            return empty_fig, html.Div(msg, style=_dim)
    else:
        raw_all = list(store_data.get("series") or [])
        skipped = int(store_data.get("skipped", 0) or 0)
        if not raw_all:
            note = store_data.get("error", "No alert path data yet — refresh to load.") \
                   if store_data else "Loading…"
            return empty_fig, html.Div(note, style=_dim)

    # ── Step 1: filter ───────────────────────────────────────────────────
    filtered = list(raw_all)
    if segment == "sectors":
        # Synthetic XLK (Technology) reference paths always pass filters
        _pa_inc = "post_alert_always_include"
        if filter_val == "top3":
            filtered = [s for s in filtered if s.get("any_top3") or s.get(_pa_inc)]
        elif filter_val == "new":
            filtered = [s for s in filtered if s.get("any_new") or s.get(_pa_inc)]
        elif filter_val == "owned":
            filtered = [s for s in filtered if s.get("any_owned") or s.get(_pa_inc)]
    else:
        if filter_val == "top3":
            filtered = [s for s in filtered if s.get("is_top3")]
        elif filter_val == "new":
            filtered = [s for s in filtered if s.get("portfolio_status") == "New"]
        elif filter_val == "owned":
            filtered = [s for s in filtered if s.get("portfolio_status") == "Already owned"]

    if not filtered:
        return empty_fig, html.Div(
            f"No paths match filter '{filter_val}' in {segment} view.",
            style=_dim,
        )

    # Diagnostics on filtered (pre collapse)
    n_raw = len(filtered)
    tickers_u = {((s.get("ticker") or "").strip().upper()) for s in filtered if s.get("ticker")}
    n_tick_u = len(tickers_u)
    pair_groups: dict[tuple[str, str], int] = {}
    for s in filtered:
        tk = (s.get("ticker") or "").strip().upper()
        sd = (s.get("scan_date") or "").strip()
        k = (tk, sd)
        pair_groups[k] = pair_groups.get(k, 0) + 1
    n_pairs = len(pair_groups)
    dup_rows = sum(max(0, c - 1) for c in pair_groups.values())

    collapsed = ms.collapse_post_alert_series_same_scan(filtered)
    n_after_sc = len(collapsed)
    ind_series = _pa_pipeline_series(filtered, dedupe_mode or "latest")
    n_ind = len(ind_series)

    # Company names for stock legend labels (cached via _fetch_ticker_info)
    stock_company: dict[str, str] = {}
    if segment == "stocks":
        for s in ind_series:
            tk = (s.get("ticker") or "").strip().upper()
            if not tk or tk in stock_company:
                continue
            inf = _fetch_ticker_info(tk)
            nm = str(inf.get("company") or "").strip()
            if not nm or nm.upper().replace(".", "") == tk:
                stock_company[tk] = ""
            else:
                if len(nm) > 44:
                    nm = nm[:41].rstrip() + "…"
                stock_company[tk] = nm

    if segment == "sectors":
        dedupe_label = {
            "none": "None",
            "latest": "Latest per sector ETF",
            "earliest": "Earliest per sector ETF",
        }.get(dedupe_mode or "latest", dedupe_mode or "latest")
    else:
        dedupe_label = {
            "none": "None",
            "latest": "Latest per ticker",
            "earliest": "Earliest per ticker",
        }.get(dedupe_mode or "latest", dedupe_mode or "latest")
    mode_label = "Days Since Alert" if mode == "days" else "Calendar Date"
    lines_label = {"all": "All", "avg": "Average", "both": "Both"}.get(lines, lines)
    view_label = "Sectors (ETF)" if segment == "sectors" else "Stocks"

    if dedupe_mode == "none":
        sym_word = "ETF" if segment == "sectors" else "ticker"
        cfg_line = (
            f"View: {view_label} · Showing {n_raw} alert instance(s) across {n_tick_u} {sym_word}(s) · "
            f"{n_pairs} ({sym_word}, date) pair(s)"
            + (f" · merged {dup_rows} same-day duplicate row(s)" if dup_rows else "")
            + f" · dedupe: {dedupe_label} · mode: {mode_label} · lines: {lines_label}"
        )
    else:
        sym_word = "ETFs" if segment == "sectors" else "tickers"
        cfg_line = (
            f"View: {view_label} · Showing {n_ind} path(s) after dedupe ({dedupe_label}) "
            f"from {n_raw} instance(s) / {n_tick_u} {sym_word}"
            + (f" · merged {dup_rows} same-day duplicate row(s)" if dup_rows else "")
            + f" · mode: {mode_label} · lines: {lines_label}"
        )

    def _trace_color(s: dict) -> str:
        if segment == "sectors":
            return _pa_sector_etf_line_color(s.get("ticker"))
        return _pa_stock_line_color(s.get("ticker"))

    def _ind_label(s: dict) -> str:
        if segment == "sectors":
            return _pa_sector_trace_label(
                s.get("ticker"), s.get("scan_date"), dedupe_mode,
            )
        tk = (s.get("ticker") or "").strip().upper()
        nm = stock_company.get(tk, "")
        core = f"{tk} ({nm})" if nm else tk
        if (dedupe_mode or "latest") == "none":
            return f"{core} · {s.get('scan_date', '')}"
        return core

    fig = go.Figure()
    x_label = "Days since alert (0 = scan date)" if mode == "days" else "Date"
    bm_list = benchmarks or []

    # Benchmark base: same pipeline as individuals for aligned scan dates
    bm_base = _pa_pipeline_series(filtered, dedupe_mode or "latest")

    def _add_avg_traces_day(
        avg_specs: list[tuple[str, list[dict]]],
        strong: bool,
    ) -> list[list] | None:
        """Returns first non-empty avg path for stats."""
        first_path: list[list] | None = None
        for name, sub in avg_specs:
            pl = _pa_pipeline_series(sub, dedupe_mode or "latest")
            ap = ms.build_average_post_alert_path(pl)
            if not ap:
                continue
            if first_path is None:
                first_path = ap
            lw = 3.5 if strong and name.startswith("Average: All") else (3 if strong else 2)
            dash = "solid"
            col = "#1a1a1a"
            if name == "Average: New":
                col = "#3b6d11"
            elif name == "Average: Already owned":
                col = "#0c447c"
            elif name == "Average: Top 3":
                col = "#6a3d9a"
            if not strong and name != "Average: All":
                dash = "dash"
            trace_kw: dict = dict(
                x=[p[0] for p in ap],
                y=[p[1] for p in ap],
                mode="lines+markers" if strong else "lines",
                name=name,
                line=dict(width=lw, color=col, dash=dash),
                hovertemplate=f"<b>{name}</b><br>Day %{{x}}: %{{y:.2f}}%<extra></extra>",
            )
            if strong:
                trace_kw["marker"] = dict(size=4, color=col)
            fig.add_trace(go.Scatter(**trace_kw))
        return first_path

    primary_avg_path: list[list] | None = None

    # ── Calendar: aggregates need day axis — only individuals or placeholder ─
    if mode == "calendar" and lines == "avg":
        fig.add_annotation(
            text="Aggregate averages use a shared day axis — switch to Days Since Alert.",
            xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
            font=dict(size=12, color="#6e6d68"),
        )
        x_label = "Date"
    elif mode == "calendar":
        cal_series = ind_series if lines in ("all", "both") else []
        for s in cal_series:
            cd = s.get("cal_dates", [])
            if not cd:
                continue
            lbl = _ind_label(s)
            if segment == "sectors":
                op = 0.82 if lines == "both" else 0.9
                lw = 1.85 if lines == "both" else 2.0
            else:
                op = 0.78 if lines == "both" else 0.88
                lw = 1.75 if lines == "both" else 1.95
            fig.add_trace(go.Scatter(
                x=[p[0] for p in cd],
                y=[p[1] for p in cd],
                mode="lines",
                name=lbl,
                legendgroup=lbl,
                line=dict(width=lw, color=_trace_color(s)),
                opacity=op,
                hovertemplate=f"<b>{lbl}</b><br>%{{x}}: %{{y:.2f}}%<extra></extra>",
            ))
    elif mode == "days":
        # Individuals: only for "all" or "both"
        if lines in ("all", "both"):
            for s in ind_series:
                dr = s.get("day_returns", [])
                if not dr:
                    continue
                lbl = _ind_label(s)
                if segment == "sectors":
                    op = 0.82 if lines == "both" else 0.9
                    lw = 1.85 if lines == "both" else 2.0
                else:
                    op = 0.78 if lines == "both" else 0.88
                    lw = 1.75 if lines == "both" else 1.95
                fig.add_trace(go.Scatter(
                    x=[p[0] for p in dr],
                    y=[p[1] for p in dr],
                    mode="lines",
                    name=lbl,
                    legendgroup=lbl,
                    line=dict(width=lw, color=_trace_color(s)),
                    opacity=op,
                    hovertemplate=f"<b>{lbl}</b><br>Day %{{x}}: %{{y:.2f}}%<extra></extra>",
                ))

        # Averages: "avg" → multi-band; "both" → primary only (Average: All / scoped)
        if lines == "avg":
            avg_specs: list[tuple[str, list[dict]]] = []
            if filter_val == "all":
                if segment == "sectors":
                    avg_specs = [
                        ("Average: All", list(filtered)),
                        ("Average: New", [x for x in filtered if x.get("any_new")]),
                        ("Average: Already owned", [
                            x for x in filtered if x.get("any_owned")
                        ]),
                    ]
                else:
                    avg_specs = [
                        ("Average: All", list(filtered)),
                        ("Average: New", [x for x in filtered if x.get("portfolio_status") == "New"]),
                        ("Average: Already owned", [
                            x for x in filtered if x.get("portfolio_status") == "Already owned"
                        ]),
                    ]
            elif filter_val == "new":
                avg_specs = [("Average: New", list(filtered))]
            elif filter_val == "owned":
                avg_specs = [("Average: Already owned", list(filtered))]
            elif filter_val == "top3":
                avg_specs = [("Average: Top 3", list(filtered))]
            primary_avg_path = _add_avg_traces_day(avg_specs, strong=True)
            if bm_list:
                for bm in bm_list:
                    bm_path = ms.build_benchmark_avg_path(bm_base, bm)
                    if not bm_path:
                        continue
                    fig.add_trace(go.Scatter(
                        x=[p[0] for p in bm_path],
                        y=[p[1] for p in bm_path],
                        mode="lines",
                        name=bm,
                        line=dict(width=2, dash="dash",
                                  color=_PA_BENCHMARK_COLORS.get(bm, "#999")),
                        hovertemplate=f"<b>{bm}</b><br>Day %{{x}}: %{{y:.2f}}%<extra></extra>",
                    ))
        elif lines == "both":
            if filter_val == "all":
                sub = [("Average: All", list(filtered))]
            elif filter_val == "new":
                sub = [("Average: New", list(filtered))]
            elif filter_val == "owned":
                sub = [("Average: Already owned", list(filtered))]
            else:
                sub = [("Average: Top 3", list(filtered))]
            primary_avg_path = _add_avg_traces_day(sub, strong=True)
            if bm_list:
                for bm in bm_list:
                    bm_path = ms.build_benchmark_avg_path(bm_base, bm)
                    if not bm_path:
                        continue
                    fig.add_trace(go.Scatter(
                        x=[p[0] for p in bm_path],
                        y=[p[1] for p in bm_path],
                        mode="lines",
                        name=bm,
                        line=dict(width=2, dash="dash",
                                  color=_PA_BENCHMARK_COLORS.get(bm, "#999")),
                        hovertemplate=f"<b>{bm}</b><br>Day %{{x}}: %{{y:.2f}}%<extra></extra>",
                    ))

    fig.add_hline(y=0, line_color="rgba(0,0,0,0.25)", line_width=1, line_dash="dot")

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#fafaf7",
        margin=dict(l=50, r=20, t=10, b=50),
        yaxis=dict(
            title="Return since alert (%)",
            zeroline=False,
            gridcolor="rgba(0,0,0,0.06)",
            ticksuffix="%",
        ),
        xaxis=dict(title=x_label, gridcolor="rgba(0,0,0,0.06)"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            font=dict(size=10),
        ),
        hovermode="x unified",
        font=dict(family="system-ui,-apple-system,'Segoe UI',sans-serif",
                  size=11, color="#1a1a1a"),
    )

    # Stats line 2: metrics (use primary avg path when available)
    parts2: list[str] = []
    if skipped:
        parts2.append(f"{skipped} skipped (no price)")
    ref_avg = primary_avg_path
    if ref_avg is None and mode == "days" and lines in ("all", "both"):
        ref_avg = ms.build_average_post_alert_path(ind_series)
    if ref_avg and mode == "days":
        for day_check in [3, 5, 10]:
            val = next((p[1] for p in ref_avg if p[0] == day_check), None)
            if val is not None:
                sign = "+" if val >= 0 else ""
                parts2.append(f"Avg Day {day_check}: {sign}{val:.2f}%")
        if bm_list and lines in ("avg", "both") and ref_avg:
            for bm in bm_list:
                bm_path = ms.build_benchmark_avg_path(bm_base, bm)
                for day_check in [5, 10]:
                    bm_val = next((p[1] for p in bm_path if p[0] == day_check), None)
                    avg_val = next((p[1] for p in ref_avg if p[0] == day_check), None)
                    if bm_val is not None and avg_val is not None:
                        diff = avg_val - bm_val
                        sign = "+" if diff >= 0 else ""
                        parts2.append(
                            f"Primary avg vs {bm} Day {day_check}: {sign}{diff:.2f}%"
                        )

    metrics_line = "  ·  ".join(parts2) if parts2 else ""

    children = html.Div([
        html.Div(cfg_line, style=_dim),
        html.Div(metrics_line, style=_dim2) if metrics_line else html.Div(),
    ])
    return fig, children


# ── 2b. Sector ETF Allocation Check ─────────────────────────────────────

def _fmt_ret(val, decimals: int = 1) -> str:
    """Format a return percent with sign and colour class; returns plain string."""
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.{decimals}f}%"


def _ret_color(val) -> str:
    """Return a CSS color for a return value."""
    if val is None:
        return C["dim"]
    if val > 0:
        return C["green_fg"]
    if val < 0:
        return C["red_fg"]
    return C["text"]


def _build_etf_allocation_check_card(check: dict) -> html.Div:
    """Render the Sector ETF Allocation Check card from a compute_etf_allocation_check result."""
    error = check.get("error")
    decision   = check.get("decision") or {}
    allocation = check.get("allocation") or {}
    vehicle    = decision.get("vehicle", "stock_alerts")
    vehicle_label = decision.get("vehicle_label", "Stock Alerts")
    owned_signal  = decision.get("owned_signal", "ok")
    new_signal    = decision.get("new_signal", "ok")
    rules_fired   = decision.get("rules_fired") or []

    primary_etf  = check.get("primary_etf") or "ETF"
    day_win      = check.get("day_window") or 10
    day_short    = check.get("day_window_short") or 5

    primary_ret  = check.get("primary_ret")
    new_ret      = check.get("new_ret")
    owned_ret    = check.get("owned_ret")
    etf_ret      = check.get("etf_ret")
    spy_ret      = check.get("spy_ret")
    qqq_ret      = check.get("qqq_ret")
    mag7_ret     = check.get("mag7_ret")
    diff         = check.get("diff_stock_vs_etf")
    n_new        = check.get("n_new", 0)
    n_owned      = check.get("n_owned", 0)

    # --- Vehicle badge colour ---
    badge_bg = {
        "sector_etf":   C["blue_bg"],
        "stock_alerts": C["green_bg"],
        "wait":         C["amber_bg"],
        "spy_qqq":      C["neutral_bg"],
    }.get(vehicle, C["neutral_bg"])
    badge_fg = {
        "sector_etf":   C["blue_fg"],
        "stock_alerts": C["green_fg"],
        "wait":         C["amber_fg"],
        "spy_qqq":      C["neutral_fg"],
    }.get(vehicle, C["neutral_fg"])

    _dim    = {"fontSize": "11px", "color": C["dim"]}
    _label  = {"fontSize": "11px", "fontWeight": 600, "color": C["muted"],
                "textTransform": "uppercase", "letterSpacing": "0.04em"}
    _val    = {"fontSize": "13px", "fontWeight": 700}
    _cell   = {"padding": "6px 12px 6px 0", "minWidth": "90px"}

    # Header
    header = html.Div([
        html.Span("Sector ETF Allocation Check",
                  style={"fontSize": "13px", "fontWeight": 700,
                         "textTransform": "uppercase", "letterSpacing": "0.05em",
                         "color": C["text"]}),
        html.Span(
            f"  Decision support only · Day {day_short}/{day_win} averages "
            f"· {check.get('n_stocks', 0)} alerts",
            style={"fontSize": "11px", "color": C["dim"], "fontStyle": "italic"},
        ),
    ], style={"marginBottom": "12px"})

    # Early-exit on error / no data
    if error:
        return html.Div([
            header,
            html.Div(f"Allocation check unavailable: {error}",
                     style={"fontSize": "12px", "color": C["dim"], "fontStyle": "italic"}),
        ], style={"background": C["surface"], "borderRadius": "12px",
                  "padding": "18px", "border": f"1px solid {C['border']}"})

    # --- Metrics table ---
    def metric_col(label, val, note=""):
        return html.Div([
            html.Div(label, style=_label),
            html.Div(_fmt_ret(val), style={**_val, "color": _ret_color(val)}),
            html.Div(note, style={**_dim, "marginTop": "1px"}) if note else html.Div(),
        ], style={**_cell})

    # Owned / New badges
    owned_badge = ""
    if owned_signal == "avoid_adding":
        owned_badge = " ⚠ avoid adding"
    new_badge = ""
    if new_signal == "prefer_new":
        new_badge = " ↑ prefer"

    diff_note = ""
    if diff is not None:
        sign = "+" if diff >= 0 else ""
        diff_note = f"stocks {sign}{diff:.1f}% vs ETF"

    metrics_row = html.Div([
        metric_col("Primary avg",          primary_ret,
                   f"{check.get('n_stocks',0)} alerts"),
        metric_col(f"New alerts",          new_ret,
                   f"{n_new} alerts{new_badge}"),
        metric_col("Owned alerts",         owned_ret,
                   f"{n_owned} alerts{owned_badge}"),
        metric_col(f"Sector ETF ({primary_etf})", etf_ret,
                   diff_note),
        metric_col("SPY",   spy_ret),
        metric_col("QQQ",   qqq_ret),
        metric_col("MAG7",  mag7_ret),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "12px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "10px"})

    # --- Vehicle badge ---
    vehicle_section = html.Div([
        html.Span("Suggested vehicle: ",
                  style={"fontSize": "12px", "color": C["muted"],
                         "fontWeight": 600, "marginRight": "8px"}),
        html.Span(vehicle_label,
                  style={"background": badge_bg, "color": badge_fg,
                         "padding": "3px 12px", "borderRadius": "20px",
                         "fontSize": "12px", "fontWeight": 700}),
    ], style={"marginBottom": "8px", "display": "flex", "alignItems": "center"})

    # Rules fired
    rules_section = html.Div(
        [html.Div(f"• {r}", style=_dim) for r in rules_fired] or
        [html.Div("No rules fired — data may be insufficient.", style=_dim)],
        style={"marginBottom": "10px"},
    )

    # --- Allocation splits ---
    def alloc_profile(label, weights: dict, highlight: bool = False):
        etf_w     = weights.get("etf", 0)
        stocks_w  = weights.get("stocks", 0)
        cash_w    = weights.get("cash", 0)
        bg = C["surface2"] if highlight else "transparent"
        return html.Div([
            html.Div(label, style={**_label, "marginBottom": "3px"}),
            html.Div([
                html.Div([
                    html.Span("ETF ", style=_dim),
                    html.Span(f"{etf_w}%",
                              style={"fontWeight": 700, "color": C["blue_fg"],
                                     "fontSize": "13px"}),
                ], style={"marginRight": "10px"}),
                html.Div([
                    html.Span("Stocks ", style=_dim),
                    html.Span(f"{stocks_w}%",
                              style={"fontWeight": 700, "color": C["green_fg"],
                                     "fontSize": "13px"}),
                ], style={"marginRight": "10px"}),
                *(
                    [html.Div([
                        html.Span("Cash ", style=_dim),
                        html.Span(f"{cash_w}%",
                                  style={"fontWeight": 700, "color": C["amber_fg"],
                                         "fontSize": "13px"}),
                    ])]
                    if cash_w > 0 else []
                ),
            ], style={"display": "flex"}),
        ], style={"background": bg, "borderRadius": "8px", "padding": "6px 10px",
                  "marginRight": "16px"})

    alloc_row = html.Div([
        alloc_profile("Conservative",
                      allocation.get("conservative") or {}, highlight=False),
        alloc_profile("Balanced",
                      allocation.get("balanced")     or {}, highlight=True),
        alloc_profile("Aggressive",
                      allocation.get("aggressive")   or {}, highlight=False),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "6px"})

    alloc_note = html.Div(
        allocation.get("note", ""),
        style={**_dim, "fontStyle": "italic", "marginTop": "4px"},
    )

    disclaimer = html.Div(
        "Decision support only — no trades are placed automatically.",
        style={"fontSize": "10px", "color": C["dim"], "fontStyle": "italic",
               "borderTop": f"1px solid {C['border']}", "paddingTop": "6px",
               "marginTop": "8px"},
    )

    return html.Div([
        header,
        metrics_row,
        vehicle_section,
        rules_section,
        alloc_row,
        alloc_note,
        disclaimer,
    ], style={"background": C["surface"], "borderRadius": "12px",
              "padding": "18px", "border": f"1px solid {C['border']}"})


@app.callback(
    Output("etf-allocation-check-outer", "children"),
    Input("post-alert-data-store", "data"),
)
def update_etf_allocation_check(pa_data):
    """Render the Sector ETF Allocation Check card from the PA data store."""
    if not pa_data:
        return html.Div()
    try:
        check = ms.compute_etf_allocation_check(pa_data)
    except Exception as exc:
        check = {
            "error": str(exc),
            "decision": {"vehicle": "wait", "vehicle_label": "Error",
                         "owned_signal": "ok", "new_signal": "ok", "rules_fired": []},
            "allocation": {},
        }
    return _build_etf_allocation_check_card(check)


def _readiness_badge(readiness: str) -> tuple[str, str]:
    """Return (background, foreground) colors for readiness badge."""
    if readiness == "ready":
        return C["green_bg"], C["green_fg"]
    if readiness == "ready_with_warnings":
        return C["amber_bg"], C["amber_fg"]
    return C["red_bg"], C["red_fg"]


def _build_atlas_audio_readiness_card(payload: dict) -> html.Div:
    """Render Atlas Morning Audio Source Readiness card."""
    readiness = payload.get("readiness") or "not_ready"
    warnings  = payload.get("warnings") or []
    fresh     = payload.get("freshness") or {}
    cov       = payload.get("sector_etf_coverage") or {}
    sector    = payload.get("sector_strength_summary") or {}
    discipline = payload.get("portfolio_discipline_summary") or {}
    tech      = payload.get("technology_subsector_summary") or {}
    alloc     = payload.get("sector_etf_allocation_check") or {}
    bullets   = payload.get("suggested_audio_bullets") or []

    badge_bg, badge_fg = _readiness_badge(readiness)
    label = readiness.replace("_", " ").title()

    _dim   = {"fontSize": "11px", "color": C["dim"]}
    _label = {"fontSize": "11px", "fontWeight": 600, "color": C["muted"],
              "textTransform": "uppercase", "letterSpacing": "0.04em"}
    _val   = {"fontSize": "13px", "fontWeight": 700}

    def kv(label_text, val, color=None):
        return html.Div([
            html.Div(label_text, style=_label),
            html.Div(val, style={**_val, **({"color": color} if color else {})}),
        ], style={"padding": "4px 16px 4px 0", "minWidth": "120px"})

    header = html.Div([
        html.Span("Atlas Morning Audio Source Readiness",
                  style={"fontSize": "13px", "fontWeight": 700,
                         "textTransform": "uppercase", "letterSpacing": "0.05em",
                         "color": C["text"]}),
        html.Span("  Audio source contract only — no audio generated yet",
                  style={"fontSize": "11px", "color": C["dim"], "fontStyle": "italic"}),
    ], style={"marginBottom": "10px"})

    readiness_row = html.Div([
        html.Span("Overall: ", style={"fontSize": "12px", "color": C["muted"], "fontWeight": 600}),
        html.Span(label, style={"background": badge_bg, "color": badge_fg,
                                "padding": "3px 12px", "borderRadius": "20px",
                                "fontSize": "12px", "fontWeight": 700}),
    ], style={"marginBottom": "12px"})

    freshness_row = html.Div([
        kv("Warren run", fresh.get("latest_warren_run_date") or "—"),
        kv("Warren freshness", fresh.get("latest_warren_freshness") or "—"),
        kv("Latest scan", fresh.get("latest_scan_date") or "—"),
        kv("Post-alert date", fresh.get("post_alert_latest_date") or fresh.get("latest_post_alert_performance_date") or "—"),
        kv("PA lag (days)", str(fresh.get("lag_days") if fresh.get("lag_days") is not None else "—")),
        kv("PA lag status", (fresh.get("lag_status") or "—").replace("_", " "),
           C["green_fg"] if fresh.get("lag_status") == "expected_lag"
           else (C["amber_fg"] if fresh.get("lag_status") == "stale_lag" else C["text"])),
        kv("Source stale", "Yes" if fresh.get("source_is_stale") else "No",
           C["amber_fg"] if fresh.get("source_is_stale") else C["green_fg"]),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    portfolio_row = html.Div([
        kv("Portfolio source", payload.get("portfolio_source") or "—"),
        kv("Positions", str(payload.get("portfolio_position_count") or 0)),
        kv("Export exists", "Yes" if payload.get("portfolio_export_exists") else "No",
           C["green_fg"] if payload.get("portfolio_export_exists") else C["amber_fg"]),
        kv("Portfolio ready", "Yes" if payload.get("portfolio_ready") else "No",
           C["green_fg"] if payload.get("portfolio_ready") else C["amber_fg"]),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    owned_perf = payload.get("owned_position_performance") or {}
    notes = payload.get("notes") or []
    perf_ready = owned_perf.get("data_status") in ("ok", "partial") and (
        owned_perf.get("positions_with_price_data", 0) > 0
    )
    leaders = owned_perf.get("leaders") or []
    laggards = owned_perf.get("laggards") or []
    avoid = owned_perf.get("avoid_adding_candidates") or []
    leader_str = ", ".join(
        f"{x.get('ticker')} ({_fmt_ret(x.get('return_pct'))})" for x in leaders[:3]
    ) or "—"
    laggard_str = ", ".join(
        f"{x.get('ticker')} ({_fmt_ret(x.get('return_pct'))})" for x in laggards[:3]
    ) or "—"
    avoid_str = ", ".join(x.get("ticker", "") for x in avoid[:3]) or "None"
    blocking = bool(warnings)
    warn_status = "Blocking" if blocking else ("Informational only" if notes else "Clear")

    owned_row = html.Div([
        kv("Owned perf ready", "Yes" if perf_ready else "No",
           C["green_fg"] if perf_ready else C["amber_fg"]),
        kv("With price data",
           f"{owned_perf.get('positions_with_price_data', 0)}/{owned_perf.get('positions_total', 0)}"),
        kv("Owned avg", _fmt_ret(owned_perf.get("owned_average_return")),
           _ret_color(owned_perf.get("owned_average_return"))),
        kv("Owned leaders", leader_str),
        kv("Owned laggards", laggard_str, C["red_fg"] if laggards else C["text"]),
        kv("Avoid adding", avoid_str, C["amber_fg"] if avoid else C["text"]),
        kv("Warning status", warn_status,
           C["red_fg"] if blocking else (C["amber_fg"] if notes else C["green_fg"])),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    cov_pct = cov.get("coverage_percent", 0)
    cov_color = C["green_fg"] if cov_pct >= 60 else (C["amber_fg"] if cov_pct >= 30 else C["red_fg"])
    coverage_row = html.Div([
        kv("Alert rows", str(cov.get("total_alert_rows", 0))),
        kv("Existing ETF", str(cov.get("rows_with_existing_sector_etf", 0))),
        kv("Inferred", str(cov.get("rows_inferred_by_mapping", 0))),
        kv("Unknown", str(cov.get("rows_still_unknown", 0)),
           C["red_fg"] if cov.get("rows_still_unknown", 0) else C["text"]),
        kv("Coverage", f"{cov_pct:.1f}%", cov_color),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    etf_adv = sector.get("etf_advantage")
    sector_row = html.Div([
        kv("Primary ETF", f"{sector.get('primary_sector_etf') or '—'} ({sector.get('primary_sector_name') or ''})"),
        kv("ETF return", _fmt_ret(sector.get("etf_return")), _ret_color(sector.get("etf_return"))),
        kv("Stock basket", _fmt_ret(sector.get("stock_basket_return")), _ret_color(sector.get("stock_basket_return"))),
        kv("ETF advantage", _fmt_ret(etf_adv) if etf_adv is not None else "—",
           _ret_color(etf_adv)),
        kv("Vehicle", sector.get("suggested_vehicle") or "—"),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    owned_sig = discipline.get("owned_signal", "ok")
    new_sig   = discipline.get("new_signal", "ok")
    discipline_row = html.Div([
        kv("Owned avg", _fmt_ret(discipline.get("owned_average_return")),
           _ret_color(discipline.get("owned_average_return"))),
        kv("New avg", _fmt_ret(discipline.get("new_average_return")),
           _ret_color(discipline.get("new_average_return"))),
        kv("Owned signal", owned_sig,
           C["amber_fg"] if owned_sig != "ok" else C["green_fg"]),
        kv("New signal", new_sig,
           C["green_fg"] if new_sig == "prefer_new" else C["text"]),
        kv("Avoid adding", str(discipline.get("avoid_adding_count", 0))),
        kv("Research", str(discipline.get("research_count", 0))),
        kv("Trim/Hold", str(discipline.get("trim_or_hold_count", 0))),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    sub_bm = tech.get("subsector_benchmark_returns") or {}
    top_leader = (tech.get("subsector_leaders") or [{}])[0] if tech.get("subsector_leaders") else {}
    tech_row = html.Div([
        kv("Tech alert rows", str(tech.get("technology_row_count", 0))),
        kv("Dominant sub-ETF", tech.get("dominant_subsector_etf") or "—"),
        kv("Subsector data", tech.get("subsector_data_status") or "—"),
        kv("Subsector leader",
           f"{top_leader.get('etf', '—')} {_fmt_ret(top_leader.get('return_pct'))}"
           if top_leader else "—"),
        kv("Benchmark ETFs loaded", str(sum(1 for v in sub_bm.values() if v is not None))),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    contract_row = html.Div([
        kv("Contract", payload.get("contract_version") or "v1"),
        kv("Generated", (payload.get("generated_at") or "—")[:19]),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px"})

    status_note = html.Div(
        payload.get("source_status_summary") or "",
        style={"fontSize": "11px", "color": C["muted"], "marginBottom": "10px",
               "fontStyle": "italic"},
    )
    warn_labels = {
        "no_current_scan": "No current Warren scan",
        "warren_ai_stale": "Warren AI data is stale",
        "sector_etf_coverage_too_low": "Sector ETF coverage too low",
        "etf_benchmark_missing": "ETF benchmark return missing",
        "owned_position_performance_missing": "Owned position price paths unavailable",
        "post_alert_data_missing": "Post alert performance data missing",
        "post_alert_lag_stale": "Post alert performance is stale (>5 trading days)",
        "portfolio_export_missing": "Portfolio export file missing",
    }
    note_labels = {
        "owned_positions_not_in_alert_paths": "Owned holdings tracked separately — not in Warren alert paths",
    }
    warnings_section = html.Div([
        html.Div("Blocking warnings", style={**_label, "marginBottom": "4px"}),
        html.Ul(
            [html.Li(warn_labels.get(w, w), style={**_dim, "marginBottom": "2px"}) for w in warnings]
            or [html.Li("None — no blocking warnings.", style={**_dim, "fontStyle": "italic", "color": C["green_fg"]})],
            style={"margin": "0", "paddingLeft": "18px"},
        ),
    ], style={"marginBottom": "6px"})
    notes_section = html.Div([
        html.Div("Informational notes", style={**_label, "marginBottom": "4px"}),
        html.Ul(
            [html.Li(note_labels.get(n, n), style={**_dim, "marginBottom": "2px"}) for n in notes]
            or [html.Li("None", style={**_dim, "fontStyle": "italic"})],
            style={"margin": "0", "paddingLeft": "18px"},
        ),
    ], style={"marginBottom": "10px"})

    bullets_section = html.Div([
        html.Div("Suggested audio bullets (preview)", style={**_label, "marginBottom": "4px"}),
        html.Ul(
            [html.Li(b, style={**_dim, "marginBottom": "3px"}) for b in bullets],
            style={"margin": "0", "paddingLeft": "18px"},
        ),
    ], style={"marginBottom": "6px"})

    api_note = html.Div(
        "API: GET /api/atlas/morning-audio-source",
        style={"fontSize": "10px", "color": C["dim"], "fontStyle": "italic",
               "borderTop": f"1px solid {C['border']}", "paddingTop": "6px"},
    )

    return html.Div([
        header, readiness_row, freshness_row, portfolio_row, owned_row, coverage_row,
        sector_row, discipline_row, tech_row, contract_row, status_note,
        warnings_section, notes_section, bullets_section, api_note,
    ], style={"background": C["surface"], "borderRadius": "12px",
              "padding": "18px", "border": f"1px solid {C['border']}"})


@app.callback(
    Output("atlas-audio-readiness-outer", "children"),
    Input("post-alert-data-store", "data"),
    Input("version", "data"),
)
def update_atlas_audio_readiness(pa_data, _version):
    """Render Atlas Morning Audio Source Readiness from PA store + live portfolio."""
    active, watchlist, _, _ = run_analysis()
    active_tickers = {getattr(p, "ticker", None) for p in active if getattr(p, "ticker", None)}
    watchlist_tickers = {getattr(p, "ticker", None) for p in watchlist if getattr(p, "ticker", None)}
    active_pos = db.get_positions(db.ACTIVE_STATUSES)
    try:
        payload = amas.build_atlas_morning_audio_source(
            pa_data,
            active_tickers=active_tickers,
            watchlist_tickers=watchlist_tickers,
            active_positions=active_pos,
        )
    except Exception as exc:
        payload = {
            "readiness": "not_ready",
            "warnings": ["post_alert_data_missing"],
            "notes": [],
            "freshness": {},
            "sector_etf_coverage": {},
            "sector_strength_summary": {},
            "portfolio_discipline_summary": {},
            "technology_subsector_summary": {},
            "owned_position_performance": {},
            "sector_etf_allocation_check": {},
            "suggested_audio_bullets": [f"Readiness check failed: {exc}"],
        }
    return _build_atlas_audio_readiness_card(payload)


def _theme_phase_color(phase: str) -> str:
    if phase == "confirmed":
        return C["green_fg"]
    if phase == "early":
        return C["amber_fg"]
    if phase in ("crowded", "fading"):
        return C["red_fg"]
    return C["text"]


def _build_theme_catalyst_radar_card(payload: dict) -> html.Div:
    """Render Theme Catalyst Radar card."""
    readiness = payload.get("readiness") or "not_ready"
    warnings = payload.get("warnings") or []
    catalysts = payload.get("catalysts") or []
    missed = payload.get("missed_theme_candidates") or []
    bullets = payload.get("suggested_audio_bullets") or []

    warren_status = payload.get("warren_theme_source_status") or "missing"
    warren_stale = payload.get("warren_theme_stale")
    manual_count = payload.get("manual_catalyst_count", 0)
    warren_count = payload.get("warren_theme_catalyst_count", 0)
    merged_count = payload.get("merged_catalyst_count", len(catalysts))
    warren_new = payload.get("warren_discovered_themes") or []
    warren_gen = payload.get("warren_theme_generated_at") or "—"

    manual_cats = [c for c in catalysts if c.get("data_source") == "manual"]
    warren_cats = [c for c in catalysts if c.get("data_source") == "warren"]
    merged_cats = [c for c in catalysts if c.get("data_source") == "merged"]
    spacex = next((c for c in catalysts if c.get("private_company") == "SpaceX"), None)

    badge_bg, badge_fg = _readiness_badge(readiness)
    label = readiness.replace("_", " ").title()

    _dim = {"fontSize": "11px", "color": C["dim"]}
    _label = {"fontSize": "11px", "fontWeight": 600, "color": C["muted"],
              "textTransform": "uppercase", "letterSpacing": "0.04em"}
    _val = {"fontSize": "13px", "fontWeight": 700}

    def kv(label_text, val, color=None):
        return html.Div([
            html.Div(label_text, style=_label),
            html.Div(val, style={**_val, **({"color": color} if color else {})}),
        ], style={"padding": "4px 16px 4px 0", "minWidth": "120px"})

    header = html.Div([
        html.Span("Theme Catalyst Radar",
                  style={"fontSize": "13px", "fontWeight": 700,
                         "textTransform": "uppercase", "letterSpacing": "0.05em",
                         "color": C["text"]}),
        html.Span("  Manual + Warren research + proxy confirmation",
                  style={"fontSize": "11px", "color": C["dim"], "fontStyle": "italic"}),
    ], style={"marginBottom": "10px"})

    readiness_row = html.Div([
        html.Span("Radar: ", style={"fontSize": "12px", "color": C["muted"], "fontWeight": 600}),
        html.Span(label, style={"background": badge_bg, "color": badge_fg,
                                "padding": "3px 12px", "borderRadius": "20px",
                                "fontSize": "12px", "fontWeight": 700}),
    ], style={"marginBottom": "12px"})

    warren_row = html.Div([
        kv("Warren research", warren_status.replace("_", " "),
           C["green_fg"] if warren_status == "ok" else C["amber_fg"]),
        kv("Warren generated", warren_gen[:19] if warren_gen != "—" else "—"),
        kv("Warren stale", "Yes" if warren_stale else "No",
           C["amber_fg"] if warren_stale else C["green_fg"]),
        kv("Manual", str(manual_count)),
        kv("Warren rows", str(warren_count)),
        kv("Merged total", str(merged_count)),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    summary_row = html.Div([
        kv("Catalysts", str(len(catalysts))),
        kv("Confirmed", ", ".join(payload.get("top_confirmed_themes") or []) or "—",
           C["green_fg"] if payload.get("top_confirmed_themes") else C["text"]),
        kv("Early watch", ", ".join(payload.get("early_watch_themes") or []) or "—",
           C["amber_fg"] if payload.get("early_watch_themes") else C["text"]),
        kv("Crowded/fading", ", ".join(payload.get("crowded_or_extended_themes") or []) or "—"),
        kv("Missed themes", str(len(missed)),
           C["amber_fg"] if missed else C["green_fg"]),
    ], style={"display": "flex", "flexWrap": "wrap", "marginBottom": "10px",
              "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    source_breakdown = html.Div([
        html.Div("Source breakdown", style={**_label, "marginBottom": "4px"}),
        html.Div([
            html.Span(f"Manual: {len(manual_cats)}  ", style=_dim),
            html.Span(f"Warren discovered: {len(warren_cats)}  ", style=_dim),
            html.Span(f"Merged: {len(merged_cats)}", style=_dim),
        ]),
    ], style={"marginBottom": "8px"})

    warren_new_section = None
    if warren_new:
        warren_new_section = html.Div([
            html.Div("Top new Warren themes", style={**_label, "marginBottom": "4px"}),
            html.Ul([html.Li(t, style=_dim) for t in warren_new[:5]],
                    style={"margin": 0, "paddingLeft": "18px"}),
        ], style={"marginBottom": "8px"})

    spacex_section = None
    if spacex:
        spacex_section = html.Div([
            html.Div("SpaceX status", style={**_label, "marginBottom": "4px"}),
            html.Div(
                f"{spacex.get('catalyst_date', 'TBD')} · phase {spacex.get('theme_phase', '—')} · "
                f"source {spacex.get('data_source', 'manual')} · "
                f"Warren watch: {spacex.get('warren_suggested_watch_status') or '—'}",
                style=_dim,
            ),
        ], style={"marginBottom": "8px",
                  "borderBottom": f"1px solid {C['border']}", "paddingBottom": "8px"})

    catalyst_rows = []
    for cat in catalysts[:4]:
        phase = cat.get("theme_phase") or "early"
        confirm = cat.get("confirmation_detail") or {}
        scores = cat.get("theme_scores") or {}
        catalyst_rows.append(html.Div([
            html.Div([
                html.Span(cat.get("theme", "—").upper(), style={"fontWeight": 700, "fontSize": "12px"}),
                html.Span(f"  {cat.get('private_company', '')} · {cat.get('event_type', '')}",
                          style={"fontSize": "11px", "color": C["muted"]}),
            ], style={"marginBottom": "4px"}),
            html.Div([
                kv("Catalyst", f"{cat.get('catalyst_label', '—')} ({cat.get('catalyst_date', 'TBD')})"),
                kv("Source", cat.get("data_source") or "manual"),
                kv("Phase", phase.replace("_", " "), _theme_phase_color(phase)),
                kv("Theme score", str(scores.get("overall_theme_score", "—"))),
                kv("Verified", cat.get("last_verified") or "—"),
                kv("RS confirmed", "Yes" if cat.get("relative_strength_confirmation") else "No",
                   C["green_fg"] if cat.get("relative_strength_confirmation") else C["text"]),
                kv("Volume confirmed", "Yes" if cat.get("volume_confirmation") else "No"),
                kv("Proxies beat SPY", str(confirm.get("proxies_beating_spy", 0))),
                kv("Missed?", "Yes" if cat.get("missed_theme_candidate") else "No",
                   C["amber_fg"] if cat.get("missed_theme_candidate") else C["green_fg"]),
            ], style={"display": "flex", "flexWrap": "wrap"}),
            html.Div(cat.get("theme_audio_summary") or cat.get("action_context") or "",
                     style={**_dim, "fontStyle": "italic", "marginTop": "4px"}),
        ], style={"marginBottom": "10px", "paddingBottom": "8px",
                  "borderBottom": f"1px solid {C['border']}"}))

    warn_section = html.Div([
        html.Div("Warnings", style={**_label, "marginBottom": "4px"}),
        html.Ul(
            [html.Li(w.replace("_", " "), style={**_dim}) for w in warnings]
            or [html.Li("None", style={**_dim, "fontStyle": "italic", "color": C["green_fg"]})],
            style={"margin": 0, "paddingLeft": "18px"},
        ),
    ], style={"marginBottom": "8px"})

    bullets_section = html.Div([
        html.Div("Suggested radar bullets", style={**_label, "marginBottom": "4px"}),
        html.Ul([html.Li(b, style={**_dim}) for b in bullets],
                style={"margin": 0, "paddingLeft": "18px"}),
    ], style={"marginBottom": "6px"})

    api_note = html.Div(
        "API: GET /api/atlas/theme-catalyst-radar",
        style={"fontSize": "10px", "color": C["dim"], "fontStyle": "italic",
               "borderTop": f"1px solid {C['border']}", "paddingTop": "6px"},
    )

    return html.Div([
        header, readiness_row, warren_row, summary_row,
        source_breakdown,
        *( [warren_new_section] if warren_new_section else [] ),
        *( [spacex_section] if spacex_section else [] ),
        *catalyst_rows,
        warn_section, bullets_section, api_note,
    ], style={"background": C["surface"], "borderRadius": "12px",
              "padding": "18px", "border": f"1px solid {C['border']}"})


@app.callback(
    Output("theme-catalyst-radar-outer", "children"),
    Input("version", "data"),
)
def update_theme_catalyst_radar(_version):
    """Render Theme Catalyst Radar from live proxy data."""
    try:
        payload = atcr.build_theme_catalyst_radar()
    except Exception as exc:
        payload = {
            "readiness": "not_ready",
            "warnings": ["radar_build_failed"],
            "catalysts": [],
            "theme_summaries": {},
            "proxy_baskets": {},
            "top_confirmed_themes": [],
            "early_watch_themes": [],
            "crowded_or_extended_themes": [],
            "missed_theme_candidates": [],
            "suggested_audio_bullets": [f"Theme Catalyst Radar failed: {exc}"],
        }
    return _build_theme_catalyst_radar_card(payload)


# ── 3. Sector table click → selected-sector store ────────────────────────

@app.callback(
    Output("selected-sector", "data", allow_duplicate=True),
    Input("tbl-sector-rotation-sel", "selected_rows"),
    State("tbl-sector-rotation-sel", "data"),
    prevent_initial_call=True,
)
def on_sector_table_click(selected_rows, table_data):
    if not selected_rows or not table_data:
        return no_update
    return table_data[selected_rows[0]]["Ticker"]


# ── 4. Top4 card click → selected-sector store ───────────────────────────

@app.callback(
    Output("selected-sector", "data", allow_duplicate=True),
    Input({"type": "top4-card", "etf": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def on_top4_card_click(n_clicks_list):
    if not any(n_clicks_list):
        return no_update
    triggered = ctx.triggered_id
    if triggered and "etf" in triggered:
        return triggered["etf"]
    return no_update


# ── 5. Render suggested tickers on sector change ──────────────────────────

@app.callback(
    Output("suggested-tickers", "children"),
    Input("selected-sector", "data"),
    Input("version", "data"),
)
def update_suggested_tickers(sector_etf, _version):
    all_positions = db.get_positions()
    return build_suggested_tickers(sector_etf, all_positions)


# ── 6a. Warren AI panel ──────────────────────────────────────────────────

@app.callback(
    Output("warren-panel", "children"),
    Input("version", "data"),
)
def update_warren_panel(_version):
    w = wl.get_latest_warren()
    try:
        ss = get_sector_statuses()
    except Exception:
        ss = {}
    return build_warren_panel(w, ss)


@app.callback(
    Output("morning-status-banner", "children"),
    Input("version", "data"),
)
def update_morning_status_banner(_version):
    w  = wl.get_latest_warren()
    pm = wl.get_latest_portfolio_monitor()
    return build_morning_status_banner(w, pm)


@app.callback(
    Output("portfolio-thesis-monitor", "children"),
    Input("version", "data"),
)
def update_portfolio_thesis_panel(_version):
    pm = wl.get_latest_portfolio_monitor()
    return build_portfolio_thesis_panel(pm)


# ── 6b. Ticker chip click → populate input ───────────────────────────────

@app.callback(
    Output("warren-ticker-input", "value", allow_duplicate=True),
    Input({"type": "ticker-chip", "ticker": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def on_ticker_chip_click(n_clicks_list):
    if not any(n_clicks_list):
        return no_update
    triggered = ctx.triggered_id
    if triggered and "ticker" in triggered:
        return triggered["ticker"]
    return no_update


# ── 6c. Ticker lookup ────────────────────────────────────────────────────

@app.callback(
    Output("warren-ticker-result", "children"),
    Input("warren-ticker-input", "value"),
)
def on_warren_ticker_lookup(ticker_value):
    if not ticker_value or not ticker_value.strip():
        # Load today's actionable tickers to show as example chips
        w = wl.get_latest_warren()
        verdict_legend = html.Div([
            html.Span("GOOD NEW BUY", style={
                "background": C["green_bg"], "color": C["green_fg"],
                "fontWeight": 700, "fontSize": "11px",
                "padding": "2px 9px", "borderRadius": "20px", "marginRight": "6px",
            }),
            html.Span("WAIT", style={
                "background": C["amber_bg"], "color": C["amber_fg"],
                "fontWeight": 700, "fontSize": "11px",
                "padding": "2px 9px", "borderRadius": "20px", "marginRight": "6px",
            }),
            html.Span("AVOID", style={
                "background": C["red_bg"], "color": C["red_fg"],
                "fontWeight": 700, "fontSize": "11px",
                "padding": "2px 9px", "borderRadius": "20px", "marginRight": "6px",
            }),
            html.Span("ALREADY OWNED", style={
                "background": C["blue_bg"], "color": C["blue_fg"],
                "fontWeight": 700, "fontSize": "11px",
                "padding": "2px 9px", "borderRadius": "20px", "marginRight": "6px",
            }),
            html.Span("NOT IN TODAY'S SCAN", style={
                "background": C["neutral_bg"], "color": C["neutral_fg"],
                "fontWeight": 700, "fontSize": "11px",
                "padding": "2px 9px", "borderRadius": "20px",
            }),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "4px",
                   "marginBottom": "14px"})

        example_chips_div = html.Div()
        if w.actionable:
            chips = [
                html.Button(t, id={"type": "ticker-chip", "ticker": t}, n_clicks=0, style={
                    "background": C["green_bg"], "color": C["green_fg"],
                    "border": "none", "padding": "4px 12px", "borderRadius": "20px",
                    "fontSize": "12px", "fontWeight": 700, "cursor": "pointer",
                    "marginRight": "4px",
                })
                for t in w.actionable[:8]
            ]
            example_chips_div = html.Div([
                html.Div("Today's actionable tickers — click to look up:",
                         style={"fontSize": "11px", "color": C["muted"],
                                "fontWeight": 600, "marginBottom": "6px",
                                "textTransform": "uppercase", "letterSpacing": "0.04em"}),
                html.Div(chips, style={"display": "flex", "flexWrap": "wrap", "gap": "5px"}),
            ])

        return html.Div([
            html.Div("Type any ticker to get a buy verdict:",
                     style={"fontSize": "13px", "color": C["muted"],
                            "marginBottom": "10px"}),
            verdict_legend,
            example_chips_div,
        ])
    t = ticker_value.strip().upper()
    w  = wl.get_latest_warren()
    pm = wl.get_latest_portfolio_monitor()
    try:
        sector_statuses = get_sector_statuses()
    except Exception:
        sector_statuses = {}

    verdict = build_ticker_verdict(t, w, pm, sector_statuses)

    # Chart — fetch from cache or yfinance (45-min TTL)
    chart_result = cd.get_daily_chart_data(t, lookback_months=6)
    chart        = build_ticker_chart(t, chart_result)

    # Two-column responsive layout: left = details (33%), right = chart (67%)
    return html.Div([verdict, chart], className="verdict-row")


# ── 6. Refresh button ─────────────────────────────────────────────────────

@app.callback(
    Output("version", "data", allow_duplicate=True),
    Input("btn-refresh", "n_clicks"),
    State("version", "data"),
    prevent_initial_call=True,
)
def on_refresh(_n, version):
    mr.get_macro_regime(force_refresh=True)  # bust FRED cache on manual refresh
    return (version or 0) + 1


# ── 7. Modal open/close ───────────────────────────────────────────────────

_MODAL_VISIBLE = {"display": "block"}
_MODAL_HIDDEN  = {"display": "none"}
_BLANK = ("", False, None, None, None, None, None, None, "in_position",
          "", "", None, "", None, "")


def _pos_to_form(pos: dict, override_status: str | None = None):
    return (
        pos["ticker"], True,
        pos.get("sector"),
        pos.get("shares"),
        pos.get("cost_basis"),
        pos.get("target_weight"),
        pos.get("current_weight"),
        pos.get("conviction") or None,
        override_status or pos.get("status", "in_position"),
        pos.get("notes") or "",
        pos.get("investingpro_url") or "",
        pos.get("fair_value_upside_pct"),
        pos.get("financial_health_score") or "",
        pos.get("analyst_target_upside_pct"),
        pos.get("manual_fundamental_note") or "",
    )


@app.callback(
    Output("modal-wrapper",       "style"),
    Output("form-ticker",         "value"),
    Output("form-ticker",         "disabled"),
    Output("form-sector",         "value"),
    Output("form-shares",         "value"),
    Output("form-cost-basis",     "value"),
    Output("form-target-weight",  "value"),
    Output("form-current-weight", "value"),
    Output("form-conviction",     "value"),
    Output("form-status",         "value"),
    Output("form-notes",          "value"),
    Output("form-url",            "value"),
    Output("form-fv-upside",      "value"),
    Output("form-health-score",   "value"),
    Output("form-analyst-upside", "value"),
    Output("form-fund-note",      "value"),
    Input("btn-add-position",    "n_clicks"),
    Input("btn-add-watchlist",   "n_clicks"),
    Input("btn-edit-position",   "n_clicks"),
    Input("btn-edit-watchlist",  "n_clicks"),
    Input("btn-cancel",          "n_clicks"),
    State("tbl-portfolio",       "selected_rows"),
    State("tbl-portfolio",       "data"),
    State("tbl-watchlist",       "selected_rows"),
    State("tbl-watchlist",       "data"),
    prevent_initial_call=True,
)
def modal_control(_ap, _aw, _ep, _ew, _cancel,
                  p_sel, p_data, w_sel, w_data):
    tid = ctx.triggered_id
    if tid == "btn-cancel":
        return (_MODAL_HIDDEN, *_BLANK)
    if tid == "btn-add-position":
        return (_MODAL_VISIBLE, *_BLANK)
    if tid == "btn-add-watchlist":
        blank_w = list(_BLANK); blank_w[8] = "watchlist"
        return (_MODAL_VISIBLE, *blank_w)
    if tid == "btn-edit-position":
        if not p_sel or not p_data:
            return (no_update, *([no_update] * 15))
        pos = db.get_position(p_data[p_sel[0]]["Ticker"])
        if not pos:
            return (no_update, *([no_update] * 15))
        return (_MODAL_VISIBLE, *_pos_to_form(pos))
    if tid == "btn-edit-watchlist":
        if not w_sel or not w_data:
            return (no_update, *([no_update] * 15))
        pos = db.get_position(w_data[w_sel[0]]["Ticker"])
        if not pos:
            return (no_update, *([no_update] * 15))
        return (_MODAL_VISIBLE, *_pos_to_form(pos))
    return (no_update, *([no_update] * 15))


# ── 8. Save position ──────────────────────────────────────────────────────

@app.callback(
    Output("modal-wrapper", "style",   allow_duplicate=True),
    Output("version",       "data",    allow_duplicate=True),
    Output("form-error",    "children"),
    Input("btn-save",            "n_clicks"),
    State("form-ticker",         "value"),
    State("form-sector",         "value"),
    State("form-shares",         "value"),
    State("form-cost-basis",     "value"),
    State("form-target-weight",  "value"),
    State("form-current-weight", "value"),
    State("form-conviction",     "value"),
    State("form-status",         "value"),
    State("form-notes",          "value"),
    State("form-url",            "value"),
    State("form-fv-upside",      "value"),
    State("form-health-score",   "value"),
    State("form-analyst-upside", "value"),
    State("form-fund-note",      "value"),
    State("version",             "data"),
    prevent_initial_call=True,
)
def save_position(_n, ticker, sector, shares, cost_basis, target_weight,
                  current_weight, conviction, status, notes, url,
                  fv_upside, health_score, analyst_upside, fund_note, version):
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return no_update, no_update, "Ticker is required."
    db.upsert_position({
        "ticker":                    ticker,
        "sector":                    (sector or "").strip().upper(),
        "shares":                    shares,
        "cost_basis":                cost_basis,
        "target_weight":             target_weight,
        "current_weight":            current_weight,
        "conviction":                conviction or "",
        "status":                    status or "in_position",
        "notes":                     notes or "",
        "investingpro_url":          url or "",
        "fair_value_upside_pct":     fv_upside,
        "financial_health_score":    health_score or "",
        "analyst_target_upside_pct": analyst_upside,
        "manual_fundamental_note":   fund_note or "",
    })
    return _MODAL_HIDDEN, (version or 0) + 1, ""


# ── 9. Archive position ───────────────────────────────────────────────────

@app.callback(
    Output("version",           "data",          allow_duplicate=True),
    Output("tbl-portfolio",     "selected_rows", allow_duplicate=True),
    Output("tbl-watchlist",     "selected_rows", allow_duplicate=True),
    Input("btn-archive-position",  "n_clicks"),
    Input("btn-archive-watchlist", "n_clicks"),
    State("tbl-portfolio",  "selected_rows"),
    State("tbl-portfolio",  "data"),
    State("tbl-watchlist",  "selected_rows"),
    State("tbl-watchlist",  "data"),
    State("version",        "data"),
    prevent_initial_call=True,
)
def archive_position(_ap, _aw, p_sel, p_data, w_sel, w_data, version):
    tid = ctx.triggered_id
    if tid == "btn-archive-position" and p_sel and p_data:
        db.archive_position(p_data[p_sel[0]]["Ticker"])
    elif tid == "btn-archive-watchlist" and w_sel and w_data:
        db.archive_position(w_data[w_sel[0]]["Ticker"])
    return (version or 0) + 1, [], []


# ── 10. Enable/disable Edit & Archive ────────────────────────────────────

@app.callback(
    Output("btn-edit-position",    "disabled"),
    Output("btn-archive-position", "disabled"),
    Input("tbl-portfolio", "selected_rows"),
)
def toggle_portfolio_btns(sel_rows):
    d = not sel_rows
    return d, d


@app.callback(
    Output("btn-edit-watchlist",    "disabled"),
    Output("btn-archive-watchlist", "disabled"),
    Input("tbl-watchlist", "selected_rows"),
)
def toggle_watchlist_btns(sel_rows):
    d = not sel_rows
    return d, d


# ── 11. Clear table selection on refresh ─────────────────────────────────

@app.callback(
    Output("tbl-portfolio", "selected_rows"),
    Output("tbl-watchlist", "selected_rows"),
    Input("version", "data"),
)
def clear_selection_on_refresh(_v):
    return [], []


# ---------------------------------------------------------------------------
# JSON import callbacks
# ---------------------------------------------------------------------------

# Preview / validate ─────────────────────────────────────────────────────────
@app.callback(
    Output("json-import-status",  "children"),
    Output("json-import-status",  "style"),
    Output("json-import-preview", "children"),
    Input("btn-json-preview", "n_clicks"),
    State("json-import-textarea", "value"),
    prevent_initial_call=True,
)
def preview_json_import(n_clicks, raw_text):
    _ok_style   = {"fontSize": "12px", "color": C["green_fg"], "marginTop": "6px"}
    _err_style  = {"fontSize": "12px", "color": C["red_fg"],   "marginTop": "6px"}
    _warn_style = {"fontSize": "12px", "color": C["amber_fg"], "marginTop": "6px"}

    if not raw_text or not raw_text.strip():
        return "Paste JSON first.", _err_style, no_update

    # ── Parse JSON ──────────────────────────────────────────────────────────
    try:
        parsed = json.loads(raw_text.strip())
    except json.JSONDecodeError as exc:
        return f"JSON parse error: {exc}", _err_style, no_update

    if isinstance(parsed, dict):
        # Moomoo sometimes wraps the array: {"positions": [...]}
        if "positions" in parsed:
            parsed = parsed["positions"]
        elif "holdings" in parsed:
            parsed = parsed["holdings"]
        else:
            return "Expected a JSON array (or object with 'positions' key).", _err_style, no_update

    if not isinstance(parsed, list):
        return "Expected a JSON array.", _err_style, no_update
    if len(parsed) == 0:
        return "Array is empty — nothing to import.", _warn_style, no_update

    # ── Enrich each row with company/sector via yfinance ──────────────────
    preview_rows = []
    unknown_count = 0
    for raw_row in parsed:
        ticker  = str(raw_row.get("ticker") or raw_row.get("symbol") or "").strip().upper()
        shares  = raw_row.get("shares") or raw_row.get("quantity") or raw_row.get("qty")
        cost    = raw_row.get("cost_basis") or raw_row.get("average_cost") or raw_row.get("avg_cost")
        mkt_val = raw_row.get("market_value") or raw_row.get("marketValue")
        price   = raw_row.get("current_price") or raw_row.get("price") or raw_row.get("lastPrice")

        # Company / sector auto-lookup via yfinance
        if ticker:
            info           = _fetch_ticker_info(ticker)
            company        = info.get("company") or ticker
            # Prefer explicitly supplied sector; fall back to yfinance raw
            explicit_raw   = (raw_row.get("sector_etf") or raw_row.get("sector") or "").strip()
            sector_raw     = explicit_raw or info.get("sector_raw") or ""
            sector_norm    = db.normalize_sector(sector_raw) if sector_raw else info.get("sector_normalized") or "UNKNOWN"
            industry       = raw_row.get("industry") or info.get("industry") or ""
        else:
            company = industry = ""
            sector_raw  = ""
            sector_norm = "UNKNOWN"

        if sector_norm == "UNKNOWN":
            unknown_count += 1

        errs = db.validate_import_row({
            "ticker": ticker, "shares": shares, "status": raw_row.get("status", "in_position"),
        })
        flags: list[str] = []
        if errs:
            flags.extend(["✗ " + e for e in errs])
        if sector_norm == "UNKNOWN":
            flags.append("⚠ sector unmapped")
        status_flag = "✓ OK" if not flags else " | ".join(flags)

        preview_rows.append({
            "Ticker":            ticker or "(missing)",
            "Name":              company,
            "Qty":               shares,
            "Mkt Value":         mkt_val,
            "Price":             price,
            "Avg Cost":          cost,
            "Sector Raw":        sector_raw or "—",
            "Sector (ETF)":      sector_norm,
            "Industry":          industry,
            "Validation":        status_flag,
        })

    error_count   = sum(1 for r in preview_rows if "✗" in r["Validation"])
    valid_count   = len(preview_rows) - error_count

    if error_count:
        status_msg   = (f"Parsed {len(preview_rows)} rows: {valid_count} valid, "
                        f"{error_count} with errors. Fix errors then confirm.")
        status_style = _warn_style
    elif unknown_count:
        status_msg   = (f"Parsed {len(preview_rows)} rows — all valid. "
                        f"{unknown_count} sector(s) could not be mapped to an ETF (shown as UNKNOWN). "
                        f"You can still import; set sector manually afterward.")
        status_style = _warn_style
    else:
        status_msg   = (f"Parsed {len(preview_rows)} rows — all valid with sectors mapped. "
                        f"Press Confirm Import to save.")
        status_style = _ok_style

    preview_table = html.Div([
        dash_table.DataTable(
            id="tbl-json-preview",
            columns=[{"name": c, "id": c} for c in preview_rows[0].keys()],
            data=preview_rows,
            style_cell={**_CELL, "minWidth": "70px", "maxWidth": "200px",
                        "whiteSpace": "normal"},
            style_header=_HEADER,
            style_table={**_TABLE, "marginTop": "10px"},
            style_data_conditional=[
                {
                    "if": {"filter_query": "{Validation} contains '✗'"},
                    "backgroundColor": C["red_bg"],
                    "color": C["red_fg"],
                },
                {
                    "if": {"filter_query": "{Validation} contains '⚠'"},
                    "backgroundColor": C["amber_bg"],
                    "color": C["amber_fg"],
                },
                {
                    "if": {"filter_query": "{Validation} = '✓ OK'"},
                    "backgroundColor": C["green_bg"],
                    "color": C["green_fg"],
                },
                {
                    "if": {"filter_query": "{Sector (ETF)} = 'UNKNOWN'"},
                    "backgroundColor": C["amber_bg"],
                    "color": C["amber_fg"],
                },
            ],
        ),
        html.Div([
            _btn("Confirm Import", "btn-json-confirm", primary=True),
        ], style={"marginTop": "10px"}),
        html.Div(id="json-confirm-status",
                 style={"fontSize": "12px", "marginTop": "6px", "minHeight": "18px"}),
    ])
    return status_msg, status_style, preview_table


# Clear ───────────────────────────────────────────────────────────────────────
@app.callback(
    Output("json-import-textarea", "value"),
    Output("json-import-status",   "children", allow_duplicate=True),
    Output("json-import-preview",  "children", allow_duplicate=True),
    Input("btn-json-clear", "n_clicks"),
    prevent_initial_call=True,
)
def clear_json_import(_n):
    return "", "", None


# Confirm import ──────────────────────────────────────────────────────────────
@app.callback(
    Output("json-confirm-status", "children"),
    Output("json-confirm-status", "style"),
    Output("version",             "data", allow_duplicate=True),
    Input("btn-json-confirm", "n_clicks"),
    State("json-import-textarea", "value"),
    prevent_initial_call=True,
)
def confirm_json_import(n_clicks, raw_text):
    _ok_style  = {"fontSize": "12px", "color": C["green_fg"], "marginTop": "6px"}
    _err_style = {"fontSize": "12px", "color": C["red_fg"],   "marginTop": "6px"}

    if not raw_text or not raw_text.strip():
        return "Nothing to import.", _err_style, no_update

    try:
        parsed = json.loads(raw_text.strip())
    except json.JSONDecodeError as exc:
        return f"JSON parse error: {exc}", _err_style, no_update

    if isinstance(parsed, dict):
        parsed = parsed.get("positions") or parsed.get("holdings") or []
    if not isinstance(parsed, list) or not parsed:
        return "No rows found in JSON.", _err_style, no_update

    # Enrich each row with yfinance sector/company before import
    enriched = []
    for raw_row in parsed:
        ticker = str(raw_row.get("ticker") or raw_row.get("symbol") or "").strip().upper()
        if ticker:
            info = _fetch_ticker_info(ticker)
            # Only inject sector_raw when the caller didn't supply one
            if not (raw_row.get("sector_etf") or raw_row.get("sector") or raw_row.get("sector_raw")):
                raw_row["sector_raw"] = info.get("sector_raw") or ""
            if not raw_row.get("notes"):
                raw_row["notes"] = info.get("industry") or ""
        raw_row.setdefault("ticker", ticker)
        enriched.append(raw_row)

    result = db.import_positions_json(enriched, dry_run=False)
    applied = result["applied"]
    skipped = result["skipped"]
    errs    = result["errors"]

    parts = [f"Imported {len(applied)} position(s): {', '.join(applied)}."]
    if skipped:
        parts.append(f"Skipped {len(skipped)}: {', '.join(skipped)}.")
    for t, msgs in errs.items():
        if t not in applied:
            parts.append(f"  {t}: {'; '.join(msgs)}")
    msg = " ".join(parts)

    # Bump store-version so the portfolio table refreshes
    version = int(datetime.now().timestamp())
    return msg, _ok_style, version


# ---------------------------------------------------------------------------
# Portfolio Exposure callbacks
# ---------------------------------------------------------------------------

# 1. Donut click → store the clicked sector (toggle off on second click)
@app.callback(
    Output("exposure-sector-filter", "data"),
    Input("donut-sector", "clickData"),
    State("exposure-sector-filter", "data"),
    prevent_initial_call=True,
)
def store_exposure_filter(click_data, current_filter):
    if not click_data:
        return None
    pts = click_data.get("points", [])
    if not pts:
        return None
    clicked = pts[0].get("label", "")
    # Cash / BOXX / NVDA slices are not sector ETF filters for the holdings table
    if not _is_donut_sector_etf_filter(str(clicked)):
        return no_update
    # Toggle: click same sector again → clear
    if clicked == current_filter:
        return None
    return clicked


# 2. Filter badge (shows "Filtered: XLF ×" when active)
@app.callback(
    Output("exposure-sector-filter-badge", "children"),
    Input("exposure-sector-filter", "data"),
)
def update_filter_badge(sector_filter):
    if not sector_filter:
        return ""
    return html.Span([
        f"Filtered: {sector_filter} ",
        html.Span("×", style={
            "cursor": "pointer", "fontWeight": 700,
            "color": C["blue_fg"], "marginLeft": "4px",
        }, id="exposure-filter-clear"),
    ], style={"fontSize": "12px", "color": C["blue_fg"]})


# 3. Filter the existing portfolio DataTable by clicked sector
@app.callback(
    Output("tbl-portfolio", "filter_query", allow_duplicate=True),
    Input("exposure-sector-filter", "data"),
    prevent_initial_call=True,
)
def filter_portfolio_by_sector(sector_filter):
    if not sector_filter or not _is_donut_sector_etf_filter(str(sector_filter)):
        return ""
    # Column id is "Sector" (see PORTFOLIO_COLS); filter_query ids are case-sensitive
    return f'{{Sector}} = "{sector_filter}"'


# ---------------------------------------------------------------------------
# Suggested Ticker Chart Review — helper + callbacks
# ---------------------------------------------------------------------------

# numpy / pandas / dateutil are imported at the top of the file


def _compute_ema(closes: list, span: int) -> list:
    """Exponential moving average (pandas-free). Returns list of same length; leading values NaN."""
    k = 2.0 / (span + 1)
    out = [float("nan")] * len(closes)
    for i, v in enumerate(closes):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if np.isnan(out[i - 1]) or i == 0:
            out[i] = v
        else:
            out[i] = v * k + out[i - 1] * (1 - k)
    return out


def _compute_sma(closes: list, window: int) -> list:
    """Simple moving average. Returns list of same length; leading values NaN."""
    out = [float("nan")] * len(closes)
    for i in range(window - 1, len(closes)):
        segment = closes[max(0, i - window + 1):i + 1]
        valid = [x for x in segment if x is not None and not np.isnan(x)]
        if len(valid) == window:
            out[i] = sum(valid) / window
    return out


def _get_default_suggested_ticker(w: "wl.WarrenResult",
                                  active_tickers: set,
                                  watchlist_tickers: set) -> str | None:
    """
    Priority:
    1. Highest-ranked NEW candidate with complete trade plan
    2. Highest-ranked ANY candidate with complete trade plan
    3. Highest-ranked suggested ticker
    4. None if no scan data
    """
    if w.freshness == "NONE":
        return None
    suggested = ms.classify_suggested_tickers(w, active_tickers, watchlist_tickers)

    def _has_plan(s) -> bool:
        return bool(
            s.entry_disp and s.entry_disp not in ("—", "")
            and s.sl_disp and s.sl_disp not in ("—", "")
            and s.rr_disp and s.rr_disp not in ("—", "")
        )

    # 1. New candidates with plan
    new_with_plan = [s for s in suggested
                     if s.portfolio_status == "New" and _has_plan(s)]
    if new_with_plan:
        new_with_plan.sort(key=lambda s: (0 if s.is_top3 else 1, s.rank or 99))
        return new_with_plan[0].ticker

    # 2. Any candidate with plan
    any_with_plan = [s for s in suggested if _has_plan(s)]
    if any_with_plan:
        any_with_plan.sort(key=lambda s: (0 if s.is_top3 else 1, s.rank or 99))
        return any_with_plan[0].ticker

    # 3. Any suggested ticker
    if suggested:
        return suggested[0].ticker
    return None


def _build_chart_review_figure(
    ticker: str,
    lookback_months: int,
    suggested: list,
    fw_rows: list,
) -> "go.Figure | None":
    """
    Build a Plotly figure with:
      - Daily candlesticks
      - 20 EMA (orange), 50 EMA (blue), 200 SMA (gray)
      - Entry zone band, stop loss line, take profit line
      - Vertical scan date marker, forward walk annotation
    Returns None if data unavailable.
    """
    try:
        ticker_upper = ticker.strip().upper()
        warmup_period = "1y" if lookback_months <= 6 else "2y"
        raw = yf.download(ticker_upper, period=warmup_period, progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
    except Exception:
        return None

    # Flatten MultiIndex columns if present
    if isinstance(raw.columns, _pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]

    # Trim to requested lookback for display
    cutoff = date.today() - _relativedelta(months=lookback_months)
    cutoff_ts = _pd.Timestamp(cutoff)
    display_df = raw[raw.index >= cutoff_ts]
    if display_df.empty:
        display_df = raw

    dates_all = list(raw.index)
    closes_all = [float(raw["Close"].iloc[i]) for i in range(len(raw))]

    # Compute indicators on full history then trim to display window
    ema20_all  = _compute_ema(closes_all, 20)
    ema50_all  = _compute_ema(closes_all, 50)
    sma200_all = _compute_sma(closes_all, 200)

    display_start = display_df.index[0]
    idx_start = next((i for i, d in enumerate(dates_all) if d >= display_start), 0)

    dates_disp  = dates_all[idx_start:]
    dates_str   = [d.strftime("%Y-%m-%d") for d in dates_disp]
    opens_disp  = [float(display_df["Open"].iloc[i])  for i in range(len(display_df))]
    highs_disp  = [float(display_df["High"].iloc[i])  for i in range(len(display_df))]
    lows_disp   = [float(display_df["Low"].iloc[i])   for i in range(len(display_df))]
    closes_disp = [float(display_df["Close"].iloc[i]) for i in range(len(display_df))]
    ema20_disp  = ema20_all[idx_start:]
    ema50_disp  = ema50_all[idx_start:]
    sma200_disp = sma200_all[idx_start:]

    # Get trade levels from the matching SuggestedTicker
    match = next((s for s in suggested if s.ticker == ticker_upper), None)
    entry_lo, entry_hi, sl_val, tp_val = None, None, None, None
    scan_date_str = None
    if match:
        scan_date_str = match.scan_date
        entry_lo, entry_hi = ms._parse_entry_range_bounds(match.entry_disp)
        sl_val  = ms._parse_scalar_price(match.sl_disp)
        tp_val  = ms._parse_scalar_price(match.tp_disp)

    # Get forward walk annotation for this ticker
    fw_match = None
    if fw_rows:
        # Prefer most recent scan date that matches
        candidates = [r for r in fw_rows if r.ticker == ticker_upper]
        if scan_date_str:
            exact = [r for r in candidates if r.scan_date == scan_date_str]
            fw_match = exact[0] if exact else (candidates[0] if candidates else None)
        else:
            fw_match = candidates[0] if candidates else None

    # Build figure
    fig = go.Figure()

    # ── Candlesticks ──────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=dates_str,
        open=opens_disp, high=highs_disp, low=lows_disp, close=closes_disp,
        name=ticker_upper,
        increasing_line_color="#3b6d11", increasing_fillcolor="#e9f3de",
        decreasing_line_color="#a32d2d", decreasing_fillcolor="#fceaea",
        line_width=1,
    ))

    # ── Moving averages ────────────────────────────────────────────────────
    def _ma_trace(vals, name, color, dash="solid", width=1):
        return go.Scatter(
            x=dates_str, y=[v if not np.isnan(v) else None for v in vals],
            mode="lines", name=name, line=dict(color=color, width=width, dash=dash),
            connectgaps=False,
        )

    fig.add_trace(_ma_trace(ema20_disp,  "EMA 20",  "#e07b00"))   # orange
    fig.add_trace(_ma_trace(ema50_disp,  "EMA 50",  "#0c447c"))   # blue
    fig.add_trace(_ma_trace(sma200_disp, "SMA 200", "#888880", dash="dot", width=1))  # gray

    # ── Entry zone ────────────────────────────────────────────────────────
    if entry_lo is not None and entry_hi is not None:
        fig.add_hrect(
            y0=entry_lo, y1=entry_hi,
            fillcolor="rgba(12, 68, 124, 0.12)", line_width=0,
            annotation_text="Entry zone", annotation_position="left",
            annotation_font_size=10,
        )
        fig.add_hline(y=entry_lo, line=dict(color="#0c447c", width=1, dash="dot"))
        fig.add_hline(y=entry_hi, line=dict(color="#0c447c", width=1, dash="dot"))
    elif entry_lo is not None:
        fig.add_hline(
            y=entry_lo, line=dict(color="#0c447c", width=1.5, dash="dash"),
            annotation_text="Entry", annotation_position="left",
            annotation_font_size=10,
        )

    # ── Stop loss ─────────────────────────────────────────────────────────
    if sl_val is not None:
        fig.add_hline(
            y=sl_val, line=dict(color="#a32d2d", width=1.5, dash="dash"),
            annotation_text=f"Stop  ${sl_val:,.2f}", annotation_position="right",
            annotation_font_size=10, annotation_font_color="#a32d2d",
        )

    # ── Take profit ───────────────────────────────────────────────────────
    if tp_val is not None:
        fig.add_hline(
            y=tp_val, line=dict(color="#3b6d11", width=1.5, dash="dash"),
            annotation_text=f"Target  ${tp_val:,.2f}", annotation_position="right",
            annotation_font_size=10, annotation_font_color="#3b6d11",
        )

    # ── Scan date vertical marker (category axis — use shape + annotation) ──
    if scan_date_str and scan_date_str in dates_str:
        scan_idx = dates_str.index(scan_date_str)
        ann_txt = "Scan"
        if fw_match and fw_match.return_pct is not None:
            sign = "+" if fw_match.return_pct >= 0 else ""
            ann_txt = f"Scan {sign}{fw_match.return_pct:.1f}%"
            if fw_match.outcome:
                ann_txt += f" · {fw_match.outcome}"
        fig.add_shape(
            type="line",
            xref="x", yref="paper",
            x0=scan_idx, x1=scan_idx, y0=0, y1=1,
            line=dict(color="#6e6d68", width=1.5, dash="dot"),
        )
        fig.add_annotation(
            x=scan_idx, y=0.98,
            xref="x", yref="paper",
            text=ann_txt, showarrow=False,
            xanchor="left", yanchor="top",
            font=dict(size=10, color="#6e6d68"),
        )

    # ── Current price line ────────────────────────────────────────────────
    if closes_disp:
        current_px = closes_disp[-1]
        fig.add_hline(
            y=current_px, line=dict(color="#1a1a1a", width=0.8, dash="dot"),
            annotation_text=f"${current_px:,.2f}", annotation_position="right",
            annotation_font_size=10, annotation_font_color="#1a1a1a",
        )

    # ── Layout ────────────────────────────────────────────────────────────
    fig.update_layout(
        height=480,
        margin=dict(l=50, r=90, t=30, b=30),
        plot_bgcolor=C["surface"],
        paper_bgcolor=C["surface"],
        font=dict(family="system-ui, -apple-system, sans-serif", size=11, color=C["text"]),
        xaxis=dict(
            rangeslider=dict(visible=False),
            gridcolor=C["border"], showgrid=True,
            tickfont=dict(size=10), type="category",
        ),
        yaxis=dict(
            gridcolor=C["border"], showgrid=True,
            tickfont=dict(size=10), tickprefix="$",
            side="left",
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1, font=dict(size=10),
        ),
        showlegend=True,
    )
    # Remove weekends/gaps from category axis
    fig.update_xaxes(type="category")
    return fig


def _build_chart_header(ticker: str, suggested: list, active_tickers: set,
                        watchlist_tickers: set, fw_rows: list) -> html.Div:
    """Context block above the candlestick chart."""
    match = next((s for s in suggested if s.ticker == ticker.upper()), None)

    if not match:
        return html.Div(
            html.Span(f"{ticker}  — Ticker not in latest scan.",
                      style={"fontSize": "12px", "color": C["muted"]}),
            style={"marginBottom": "8px"},
        )

    sector_nm  = sel.SECTOR_NAMES.get(match.sector_etf, match.sector_etf or "—")
    theme_names = tr.get_ticker_themes(match.ticker)

    # Status badge
    if match.portfolio_status == "Already owned":
        sbg, sfg, stxt = C["blue_bg"], C["blue_fg"], "Already owned"
    elif match.portfolio_status == "Watchlist":
        sbg, sfg, stxt = C["amber_bg"], C["amber_fg"], "Watchlist"
    else:
        sbg, sfg, stxt = C["green_bg"], C["green_fg"], "New"

    # Freshness — derive from scan_date vs today
    is_fresh = (match.scan_date == date.today().strftime("%Y-%m-%d"))
    fbg  = C["green_bg"] if is_fresh else C["amber_bg"]
    ffg  = C["green_fg"] if is_fresh else C["amber_fg"]
    ftxt = "Fresh" if is_fresh else f"Stale — {match.scan_date}"

    def _badge(txt, bg, fg):
        return html.Span(txt, style={
            "background": bg, "color": fg, "borderRadius": "5px",
            "padding": "2px 8px", "fontSize": "10px", "fontWeight": 700,
            "marginLeft": "6px", "whiteSpace": "nowrap",
        })

    def _lbl(label, val, color=None):
        return html.Span([
            html.Span(label + " ", style={"color": C["dim"], "fontSize": "11px"}),
            html.Span(val, style={
                "fontWeight": 700, "fontSize": "11px",
                "color": color or C["text"],
            }),
        ], style={"marginRight": "16px", "whiteSpace": "nowrap"})

    has_plan = bool(
        match.entry_disp and match.entry_disp not in ("—", "")
        and match.sl_disp  and match.sl_disp  not in ("—", "")
        and match.rr_disp  and match.rr_disp  not in ("—", "")
    )

    top_row = html.Div([
        html.Span(match.ticker, style={
            "fontWeight": 800, "fontSize": "16px", "marginRight": "10px",
        }),
        html.Span(f"{match.sector_etf} · {sector_nm}",
                  style={"fontSize": "11px", "color": C["dim"], "marginRight": "10px"}),
        *([html.Span(tn, style={
            "background": "#1e2a3a", "color": "#7eb3d6",
            "borderRadius": "4px", "padding": "1px 6px",
            "fontSize": "10px", "fontWeight": 600, "marginRight": "4px",
        }) for tn in theme_names]),
        _badge(stxt, sbg, sfg),
        _badge(ftxt, fbg, ffg),
    ], style={
        "display": "flex", "alignItems": "center",
        "flexWrap": "wrap", "gap": "4px", "marginBottom": "6px",
    })

    if has_plan:
        levels_row = html.Div([
            _lbl("Setup:", match.setup or "—"),
            _lbl("Scan:", match.scan_date or "—"),
            _lbl("Entry:", match.entry_disp or "—", C["blue_fg"]),
            _lbl("Stop:", match.sl_disp or "—", C["red_fg"]),
            _lbl("Target:", match.tp_disp or "—", C["green_fg"]),
            _lbl("R/R:", match.rr_disp or "—"),
        ], style={
            "display": "flex", "flexWrap": "wrap", "alignItems": "center",
        })
    else:
        levels_row = html.Div([
            html.Span("⚠  Mentioned only — no full trade plan (entry / stop / target missing).",
                      style={"fontSize": "11px", "color": C["amber_fg"], "fontWeight": 600}),
        ])

    return html.Div([top_row, levels_row], style={"marginBottom": "10px"})


# ── Callback 1: set default selected ticker when dashboard loads ───────────
@app.callback(
    Output("selected-suggested-ticker", "data"),
    Input("version", "data"),
    prevent_initial_call=False,
)
def set_default_suggested_ticker(_version):
    w = wl.get_latest_warren()
    active    = {p.get("ticker", "").upper() for p in db.get_positions(db.ACTIVE_STATUSES) if p.get("ticker")}
    watchlist = {p.get("ticker", "").upper() for p in db.get_positions(["watchlist"])      if p.get("ticker")}
    return _get_default_suggested_ticker(w, active, watchlist)


# ── Callback 2: chip clicks from New Ideas or Suggested Tickers update store
@app.callback(
    Output("selected-suggested-ticker", "data", allow_duplicate=True),
    Input({"type": "chart-chip", "ticker": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def chip_click_update_ticker(n_clicks_list):
    if not ctx.triggered or not any(n for n in (n_clicks_list or []) if n):
        return no_update
    prop_id = ctx.triggered[0]["prop_id"]
    try:
        raw = prop_id.split(".")[0]
        data = json.loads(raw)
        t = data.get("ticker")
        if t:
            return t
    except Exception:
        pass
    return no_update


# ── Callback 3a: populate dropdown options + sync value from store ─────────
@app.callback(
    Output("chart-ticker-dropdown", "options"),
    Output("chart-ticker-dropdown", "value"),
    Input("version",                   "data"),
    Input("selected-suggested-ticker", "data"),
)
def populate_chart_dropdown(_version, selected_ticker):
    w = wl.get_latest_warren()
    active    = {p.get("ticker", "").upper() for p in db.get_positions(db.ACTIVE_STATUSES) if p.get("ticker")}
    watchlist = {p.get("ticker", "").upper() for p in db.get_positions(["watchlist"])      if p.get("ticker")}
    suggested = ms.classify_suggested_tickers(w, active, watchlist) if w.freshness != "NONE" else []

    opts = []
    for s in suggested:
        has = bool(
            s.entry_disp and s.entry_disp not in ("—", "")
            and s.sl_disp  and s.sl_disp  not in ("—", "")
            and s.rr_disp  and s.rr_disp  not in ("—", "")
        )
        plan_tag = "" if has else " ⚠ · mentioned only"
        opts.append({
            "label": f"{s.ticker}{plan_tag}  [{s.portfolio_status}]",
            "value": s.ticker,
        })

    eff = selected_ticker
    if not eff and suggested:
        eff = _get_default_suggested_ticker(w, active, watchlist)
    return opts, eff


# ── Callback 3b: render the chart header + figure ─────────────────────────
@app.callback(
    Output("chart-review-section", "children"),
    Input("selected-suggested-ticker", "data"),
    Input("chart-lookback",            "value"),
    prevent_initial_call=False,
)
def render_chart_review(ticker, lookback_months):
    lookback_months = int(lookback_months or 6)

    w = wl.get_latest_warren()
    active    = {p.get("ticker", "").upper() for p in db.get_positions(db.ACTIVE_STATUSES) if p.get("ticker")}
    watchlist = {p.get("ticker", "").upper() for p in db.get_positions(["watchlist"])      if p.get("ticker")}
    suggested = ms.classify_suggested_tickers(w, active, watchlist) if w.freshness != "NONE" else []

    eff_ticker = ticker
    if not eff_ticker and suggested:
        eff_ticker = _get_default_suggested_ticker(w, active, watchlist)

    # Forward walk rows
    try:
        fw_rows = ms.get_forward_walk_results(60, active_tickers=active,
                                              watchlist_tickers=watchlist)
    except Exception:
        fw_rows = []

    # Empty state
    if not eff_ticker:
        return html.Div(
            "No scan data available. Run morning-full to populate suggested tickers.",
            style={"fontSize": "12px", "color": C["dim"], "padding": "10px 0"},
        )

    # Context header
    header_div = _build_chart_header(eff_ticker, suggested, active, watchlist, fw_rows)

    # Check plan completeness for the candles-only note
    with_plan_match = next((s for s in suggested
                            if s.ticker == eff_ticker.upper()
                            and s.entry_disp and s.entry_disp not in ("—", "")
                            and s.sl_disp   and s.sl_disp   not in ("—", "")), None)

    try:
        fig = _build_chart_review_figure(eff_ticker, lookback_months, suggested, fw_rows)
    except Exception:
        fig = None

    if fig is None:
        chart_el = html.Div(
            f"Chart data unavailable for {eff_ticker}.",
            style={"fontSize": "12px", "color": C["dim"],
                   "padding": "40px 0", "textAlign": "center"},
        )
    else:
        notes = []
        if not with_plan_match:
            notes.append(html.Div(
                "No complete trade plan levels available — candles and indicators only.",
                style={"fontSize": "11px", "color": C["amber_fg"],
                       "fontStyle": "italic", "marginBottom": "6px"},
            ))
        graph = dcc.Graph(
            figure=fig,
            config={"displayModeBar": "hover", "responsive": True,
                    "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
            style={"width": "100%"},
        )
        chart_el = html.Div([*notes, graph])

    return html.Div([
        header_div,
        dcc.Loading(chart_el, type="circle"),
    ])


# ── History tab (local Warren AI archive) ─────────────────────────────────
@app.callback(
    Output("history-date-select", "options"),
    Output("history-date-select", "value"),
    Output("history-tab-body", "children"),
    Input("version", "data"),
    Input("history-date-select", "value"),
)
def update_history_tab(_version, selected_date):
    active, watchlist, _, _ = run_analysis()
    active_tickers = {getattr(p, "ticker", None) for p in active if getattr(p, "ticker", None)}
    watch_tickers = {getattr(p, "ticker", None) for p in watchlist if getattr(p, "ticker", None)}

    metas = wh.list_history_dates()
    if not metas:
        return [], None, build_history_tab_panel(None, active_tickers, watch_tickers)

    opts = [{"label": f"{m.date} — {m.status}", "value": m.date} for m in metas]
    vals = [m.date for m in metas]
    sel = selected_date if selected_date in vals else vals[0]
    body = build_history_tab_panel(sel, active_tickers, watch_tickers)
    return opts, sel, body


# ── Callback 4: dropdown selection updates the store ───────────────────────
@app.callback(
    Output("selected-suggested-ticker", "data", allow_duplicate=True),
    Input("chart-ticker-dropdown", "value"),
    prevent_initial_call=True,
)
def dropdown_update_ticker(ticker):
    if not ticker:
        return no_update
    return ticker


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Decision Cockpit starting on http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=True)
