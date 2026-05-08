"""
Trim Monitor — core analysis engine.

For each position, fetches price history from yfinance and computes
technical indicators, trim flags, and a final verdict.

All thresholds and rules are defined as module-level constants so you
can tweak them without digging through code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

import data as db

logger = logging.getLogger(__name__)

# Workflow values that must never be treated as a sector ETF code
_STATUS_LIKE: frozenset[str] = frozenset(s.lower() for s in db.ALL_STATUSES)


def _resolve_sector_field(position: dict) -> str:
    """
    Return the sector ETF code for engine + UI tables.

    If ``sector`` is empty or accidentally holds a workflow status string,
    fall back to ``sector_raw`` and normalize to an ETF via ``data``.
    """
    raw = str(position.get("sector", "") or "").strip()
    if raw.lower() in _STATUS_LIKE:
        raw = str(position.get("sector_raw", "") or "").strip()
    if not raw:
        raw = str(position.get("sector_raw", "") or "").strip()
    if not raw:
        return ""
    up = raw.upper()
    if up in db.SECTOR_ETFS:
        return up
    return db.normalize_sector(raw)

# ---------------------------------------------------------------------------
# Tunable thresholds — edit these to change behavior
# ---------------------------------------------------------------------------

PCT_ABOVE_20EMA_THRESHOLD = 8.0      # Flag #1
PCT_ABOVE_50EMA_THRESHOLD = 12.0     # Flag #2
RSI_THRESHOLD = 70.0                 # Flag #3
GAIN_THRESHOLD = 20.0                # Flag #4
WEIGHT_OVERAGE_THRESHOLD = 1.25      # Flag #5: 25% over target

VERDICT_LEVELS = ["Hold", "Watch", "Trim candidate", "Strong trim"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PositionResult:
    """Result of analyzing a single position. Includes errors if they occurred."""
    ticker: str
    sector: str = ""
    sector_status: Optional[str] = None
    conviction: str = ""
    target_weight: Optional[float] = None
    current_weight: Optional[float] = None
    cost_basis: Optional[float] = None
    shares: Optional[float] = None
    investingpro_url: str = ""
    notes: str = ""

    # Computed fields
    current_price: Optional[float] = None
    pct_gain: Optional[float] = None
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    pct_above_20ema: Optional[float] = None
    pct_above_50ema: Optional[float] = None
    rsi: Optional[float] = None
    atr: Optional[float] = None
    atr_multiple: Optional[float] = None
    return_30d: Optional[float] = None
    return_90d: Optional[float] = None

    sector_direction: Optional[str] = None  # directional acceleration from sector_engine

    flags: list = field(default_factory=list)
    verdict: str = "Hold"
    error: Optional[str] = None
    last_updated: Optional[str] = None
    status: str = "in_position"  # workflow status from DB

    @property
    def n_flags(self) -> int:
        return len(self.flags)

    @property
    def market_value(self) -> Optional[float]:
        if self.shares is not None and self.current_price is not None:
            return self.shares * self.current_price
        return None


# ---------------------------------------------------------------------------
# Indicator math (no external TA libraries — keep deps small)
# ---------------------------------------------------------------------------

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# ---------------------------------------------------------------------------
# Price fetching with error capture
# ---------------------------------------------------------------------------

def fetch_history(ticker: str, period: str = "1y") -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Fetch OHLC history. Returns (data, error_msg). Never raises."""
    try:
        data = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if data is None or data.empty:
            return None, f"No data returned for {ticker}"
        # Need at least 50 bars for meaningful EMAs
        if len(data) < 50:
            return None, f"Only {len(data)} bars available for {ticker} — need 50+"
        return data, None
    except Exception as e:
        logger.exception("yfinance error for %s", ticker)
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Flag and verdict logic
# ---------------------------------------------------------------------------

def compute_flags(r: PositionResult) -> list[str]:
    """Build the list of active flags for a position. Order matches the spec."""
    flags = []

    if r.pct_above_20ema is not None and r.pct_above_20ema > PCT_ABOVE_20EMA_THRESHOLD:
        flags.append(f"Above 20 EMA by {r.pct_above_20ema:.1f}%")

    if r.pct_above_50ema is not None and r.pct_above_50ema > PCT_ABOVE_50EMA_THRESHOLD:
        flags.append(f"Above 50 EMA by {r.pct_above_50ema:.1f}%")

    if r.rsi is not None and r.rsi > RSI_THRESHOLD:
        flags.append(f"RSI {r.rsi:.0f}")

    if r.pct_gain is not None and r.pct_gain > GAIN_THRESHOLD:
        flags.append(f"Gain {r.pct_gain:.0f}%")

    if (r.target_weight is not None
            and r.current_weight is not None
            and r.target_weight > 0
            and r.current_weight > r.target_weight * WEIGHT_OVERAGE_THRESHOLD):
        flags.append("Weight 25% over target")

    # Flag #6: sector status, direction-aware
    # Only applied for active positions (not watchlist / researching).
    if r.status in ("in_position", "trimming_zone", ""):
        if r.sector_status == "Weakening":
            if r.sector_direction == "↓ deteriorating":
                flags.append("Sector weakening & deteriorating")
            else:
                flags.append("Sector weakening")
        elif r.sector_status == "Leading" and r.sector_direction == "↓ deteriorating":
            flags.append("Sector: leading but fading ⚠")
    else:
        # Watchlist / researching: plain quadrant note only
        if r.sector_status == "Weakening":
            flags.append("Sector weakening")

    # Flag #7 (earnings soon) is a placeholder — deliberately not active in Phase 1
    return flags


def derive_verdict(n_flags: int, conviction: str, sector_status: Optional[str],
                   sector_direction: Optional[str] = None) -> str:
    """
    Initial verdict from flag count, then apply conviction and sector modifiers.
    Order: count -> conviction downgrade -> sector modifiers.

    Sector modifiers:
    - Weakening + (3+ flags OR direction is "↓ deteriorating") → upgrade one level.
    - Leading + "↓ deteriorating" adds a caution flag (handled in compute_flags).
    """
    if n_flags >= 4:
        idx = 3
    elif n_flags == 3:
        idx = 2
    elif n_flags == 2:
        idx = 1
    else:
        idx = 0

    # Conviction A downgrades by one, but never below Watch when 3+ flags
    if (conviction or "").strip().upper() == "A":
        new_idx = max(0, idx - 1)
        if n_flags >= 3:
            new_idx = max(1, new_idx)
        idx = new_idx

    # Sector weakening: upgrade if 3+ flags OR sector is also deteriorating
    if sector_status == "Weakening" and (
        n_flags >= 3 or sector_direction == "↓ deteriorating"
    ):
        idx = min(len(VERDICT_LEVELS) - 1, idx + 1)

    return VERDICT_LEVELS[idx]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_position(position: dict, sector_status: Optional[str] = None,
                     sector_direction: Optional[str] = None) -> PositionResult:
    """Analyze a single position dict. Returns a PositionResult (may carry an error)."""
    ticker = str(position["ticker"]).strip().upper()

    result = PositionResult(
        ticker=ticker,
        sector=_resolve_sector_field(position),
        sector_status=sector_status,
        sector_direction=sector_direction,
        conviction=str(position.get("conviction", "") or "").strip().upper(),
        target_weight=_to_float(position.get("target_weight")),
        current_weight=_to_float(position.get("current_weight")),
        cost_basis=_to_float(position.get("cost_basis")),
        shares=_to_float(position.get("shares")),
        investingpro_url=str(position.get("investingpro_url", "") or ""),
        notes=str(position.get("notes", "") or ""),
        status=str(position.get("status", "in_position") or "in_position"),
    )

    data, err = fetch_history(ticker)
    if err:
        result.error = err
        return result

    close = data["Close"]
    result.current_price = float(close.iloc[-1])
    result.last_updated = data.index[-1].strftime("%Y-%m-%d")

    result.ema_20 = float(ema(close, 20).iloc[-1])
    result.ema_50 = float(ema(close, 50).iloc[-1])
    if len(close) >= 200:
        result.ema_200 = float(ema(close, 200).iloc[-1])

    result.pct_above_20ema = (result.current_price - result.ema_20) / result.ema_20 * 100
    result.pct_above_50ema = (result.current_price - result.ema_50) / result.ema_50 * 100

    rsi_series = rsi(close, 14)
    if not rsi_series.dropna().empty:
        result.rsi = float(rsi_series.iloc[-1])

    atr_series = atr(data, 14)
    if not atr_series.dropna().empty:
        result.atr = float(atr_series.iloc[-1])

    if result.cost_basis and result.cost_basis > 0:
        result.pct_gain = (result.current_price - result.cost_basis) / result.cost_basis * 100
        if result.atr and result.atr > 0:
            result.atr_multiple = (result.current_price - result.cost_basis) / result.atr

    if len(close) >= 30:
        ref_30 = close.iloc[-30]
        if ref_30 > 0:
            result.return_30d = (result.current_price - ref_30) / ref_30 * 100
    if len(close) >= 90:
        ref_90 = close.iloc[-90]
        if ref_90 > 0:
            result.return_90d = (result.current_price - ref_90) / ref_90 * 100

    result.flags = compute_flags(result)
    result.verdict = derive_verdict(
        result.n_flags, result.conviction,
        result.sector_status, result.sector_direction,
    )
    return result


def analyze_portfolio(positions: list[dict],
                      sector_status_map: Optional[dict] = None,
                      sector_direction_map: Optional[dict] = None) -> list[PositionResult]:
    """
    Analyze every position.

    sector_status_map:    {sector_etf: 'Leading'|'Improving'|'Weakening'|'Lagging'}
    sector_direction_map: {sector_etf: '↑ strengthening'|'↓ deteriorating'|...}
    """
    sector_status_map    = sector_status_map    or {}
    sector_direction_map = sector_direction_map or {}
    results = []
    for pos in positions:
        sector_etf       = _resolve_sector_field(pos)
        sector_status    = sector_status_map.get(sector_etf)
        sector_direction = sector_direction_map.get(sector_etf)
        results.append(analyze_position(
            pos,
            sector_status=sector_status,
            sector_direction=sector_direction,
        ))
    return results


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if not np.isnan(f) else None
    except (TypeError, ValueError):
        return None
