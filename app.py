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

import data as db
import selector as sel
import macro_regime as mr
import warren_loader as wl
import chart_data as cd
import portfolio_exposure as pe
import morning_status as ms
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
        return html.Div([
            section_hdr("Capital Breakdown"),
            html.Div(
                "Set TOTAL_PORTFOLIO_VALUE (and optional BOXX_VALUE / NVDA_VALUE) "
                "as environment variables, or edit the constants in app.py.",
                style={"fontSize": "12px", "color": C["muted"], "marginTop": "6px"},
            ),
        ], style=_MB)

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
            rows.append({
                "Ticker": ("★ " if s.is_top3 else "") + s.ticker,
                "Sector": s.sector_etf or "—",
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
                     ["Ticker", "Sector", "Rank", "Setup", "Entry", "SL", "TP", "R/R",
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
        health = ms.get_automation_health(7)

        today_health = health[0] if health else None
        pm_missing = today_health and not today_health.portfolio_mon
        last_complete = next((h for h in health if h.status == "Complete"), None)
        stale = (last_complete is None) or (
            last_complete.date != date.today().strftime("%Y-%m-%d")
            and last_complete.status != "Complete"
        )

        hints: list = []
        if pm_missing:
            hints.append(html.Div([
                html.Span("Portfolio monitor has not run today. Run: ",
                          style={"color": C["amber_fg"], "fontSize": "11px"}),
                html.Code("npm run morning-full",
                          style={"fontSize": "11px", "color": C["muted"]}),
            ], style={"marginBottom": "3px"}))

        tbl_data = []
        for h in health:
            tbl_data.append({
                "Date":      h.date,
                "Full run":  "Yes" if h.full_run else "No",
                "Scan":      "Yes" if h.warren_scan else "No",
                "Portfolio mon": "Yes" if h.portfolio_mon else "No",
                "Export":    "Yes" if h.export_done else "No",
                "Modified":  h.last_modified,
                "Status":    h.status,
            })

        missing_dates = [h.date for h in health if h.status == "Missing"]
        partial_dates = [h.date for h in health if h.status == "Partial"]

        date_notes: list = []
        if missing_dates:
            date_notes.append(html.Div([
                html.Span(
                    "No output folder (Missing): ",
                    style={"fontSize": "11px", "color": C["dim"], "fontWeight": 600},
                ),
                html.Span(
                    ", ".join(missing_dates),
                    style={"fontSize": "11px", "color": C["muted"]},
                ),
            ], style={"marginBottom": "4px", "lineHeight": "1.45"}))
        if partial_dates:
            date_notes.append(html.Div([
                html.Span(
                    "Incomplete runs (Partial): ",
                    style={"fontSize": "11px", "color": C["dim"], "fontWeight": 600},
                ),
                html.Span(
                    ", ".join(partial_dates),
                    style={"fontSize": "11px", "color": C["muted"]},
                ),
            ], style={"marginBottom": "8px", "lineHeight": "1.45"}))

        health_tbl = dash_table.DataTable(
            data=tbl_data,
            columns=[{"name": c, "id": c} for c in
                     ["Date", "Full run", "Scan", "Portfolio mon", "Export", "Modified", "Status"]],
            style_table={"overflowX": "auto"},
            style_cell={**_CELL, "minWidth": "60px"},
            style_header={**_HEADER},
            style_data_conditional=[
                {"if": {"filter_query": '{Status} = "Complete"'},
                 "color": C["green_fg"], "fontWeight": 600},
                {"if": {"filter_query": '{Status} = "Partial"'},
                 "color": C["amber_fg"]},
                {"if": {"filter_query": '{Status} = "Missing"'},
                 "color": C["dim"]},
                {"if": {"filter_query": '{Status} contains "error"'},
                 "color": C["red_fg"]},
                {"if": {"filter_query": '{Full run} = "No"', "column_id": "Full run"},
                 "color": C["dim"]},
                {"if": {"filter_query": '{Scan} = "No"', "column_id": "Scan"},
                 "color": C["dim"]},
            ],
        )

        last_fail = ms.get_automation_last_failure_banner()
        last_fail_div = html.Div()
        if last_fail:
            last_fail_div = html.Div(
                last_fail,
                style={
                    "fontSize": "11px",
                    "color": C["amber_fg"],
                    "marginBottom": "8px",
                    "lineHeight": "1.45",
                    "fontWeight": 600,
                },
            )

        return html.Div([
            section_hdr("Automation Health — Last 7 Days"),
            last_fail_div,
            *hints,
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

app.layout = html.Div(
    style={
        "background": C["bg"], "minHeight": "100vh",
        "padding": "28px 40px 48px",
        "fontFamily": "system-ui, -apple-system, 'Segoe UI', sans-serif",
        "color": C["text"],
    },
    children=[
        dcc.Store(id="version",                 data=0),
        dcc.Store(id="selected-sector",         data=None),
        dcc.Store(id="exposure-sector-filter",  data=None),
        dcc.Store(id="post-alert-data-store",   data=None),

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
                        # Macro Regime
                        html.Div(id="macro-regime-dash", style=_MB),

                        # Morning Scan Status
                        html.Div(id="morning-scan-dash", style=_MB),

                        # Post Alert Performance chart (interactive; below Morning Scan)
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
                                            value="both",
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
                                            value=[],
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

                        # Opportunity Cost Check (after Morning Scan, before Sector Rotation)
                        html.Div(id="opportunity-cost-dash", style=_MB),

                        # Verdict banner
                        html.Div(id="verdict-banner", style=_MB),

                        # Metric tiles
                        html.Div(id="metric-row", style=_MB),

                        # Sector Rotation (Dashboard — read-only)
                        html.Div([
                            html.Div(id="sector-rotation-dash"),
                        ], style=_MB),

                        # Capital Breakdown (above Portfolio Exposure)
                        html.Div(id="capital-breakdown-section", style=_MB),

                        # Mag 7 and Leadership Exposure
                        html.Div(id="mag7-exposure-dash", style=_MB),

                        # Portfolio Exposure (between metric tiles and alerts)
                        html.Div(id="exposure-section", style=_MB),

                        # Alerts
                        html.Div([
                            html.Div("Active alerts", style={
                                "fontSize": "13px", "fontWeight": 700,
                                "textTransform": "uppercase", "letterSpacing": "0.05em",
                                "marginBottom": "10px",
                            }),
                            html.Div(id="alerts-panel"),
                        ], style=_MB),

                        # Portfolio table
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
                        ], style=_MB),

                        # Watchlist table
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
                        ], style=_MB),

                        # ── JSON / Moomoo import panel ───────────────────
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
                            # Validation / status message
                            html.Div(id="json-import-status",
                                     style={"fontSize": "12px", "marginTop": "6px",
                                            "minHeight": "18px"}),
                            # Preview table (hidden until populated)
                            dcc.Loading(
                                html.Div(id="json-import-preview"),
                                type="dot",
                            ),
                        ], style={
                            **_MB,
                            "background": C["surface"],
                            "border": f"1px solid {C['border']}",
                            "borderRadius": "10px",
                            "padding": "16px",
                        }),
                    ],
                ),

                # ── Selector tab ─────────────────────────────────────────
                dcc.Tab(
                    label="Selector",
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

    # Pre-compute post-alert series for the interactive chart
    try:
        active_set   = {getattr(p, "ticker", None) for p in active}
        watch_set    = {getattr(p, "ticker", None) for p in watchlist}
        fw_rows      = ms.get_forward_walk_results(days=60,
                                                   active_tickers=active_set,
                                                   watchlist_tickers=watch_set)
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
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Decision Cockpit starting on http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=True)
