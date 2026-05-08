"""
chart_data.py — Daily OHLCV + indicators for the Decision Cockpit.

Source: yfinance (auto_adjust=True, daily bars).
Cache:  in-process, per ticker+months, TTL=2700s (45 min).

Public API:
    get_daily_chart_data(ticker, lookback_months=6)  →  dict
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_TTL: int = 2700  # 45 minutes

_cache: dict[str, tuple[float, dict]] = {}   # cache_key → (fetched_at, result)


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0.0, float("nan"))
    return (100 - (100 / (1 + rs))).round(2)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().round(4)


def _fv(x) -> Optional[float]:
    """Safe float conversion — returns None for NaN/None."""
    try:
        f = float(x)
        return None if (f != f) else round(f, 4)  # NaN check
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def get_daily_chart_data(ticker: str, lookback_months: int = 6) -> dict:
    """
    Fetch daily OHLCV + compute 20 EMA, 50 EMA, 200 SMA, RSI-14, ATR-14.

    Returns:
        {
            ticker, months, source, last_updated,
            rows: [ {date, open, high, low, close, volume,
                     ema20, ema50, sma200, rsi14, atr14}, ... ],
            warnings: [str, ...]
        }

    Never raises — always returns a dict (may have empty rows + warnings).
    """
    key = f"{ticker.strip().upper()}:{lookback_months}"
    now = time.time()

    # ── Cache hit ────────────────────────────────────────────────────────
    if key in _cache and (now - _cache[key][0]) < CACHE_TTL:
        return _cache[key][1]

    t        = ticker.strip().upper()
    warnings: list[str] = []

    # Fetch a longer window so 200 SMA has enough data even at the start
    fetch_months = max(lookback_months + 6, 13)

    def _fail(msg: str) -> dict:
        r = {
            "ticker": t, "months": lookback_months,
            "source": "yfinance",
            "last_updated": datetime.utcnow().isoformat(),
            "rows": [], "warnings": [msg],
        }
        _cache[key] = (now, r)
        return r

    # ── Fetch from yfinance ──────────────────────────────────────────────
    try:
        raw = yf.Ticker(t).history(
            period=f"{fetch_months}mo",
            auto_adjust=True,
            actions=False,
        )
    except Exception as exc:
        return _fail(f"yfinance fetch error: {type(exc).__name__}: {exc}")

    if raw is None or raw.empty:
        return _fail(f"No price data returned for {t}")

    # ── Normalise index ──────────────────────────────────────────────────
    raw.index = pd.to_datetime(raw.index)
    if raw.index.tz is not None:
        raw.index = raw.index.tz_localize(None)

    # ── Compute indicators on full history first (so 200 SMA is accurate) ─
    raw["ema20"]  = _ema(raw["Close"], 20)
    raw["ema50"]  = _ema(raw["Close"], 50)
    raw["sma200"] = _sma(raw["Close"], 200)
    raw["rsi14"]  = _rsi(raw["Close"], 14)
    raw["atr14"]  = _atr(raw["High"], raw["Low"], raw["Close"], 14)

    # ── Trim to requested lookback window ────────────────────────────────
    cutoff = datetime.utcnow() - timedelta(days=lookback_months * 31)
    df = raw[raw.index >= cutoff].copy()

    if df.empty:
        return _fail(f"No bars in the requested {lookback_months}-month window for {t}")

    has_200 = bool(df["sma200"].notna().any())
    if not has_200:
        warnings.append("Not enough history for 200 SMA — will be omitted from chart")

    # ── Serialise ────────────────────────────────────────────────────────
    rows = []
    for dt, row in df.iterrows():
        rows.append({
            "date":   dt.strftime("%Y-%m-%d"),
            "open":   _fv(row.get("Open")),
            "high":   _fv(row.get("High")),
            "low":    _fv(row.get("Low")),
            "close":  _fv(row.get("Close")),
            "volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else None,
            "ema20":  _fv(row["ema20"]),
            "ema50":  _fv(row["ema50"]),
            "sma200": _fv(row["sma200"]) if has_200 else None,
            "rsi14":  _fv(row["rsi14"]),
            "atr14":  _fv(row["atr14"]),
        })

    result = {
        "ticker":       t,
        "months":       lookback_months,
        "source":       "yfinance",
        "last_updated": datetime.utcnow().isoformat(),
        "rows":         rows,
        "warnings":     warnings,
    }
    _cache[key] = (now, result)
    return result
