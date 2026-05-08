"""
selector.py — Sector entry analysis and swing candidate ranking.

This is the entry-side counterpart to trim_monitor.py.

Trim Monitor asks: "What should I trim?"
Selector  asks:   "Where should I focus new capital next?"

All thresholds are module-level constants — edit them to change behavior.
No AI, no scraping, no PDF parsing: purely rule-based and transparent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# Reuse indicator math from trim_monitor — no duplication
from trim_monitor import ema, rsi, fetch_history

# ---------------------------------------------------------------------------
# Constants — edit these to change scoring / flagging behavior
# ---------------------------------------------------------------------------

SECTORS = [
    "XLK", "XLF", "XLE", "XLI", "XLY",
    "XLP", "XLU", "XLV", "XLRE", "XLB", "XLC",
]

SECTOR_NAMES: dict[str, str] = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discr.",
    "XLP":  "Consumer Staples",
    "XLU":  "Utilities",
    "XLV":  "Health Care",
    "XLRE": "Real Estate",
    "XLB":  "Materials",
    "XLC":  "Communications",
}

# Sector board: base score by RRG quadrant
# Improving = 85: may be early in the move — best for fresh entries
# Leading   = 70: strong but often extended
# Unknown   = 40: neutral fallback when data is unavailable
# Weakening = 30: losing momentum — lower priority for longs
# Lagging   = 15: weakest — avoid for new long entries
QUADRANT_BASE_SCORE: dict[str, int] = {
    "Improving": 85,
    "Leading":   70,
    "Unknown":   40,
    "Weakening": 30,
    "Lagging":   15,
}

# Sector ETF technical modifiers
SECTOR_EXTENDED_RSI          = 70.0   # ETF overbought → reduce score
SECTOR_EXTENDED_PCT_ABOVE_50 = 10.0   # ETF too far above 50 EMA → reduce score
SECTOR_CLEAN_RSI             = 60.0   # ETF not overbought → add score
SECTOR_CLEAN_PCT_ABOVE_50    = 5.0    # ETF near 50 EMA → add score
SECTOR_EXTENDED_MODIFIER     = -15    # Applied when ETF is extended
SECTOR_CLEAN_MODIFIER        = +10    # Applied when ETF has room to run

# Candidate entry flag thresholds (mirror of trim_monitor thresholds)
RSI_ENTRY_MIN          = 35.0  # Flag 3 lower bound: not collapsing
RSI_ENTRY_MAX          = 60.0  # Flag 3 upper bound: not overbought
MAX_PCT_ABOVE_20EMA    = 3.0   # Flag 1: within 3 % of 20 EMA or below it
MAX_PCT_ABOVE_50EMA    = 5.0   # Flag 2: within 5 % of 50 EMA or below it
RETURN_30D_THRESHOLD   = 0.0   # Flag 5: positive 30-day return

ENTRY_VERDICT_LEVELS = ["Skip", "Watch", "Setup forming", "Strong entry"]

ETF_CACHE_TTL = 3600  # seconds (1 hour)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SectorBoardRow:
    etf:               str
    name:              str
    quadrant:          str
    etf_rsi:           Optional[float]
    etf_pct_above_50:  Optional[float]
    etf_30d_return:    Optional[float]
    candidate_count:   int
    entry_score:       int
    modifier:          int
    reason:            str
    error:             Optional[str] = None


@dataclass
class CandidateResult:
    ticker:                    str
    sector:                    str
    conviction:                str
    current_price:             Optional[float] = None
    rsi:                       Optional[float] = None
    pct_vs_20ema:              Optional[float] = None
    pct_vs_50ema:              Optional[float] = None
    above_200ema:              Optional[bool]  = None
    return_30d:                Optional[float] = None
    rs_vs_sector:              Optional[float] = None
    flags:                     list            = field(default_factory=list)
    verdict:                   str             = "Skip"
    candidate_score:           float           = 0.0
    investingpro_url:          str             = ""
    reason:                    str             = ""
    error:                     Optional[str]   = None
    notes:                     str             = ""
    fair_value_upside_pct:     Optional[float] = None
    financial_health_score:    str             = ""
    analyst_target_upside_pct: Optional[float] = None
    manual_fundamental_note:   str             = ""


# ---------------------------------------------------------------------------
# ETF data cache (batch download of all 11 sector ETFs)
# ---------------------------------------------------------------------------

_etf_cache: Optional[tuple] = None   # (info_dict, close_map)
_etf_cache_ts: float = 0.0


def _fetch_etf_data() -> tuple[dict, dict[str, pd.Series]]:
    """
    Batch-download 1-year OHLC for all 11 sector ETFs.
    Returns (info_dict, close_map) where:
      info_dict  = {etf: {"rsi", "pct_above_50ema", "return_30d", "error"}}
      close_map  = {etf: pd.Series of adjusted close prices}
    Results are cached for ETF_CACHE_TTL seconds.
    """
    global _etf_cache, _etf_cache_ts
    now = time.monotonic()
    if _etf_cache is not None and (now - _etf_cache_ts) < ETF_CACHE_TTL:
        return _etf_cache

    try:
        raw = yf.download(
            SECTORS, period="1y", progress=False,
            auto_adjust=True, actions=False,
        )
        close_df = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    except Exception as exc:
        empty_info = {etf: {"rsi": None, "pct_above_50ema": None,
                            "return_30d": None, "error": str(exc)}
                      for etf in SECTORS}
        result = (empty_info, {})
        _etf_cache, _etf_cache_ts = result, now
        return result

    info: dict = {}
    close_map: dict[str, pd.Series] = {}

    for etf in SECTORS:
        if etf not in close_df.columns:
            info[etf] = {"rsi": None, "pct_above_50ema": None,
                         "return_30d": None, "error": f"No data for {etf}"}
            continue

        s = close_df[etf].dropna()
        if len(s) < 50:
            info[etf] = {"rsi": None, "pct_above_50ema": None,
                         "return_30d": None,
                         "error": f"Only {len(s)} bars for {etf}"}
            continue

        cur          = float(s.iloc[-1])
        e50          = float(ema(s, 50).iloc[-1])
        pct_above_50 = (cur - e50) / e50 * 100

        rsi_s    = rsi(s, 14).dropna()
        etf_rsi  = round(float(rsi_s.iloc[-1]), 1) if not rsi_s.empty else None

        ret_30d = None
        if len(s) >= 30:
            ref = float(s.iloc[-30])
            if ref > 0:
                ret_30d = round((cur - ref) / ref * 100, 2)

        info[etf] = {
            "rsi":             etf_rsi,
            "pct_above_50ema": round(pct_above_50, 2),
            "return_30d":      ret_30d,
            "error":           None,
        }
        close_map[etf] = s

    _etf_cache, _etf_cache_ts = (info, close_map), now
    return _etf_cache


def get_etf_close_map() -> dict[str, pd.Series]:
    """Return cached ETF close series (fetches if cache is cold)."""
    return _fetch_etf_data()[1]


# ---------------------------------------------------------------------------
# Sector board scoring
# ---------------------------------------------------------------------------

def compute_sector_score(
    quadrant: str,
    etf_rsi: Optional[float],
    etf_pct_above_50: Optional[float],
) -> tuple[int, int, str]:
    """
    Return (score, modifier, reason_string).

    Score formula:
      base     = QUADRANT_BASE_SCORE[quadrant]
      modifier = SECTOR_EXTENDED_MODIFIER  if ETF is extended
               = SECTOR_CLEAN_MODIFIER     if ETF has room
               = 0                         otherwise
      score    = clamp(base + modifier, 0, 100)
    """
    base     = QUADRANT_BASE_SCORE.get(quadrant, 40)
    parts    = [f"Quadrant: {quadrant} (base {base})"]
    modifier = 0

    if etf_rsi is not None and etf_pct_above_50 is not None:
        extended = (etf_rsi > SECTOR_EXTENDED_RSI
                    or etf_pct_above_50 > SECTOR_EXTENDED_PCT_ABOVE_50)
        clean    = (etf_rsi < SECTOR_CLEAN_RSI
                    and etf_pct_above_50 < SECTOR_CLEAN_PCT_ABOVE_50)
        if extended:
            modifier = SECTOR_EXTENDED_MODIFIER
            parts.append(
                f"ETF extended (RSI {etf_rsi:.0f}, "
                f"{etf_pct_above_50:+.1f}% vs 50 EMA) → {modifier:+d}"
            )
        elif clean:
            modifier = SECTOR_CLEAN_MODIFIER
            parts.append(
                f"ETF not extended (RSI {etf_rsi:.0f}, "
                f"{etf_pct_above_50:+.1f}% vs 50 EMA) → {modifier:+d}"
            )
        else:
            parts.append(f"ETF neutral (RSI {etf_rsi:.0f})")
    else:
        parts.append("ETF data unavailable")

    score = max(0, min(100, base + modifier))
    return score, modifier, ". ".join(parts)


def analyze_sector_board(
    all_positions: list[dict],
    sector_statuses: dict,
) -> list[SectorBoardRow]:
    """
    Build the sector rotation board for all 11 sector ETFs.
    Returns rows sorted by entry_score descending.
    Sectors with 0 watchlist candidates still appear.
    """
    etf_info, _ = _fetch_etf_data()

    # Count watchlist candidates per sector
    candidate_counts: dict[str, int] = {}
    for pos in all_positions:
        if pos.get("status") == "watchlist":
            s = pos.get("sector", "").strip().upper()
            if s:
                candidate_counts[s] = candidate_counts.get(s, 0) + 1

    rows: list[SectorBoardRow] = []
    for etf in SECTORS:
        ss       = sector_statuses.get(etf)
        quadrant = ss.quadrant if ss else "Unknown"
        info     = etf_info.get(etf, {})

        score, mod, reason = compute_sector_score(
            quadrant,
            info.get("rsi"),
            info.get("pct_above_50ema"),
        )
        rows.append(SectorBoardRow(
            etf            = etf,
            name           = SECTOR_NAMES.get(etf, etf),
            quadrant       = quadrant,
            etf_rsi        = info.get("rsi"),
            etf_pct_above_50 = info.get("pct_above_50ema"),
            etf_30d_return = info.get("return_30d"),
            candidate_count= candidate_counts.get(etf, 0),
            entry_score    = score,
            modifier       = mod,
            reason         = reason,
            error          = info.get("error"),
        ))

    rows.sort(key=lambda r: r.entry_score, reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Candidate entry flags and verdicts
# ---------------------------------------------------------------------------

def compute_relative_strength(
    stock_close: pd.Series,
    sector_close: pd.Series,
    days: int = 30,
) -> float:
    """
    Return (stock 30-day return) − (sector ETF 30-day return), in percent.
    Positive value = stock outperforming its sector ETF.
    Raises ValueError if either series has fewer than `days` bars.
    """
    if len(stock_close) < days or len(sector_close) < days:
        raise ValueError("Insufficient data for relative strength")

    s_end = float(stock_close.iloc[-1]);  s_beg = float(stock_close.iloc[-days])
    e_end = float(sector_close.iloc[-1]); e_beg = float(sector_close.iloc[-days])

    if s_beg == 0 or e_beg == 0:
        raise ValueError("Zero reference price")

    return round((s_end / s_beg - 1) * 100 - (e_end / e_beg - 1) * 100, 2)


def compute_entry_flags(r: CandidateResult) -> list[str]:
    """
    Mirror image of trim_monitor.compute_flags.
    Trim flags detect over-extension; entry flags detect clean setups.

    Flag 1: Price within MAX_PCT_ABOVE_20EMA of 20 EMA, or below it
    Flag 2: Price within MAX_PCT_ABOVE_50EMA of 50 EMA, or below it
    Flag 3: RSI between RSI_ENTRY_MIN and RSI_ENTRY_MAX
    Flag 4: Price above 200 EMA (broader uptrend intact)
    Flag 5: Positive 30-day return (momentum confirms)
    Flag 6: Outperforming sector ETF over 30 days
    """
    flags = []

    if r.pct_vs_20ema is not None and r.pct_vs_20ema <= MAX_PCT_ABOVE_20EMA:
        flags.append(f"Near 20 EMA ({r.pct_vs_20ema:+.1f}%)")

    if r.pct_vs_50ema is not None and r.pct_vs_50ema <= MAX_PCT_ABOVE_50EMA:
        flags.append(f"Near 50 EMA ({r.pct_vs_50ema:+.1f}%)")

    if r.rsi is not None and RSI_ENTRY_MIN <= r.rsi <= RSI_ENTRY_MAX:
        flags.append(f"RSI {r.rsi:.0f}")

    if r.above_200ema is True:
        flags.append("Above 200 EMA")

    if r.return_30d is not None and r.return_30d > RETURN_30D_THRESHOLD:
        flags.append(f"30d return {r.return_30d:+.1f}%")

    if r.rs_vs_sector is not None and r.rs_vs_sector > 0:
        flags.append(f"RS vs sector {r.rs_vs_sector:+.1f}%")

    return flags


def derive_entry_verdict(n_flags: int, conviction: str = "") -> str:
    """
    Map flag count to entry verdict, then apply conviction modifier.

    0–1 flags → Skip
    2   flags → Watch
    3–4 flags → Setup forming
    5–6 flags → Strong entry

    Conviction A upgrades one level; never above Strong entry.
    """
    if n_flags >= 5:
        idx = 3  # Strong entry
    elif n_flags >= 3:
        idx = 2  # Setup forming
    elif n_flags == 2:
        idx = 1  # Watch
    else:
        idx = 0  # Skip

    if (conviction or "").strip().upper() == "A":
        idx = min(3, idx + 1)

    return ENTRY_VERDICT_LEVELS[idx]


def compute_candidate_score(r: CandidateResult) -> float:
    """
    Transparent 0–100 ranking score. Not a buy signal — a ranking helper.

    Formula:
      entry_flag_score  = (len(flags) / 6) × 70
      conviction_bonus  = A→15, B→8, else 0
      fundamental_bonus = up to +15:
                          financial_health ≥ 4 (or "Good"/"Excellent") → +5
                          fair_value_upside_pct > 10 %                 → +5
                          analyst_target_upside_pct > 10 %             → +5
      score = clamp(entry_flag_score + conviction_bonus + fundamental_bonus, 0, 100)
    """
    entry_flag_score = len(r.flags) / 6 * 70

    conv = (r.conviction or "").strip().upper()
    conviction_bonus = 10 if conv == "A" else 5 if conv == "B" else 0

    fundamental_bonus = 0
    fh = r.financial_health_score or ""
    try:
        if float(fh) >= 4:
            fundamental_bonus += 5
    except (TypeError, ValueError):
        if any(w in fh.lower() for w in ("good", "excellent", "great", "strong")):
            fundamental_bonus += 5

    if r.fair_value_upside_pct is not None and r.fair_value_upside_pct > 10:
        fundamental_bonus += 5
    if r.analyst_target_upside_pct is not None and r.analyst_target_upside_pct > 10:
        fundamental_bonus += 5

    return round(min(100.0, max(0.0, entry_flag_score + conviction_bonus + fundamental_bonus)), 1)


def _build_reason(r: CandidateResult) -> str:
    n = len(r.flags)
    if n == 0:
        return "0 of 6 entry flags active: no technical setup currently."
    return f"{n} of 6 entry flags active: " + ", ".join(r.flags) + "."


# ---------------------------------------------------------------------------
# Candidate analysis
# ---------------------------------------------------------------------------

def analyze_candidates(
    all_positions: list[dict],
    sector_etf: str,
    etf_close: Optional[pd.Series] = None,
) -> list[CandidateResult]:
    """
    Analyze all watchlist rows whose sector matches sector_etf.
    Returns CandidateResult list sorted by candidate_score descending.
    Does not create new database tables or status values.
    """
    candidates = [
        p for p in all_positions
        if p.get("status") == "watchlist"
        and p.get("sector", "").strip().upper() == sector_etf
    ]
    results = [_analyze_one(pos, etf_close) for pos in candidates]
    results.sort(key=lambda r: r.candidate_score, reverse=True)
    return results


def _analyze_one(pos: dict, sector_close: Optional[pd.Series]) -> CandidateResult:
    ticker = str(pos.get("ticker", "")).strip().upper()
    r = CandidateResult(
        ticker                    = ticker,
        sector                    = str(pos.get("sector", "") or ""),
        conviction                = str(pos.get("conviction", "") or "").strip().upper(),
        investingpro_url          = str(pos.get("investingpro_url", "") or ""),
        notes                     = str(pos.get("notes", "") or ""),
        fair_value_upside_pct     = _to_float(pos.get("fair_value_upside_pct")),
        financial_health_score    = str(pos.get("financial_health_score", "") or ""),
        analyst_target_upside_pct = _to_float(pos.get("analyst_target_upside_pct")),
        manual_fundamental_note   = str(pos.get("manual_fundamental_note", "") or ""),
    )

    data, err = fetch_history(ticker)
    if err:
        r.error  = err
        r.reason = f"Data error: {err}"
        return r

    close          = data["Close"]
    r.current_price = float(close.iloc[-1])

    e20            = float(ema(close, 20).iloc[-1])
    e50            = float(ema(close, 50).iloc[-1])
    r.pct_vs_20ema = round((r.current_price - e20) / e20 * 100, 2)
    r.pct_vs_50ema = round((r.current_price - e50) / e50 * 100, 2)

    if len(close) >= 200:
        e200           = float(ema(close, 200).iloc[-1])
        r.above_200ema = r.current_price > e200

    rsi_s = rsi(close, 14).dropna()
    if not rsi_s.empty:
        r.rsi = round(float(rsi_s.iloc[-1]), 1)

    if len(close) >= 30:
        ref = float(close.iloc[-30])
        if ref > 0:
            r.return_30d = round((r.current_price - ref) / ref * 100, 2)

    if sector_close is not None:
        try:
            r.rs_vs_sector = compute_relative_strength(close, sector_close)
        except Exception:
            pass

    r.flags          = compute_entry_flags(r)
    r.verdict        = derive_entry_verdict(len(r.flags), r.conviction)
    r.candidate_score = compute_candidate_score(r)
    r.reason         = _build_reason(r)
    return r


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Unit tests for derive_entry_verdict
# ---------------------------------------------------------------------------

def run_tests() -> None:
    """
    Verify derive_entry_verdict against the full spec.
    Raises AssertionError on any mismatch.
    """
    cases = [
        (0, "",   "Skip"),
        (1, "",   "Skip"),
        (1, None, "Skip"),           # None conviction must not crash
        (2, "",   "Watch"),
        (3, "",   "Setup forming"),
        (4, "",   "Setup forming"),
        (5, "",   "Strong entry"),
        (6, "",   "Strong entry"),
        (0, "A",  "Watch"),          # Skip → +1 → Watch
        (2, "A",  "Setup forming"),  # Watch → +1 → Setup forming
        (4, "A",  "Strong entry"),   # Setup forming → +1 → Strong entry
        (5, "A",  "Strong entry"),   # Strong entry → capped
        (6, "A",  "Strong entry"),   # Strong entry → capped
        (3, "B",  "Setup forming"),  # B does not trigger the upgrade
        (2, "C",  "Watch"),
    ]
    errors = []
    for n, conv, expected in cases:
        got = derive_entry_verdict(n, conv)
        if got != expected:
            errors.append(
                f"derive_entry_verdict({n}, {conv!r}): "
                f"expected {expected!r}, got {got!r}"
            )
    if errors:
        raise AssertionError("\n".join(errors))
    print(f"selector.run_tests: all {len(cases)} cases passed ✓")


if __name__ == "__main__":
    run_tests()
