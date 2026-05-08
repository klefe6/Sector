"""
warren_loader.py — Parser for Warren AI daily output files.

Finds the most recent successful run folder and exposes structured data
for use by the Decision Cockpit's Selector tab and /api/warren-ai/latest.

Public API:
    get_latest_warren()  →  WarrenResult
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Config — path to warren_ai_daily output relative to this file's dir
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
WARREN_OUTPUT_DIR = os.path.join(_HERE, "warren_ai_daily", "output")

# How many calendar days back to search for a successful run
MAX_LOOKBACK_DAYS = 7

# Age in calendar days beyond which we label the data STALE
STALE_AFTER_DAYS = 2

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TickerRow:
    """One row from the step_03 technical table."""
    ticker:     str
    sector_etf: str
    price:      str = ""
    vs_20ema:   str = ""
    vs_50ema:   str = ""
    vs_200ema:  str = ""
    rsi:        str = ""
    setup:      str = ""
    fv_upside:  str = ""
    consensus:  str = ""
    eps:        str = ""
    # step_04 execution fields (filled in if available)
    rank:       int = 0
    verdict:    str = ""     # "Strong Buy", "Buy Breakout", etc.
    entry_zone: str = ""
    stop:       str = ""
    target:     str = ""
    rr:         str = ""
    size_note:  str = ""
    key_risk:   str = ""


@dataclass
class WarrenResult:
    """Structured output of the latest Warren AI daily run."""
    date:             str               # YYYY-MM-DD
    freshness:        str               # FRESH | STALE | FAILED | NONE
    success:          bool
    output_path:      str
    generated_at:     str               # ISO timestamp from run_summary

    # Market regime (from final_action_plan)
    regime:           str = ""
    regime_one_liner: str = ""
    sizing_note:      str = ""

    # Top 4 sectors (from step_02)
    top_sectors:      list[str] = field(default_factory=list)   # e.g. ["XLI","XLE","XLF","XLY"]
    sector_reasons:   dict[str, str] = field(default_factory=dict)

    # 16 candidate tickers
    all_tickers:      list[str] = field(default_factory=list)

    # Sector assignment for each ticker
    ticker_sector:    dict[str, str] = field(default_factory=dict)

    # Full TickerRow data keyed by ticker
    ticker_rows:      dict[str, TickerRow] = field(default_factory=dict)

    # step_04 top-8 actionable tickers (ranked)
    actionable:       list[str] = field(default_factory=list)

    # step_04 watch-tomorrow tickers
    watch_tomorrow:   list[str] = field(default_factory=list)

    # step_04 avoid/removed tickers
    avoid:            list[str] = field(default_factory=list)

    # Top-3 trades from final report
    top3_trades:      list[dict] = field(default_factory=list)

    # Names on Watch from final report
    watch_tickers:    list[str] = field(default_factory=list)

    # Sectors to avoid
    avoid_sectors:    list[str] = field(default_factory=list)

    # Full final action plan text (truncated to 4000 chars if very long)
    final_plan_text:  str = ""

    # Warnings from parsing
    warnings:         list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')


def _extract_tickers_from_line(line: str) -> list[str]:
    """Return all uppercase 1-5 letter tokens that look like tickers."""
    # Filter out very common noise words
    NOISE = {"A", "I", "AT", "IN", "TO", "BE", "OF", "ON", "OR", "IS",
             "NO", "UP", "BY", "AS", "DO", "IF", "SO", "IT", "AN",
             "MY", "WE", "VS", "FV", "RS", "EMA", "RSI", "ATR", "EPS",
             "ETF", "SPY", "VIX", "SMA", "IPO", "CEO", "CFO", "USD",
             "GDP", "CPI", "FED", "RRG", "XLI", "XLE", "XLF", "XLY",
             "XLK", "XLV", "XLU", "XLP", "XLC", "XLB", "XLRE",
             "NYSE", "NASDAQ", "SEC", "ROI", "ROE", "PE", "PB",
             "NOT", "THE", "AND", "FOR", "ALL", "ARE", "FROM",
             "WITH", "THIS", "THAT", "HAVE", "WILL", "BEEN", "EACH",
             "TOP", "NEW", "CAN", "MAY", "NOW", "BUT", "HAS", "ITS",
             "ONE", "TWO", "USE", "SET", "ANY", "HOW", "WHY", "DAY"}
    return [t for t in _TICKER_RE.findall(line) if t not in NOISE and len(t) >= 2]


def _find_latest_successful_dir() -> Optional[str]:
    """Return the most recent directory under WARREN_OUTPUT_DIR with success=true."""
    if not os.path.isdir(WARREN_OUTPUT_DIR):
        return None

    today = date.today()
    for delta in range(MAX_LOOKBACK_DAYS + 1):
        d = today - timedelta(days=delta)
        folder = os.path.join(WARREN_OUTPUT_DIR, d.strftime("%Y-%m-%d"))
        summary = os.path.join(folder, "run_summary.json")
        if os.path.isfile(summary):
            try:
                with open(summary, "r", encoding="utf-8") as fh:
                    s = json.load(fh)
                if s.get("success") is True:
                    return folder
            except Exception:
                pass
    return None


def _read_file(folder: str, name: str) -> str:
    path = os.path.join(folder, name)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    return ""


def _freshness(run_date_str: str) -> str:
    try:
        run_date = datetime.strptime(run_date_str, "%Y-%m-%d").date()
        delta = (date.today() - run_date).days
        if delta == 0:
            return "FRESH"
        elif delta <= STALE_AFTER_DAYS:
            return "STALE"
        else:
            return "STALE"
    except Exception:
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Step parsers
# ---------------------------------------------------------------------------

def _parse_run_summary(folder: str) -> dict:
    text = _read_file(folder, "run_summary.json")
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _parse_final_plan(text: str, result: WarrenResult) -> None:
    """Extract regime, top-3 trades, watch, avoid from final_action_plan.md."""
    if not text:
        result.warnings.append("final_action_plan.md missing or empty")
        return

    result.final_plan_text = text[:5000]

    lines = text.splitlines()

    # ── Regime ───────────────────────────────────────────────────────────────
    for i, line in enumerate(lines):
        if "Regime:" in line and not result.regime:
            # "Regime: Mild Risk-On" or "Regime: Risk On"
            m = re.search(r'Regime:\s*(.+)', line)
            if m:
                result.regime = m.group(1).strip()
        if "One-line read:" in line and not result.regime_one_liner:
            m = re.search(r'One-line read:\s*(.+)', line)
            if m:
                result.regime_one_liner = m.group(1).strip()
        if "Implication for sizing:" in line and not result.sizing_note:
            m = re.search(r'Implication for sizing:\s*(.+)', line)
            if m:
                result.sizing_note = m.group(1).strip()

    # ── Top 3 Trades ─────────────────────────────────────────────────────────
    trade: Optional[dict] = None
    for line in lines:
        # "Trade 1: CNX — CNX Resources"
        m = re.match(r'Trade\s*\d+:\s*([A-Z]{1,5})\s*[—–-]+\s*(.+)', line)
        if m:
            if trade:
                result.top3_trades.append(trade)
            trade = {
                "ticker":  m.group(1).strip(),
                "name":    m.group(2).strip(),
                "verdict": "",
                "entry":   "",
                "stop":    "",
                "target":  "",
                "rr":      "",
                "why":     "",
            }
        elif trade:
            for key, pattern in [
                ("verdict", r'Verdict:\s*(.+)'),
                ("why",     r'Why today:\s*(.+)'),
                ("entry",   r'Entry:\s*(.+)'),
                ("stop",    r'Stop:\s*(.+)'),
                ("target",  r'Target:\s*(.+)'),
                ("rr",      r'Risk:Reward:\s*(.+)'),
            ]:
                mm = re.search(pattern, line)
                if mm and not trade[key]:
                    trade[key] = mm.group(1).strip()
    if trade:
        result.top3_trades.append(trade)

    # ── Names on Watch ────────────────────────────────────────────────────────
    in_watch = False
    for line in lines:
        if "Names on Watch" in line or "Names On Watch" in line:
            in_watch = True
            continue
        if in_watch:
            # End of section
            if re.match(r'\d+\.', line) and "Names" not in line:
                in_watch = False
                continue
            if line.strip().startswith("4.") or "Sectors to Avoid" in line:
                in_watch = False
                continue
            # "EQT: Needs breakout..."  or  "EQT — ..."
            m = re.match(r'^([A-Z]{1,5})[:\s—–]', line.strip())
            if m:
                result.watch_tickers.append(m.group(1))

    # ── Sectors to Avoid ──────────────────────────────────────────────────────
    in_avoid_sec = False
    for line in lines:
        if "Sectors to Avoid" in line or "Sectors To Avoid" in line:
            in_avoid_sec = True
            continue
        if in_avoid_sec:
            if re.match(r'\d+\.', line) and "Sectors" not in line:
                in_avoid_sec = False
                continue
            if line.strip().startswith("5.") or "Key Risks" in line:
                in_avoid_sec = False
                continue
            # "Utilities (XLU): ..."
            m = re.search(r'\(([A-Z]{2,5})\)', line)
            if m:
                result.avoid_sectors.append(m.group(1))


def _parse_step02(text: str, result: WarrenResult) -> None:
    """Extract top 4 sectors and 16 tickers from step_02.md."""
    if not text:
        result.warnings.append("step_02.md missing or empty")
        return

    lines = text.splitlines()

    # ── All 16 tickers line ───────────────────────────────────────────────────
    # "All 16 Tickers:\nDE, LMT, ETN, PH, VLO, ..."
    for i, line in enumerate(lines):
        if "All 16 Tickers" in line or "16 Tickers" in line:
            # Could be on same line or next line
            candidate = line.split(":", 1)[-1].strip() if ":" in line else ""
            if not candidate and i + 1 < len(lines):
                candidate = lines[i + 1].strip()
            if candidate:
                tickers = [t.strip().upper() for t in candidate.split(",") if re.match(r'^[A-Z]{1,5}$', t.strip())]
                if len(tickers) >= 8:
                    result.all_tickers = tickers

    # ── Top sectors (from "Sector: XLI" headers) ──────────────────────────────
    sector_order: list[str] = []
    current_sector_etf: Optional[str] = None
    for line in lines:
        # "Sector: XLI — Industrials" or "Sector: XLE — Energy"
        m = re.match(r'Sector:\s*(XL[A-Z]+)\s*[—–-]?\s*(.*)', line)
        if m:
            etf = m.group(1).strip()
            if etf not in sector_order:
                sector_order.append(etf)
            current_sector_etf = etf
        elif current_sector_etf:
            # Lines like "Why this sector today: ..."
            m2 = re.match(r'Why this sector today:\s*(.+)', line)
            if m2:
                result.sector_reasons[current_sector_etf] = m2.group(1).strip()
            # Ticker rows: "1 DE Deere & Co. ..."
            m3 = re.match(r'\d+\s+([A-Z]{1,5})\s+', line)
            if m3:
                t = m3.group(1)
                result.ticker_sector[t] = current_sector_etf

    if sector_order:
        result.top_sectors = sector_order[:4]

    # If no all_tickers found, extract from sector tables
    if not result.all_tickers and result.ticker_sector:
        result.all_tickers = list(result.ticker_sector.keys())


def _parse_step03(text: str, result: WarrenResult) -> None:
    """Parse the TSV-like technical table from step_03.md."""
    if not text:
        result.warnings.append("step_03.md missing or empty")
        return

    lines = text.splitlines()

    # Find header line
    # "Ticker\tPrice\tvs 20EMA\t..."
    header_line_idx: int = -1
    for i, line in enumerate(lines):
        if line.startswith("Ticker\t") or line.startswith("Ticker ") and "Price" in line:
            header_line_idx = i
            break

    if header_line_idx < 0:
        result.warnings.append("step_03: could not find table header")
        return

    # Detect delimiter
    header = lines[header_line_idx]
    delim = "\t" if "\t" in header else None
    if delim is None:
        # Multiple spaces
        result.warnings.append("step_03: tab delimiter not found, using split")
        return

    headers = [h.strip() for h in header.split(delim)]

    def col(row_parts: list[str], name: str, default: str = "") -> str:
        try:
            idx = next(i for i, h in enumerate(headers) if name.lower() in h.lower())
            return row_parts[idx].strip() if idx < len(row_parts) else default
        except StopIteration:
            return default

    for line in lines[header_line_idx + 1:]:
        if not line.strip() or line.startswith("Notable") or line.startswith("Historical") or line.startswith("This content"):
            continue
        parts = line.split(delim)
        if len(parts) < 5:
            continue
        ticker = parts[0].strip().upper()
        if not re.match(r'^[A-Z]{1,5}$', ticker):
            continue

        # Determine sector ETF from our ticker_sector map (step02) first,
        # then fall back to "Fair Value" column which contains the ETF in step_03
        sector_etf = result.ticker_sector.get(ticker, "")
        # step_03 "Fair Value" column actually has the ETF string like "XLE" or "XLI"
        fv_col_val = col(parts, "Fair Value")
        if not sector_etf and re.match(r'^XL[A-Z]+$', fv_col_val):
            sector_etf = fv_col_val

        # In step_03, "Fair Value" column contains the sector ETF ticker (e.g. XLI),
        # "FV Upside" contains the fair value dollar price, and "P/E" contains
        # the fair value upside % (e.g. "-33.8%", "43.2%"). We surface the % as fv_upside.
        tr = TickerRow(
            ticker=ticker,
            sector_etf=sector_etf,
            price=col(parts, "Price"),
            vs_20ema=col(parts, "vs 20EMA"),
            vs_50ema=col(parts, "vs 50EMA"),
            vs_200ema=col(parts, "vs 200EMA"),
            rsi=col(parts, "RSI"),
            setup=col(parts, "Setup"),
            fv_upside=col(parts, "P/E"),     # P/E column = FV upside %
            eps=col(parts, "EPS"),
            consensus=col(parts, "Consensus"),
        )
        result.ticker_rows[ticker] = tr

        # Update ticker_sector if we learned it here
        if sector_etf and ticker not in result.ticker_sector:
            result.ticker_sector[ticker] = sector_etf


def _parse_step04(text: str, result: WarrenResult) -> None:
    """Extract top-8 actionable, watch-tomorrow, and avoid from step_04.md."""
    if not text:
        result.warnings.append("step_04.md missing or empty")
        return

    lines = text.splitlines()

    # ── Execution table (tab-separated) ──────────────────────────────────────
    # "Rank\tTicker\tVerdict\tEntry Zone\tStop Loss\tTarget\tRisk:Reward\t..."
    in_table = False
    table_headers: list[str] = []
    for line in lines:
        # Require "Entry Zone" so Part A rank-only tables like
        # "Rank\tTicker\tReason for Rank" are not parsed as execution rows
        # (they would clear levels and duplicate actionable tickers).
        hdr_cells = [h.strip() for h in line.split("\t")] if "\t" in line else []
        hdr_blob = " ".join(h.lower() for h in hdr_cells)
        if hdr_cells and "entry zone" in hdr_blob:
            in_table = True
            table_headers = hdr_cells
            continue
        if in_table:
            if not line.strip() or line.startswith("Part") or line.startswith("Just"):
                in_table = False
                continue
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 3:
                continue
            try:
                rank_str = parts[0]
                rank = int(rank_str) if rank_str.isdigit() else 0
            except Exception:
                rank = 0
            if len(parts) < 2:
                continue
            ticker = parts[1].strip().upper() if len(parts) > 1 else ""
            if not re.match(r'^[A-Z]{1,5}$', ticker):
                continue

            def tc(name: str, default: str = "") -> str:
                try:
                    idx = next(i for i, h in enumerate(table_headers) if name.lower() in h.lower())
                    return parts[idx] if idx < len(parts) else default
                except StopIteration:
                    return default

            # Populate or update TickerRow
            if ticker not in result.ticker_rows:
                result.ticker_rows[ticker] = TickerRow(
                    ticker=ticker,
                    sector_etf=result.ticker_sector.get(ticker, ""),
                )
            tr = result.ticker_rows[ticker]
            tr.rank       = rank
            tr.verdict    = tc("Verdict")
            tr.entry_zone = tc("Entry Zone")
            tr.stop       = tc("Stop Loss") or tc("Stop")
            tr.target     = tc("Target")
            tr.rr         = tc("Risk:Reward") or tc("Risk/Reward")
            tr.size_note  = tc("Position Size")
            tr.key_risk   = tc("Key Risk")

            if rank > 0:
                result.actionable.append(ticker)

    # ── Simple ranked list fallback if table parsing found nothing ─────────────
    if not result.actionable:
        for line in lines:
            # "CNX (Energy) — Pullback, ..."  or  "1. CNX —  ..."
            m = re.match(r'^(?:\d+\.)?\s*([A-Z]{1,5})\s*[\(—–]', line.strip())
            if m:
                t = m.group(1)
                if t not in result.actionable:
                    result.actionable.append(t)

    # ── Watch tomorrow / Part C ────────────────────────────────────────────────
    in_watch = False
    for line in lines:
        if "Part C" in line or "Watch Tomorrow" in line or "Names to Watch Tomorrow" in line:
            in_watch = True
            continue
        if in_watch:
            if line.startswith("Part D") or "Remove from Research" in line:
                in_watch = False
                continue
            m = re.match(r'^([A-Z]{1,5})\s*[—–]', line.strip())
            if m:
                result.watch_tomorrow.append(m.group(1))

    # ── Avoid / Part D ────────────────────────────────────────────────────────
    in_avoid = False
    for line in lines:
        if "Part D" in line or "Remove from Research" in line or "Names to Remove" in line:
            in_avoid = True
            continue
        if in_avoid:
            if line.startswith("Part") and "D" not in line:
                in_avoid = False
                continue
            m = re.match(r'^([A-Z]{1,5})\s*[—–]', line.strip())
            if m:
                result.avoid.append(m.group(1))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_latest_warren(force_refresh: bool = False) -> WarrenResult:
    """
    Parse and return the latest successful Warren AI daily run.

    Returns a WarrenResult with freshness='NONE' if no successful run found.
    """
    folder = _find_latest_successful_dir()
    if folder is None:
        return WarrenResult(
            date="", freshness="NONE", success=False,
            output_path="", generated_at="",
            warnings=["No successful Warren AI run found in the last 7 days"],
        )

    run_date = os.path.basename(folder)  # "YYYY-MM-DD"
    summary  = _parse_run_summary(folder)

    result = WarrenResult(
        date=run_date,
        freshness=_freshness(run_date),
        success=summary.get("success", False),
        output_path=folder,
        generated_at=summary.get("completedAt", ""),
    )

    # Parse each output file
    final_text  = _read_file(folder, "final_action_plan.md")
    step02_text = _read_file(folder, "step_02.md")
    step03_text = _read_file(folder, "step_03.md")
    step04_text = _read_file(folder, "step_04.md")

    _parse_final_plan(final_text,  result)
    _parse_step02(step02_text, result)
    _parse_step03(step03_text, result)
    _parse_step04(step04_text, result)

    return result


def warren_to_dict(r: WarrenResult) -> dict:
    """Convert a WarrenResult to a JSON-serialisable dict for the API endpoint."""
    def tr_dict(tr: TickerRow) -> dict:
        return {
            "ticker":     tr.ticker,
            "sector_etf": tr.sector_etf,
            "price":      tr.price,
            "vs_20ema":   tr.vs_20ema,
            "vs_50ema":   tr.vs_50ema,
            "vs_200ema":  tr.vs_200ema,
            "rsi":        tr.rsi,
            "setup":      tr.setup,
            "fv_upside":  tr.fv_upside,
            "eps":        tr.eps,
            "rank":       tr.rank,
            "verdict":    tr.verdict,
            "entry_zone": tr.entry_zone,
            "stop":       tr.stop,
            "target":     tr.target,
            "rr":         tr.rr,
            "size_note":  tr.size_note,
            "key_risk":   tr.key_risk,
        }

    return {
        "date":             r.date,
        "freshness":        r.freshness,
        "success":          r.success,
        "output_path":      r.output_path,
        "generated_at":     r.generated_at,
        "regime":           r.regime,
        "regime_one_liner": r.regime_one_liner,
        "sizing_note":      r.sizing_note,
        "top_sectors":      r.top_sectors,
        "sector_reasons":   r.sector_reasons,
        "all_tickers":      r.all_tickers,
        "ticker_sector":    r.ticker_sector,
        "actionable":       r.actionable,
        "watch_tomorrow":   r.watch_tomorrow,
        "avoid":            r.avoid,
        "top3_trades":      r.top3_trades,
        "watch_tickers":    r.watch_tickers,
        "avoid_sectors":    r.avoid_sectors,
        "ticker_rows":      {t: tr_dict(tr) for t, tr in r.ticker_rows.items()},
        "final_plan_text":  r.final_plan_text,
        "warnings":         r.warnings,
    }


# ---------------------------------------------------------------------------
# Portfolio monitor outputs (Prompts 6 & 7) — separate from 5-step workflow
# ---------------------------------------------------------------------------

@dataclass
class PortfolioMonitorResult:
    """Latest portfolio monitor run (Warren prompts 6 & 7)."""
    date:              str
    freshness:        str   # FRESH | STALE | NONE | FAILED
    success:          bool
    output_path:      str
    generated_at:     str
    positions_used:   list[dict] = field(default_factory=list)
    step06_text:      str = ""
    step07_text:      str = ""
    warnings:         list[str] = field(default_factory=list)
    ticker_actions:   list[dict] = field(default_factory=list)  # {ticker, action_today}
    improved:         list[str] = field(default_factory=list)
    increased_risk:   list[str] = field(default_factory=list)


def _list_output_date_dirs() -> list[str]:
    if not os.path.isdir(WARREN_OUTPUT_DIR):
        return []
    out: list[str] = []
    for name in os.listdir(WARREN_OUTPUT_DIR):
        if len(name) == 10 and name[4] == "-" and name[7] == "-":
            out.append(name)
    out.sort(reverse=True)
    return out


def _find_latest_portfolio_dir() -> Optional[str]:
    """Most recent day folder with any portfolio monitor artifact."""
    for d in _list_output_date_dirs():
        folder = os.path.join(WARREN_OUTPUT_DIR, d)
        for name in ("portfolio_step_07.md", "portfolio_summary.json", "portfolio_step_06.md"):
            if os.path.isfile(os.path.join(folder, name)):
                return folder
    return None


def _read_portfolio_summary(folder: str) -> dict:
    p = os.path.join(folder, "portfolio_summary.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _parse_pipe_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Return (header_cells, data_rows) for the first pipe-style markdown table found."""
    lines = text.splitlines()
    header: list[str] = []
    rows: list[list[str]] = []
    in_table = False
    for line in lines:
        raw = line.strip()
        if "|" not in raw or raw.count("|") < 2:
            if in_table and rows:
                break
            continue
        if re.match(r"^\|\s*:?-{3,}", raw):
            in_table = True
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if not cells:
            continue
        if not in_table:
            header = [c.lower() for c in cells]
            in_table = True
            continue
        rows.append(cells)
    return header, rows


def _parse_tab_action_rows(step07: str) -> list[dict]:
    """Parse tab-separated execution-style rows (Rank Ticker ... Action today ...)."""
    out: list[dict] = []
    header_line_idx = -1
    lines = step07.splitlines()
    for i, line in enumerate(lines):
        if "\t" in line and "Ticker" in line and ("Action" in line or "Verdict" in line):
            header_line_idx = i
            headers = [h.strip().lower() for h in line.split("\t")]
            break
    if header_line_idx < 0:
        return out
    headers = [h.strip().lower() for h in lines[header_line_idx].split("\t")]
    try:
        ti = next(j for j, h in enumerate(headers) if h == "ticker")
    except StopIteration:
        ti = 1 if len(headers) > 1 else 0
    ai = -1
    for j, h in enumerate(headers):
        if "action" in h and "today" in h.replace(" ", ""):
            ai = j
            break
    if ai < 0:
        for j, h in enumerate(headers):
            if h.startswith("action"):
                ai = j
                break
    if ai < 0:
        return out
    for line in lines[header_line_idx + 1:]:
        if not line.strip() or line.startswith("Then give") or line.startswith("Holdings"):
            break
        parts = line.split("\t")
        if len(parts) <= max(ti, ai):
            continue
        t = parts[ti].strip().upper()
        if not re.match(r"^[A-Z]{1,5}$", t):
            continue
        act = parts[ai].strip()
        if act:
            out.append({"ticker": t, "action_today": act})
    return out


def _parse_portfolio_actions(step07: str) -> list[dict]:
    """Best-effort: extract Ticker + Action today from tab or markdown pipe table."""
    tab_rows = _parse_tab_action_rows(step07)
    if tab_rows:
        return tab_rows
    header, rows = _parse_pipe_table(step07)
    if not rows:
        return []
    # locate column indices
    def idx(name: str) -> int:
        for i, h in enumerate(header):
            if name in h.replace(" ", ""):
                return i
        return -1

    ti = idx("ticker")
    ai = -1
    for i, h in enumerate(header):
        if "actiontoday" in h.replace(" ", "").lower() or h.lower().startswith("action"):
            ai = i
            break
    if ti < 0 or ai < 0:
        # Fallback: column 2 often Action today per prompt template
        ti, ai = 0, min(2, max(0, len(header) - 1))

    out: list[dict] = []
    for cells in rows:
        if ti >= len(cells) or ai >= len(cells):
            continue
        t = cells[ti].strip().upper()
        if not re.match(r"^[A-Z]{1,5}$", t):
            continue
        act = cells[ai].strip()
        if act:
            out.append({"ticker": t, "action_today": act})
    return out


def _extract_tickers_after_heading(text: str, heading_sub: str, max_n: int = 8) -> list[str]:
    tickers: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if heading_sub.lower() in line.lower():
            for j in range(i + 1, min(i + 12, len(lines))):
                for m in re.finditer(r"\b([A-Z]{1,5})\b", lines[j]):
                    t = m.group(1)
                    if t in {"TOP", "THE", "AND", "FOR", "NOT", "ALL", "RSI", "EMA", "VIX"}:
                        continue
                    if t not in tickers:
                        tickers.append(t)
                    if len(tickers) >= max_n:
                        return tickers
            break
    return tickers


def get_latest_portfolio_monitor() -> PortfolioMonitorResult:
    folder = _find_latest_portfolio_dir()
    if folder is None:
        return PortfolioMonitorResult(
            date="", freshness="NONE", success=False,
            output_path="", generated_at="",
            warnings=["No portfolio monitor output found"],
        )

    run_date = os.path.basename(folder)
    summ = _read_portfolio_summary(folder)
    success = bool(summ.get("success"))
    step06 = _read_file(folder, "portfolio_step_06.md")
    step07 = _read_file(folder, "portfolio_step_07.md")

    freshness = _freshness(run_date)
    if not success and (step06 or step07):
        freshness = "FAILED"

    positions_used: list[dict] = []
    if isinstance(summ.get("positionsUsed"), list):
        positions_used = summ["positionsUsed"]  # type: ignore[assignment]

    warnings: list[str] = []
    actions = _parse_portfolio_actions(step07)
    if step07 and not actions:
        warnings.append("Could not parse portfolio action table from step 07 (format may differ).")

    improved = _extract_tickers_after_heading(step06, "improved", 6)
    risk = _extract_tickers_after_heading(step06, "increased risk", 6)

    return PortfolioMonitorResult(
        date=run_date,
        freshness=freshness,
        success=success,
        output_path=folder,
        generated_at=str(summ.get("completedAt") or ""),
        positions_used=positions_used,
        step06_text=step06[:120_000],
        step07_text=step07[:120_000],
        warnings=warnings,
        ticker_actions=actions,
        improved=improved,
        increased_risk=risk,
    )


def portfolio_monitor_to_dict(r: PortfolioMonitorResult) -> dict:
    return {
        "date":             r.date,
        "freshness":        r.freshness,
        "success":          r.success,
        "output_path":      r.output_path,
        "generated_at":     r.generated_at,
        "positions_used":   r.positions_used,
        "portfolio_step_06": r.step06_text,
        "portfolio_step_07": r.step07_text,
        "ticker_actions":   r.ticker_actions,
        "improved":         r.improved,
        "increased_risk":   r.increased_risk,
        "warnings":         r.warnings,
    }


def classify_owned_vs_monitor(ticker: str, pm: PortfolioMonitorResult) -> str:
    """
    Map portfolio monitor + parsed actions to a short UI label for an owned name.
    """
    t = ticker.strip().upper()
    if not pm.success or not pm.step07_text:
        return "Already owned (no monitor run)"
    for row in pm.ticker_actions:
        if row.get("ticker") == t:
            act = (row.get("action_today") or "").lower()
            if "add allowed" in act:
                return "Already owned — add candidate"
            if "trim" in act:
                return "Already owned — trim risk"
            if "exit" in act:
                return "Already owned — exit risk"
            if "hold only" in act or "no action" in act:
                return "Already owned — hold only"
            if "wait" in act:
                return "Already owned — wait"
            return "Already owned — " + (row.get("action_today") or "see report")
    if t in pm.increased_risk:
        return "Already owned — trim risk (monitor list)"
    if t in pm.improved:
        return "Already owned — thesis improved"
    if pm.step07_text:
        return "Already owned — see monitor report"
    return "Already owned"
